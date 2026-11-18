#!/usr/bin/env python3
"""ChatBridge hub CLI (P3 skeleton).

    python -m hub status   --spool D:\\aimem-spool
    python -m hub merge    --spool D:\\aimem-spool
    python -m hub validate --spool D:\\aimem-spool --strict
    python -m hub export wechat --spool D:\\aimem-spool --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"


def fragments(spool: Path) -> dict:
    out = {}
    for frag in sorted(spool.glob(".manifest.*.json")):
        try:
            data = json.loads(frag.read_text("utf-8-sig"))
        except Exception as exc:
            out[frag.name] = {"status": "error", "errors": [str(exc)]}
            continue
        out[data.get("source") or frag.stem] = data
    return out


def cmd_status(args) -> int:
    spool = Path(args.spool)
    rows = fragments(spool)
    if not rows:
        print(f"no fragments under {spool}")
        return 1
    for source, spec in rows.items():
        print(
            f"{source:8} {spec.get('status','?'):8} "
            f"conversations={spec.get('conversations')} messages={spec.get('messages')}"
        )
        for account in spec.get("accounts") or []:
            print(
                f"  - {account.get('id')}: "
                f"{account.get('conversations')} conv / {account.get('messages')} msg"
            )
        for err in spec.get("errors") or []:
            if isinstance(err, dict):
                print(f"  ! {err.get('account','')}: {', '.join(err.get('errors') or [])}")
            else:
                print(f"  ! {err}")
    manifest = spool / "manifest.json"
    print(f"manifest: {'yes' if manifest.exists() else 'no'}")
    return 0


def cmd_merge(args) -> int:
    return subprocess.call(
        [sys.executable, str(CORE / "merge_manifest.py"), args.spool]
    )


def cmd_validate(args) -> int:
    cmd = [sys.executable, str(CORE / "validate_spool.py"), args.spool]
    if args.strict:
        cmd.append("--strict")
    if args.json:
        cmd.append("--json")
    return subprocess.call(cmd)


def cmd_export(args) -> int:
    cmd = [
        sys.executable,
        "-m",
        f"adapters.{args.source}",
        "export",
        "--spool",
        args.spool,
    ]
    if args.account:
        cmd += ["--account", args.account]
    if args.source == "email":
        if args.since:
            cmd += ["--since", args.since]
        if args.folders:
            cmd += ["--folders", args.folders]
    if args.json:
        cmd.append("--json")
    if args.dry_run:
        cmd.append("--dry-run")
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = str(ROOT) + __import__("os").pathsep + env.get("PYTHONPATH", "")
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(ROOT), env=env)


def cmd_web(args) -> int:
    from .web import main as web_main
    return web_main(["--spool", args.spool, "--host", args.host, "--port", str(args.port)])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hub")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status")
    p.add_argument("--spool", default="D:\\aimem-spool")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("merge")
    p.add_argument("--spool", default="D:\\aimem-spool")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("validate")
    p.add_argument("--spool", default="D:\\aimem-spool")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("web")
    p.add_argument("--spool", default="D:\\aimem-spool")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8766)
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("export")
    p.add_argument("source", choices=["wechat", "qq", "email"])
    p.add_argument("--spool", default="D:\\aimem-spool")
    p.add_argument("--account", default="auto")
    p.add_argument("--since", default=None)
    p.add_argument("--folders", default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_export)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
