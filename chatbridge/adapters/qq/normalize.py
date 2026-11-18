"""qqcli JSONL -> chat-md/1.

qqcli's `timestamp_iso` is UTC; the contract wants local time, so the adapter
formats from `timestamp_ms` in Asia/Shanghai instead.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
CONTRACT = "chat-md/1"


def _local(ms: int) -> datetime:
  return datetime.fromtimestamp(ms / 1000, TZ)


UID_RE = re.compile(r"^(uid_|u_)[A-Za-z0-9_-]+$")


def is_uid(value: str) -> bool:
  return bool(value) and bool(UID_RE.match(value))


def clean(text: str) -> str:
  """Drop C0 controls and Unicode separators that QQ embeds in message text."""
  out = []
  for ch in text or "":
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


def render_segments(segments: list[dict]) -> str:
  parts: list[str] = []
  for seg in segments or []:
    kind = seg.get("type") or ""
    text = clean(seg.get("text") or "").strip()
    if kind == "文本" or (not kind and text):
      if text:
        parts.append(text)
    elif kind == "图片":
      parts.append("🖼 图片")
    elif kind == "文件":
      name = seg.get("file_name") or "文件"
      parts.append(f"📎 {name}")
    elif kind in ("表情", "表情包"):
      parts.append(f"[{kind}]")
    elif kind:
      parts.append(f"[{kind}]")
  return "\n".join(p for p in parts if p) or "[空]"


def quote(body: str) -> list[str]:
  out = []
  for line in body.split("\n"):
    if not line.strip():
      continue
    out.append("> " + line if not line.startswith("> ") else line)
  return out or ["> [空]"]


def build(session: dict, rows: list[dict], *, account: str, exported_at: str) -> tuple[str, dict, int]:
  """Return (markdown, meta, message count) for one conversation."""
  rows = sorted(rows, key=lambda r: int(r.get("timestamp_ms") or 0))
  kind = "group" if session.get("is_group") or session.get("chat_type") == "group" else "private"
  # qqcli gives `name` = uid_xxx and `chat_name` = null; use anything readable
  # it does expose, otherwise keep the uid (reported via stats below).
  title = (session.get("chat_name") or session.get("name") or str(session.get("id") or "")).strip()
  unresolved_title = is_uid(title)
  cid = f"{account}:{session.get('id')}"

  lines = [
    "---",
    f"contract: {CONTRACT}",
    "platform: qq",
    f"account: {account}",
    f"kind: {kind}",
    f"conversation: {json.dumps(title, ensure_ascii=False)}",
    f"conversation_id: {json.dumps(cid, ensure_ascii=False)}",
    f"exported_at: {json.dumps(exported_at)}",
    "---",
    f"# {title}",
    "",
  ]

  current_date = None
  count = 0
  unresolved_senders = 0
  for row in rows:
    ms = int(row.get("timestamp_ms") or 0)
    if ms <= 0:
      continue
    dt = _local(ms)
    day = dt.strftime("%Y-%m-%d")
    if day != current_date:
      lines += [f"## {day}", ""]
      current_date = day
    sender = (row.get("sender_card") or row.get("sender_name")
              or str(row.get("sender_id") or "")).strip()
    if is_uid(sender):
      unresolved_senders += 1
    if str(row.get("sender_id")) == account or row.get("is_mine"):
      sender = "我"
    lines += [f"**{dt:%H:%M:%S} · {sender}**", ""]
    body = quote(render_segments(row.get("segments") or []))
    mid = row.get("message_id")
    if mid:
      body = [f"> <!-- mid: {mid} -->"] + body
    lines += body
    lines.append("")
    count += 1

  meta = {"kind": kind, "conversation": title, "conversation_id": cid,
          "unresolved_title": unresolved_title,
          "unresolved_senders": unresolved_senders}
  text = "\n".join(lines)
  if not text.endswith("\n"):
    text += "\n"
  return text, meta, count
