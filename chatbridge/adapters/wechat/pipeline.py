"""WeChat export pipeline.

Wraps the local tools/wechat steps（也可用 CHATBRIDGE_WECHAT_TOOLS 覆盖）：

    wechat4_extract_keys -> decrypt -> wechat_media_index
    -> find_image_key -> wechat4_decode_images -> wechat4_export_md

The only logic added here is account discovery/verification, contract
normalisation, atomic placement into the spool and manifest bookkeeping.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import cipher, normalize

_LOCAL_TOOLS = Path(__file__).resolve().parents[2] / "tools" / "wechat"
DEFAULT_TOOLS = Path(os.environ.get(
  "CHATBRIDGE_WECHAT_TOOLS",
  str(_LOCAL_TOOLS if _LOCAL_TOOLS.is_dir() else Path.home() / "qqmd" / "fmt"),
))
DB_SUFFIXES = (".db", ".db-wal", ".db-shm")


class SetupError(RuntimeError):
  """Missing prerequisite: client, keys, tools. Maps to exit code 3."""


@dataclass
class Account:
  id: str                      # directory name, e.g. wxid_abc_058d
  wxid: str                    # wxid_abc
  db_dir: Path
  msg_dir: Path | None = None
  errors: list[str] = field(default_factory=list)
  conversations: int = 0
  messages: int = 0
  media: int = 0
  applied_wal: int = 0
  skipped: bool = False

  @property
  def attach_dir(self) -> Path | None:
    return self.msg_dir / "attach" if self.msg_dir else None


# ---------------------------------------------------------------- platform
def is_windows() -> bool:
  return sys.platform == "win32"


def to_windows_path(path: Path) -> str:
  p = str(path)
  if not is_windows() and p.startswith("/mnt/") and len(p) > 6 and p[5].isalpha():
    return p[5].upper() + ":" + p[6:].replace("/", "\\")
  return p


def find_win_python() -> str:
  if is_windows():
    return sys.executable
  env = os.environ.get("CHATBRIDGE_WIN_PYTHON")
  candidates = [env, shutil.which("python.exe")]
  user = os.environ.get("USER", "")
  candidates += [f"/mnt/c/Users/{user}/anaconda3/python.exe",
                 f"/mnt/c/Users/{user}/miniconda3/python.exe"]
  for cand in candidates:
    if cand and Path(cand).exists():
      return cand
  raise SetupError("找不到 Windows Python（取密钥需要它）；设置 CHATBRIDGE_WIN_PYTHON")


def wechat_root() -> Path:
  env = os.environ.get("CHATBRIDGE_WECHAT_ROOT")
  if env:
    return Path(env)
  if is_windows():
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents" / "xwechat_files"
  # WSL: the Linux $USER is not the Windows profile name, so probe the drive.
  candidates = sorted(Path("/mnt/c/Users").glob("*/Documents/xwechat_files"))
  existing = [c for c in candidates if c.is_dir() and list(c.glob("wxid_*"))]
  if existing:
    return existing[0]
  if candidates:
    return candidates[0]
  user = os.environ.get("USER", "")
  return Path(f"/mnt/c/Users/{user}/Documents/xwechat_files")


def discover_accounts(root: Path) -> list[Account]:
  accounts: list[Account] = []
  if not root.exists():
    return accounts
  for db_dir in sorted(root.glob("wxid_*/db_storage")):
    if not db_dir.is_dir():
      continue
    name = db_dir.parent.name
    wxid = name.rsplit("_", 1)[0] if name.count("_") >= 2 else name
    accounts.append(Account(id=name, wxid=wxid, db_dir=db_dir,
                            msg_dir=db_dir.parent / "msg"))
  return accounts


# ---------------------------------------------------------------- tools
def run_tool(script: str, args: list[str], *, python: str | None = None,
             tools: Path = DEFAULT_TOOLS) -> subprocess.CompletedProcess:
  exe = python or sys.executable
  if python and not is_windows():
    args = [to_windows_path(Path(a)) if a.startswith("/mnt/") else a for a in args]
  cmd = [exe, str(tools / script), *args]
  proc = subprocess.run(cmd, capture_output=True, text=True)
  if proc.returncode != 0:
    raise SetupError(f"{script} 失败: {(proc.stderr or proc.stdout or '').strip()[:300]}")
  return proc


def extract_keys(account: Account, out: Path, tools: Path) -> Path:
  run_tool("wechat4_extract_keys.py", ["--db-dir", str(account.db_dir), "--out", str(out)],
           python=find_win_python(), tools=tools)
  if not out.exists():
    raise SetupError(f"{account.id}: 未生成 keys.json")
  return out


def load_keys(path: Path) -> dict[str, bytes]:
  raw = json.loads(path.read_text("utf-8"))
  return {k: bytes.fromhex(v) for k, v in raw.items()}


def verify_account(account: Account, keys: dict[str, bytes]) -> int:
  """Count keys that actually open this account's databases."""
  ok = 0
  for rel, key in keys.items():
    path = account.db_dir / rel.replace("\\", "/")
    if path.exists() and cipher.verify_key(key, path.read_bytes()[:cipher.PAGE]):
      ok += 1
  return ok


def snapshot_account(account: Account, dst: Path) -> int:
  """Copy db + wal + shm so decryption never touches the live client files."""
  if dst.exists():
    shutil.rmtree(dst)
  n = 0
  for src in account.db_dir.rglob("*"):
    if not src.is_file():
      continue
    if src.suffix != ".db" and not src.name.endswith((".db-wal", ".db-shm")):
      continue
    rel = src.relative_to(account.db_dir)
    target = dst / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target)
    n += 1
  return n


def decrypt_all(account: Account, keys: dict[str, bytes], out: Path,
                src_root: Path | None = None) -> int:
  """Decrypt every db; prefers sqlcipher3 when importable, else pure pages."""
  out.mkdir(parents=True, exist_ok=True)
  source = src_root or account.db_dir
  applied = 0
  try:
    import sqlcipher3  # noqa: F401
  except ImportError:
    sqlcipher3 = None
  for rel, key in keys.items():
    src = source / rel.replace("\\", "/")
    if not src.exists():
      continue
    dst = out / rel.replace("\\", "__").replace("/", "__")
    if sqlcipher3 is not None:
      applied += _decrypt_with_binding(src, dst, key)
    else:
      applied += cipher.decrypt(src, dst, key)
  return applied


def _decrypt_with_binding(src: Path, dst: Path, key: bytes) -> int:
  import sqlcipher3.dbapi2 as sc
  dst.unlink(missing_ok=True)
  conn = sc.connect(str(src))
  try:
    conn.execute("PRAGMA cipher_page_size = 4096;")
    conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
    conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()
    conn.execute(f"ATTACH DATABASE '{dst}' AS plain KEY '';")
    conn.execute("SELECT sqlcipher_export('plain');")
    conn.execute("DETACH DATABASE plain;")
  finally:
    conn.close()
  return 0


def media_index(plain: Path, attach: Path, out: Path, tools: Path) -> Path:
  run_tool("wechat_media_index.py", [str(plain), str(attach), str(out)], tools=tools)
  return out


def find_image_key(account: Account, out: Path, tools: Path, samples: int = 2) -> bool:
  if not account.attach_dir or not account.attach_dir.exists():
    return False
  datas = list(account.attach_dir.rglob("*.dat"))[:samples]
  for dat in datas:
    try:
      run_tool("find_image_key.py", ["--dat", str(dat), "--out", str(out)],
               python=find_win_python(), tools=tools)
    except SetupError:
      continue
    if out.exists() and out.read_text("utf-8").strip():
      return True
  return False


def decode_images(index: Path, media_dir: Path, map_path: Path, key: str,
                  tools: Path, xor_key: int = 0xAC) -> dict[str, str]:
  run_tool("wechat4_decode_images.py",
           ["--index", str(index), "--out", str(media_dir), "--map", str(map_path),
            "--key", key, "--xor-key", hex(xor_key)], tools=tools)
  if not map_path.exists():
    return {}
  return json.loads(map_path.read_text("utf-8"))


def export_markdown(plain: Path, out: Path, account: Account, msg_dir: Path | None,
                    image_map: Path | None, tools: Path) -> None:
  args = ["--plain", str(plain), "--out", str(out), "--me", account.wxid]
  if msg_dir:
    args += ["--media", str(msg_dir)]
  if image_map and image_map.exists():
    args += ["--image-map", str(image_map)]
  run_tool("wechat4_export_md.py", args, tools=tools)


# ---------------------------------------------------------------- output
def collect_markdown(staging: Path, account: Account, exported_at: str) -> list[tuple[str, str, dict, int]]:
  """Normalise every exported conversation; returns [(kind, name, meta, msgs)]."""
  items = []
  for kind in ("private", "group"):
    folder = staging / kind
    if not folder.is_dir():
      continue
    for src in sorted(folder.glob("*.md")):
      text = src.read_text("utf-8")
      norm, meta, msgs = normalize.normalise(
        text, account=account.id, exported_at=exported_at, default_kind=kind)
      items.append((kind, src.name, meta, msgs, norm))
  return items


def _rewrite_links(text: str, target: Path, spool: Path) -> str:
  """Drop attachment links that would point outside the spool (name only)."""
  import re
  pat = re.compile(r"📎\s*\[([^\]]+)\]\(<([^>]+)>\)")
  root = spool.resolve()

  def repl(m):
    name, rel = m.group(1), m.group(2)
    try:
      resolved = (target.parent / rel).resolve()
      resolved.relative_to(root)
      return m.group(0)
    except (ValueError, OSError):
      return f"📎 {name}"

  return pat.sub(repl, text)


def place(items, account_dir: Path, spool: Path | None = None) -> tuple[int, int]:
  """Write normalised files atomically and prune stale conversation files."""
  kept: set[Path] = set()
  conversations = messages = 0
  for kind, name, meta, msgs, text in items:
    target = account_dir / kind / name
    if spool is not None:
      text = _rewrite_links(text, target, spool)
    normalize.write_atomic(target, text.encode("utf-8"))
    kept.add(target.resolve())
    conversations += 1
    messages += msgs
  for kind in ("private", "group"):
    folder = account_dir / kind
    if not folder.is_dir():
      continue
    for stale in folder.glob("*.md"):
      if stale.resolve() not in kept:
        stale.unlink()
  return conversations, messages


def signature(account: Account) -> str:
  """Change detector over the message databases only.

  WeChat writes contact/session databases constantly (heartbeats), so hashing
  every file would defeat the incremental default. Message shards change when
  messages actually arrive.
  """
  parts = []
  msg_dir = account.db_dir / "message"
  for path in sorted(msg_dir.glob("message_*.db")) + sorted(msg_dir.glob("message_*.db-wal")):
    if path.is_file():
      st = path.stat()
      parts.append(f"{path.name}:{st.st_size}:{st.st_mtime_ns}")
  import hashlib
  return hashlib.sha1("|".join(parts).encode()).hexdigest()


def build_account(account: Account, *, spool: Path, work: Path, tools: Path,
                  force: bool, allow_placeholder: bool = True) -> dict:
  """Export one account into <spool>/wechat/<account>/. Returns manifest entry."""
  exported_at = _now()
  account_dir = spool / "wechat" / account.id
  account_dir.mkdir(parents=True, exist_ok=True)
  staging = Path(tempfile.mkdtemp(prefix=f"wechat-{account.id}-", dir=work))
  try:
    keys_path = staging / "keys.json"
    extract_keys(account, keys_path, tools)
    keys = load_keys(keys_path)
    verified = verify_account(account, keys)
    if verified == 0:
      raise SetupError(f"{account.id}: 密钥无法解开该账号的库（账号或密钥不匹配）")

    snap = staging / "snapshot"
    snapshot_account(account, snap)
    plain = staging / "plain"
    account.applied_wal = decrypt_all(account, keys, plain, src_root=snap)

    index = staging / "media-index.jsonl"
    if account.attach_dir and account.attach_dir.exists():
      media_index(plain, account.attach_dir, index, tools)
    else:
      index.write_text("", "utf-8")

    # Images must live inside the exporter's output dir so the emitted
    # `../media/x` link is correct after the files are placed in the spool.
    md = staging / "md"
    md.mkdir(parents=True, exist_ok=True)
    media_dir = md / "media"
    map_path = staging / "image-map.json"
    image_key = staging / "imgkey.txt"
    if index.exists() and index.stat().st_size and find_image_key(account, image_key, tools):
      decode_images(index, media_dir, map_path, image_key.read_text("utf-8").strip(), tools)
    else:
      account.errors.append("未取得图片密钥：图片保留占位（请在微信中打开 2-3 张图片后重跑）")

    export_markdown(plain, md, account, account.msg_dir, map_path, tools)

    items = collect_markdown(md, account, exported_at)
    if not items:
      raise SetupError(f"{account.id}: 未导出任何会话")
    account.conversations, account.messages = place(items, account_dir, spool)

    if media_dir.is_dir():
      target_media = account_dir / "media"
      target_media.mkdir(parents=True, exist_ok=True)
      kept_media = set()
      for src in media_dir.iterdir():
        if not src.is_file():
          continue
        dst = target_media / src.name
        normalize.write_atomic(dst, src.read_bytes())
        kept_media.add(dst.name)
      for stale in target_media.iterdir():
        if stale.is_file() and stale.name not in kept_media:
          stale.unlink()
      account.media = len(kept_media)
    return {
      "id": account.id,
      "label": account.id,
      "conversations": account.conversations,
      "messages": account.messages,
      "media": account.media,
      "wal_pages": account.applied_wal,
      "status": "partial" if account.errors else "ok",
      "errors": list(account.errors),
    }
  finally:
    shutil.rmtree(staging, ignore_errors=True)


def _now() -> str:
  from datetime import datetime, timedelta, timezone
  return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
