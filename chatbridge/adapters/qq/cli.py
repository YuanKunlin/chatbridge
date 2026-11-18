"""CLI: python -m adapters.qq export --spool D:\\aimem-spool --account auto --json

Three read-only sources, merged at message level (Lead decision):

* qqcli    -> c2c private chats (native message ids; never `qq search`)
* decoder  -> live groups + nicknames via ~/qqmd/ntqq_export_md.py
* snapshot -> ~/qqmd/out legacy export (fallback when the decoder key is absent)

Priority for duplicate messages: decoder (newest) > snapshot > qqcli; the qqcli
copy still contributes its `<!-- mid -->`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path

from . import decoder, mdio, normalize, pipeline, snapshot

EXIT_OK, EXIT_CONSENT, EXIT_SETUP, EXIT_PARTIAL = 0, 2, 3, 4
HEAD_RE = re.compile(r"^\*\*\d{1,2}:\d{2}(?::\d{2})?\s*·\s*.+\*\*\s*$")
MEDIA_RE = re.compile(r"\.\./media/([^>]+)")


def _now() -> str:
  from datetime import datetime, timedelta, timezone
  return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: dict) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_name(path.name + ".tmp")
  tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
  tmp.replace(path)


def _load(path: Path) -> dict:
  try:
    return json.loads(path.read_text("utf-8"))
  except (OSError, ValueError):
    return {}


def _safe(name: str) -> str:
  s = unicodedata.normalize("NFKC", name or "").strip()
  return re.sub(r'[\\/:*?"<>|\r\n\t]', "_", s)[:40] or "conversation"


def count_on_disk(spool: Path, account: str) -> tuple[int, int]:
  convs = msgs = 0
  for path in sorted((spool / "qq" / account).glob("*/*.md")):
    convs += 1
    msgs += sum(1 for line in path.read_text("utf-8").split("\n") if HEAD_RE.match(line))
  return convs, msgs


def place(spool: Path, account: str, convs: dict[str, dict], exported_at: str) -> tuple[int, int]:
  base = spool / "qq" / account
  kept: set[Path] = set()
  for native, conv in convs.items():
    text = mdio.render(conv, account=account, exported_at=exported_at)
    name = f"{_safe(conv['title'])}_{_safe(native)}.md"
    target = base / conv["kind"] / name
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, "utf-8", newline="\n")
    tmp.replace(target)
    kept.add(target.resolve())
  for kind in ("private", "group"):
    folder = base / kind
    if folder.is_dir():
      for stale in folder.glob("*.md"):
        if stale.resolve() not in kept:
          stale.unlink()
  # Keep media in sync with the Markdown: only referenced files survive.
  media_dir = base / "media"
  if media_dir.is_dir():
    referenced = set()
    for native, conv in convs.items():
      text = mdio.render(conv, account=account, exported_at=exported_at)
      referenced.update(MEDIA_RE.findall(text))
    for path in media_dir.iterdir():
      if path.is_file() and path.name not in referenced:
        path.unlink()
  return count_on_disk(spool, account)


def _qqcli_convs(account: str, sessions: list[dict], work: Path,
                 errors: list[dict], uid_map: dict[str, str]) -> dict[str, dict]:
  convs: dict[str, dict] = {}
  seen: set[str] = set()
  for session in sessions:
    sid = str(session.get("id") or "")
    if not sid:
      continue
    if sid in seen:
      errors.append({"session": sid, "error": "duplicate session id, skipped"})
      continue
    seen.add(sid)
    if sid == "0":
      errors.append({"session": sid, "error": "conversation id 0 has no identity, skipped"})
      continue
    tmp_jsonl = work / f"{sid}.jsonl"
    try:
      pipeline.export_session(sid, tmp_jsonl)
      rows = pipeline.read_jsonl(tmp_jsonl)
    except (pipeline.SetupError, ValueError) as exc:
      errors.append({"session": sid, "error": str(exc)[:300]})
      continue
    text, meta, n = normalize.build(session, rows, account=account,
                                    exported_at=_now())
    if n == 0:
      errors.append({"session": sid, "error": "empty conversation, skipped"})
      continue
    conv = mdio.parse(text)
    conv["native"] = uid_map.get(sid, sid)      # unify with decoder uid keys
    conv["source"] = "qqcli"
    conv["names_resolved"] = not normalize.is_uid(str(conv["title"]))
    convs[conv["native"]] = conv
  return convs


def _match_key(msg: dict, kind: str) -> tuple:
  """Cross-source identity: qqcli repeats the same message with a uid sender.

  Private chats therefore match on (date, time, ordinal) only; groups keep the
  sender because both sides resolve nicknames there.
  """
  if kind == "private":
    return (msg["date"], msg["time"], msg.get("ord", 0))
  return mdio.key(msg)


def _merge(convs: dict[str, dict], extra: dict[str, dict], label: str,
           dup: set[str], add_missing: bool = False) -> None:
  for native, conv in extra.items():
    if native not in convs:
      convs[native] = conv
      continue
    dup.add(native)
    base = convs[native]
    mdio.assign_order(base["msgs"])
    mdio.assign_order(conv["msgs"])
    known = {_match_key(m, base["kind"]): m for m in base["msgs"]}
    added = 0
    for msg in conv["msgs"]:
      k = _match_key(msg, base["kind"])
      if k in known:
        if msg["mid"] and not known[k]["mid"]:
          known[k]["mid"] = msg["mid"]
        continue
      if not add_missing:
        continue
      base["msgs"].append(msg)
      known[k] = msg
      added += 1
    if not base.get("names_resolved") and conv.get("names_resolved"):
      base["title"] = conv["title"]
      base["names_resolved"] = True


def cmd_export(args: argparse.Namespace) -> int:
  spool = Path(args.spool)
  spool.mkdir(parents=True, exist_ok=True)
  frag_path = spool / ".manifest.qq.json"
  state_path = spool / "_state" / "qq.json"
  state = _load(state_path)

  try:
    info = pipeline.init()
  except pipeline.ConsentRequired as exc:
    print(json.dumps({"contract": "aimem-spool/1", "source": "qq",
                      "status": "consent_required", "accounts": [],
                      "conversations": 0, "messages": 0, "consent": exc.scope},
                     ensure_ascii=False, indent=2))
    return EXIT_CONSENT
  except pipeline.SetupError as exc:
    return _fail(args, EXIT_SETUP, str(exc))

  account = pipeline.infer_account(info)
  version = pipeline.version()
  sessions = pipeline.sessions()

  if args.dry_run:
    print(json.dumps({"contract": "aimem-spool/1", "source": "qq", "status": "ok",
                      "qqcli": version, "account": account,
                      "qqcli_sessions": len(sessions),
                      "ids": [s.get("id") for s in sessions],
                      "dry_run": True}, ensure_ascii=False, indent=2))
    return EXIT_OK

  (spool / "_state").mkdir(parents=True, exist_ok=True)
  # qq.exe is a Windows binary: its -o paths must be Windows-visible. A spool
  # outside /mnt (e.g. /tmp during tests) gets a temp dir on the C: drive.
  work_root = spool / "_state"
  if sys.platform != "win32" and not str(spool.resolve()).startswith("/mnt/"):
    candidates = [p for p in Path("/mnt/c/Users").glob("*/AppData/Local/Temp") if p.is_dir()]
    work_root = candidates[0] if candidates else spool / "_state"
  work = Path(tempfile.mkdtemp(prefix="qq-export-", dir=str(work_root)))
  errors: list[dict] = []
  qqcli: dict[str, dict] = {}
  dec: dict[str, dict] = {}
  snap: dict[str, dict] = {}
  merged: dict[str, dict] = {}
  try:
    uid_map = pipeline.load_uid_map(info.get("db_path") or "")
    qqcli = _qqcli_convs(account, sessions, work, errors, uid_map)

    media_dir = spool / "qq" / account / "media"
    try:
      dec_dir = work / "decoder"
      decoder.run_decoder(dec_dir, my_uin=account if account.isdigit() else None)
      dec = decoder.collect(dec_dir, account, _now(), media_dir=media_dir)
    except decoder.DecoderError as exc:
      errors.append({"source": "decoder", "error": str(exc)[:300]})

    snap = snapshot.collect(snapshot.DEFAULT_ROOT, media_dir=media_dir)
    id0_msgs = 0
    for source in (dec, snap):
      if "0" in source:
        id0_msgs += len(source.pop("0")["msgs"])
    if id0_msgs:
      errors.append({"source": "decoder/snapshot",
                     "error": f"会话 id=0（{id0_msgs} 条系统消息）无稳定身份，已跳过"})

    # Decoder is the freshest source and renders events better than the old
    # snapshot, so it is the base. The snapshot only fills conversations the
    # decoder does not know (deleted since), and entirely replaces it when the
    # decoder key is unavailable.
    dup: set[str] = set()
    if dec:
      merged = dict(dec)
      for native, conv in snap.items():
        if native not in merged:
          merged[native] = conv
        else:
          dup.add(native)
    else:
      merged = dict(snap)
      errors.append({"source": "decoder/fallback",
                     "error": "解码器不可用，已用 ~/qqmd/out 快照兜底"})
    # qqcli's self-session repeats messages that belong to other chats, so its
    # unmatched rows are never appended; it only contributes native ids.
    _merge(merged, qqcli, "qqcli", dup, add_missing=False)

    groups = sum(1 for c in merged.values() if c["kind"] == "group")
    names_resolved = sum(1 for c in merged.values() if c.get("names_resolved"))
    total_msgs = sum(len(c["msgs"]) for c in merged.values())
    texts = {k: mdio.render(c, account=account, exported_at=_now())
             for k, c in merged.items()}
    img_links = sum(t.count("![图片](<") for t in texts.values())
    img_files = len({m for t in texts.values() for m in MEDIA_RE.findall(t)})
    placeholders = sum(t.count("🖼 图片") for t in texts.values())
    if placeholders:
      errors.append({"account": account,
                     "error": f"{placeholders} 张图片本地缓存缺失，已用 🖼 图片 占位；"
                              f"{img_links} 处链接引用 {img_files} 个 media 文件"})
    unresolved = len(merged) - names_resolved
    if unresolved:
      errors.append({"account": account,
                     "error": f"{unresolved} 个会话标题仍为 uid/编号（昵称不可用）"})
  finally:
    shutil.rmtree(work, ignore_errors=True)

  # Coverage guard: never replace a healthy export with a much smaller partial
  # one (missing key / missing snapshot). This is what caused the 3,099 -> 175
  # regression. Use --full only when an intentional rebuild is required.
  prev = _load(frag_path)
  prev_msgs = int(prev.get("messages") or state.get("messages") or 0)
  # Even if the fragment/state were lost, the files on disk are the last good
  # export and must not be replaced by a smaller partial one.
  _, disk_msgs = count_on_disk(spool, account)
  prev_msgs = max(prev_msgs, disk_msgs)
  cur_msgs = sum(len(c["msgs"]) for c in merged.values())
  cur_convs = len(merged)
  complete = bool(dec or snap)
  if (
      not args.full
      and prev_msgs
      and cur_msgs < prev_msgs * 0.8
  ):
    print(json.dumps({
        "contract": "aimem-spool/1", "source": "qq", "status": "incomplete",
        "account": account, "conversations": cur_convs, "messages": cur_msgs,
        "previous_conversations": prev.get("conversations"),
        "previous_messages": prev_msgs,
        "complete": False,
        "errors": [{"source": "coverage-guard",
                    "error": f"本次仅 {cur_msgs} 条，低于上次 {prev_msgs} 条的 80%；"
                             "已保留旧 spool，未覆盖。检查 NTQQ_DB_KEY / qqmd/out 后重试，"
                             "或显式 --full。"}],
    }, ensure_ascii=False, indent=2))
    return EXIT_PARTIAL

  convs, msgs = place(spool, account, merged, _now())
  status = "partial" if errors else "ok"
  if not merged:
    status = "error"

  _atomic_json(frag_path, {
    "contract": "aimem-spool/1", "source": "qq", "adapter": "qqcli+decoder+snapshot",
    "version": version, "status": status, "complete": complete, "root": "qq",
    "accounts": [{"id": account, "label": account,
                  "conversations": convs, "messages": msgs}],
    "conversations": convs, "messages": msgs,
    "qqcli_sessions": len(qqcli),
    "qqcli_messages": sum(len(c["msgs"]) for c in qqcli.values()),
    "decoder_sessions": len(dec),
    "decoder_messages": sum(len(c["msgs"]) for c in dec.values()),
    "out_sessions": len(snap),
    "out_messages": sum(len(c["msgs"]) for c in snap.values()),
    "merged_conversations": convs, "merged_messages": msgs,
    "groups": groups, "duplicates": len(dup),
    "names_resolved": names_resolved,
    "unresolved_titles": [c["title"] for c in merged.values()
                          if not c.get("names_resolved")],
    "images_linked": img_links, "images_files": img_files,
    "images_placeholder": placeholders,
    "last_run": _now(), "errors": errors,
  })
  state.update({"contract": "aimem-spool/1", "source": "qq", "account": account,
                "qqcli": version, "last_run": _now(), "complete": complete,
                "conversations": convs, "messages": msgs})
  _atomic_json(state_path, state)

  print(json.dumps({"contract": "aimem-spool/1", "source": "qq", "status": status,
                    "complete": complete, "qqcli": version, "account": account,
                    "qqcli_sessions": len(qqcli),
                    "qqcli_messages": sum(len(c["msgs"]) for c in qqcli.values()),
                    "decoder_sessions": len(dec),
                    "decoder_messages": sum(len(c["msgs"]) for c in dec.values()),
                    "out_sessions": len(snap),
                    "out_messages": sum(len(c["msgs"]) for c in snap.values()),
                    "merged_conversations": convs, "merged_messages": msgs,
                    "groups": groups,
                    "conversations": convs, "messages": msgs,
                    "duplicates": len(dup), "names_resolved": names_resolved,
                    "images_linked": img_links, "images_files": img_files,
    "images_placeholder": placeholders,
                    "fragment": frag_path.name, "errors": errors},
                   ensure_ascii=False, indent=2))
  if status == "error":
    return EXIT_SETUP
  return EXIT_PARTIAL if status == "partial" else EXIT_OK


def _fail(args, code: int, message: str) -> int:
  print(json.dumps({"contract": "aimem-spool/1", "source": "qq",
                    "status": "setup_required", "accounts": [], "conversations": 0,
                    "messages": 0, "errors": [{"error": message}]},
                   ensure_ascii=False, indent=2))
  return code


def build_parser() -> argparse.ArgumentParser:
  ap = argparse.ArgumentParser(prog="python -m adapters.qq")
  sub = ap.add_subparsers(dest="cmd", required=True)
  ex = sub.add_parser("export", help="导出 QQ 聊天记录到 spool")
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
