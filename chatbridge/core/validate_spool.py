#!/usr/bin/env python3
"""Validate an aimem spool against the ChatBridge contract.

Stdlib only. Run on Windows or WSL:

    python core/validate_spool.py D:\\aimem-spool --strict
    python core/validate_spool.py spool-dev/sample --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

CONTRACT = "aimem-spool/1"
CHAT_CONTRACT = "chat-md/1"
DATE_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$")
MSG_RE = re.compile(r"^\*\*(\d{1,2}:\d{2}(?::\d{2})?)\s*[·|]\s*(.+?)\*\*\s*$")
IMAGE_RE = re.compile(r"!\[图片\]\(<([^>]+)>\)")
ATTACH_RE = re.compile(r"📎\s*(?:\[([^\]]+)\]\(<([^>]+)>\)|([^\s]+))")
META_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$")
MID_RE = re.compile(r"^\s*<!--\s*mid:.*-->\s*$")


class Report:
    def __init__(self, strict: bool) -> None:
        self.strict = strict
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.sources: dict[str, dict] = {}

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str, critical: bool = False) -> None:
        if critical and self.strict:
            self.errors.append(msg)
        else:
            self.warnings.append(msg)


def parse_front_matter(text: str) -> tuple[dict[str, str], list[str]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, lines
    meta: dict[str, str] = {}
    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "---":
            return meta, lines[i + 1 :]
        m = META_RE.match(line)
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        meta[key] = value
    return {}, lines


def rel_path_ok(value: str, base: Path, spool: Path) -> tuple[bool, str | None]:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value) or value.startswith(("\\", "/")):
        return False, "absolute/URL path"
    target = (base / value).resolve()
    try:
        target.relative_to(spool.resolve())
    except ValueError:
        return False, "escapes spool"
    return target.exists(), None if target.exists() else "missing"


def validate_markdown(path: Path, source: str, spool: Path, rep: Report) -> tuple[int, str]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        rep.error(f"{path}: UTF-8 BOM")
    if b"\r\n" in raw:
        rep.error(f"{path}: CRLF (contract requires LF)")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        rep.error(f"{path}: not UTF-8 ({exc})")
        return 0, ""

    meta, body = parse_front_matter(text)
    for key in ("contract", "platform", "account", "kind", "conversation", "conversation_id"):
        if not meta.get(key):
            rep.error(f"{path}: missing front matter `{key}`")
    if meta.get("contract") and meta["contract"] != CHAT_CONTRACT:
        rep.error(f"{path}: contract={meta['contract']} expected {CHAT_CONTRACT}")
    if meta.get("platform") and meta["platform"] != source:
        rep.error(f"{path}: platform={meta['platform']} != source={source}")
    if meta.get("kind") and meta["kind"] not in ("private", "group", "thread"):
        rep.error(f"{path}: kind={meta['kind']} invalid")

    messages = 0
    cur_date: str | None = None
    last_date: str | None = None
    last_seconds = -1
    i = 0
    while i < len(body):
        line = body[i]
        dm = DATE_RE.match(line)
        if dm:
            cur_date = dm.group(1)
            if last_date and cur_date <= last_date:
                rep.error(f"{path}: date {cur_date} not ascending after {last_date}")
            last_date = cur_date
            last_seconds = -1
            i += 1
            continue
        if line.startswith("## "):
            rep.error(f"{path}: invalid date header {line!r}")
            i += 1
            continue
        mm = MSG_RE.match(line)
        if mm:
            if not cur_date:
                rep.error(f"{path}: message before any `## YYYY-MM-DD`")
            clock = mm.group(1)
            h, m, *rest = clock.split(":")
            sec = int(h) * 3600 + int(m) * 60 + (int(rest[0]) if rest else 0)
            if sec < last_seconds:
                rep.error(f"{path}: message time {clock} goes backwards")
            last_seconds = sec
            messages += 1
            i += 1
            # body lines until next date/header
            body_seen = False
            while i < len(body):
                nxt = body[i]
                if DATE_RE.match(nxt) or MSG_RE.match(nxt):
                    break
                if MID_RE.match(nxt):
                    i += 1
                    continue
                if nxt.strip():
                    body_seen = True
                    if not nxt.startswith("> "):
                        rep.error(f"{path}: body line not starting with '> ': {nxt[:40]!r}")
                    for lm in IMAGE_RE.finditer(nxt):
                        rel = lm.group(1)
                        exists, why = rel_path_ok(rel, path.parent, spool)
                        if not exists:
                            rep.warn(f"{path}: image {rel} {why or 'missing'}", critical=True)
                    for lm in ATTACH_RE.finditer(nxt):
                        # `📎 文件名` is allowed (name only); only linked
                        # attachments are expected to exist on disk.
                        rel = lm.group(2)
                        if not rel:
                            continue
                        exists, why = rel_path_ok(rel, path.parent, spool)
                        if not exists:
                            rep.warn(f"{path}: attachment {rel} {why or 'missing'}", critical=True)
                i += 1
            if not body_seen:
                rep.error(f"{path}: message {clock} has no body")
            continue
        i += 1
    return messages, meta.get("conversation_id", "")


def validate_fragment(path: Path, source: str, rep: Report) -> None:
    try:
        data = json.loads(path.read_text("utf-8-sig"))
    except Exception as exc:
        rep.error(f"{path}: invalid JSON ({exc})")
        return
    if data.get("contract") != CONTRACT:
        rep.error(f"{path}: contract != {CONTRACT}")
    if data.get("source") != source:
        rep.error(f"{path}: source={data.get('source')} expected {source}")


def validate(spool: Path, strict: bool) -> Report:
    rep = Report(strict)
    manifest_path = spool / "manifest.json"
    if not manifest_path.exists():
        # Adapters run before Hub merges the manifest; validate fragments.
        frags = sorted(spool.glob(".manifest.*.json"))
        if not frags:
            rep.error(f"missing {manifest_path} and no .manifest.*.json fragments")
            return rep
        sources = {}
        for frag in frags:
            try:
                data = json.loads(frag.read_text("utf-8-sig"))
            except Exception as exc:
                rep.error(f"{frag}: invalid JSON ({exc})")
                continue
            src = data.get("source")
            if not src:
                rep.error(f"{frag}: missing source")
                continue
            sources[src] = {
                "root": data.get("root") or src,
                "status": data.get("status", "ok"),
                "accounts": data.get("accounts", []),
                "conversations": data.get("conversations"),
                "messages": data.get("messages"),
            }
        rep.warn("manifest.json missing; merged fragments for validation")
        manifest = {"contract": CONTRACT, "sources": sources}
    else:
        try:
            manifest = json.loads(manifest_path.read_text("utf-8-sig"))
        except Exception as exc:
            rep.error(f"manifest.json invalid JSON: {exc}")
            return rep
        if manifest.get("contract") != CONTRACT:
            rep.error(f"manifest contract != {CONTRACT}")
    sources = manifest.get("sources")
    if not isinstance(sources, dict) or not sources:
        rep.error("manifest.sources missing/empty")
        return rep

    for source, spec in sources.items():
        if not isinstance(spec, dict):
            rep.error(f"{source}: manifest spec is not an object")
            continue
        root = spool / str(spec.get("root") or source)
        if not root.exists():
            rep.error(f"{source}: root missing {root}")
            continue
        frag = spool / f".manifest.{source}.json"
        if frag.exists():
            validate_fragment(frag, source, rep)
        else:
            rep.warn(f"{source}: missing fragment {frag.name}")

        accounts = spec.get("accounts") or []
        account_ids = {str(a.get("id")) for a in accounts if isinstance(a, dict)}
        md_files = [
            p for p in root.rglob("*.md")
            if p.name.lower() != "index.md" and not p.name.lower().endswith("-all.md")
        ]
        for p in root.rglob("*-ALL.md"):
            rep.error(f"{p}: -ALL.md must not be inside spool")
        total = 0
        convs = 0
        for md in md_files:
            if md.name.lower() == "index.md":
                continue
            n, cid = validate_markdown(md, source, spool, rep)
            total += n
            convs += 1
            acct = md.relative_to(root).parts[0] if len(md.relative_to(root).parts) > 1 else ""
            if account_ids and acct not in account_ids:
                rep.warn(f"{md}: account {acct!r} not in manifest accounts")
        if spec.get("messages") is not None and int(spec["messages"]) != total:
            rep.warn(
                f"{source}: manifest messages={spec['messages']} actual={total}",
                critical=True,
            )
        if spec.get("conversations") is not None and int(spec["conversations"]) != convs:
            rep.warn(
                f"{source}: manifest conversations={spec['conversations']} actual={convs}",
                critical=True,
            )
        rep.sources[source] = {"conversations": convs, "messages": total, "accounts": sorted(account_ids)}
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spool", nargs="?", default="spool-dev/sample")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    spool = Path(args.spool).resolve()
    rep = validate(spool, args.strict)
    payload = {
        "ok": not rep.errors,
        "strict": args.strict,
        "spool": str(spool),
        "sources": rep.sources,
        "errors": rep.errors,
        "warnings": rep.warnings,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"spool: {spool}")
        for source, s in rep.sources.items():
            print(f"  {source}: {s['conversations']} conversations, {s['messages']} messages")
        if rep.warnings:
            print(f"warnings: {len(rep.warnings)}")
            for w in rep.warnings:
                print("  WARN", w)
        if rep.errors:
            print(f"errors: {len(rep.errors)}")
            for e in rep.errors:
                print("  ERR ", e)
        print("OK" if not rep.errors else "FAILED")
    return 0 if not rep.errors else 1


if __name__ == "__main__":
    sys.exit(main())
