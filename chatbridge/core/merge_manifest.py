#!/usr/bin/env python3
"""Merge adapter fragments into <spool>/manifest.json (Hub-equivalent, stdlib only)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

CONTRACT = "aimem-spool/1"
FIELDS = ("root", "status", "accounts", "conversations", "messages", "last_run", "errors")


def merge(spool: Path) -> dict:
    sources: dict[str, dict] = {}
    for frag in sorted(spool.glob(".manifest.*.json")):
        data = json.loads(frag.read_text("utf-8-sig"))
        src = data.get("source")
        if not src:
            raise ValueError(f"{frag}: missing source")
        sources[src] = {"adapter": data.get("adapter", ""), **{k: data.get(k) for k in FIELDS}}
    if not sources:
        raise ValueError(f"no .manifest.*.json under {spool}")
    manifest = {
        "contract": CONTRACT,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sources": sources,
    }
    tmp = spool / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, spool / "manifest.json")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spool", nargs="?", default="D:\\aimem-spool")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        manifest = merge(Path(args.spool))
    except Exception as exc:
        print(f"merge failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        print(f"merged {len(manifest['sources'])} sources -> {Path(args.spool) / 'manifest.json'}")
        for src, spec in manifest["sources"].items():
            print(f"  {src}: {spec.get('status')} conversations={spec.get('conversations')} messages={spec.get('messages')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
