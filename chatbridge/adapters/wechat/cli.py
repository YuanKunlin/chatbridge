"""CLI: python -m adapters.wechat export --spool D:\\aimem-spool --account auto --json"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import pipeline

EXIT_OK, EXIT_CONSENT, EXIT_SETUP, EXIT_PARTIAL = 0, 2, 3, 4


def _atomic_json(path: Path, payload: dict) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_name(path.name + ".tmp")
  tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
  tmp.replace(path)


def _load_json(path: Path) -> dict:
  try:
    return json.loads(path.read_text("utf-8"))
  except (OSError, ValueError):
    return {}


def count_on_disk(spool: Path, account_id: str) -> tuple[int, int]:
  """Recount conversations/messages from the files that actually landed.

  The fragment must describe the spool as it exists now; counting the build
  result instead would drift whenever a run is interrupted or races a reader.
  """
  account_dir = spool / "wechat" / account_id
  conversations = messages = 0
  for kind in ("private", "group"):
    folder = account_dir / kind
    if not folder.is_dir():
      continue
    for path in sorted(folder.glob("*.md")):
      text = path.read_text("utf-8")
      _, body = pipeline.normalize.split_front_matter(text)
      conversations += 1
      messages += pipeline.normalize.count_messages(body)
  return conversations, messages


def _select(accounts: list[pipeline.Account], selector: str) -> list[pipeline.Account]:
  if selector in ("", "auto"):
    return accounts
  picked = [a for a in accounts if a.id == selector or a.wxid == selector]
  return picked


def cmd_export(args: argparse.Namespace) -> int:
  spool = Path(args.spool)
  spool.mkdir(parents=True, exist_ok=True)
  state_path = spool / "_state" / "wechat.json"
  frag_path = spool / ".manifest.wechat.json"
  work = spool / "_state" / "tmp"
  work.mkdir(parents=True, exist_ok=True)

  root = pipeline.wechat_root()
  discovered = pipeline.discover_accounts(root)
  if not discovered:
    return _fail(args, EXIT_SETUP, f"未找到微信数据目录: {root}")
  accounts = _select(discovered, args.account)
  if not accounts:
    return _fail(args, EXIT_SETUP, f"账号不存在: {args.account}")

  state = _load_json(state_path)
  known = state.get("accounts", {})
  entries: list[dict] = []
  errors: list[dict] = []
  skipped = 0

  for account in accounts:
    sig = pipeline.signature(account)
    previous = known.get(account.id, {})
    account_dir = spool / "wechat" / account.id
    up_to_date = (
      not args.full
      and previous.get("signature") == sig
      and account_dir.is_dir()
      and any(account_dir.glob("*/*.md"))
    )
    if args.dry_run:
      keys_path = work / f"dry-{account.id}.json"
      try:
        pipeline.extract_keys(account, keys_path, pipeline.DEFAULT_TOOLS)
        keys = pipeline.load_keys(keys_path)
        verified = pipeline.verify_account(account, keys)
      except pipeline.SetupError as exc:
        errors.append({"account": account.id, "error": str(exc)})
        continue
      finally:
        keys_path.unlink(missing_ok=True)
      entries.append({"id": account.id, "label": account.id, "verified_keys": verified,
                      "conversations": previous.get("conversations", 0),
                      "messages": previous.get("messages", 0)})
      continue

    if up_to_date:
      skipped += 1
      convs, msgs = count_on_disk(spool, account.id)
      entries.append({"id": account.id, "label": account.id, "skipped": True,
                      "conversations": convs, "messages": msgs,
                      "media": previous.get("media", 0),
                      "status": previous.get("status", "ok"),
                      "errors": previous.get("errors", [])})
      if previous.get("errors"):
        errors.append({"account": account.id, "errors": previous["errors"]})
      continue

    try:
      entry = pipeline.build_account(account, spool=spool, work=work,
                                     tools=pipeline.DEFAULT_TOOLS, force=args.full)
    except pipeline.SetupError as exc:
      errors.append({"account": account.id, "error": str(exc)})
      continue
    # Trust the files on disk over the in-memory build counters.
    entry["conversations"], entry["messages"] = count_on_disk(spool, account.id)
    entries.append(entry)
    known[account.id] = {
      "signature": sig,
      "last_run": _now(),
      "media": entry.get("media", 0),
      "status": entry.get("status", "ok"),
      "errors": entry.get("errors", []),
      "conversations": entry["conversations"],
      "messages": entry["messages"],
    }
    if entry.get("errors"):
      errors.append({"account": account.id, "errors": entry["errors"]})

  conversations = sum(int(e.get("conversations", 0)) for e in entries)
  messages = sum(int(e.get("messages", 0)) for e in entries)
  status = "ok"
  if errors:
    status = "partial" if conversations else "error"
  elif any(e.get("status") == "partial" for e in entries):
    status = "partial"

  if not args.dry_run:
    _atomic_json(frag_path, {
      "contract": "aimem-spool/1",
      "source": "wechat",
      "adapter": "wechat4",
      "version": "1.0",
      "status": status,
      "complete": bool(conversations) and status != "error",
      "root": "wechat",
      "accounts": entries,
      "conversations": conversations,
      "messages": messages,
      "last_run": _now(),
      "errors": errors,
    })
    state.update({"contract": "aimem-spool/1", "source": "wechat",
                  "accounts": known, "last_run": _now()})
    _atomic_json(state_path, state)

  payload = {
    "contract": "aimem-spool/1",
    "source": "wechat",
    "status": status,
    "accounts": [e["id"] for e in entries],
    "conversations": conversations,
    "messages": messages,
    "skipped": skipped,
    "fragment": frag_path.name,
    "errors": errors,
  }
  if args.dry_run:
    payload["dry_run"] = True
  print(json.dumps(payload, ensure_ascii=False, indent=2))

  if status == "error":
    return EXIT_SETUP
  if status == "partial":
    return EXIT_PARTIAL
  return EXIT_OK


def _now() -> str:
  from datetime import datetime, timedelta, timezone
  return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def _fail(args: argparse.Namespace, code: int, message: str) -> int:
  payload = {"contract": "aimem-spool/1", "source": "wechat",
             "status": "setup_required", "accounts": [], "conversations": 0,
             "messages": 0, "errors": [{"error": message}]}
  if getattr(args, "json", False):
    print(json.dumps(payload, ensure_ascii=False, indent=2))
  else:
    print(f"setup required: {message}", file=sys.stderr)
  return code


def build_parser() -> argparse.ArgumentParser:
  ap = argparse.ArgumentParser(prog="python -m adapters.wechat")
  sub = ap.add_subparsers(dest="cmd", required=True)
  ex = sub.add_parser("export", help="导出微信聊天记录到 spool")
  ex.add_argument("--spool", required=True)
  ex.add_argument("--account", default="auto")
  ex.add_argument("--full", "--force", dest="full", action="store_true")
  ex.add_argument("--dry-run", action="store_true")
  ex.add_argument("--json", action="store_true")
  ex.set_defaults(func=cmd_export)
  return ap


def main(argv: list[str] | None = None) -> int:
  args = build_parser().parse_args(argv)
  try:
    return args.func(args)
  except pipeline.SetupError as exc:
    return _fail(args, EXIT_SETUP, str(exc))


if __name__ == "__main__":
  sys.exit(main())
