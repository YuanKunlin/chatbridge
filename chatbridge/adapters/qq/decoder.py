"""Run the existing QQ decoder (~/qqmd/ntqq_export_md.py) and normalise its output.

qqcli only reaches c2c; the decoder covers groups and resolves nicknames. Both
are read-only exporters — no search, no DuckDB index.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import mdio, normalize

DEFAULT_DECODER = Path(os.environ.get("CHATBRIDGE_QQ_DECODER",
                                     str(Path.home() / "qqmd" / "ntqq_export_md.py")))
CONTRACT = "chat-md/1"
META_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$")
MSG_RE = re.compile(r"^\*\*\d{1,2}:\d{2}(?::\d{2})?\s*·\s*.+\*\*")


class DecoderError(RuntimeError):
  pass


def find_nt_db() -> Path | None:
  roots = [Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents" / "Tencent Files"]
  if sys.platform != "win32":
    roots += sorted(Path("/mnt/c/Users").glob("*/Documents/Tencent Files"))
  for root in roots:
    if not root.exists():
      continue
    for cand in sorted(root.glob("*/nt_qq/nt_db")):
      if (cand / "nt_msg.db").exists():
        return cand
  return None


def run_decoder(out_dir: Path, *, my_uin: str | None) -> int:
  key = os.environ.get("NTQQ_DB_KEY", "")
  if len(key) != 16:
    raise DecoderError("缺少 NTQQ_DB_KEY（16 字符），无法用解码器补群聊/昵称")
  nt_db = find_nt_db()
  if nt_db is None:
    raise DecoderError("未找到 nt_qq/nt_db")
  decoder = DEFAULT_DECODER
  if not decoder.exists():
    raise DecoderError(f"找不到解码器: {decoder}")
  out_dir.mkdir(parents=True, exist_ok=True)
  env = dict(os.environ, NTQQ_DB_KEY=key)
  cmd = [sys.executable, str(decoder), "--nt-db", str(nt_db), "--out", str(out_dir)]
  pic_root = nt_db.parent / "nt_data" / "Pic"   # .../nt_qq/nt_data/Pic
  if pic_root.is_dir():
    cmd += ["--pic-root", str(pic_root)]
  if my_uin:
    cmd += ["--my-uin", str(my_uin)]
  proc = subprocess.run(cmd, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", env=env, timeout=900)
  if proc.returncode != 0 or not any(out_dir.glob("*/*.md")):
    raise DecoderError(f"解码器失败: {(proc.stderr or proc.stdout or '').strip()[:300]}")
  return 0


def _split(text: str) -> tuple[dict[str, str], str]:
  lines = text.split("\n")
  if not lines or lines[0].strip() != "---":
    return {}, text
  meta: dict[str, str] = {}
  for i in range(1, len(lines)):
    if lines[i].strip() == "---":
      return meta, "\n".join(lines[i + 1:])
    m = META_RE.match(lines[i])
    if m:
      v = m.group(2)
      if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
      meta[m.group(1).lower()] = v
  return {}, text


ATTACH_LINK_RE = re.compile(r"📎\s*\[([^\]]+)\]\(<[^>]+>\)")
# `![图片]（未缓存：xxx）` is decoder placeholder text, not a link.
UNCACHED_RE = re.compile(r"!\[图片\]（未缓存[^）]*）")


def _drop_external_links(body: str) -> str:
  """Contract: attachments are name-only; never link outside the spool."""
  return ATTACH_LINK_RE.sub(lambda m: f"📎 {m.group(1)}", body)


def _sanitise(body: str) -> str:
  out = []
  for ch in body:
    o = ord(ch)
    if ch in "\n\t":
      out.append(ch)
    elif o < 0x20 or o == 0x7F:
      continue
    elif o in (0x2028, 0x2029, 0x85):
      out.append(" ")
    else:
      out.append(ch)
  return "".join(out)


IMG_RE = re.compile(r"!\[图片\]\(<([^>]+)>\)")


def _relink_images(body: str, out_dir: Path, media_dir: Path) -> tuple[str, int, int]:
  """Copy cached images into the spool and point links at ../media/."""
  media_dir.mkdir(parents=True, exist_ok=True)
  copied = missing = 0

  def repl(match):
    nonlocal copied, missing
    rel = match.group(1)
    try:
      src = (out_dir / rel).resolve()
    except (OSError, ValueError):
      src = Path()
    if src.is_file():
      name = f"{hashlib.sha1(str(src).encode()).hexdigest()[:16]}{src.suffix.lower() or '.jpg'}"
      target = media_dir / name
      if not target.exists():
        shutil.copyfile(src, target)
      copied += 1
      return f"![图片](<../media/{name}>)"
    missing += 1
    return "🖼 图片"

  return IMG_RE.sub(repl, body), copied, missing


def clean_text(text: str, base_dir: Path, media_dir: Path | None) -> str:
  """Sanitise, drop external attachment links, copy images into the spool."""
  body = _drop_external_links(_sanitise(text))
  if media_dir is not None:
    body, _, _ = _relink_images(body, base_dir, media_dir)
  return UNCACHED_RE.sub("🖼 图片", body)


def collect(out_dir: Path, account: str, exported_at: str,
            media_dir: Path | None = None) -> dict[str, dict]:
  """Normalise decoder files into {native_id: conv} using the shared md parser."""
  result: dict[str, dict] = {}
  for kind in ("private", "group"):
    folder = out_dir / kind
    if not folder.is_dir():
      continue
    for path in sorted(folder.glob("*.md")):
      text = clean_text(path.read_text("utf-8"), out_dir, media_dir)
      conv = mdio.parse(text)
      if not conv["native"]:
        conv["native"] = path.stem.rsplit("_", 1)[-1]
      conv["kind"] = kind
      conv["source"] = "decoder"
      conv["names_resolved"] = not normalize.is_uid(str(conv["title"]))
      result[conv["native"]] = conv
  return result
