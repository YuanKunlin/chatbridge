"""qqcli process wrapper: locate the tool, run JSON commands, export sessions."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_TOOLS = Path(os.environ.get("CHATBRIDGE_QQCLI_DIR", r"D:\aimem-tools\qqcli"))


class SetupError(RuntimeError):
  """Missing tool / DB / consent. Maps to exit 3 (or 2 for consent)."""


class ConsentRequired(RuntimeError):
  def __init__(self, scope: dict) -> None:
    super().__init__("consent required")
    self.scope = scope


def find_qq() -> Path:
  env = os.environ.get("CHATBRIDGE_QQCLI")
  if env and Path(env).exists():
    return Path(env)
  root = DEFAULT_TOOLS
  if not root.exists() and sys.platform != "win32":
    # D:\aimem-tools\qqcli -> /mnt/d/aimem-tools/qqcli
    parts = str(root).replace("/", "\\").split("\\")
    if len(parts) > 1 and len(parts[0]) == 2 and parts[0][1] == ":":
      root = Path("/mnt") / parts[0][0].lower() / Path(*parts[1:])
  if not root.exists():
    raise SetupError(f"找不到 qqcli 目录: {root}")
  hits = sorted(root.glob("**/qq.exe"))
  if not hits:
    raise SetupError(f"{root} 下没有 qq.exe")
  return hits[0]


def _json_tail(text: str):
  """qqcli prints log lines before/after JSON; decode the first JSON document."""
  decoder = json.JSONDecoder()
  for i, ch in enumerate(text):
    if ch in "[{":
      try:
        value, _ = decoder.raw_decode(text[i:])
        return value
      except ValueError:
        continue
  raise SetupError(f"无法解析 qqcli 输出: {text[:200]}")


def to_windows_path(value: str) -> str:
  v = str(value)
  if sys.platform != "win32" and v.startswith("/mnt/") and len(v) > 6 and v[5].isalpha():
    return v[5].upper() + ":" + v[6:].replace("/", "\\")
  return v


def run(args: list[str], *, timeout: int = 300) -> tuple[int, str]:
  args = [to_windows_path(a) for a in args]
  qq = find_qq()
  proc = subprocess.run([str(qq), "--json", *args], capture_output=True,
                        text=True, encoding="utf-8", errors="replace",
                        timeout=timeout, cwd=str(qq.parent))
  return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def init() -> dict:
  code, out = run(["init"])
  data = _json_tail(out) if "{" in out else {}
  status = data.get("status")
  if status in ("consent_required", "decryption_consent_required") or code == 2:
    raise ConsentRequired(data.get("consent") or {"status": status, "raw": out[:1000]})
  if status == "ready":
    return data
  raise SetupError(data.get("message") or f"qqcli init 未就绪 (status={status}, exit={code})")


def sessions() -> list[dict]:
  code, out = run(["sessions", "--limit", "10000"])
  if code != 0:
    raise SetupError(f"qq sessions 失败: {out.strip()[:200]}")
  data = _json_tail(out)
  if not isinstance(data, list):
    raise SetupError("qq sessions 未返回数组")
  return data


def export_session(session_id: str, out_path: Path, *, limit: int = 100000) -> None:
  out_path.parent.mkdir(parents=True, exist_ok=True)
  # Export is an External Disclosure action; the Lead authorised writing to the
  # spool, so the adapter passes the flag explicitly and records the scope.
  code, out = run(["export", str(session_id), "--format", "jsonl",
                   "--limit", str(limit), "-o", str(out_path),
                   "--consent-external-disclosure"], timeout=600)
  if code != 0 or not out_path.exists():
    raise SetupError(f"export {session_id} 失败: {(out or '').strip()[:200]}")


def read_jsonl(path: Path) -> list[dict]:
  rows = []
  for line in path.read_text("utf-8").splitlines():
    line = line.strip()
    if line:
      rows.append(json.loads(line))
  return rows


def infer_account(init_data: dict) -> str:
  """Prefer qqcli's account; else the uin in the Tencent Files path."""
  acc = init_data.get("account")
  if acc:
    return str(acc)
  db = init_data.get("db_path") or ""
  parts = [p for p in str(db).replace("/", "\\").split("\\") if p]
  for i, part in enumerate(parts):
    if part.lower() == "tencent files" and i + 1 < len(parts) and parts[i + 1].isdigit():
      return parts[i + 1]
  env = os.environ.get("CHATBRIDGE_QQ_ACCOUNT")
  if env:
    return env
  # Scan the live client data dir for the single uin.
  roots = [Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents" / "Tencent Files"]
  if sys.platform != "win32":
    roots += sorted(Path("/mnt/c/Users").glob("*/Documents/Tencent Files"))
  candidates = []
  for root in roots:
    if root.exists():
      candidates += [p.parent.name for p in root.glob("*/nt_qq") if p.is_dir()]
  if len(candidates) == 1:
    return candidates[0]
  # last resort: a numeric session id that looks like a uin
  return "qq"


def from_windows_path(value: str) -> str:
  v = str(value)
  if sys.platform != "win32" and len(v) > 2 and v[1] == ":":
    return "/mnt/" + v[0].lower() + v[2:].replace("\\", "/")
  return v


def load_uid_map(db_path: str) -> dict[str, str]:
  """uin (as string) -> uid, from nt_uid_mapping_table in the plaintext DB."""
  import sqlite3
  mapping: dict[str, str] = {}
  try:
    conn = sqlite3.connect(from_windows_path(str(db_path)))
    for uid, uin in conn.execute("SELECT [48902], [1002] FROM nt_uid_mapping_table"):
      if uid and uin:
        mapping[str(uin)] = uid
    conn.close()
  except sqlite3.Error:
    return {}
  return mapping


def version() -> str:
  code, out = run(["version"])
  data = _json_tail(out)
  return str(data.get("version", "?"))
