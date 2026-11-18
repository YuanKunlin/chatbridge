"""Legacy snapshot at ~/qqmd/out (decoder output from an earlier date).

No front matter, but it has group names and nicknames; used as a fallback when
the live decoder key is unavailable, and as an extra read-only source otherwise.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import decoder, mdio

DEFAULT_ROOT = Path(os.environ.get("CHATBRIDGE_QQ_SNAPSHOT", str(Path.home() / "qqmd" / "out")))


def collect(root: Path, media_dir: Path | None = None) -> dict[str, dict]:
  result: dict[str, dict] = {}
  if not root.exists():
    return result
  for kind in ("private", "group"):
    folder = root / kind
    if not folder.is_dir():
      continue
    for path in sorted(folder.glob("*.md")):
      text = decoder.clean_text(path.read_text("utf-8"), root, media_dir)
      conv = mdio.parse(text)
      if not conv["native"]:
        continue
      conv["kind"] = kind
      conv["source"] = "snapshot"
      conv["names_resolved"] = not ("uid_" in str(conv["title"]))
      result[conv["native"]] = conv
  return result
