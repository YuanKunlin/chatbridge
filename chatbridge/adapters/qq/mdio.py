"""Parse and render conversation Markdown (decoder output, legacy snapshot, qqcli).

One parser for all three sources so the merge compares like with like:

    {kind, title, native, msgs: [{date, time, sender, lines, mid}]}
"""
from __future__ import annotations

import json
import re

CONTRACT = "chat-md/1"
DATE_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$")
MSG_RE = re.compile(r"^\*\*(\d{1,2}:\d{2}(?::\d{2})?)\s*·\s*(.+?)\*\*\s*$")
META_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$")
MID_RE = re.compile(r"^>\s*<!--\s*mid:\s*(\S+?)\s*-->")
ID_RE = re.compile(r"会话 ID[：:]\s*`([^`]+)`")
KIND_RE = re.compile(r"类型[：:]\s*(\S+)")


def split_front_matter(text: str) -> tuple[dict[str, str], str, str]:
  """Return (meta, title, body). Tolerates files without front matter."""
  lines = text.split("\n")
  meta: dict[str, str] = {}
  body_start = 0
  if lines and lines[0].strip() == "---":
    for i in range(1, len(lines)):
      if lines[i].strip() == "---":
        body_start = i + 1
        break
      m = META_RE.match(lines[i])
      if m:
        v = m.group(2)
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
          v = v[1:-1]
        meta[m.group(1).lower()] = v
  title = ""
  body_lines: list[str] = []
  for line in lines[body_start:]:
    if not title and line.startswith("# "):
      title = line[2:].strip()
      continue
    if not title and re.match(r"^-\s*(类型|会话 ID|消息数|时间范围)", line):
      continue
    body_lines.append(line)
  return meta, title, "\n".join(body_lines)


def parse(text: str) -> dict:
  meta, title, body = split_front_matter(text)
  native = meta.get("conversation_id") or ""
  if ":" in native:
    native = native.split(":", 1)[1]
  kind = meta.get("kind") or ""
  for line in body.split("\n"):
    if not native:
      m = ID_RE.search(line)
      if m:
        native = m.group(1)
    if not kind:
      m = KIND_RE.search(line)
      if m:
        kind = "group" if "群" in m.group(1) else "private"
  title = meta.get("conversation") or title or native
  msgs: list[dict] = []
  date = ""
  cur: dict | None = None
  for line in body.split("\n"):
    m = DATE_RE.match(line)
    if m:
      date = m.group(1)
      cur = None
      continue
    m = MSG_RE.match(line)
    if m:
      cur = {"date": date, "time": m.group(1), "sender": m.group(2),
             "lines": [], "mid": None}
      msgs.append(cur)
      continue
    if cur is None:
      continue
    mid = MID_RE.match(line)
    if mid:
      cur["mid"] = mid.group(1)
      continue
    if line.startswith("> "):
      cur["lines"].append(line[2:])
    elif line.strip() == ">":
      cur["lines"].append("")
  for msg in msgs:
    while msg["lines"] and not msg["lines"][-1].strip():
      msg["lines"].pop()
  return {"kind": kind or "private", "title": title, "native": native, "msgs": msgs}


def assign_order(msgs: list[dict]) -> None:
  """Ordinal per (date,time,sender) so multi-message seconds stay aligned."""
  seen: dict[tuple[str, str, str], int] = {}
  for msg in msgs:
    k = (msg["date"], msg["time"], msg["sender"])
    msg["ord"] = seen.get(k, 0)
    seen[k] = msg["ord"] + 1


def key(msg: dict) -> tuple[str, str, str, int]:
  """Same-sender messages in one second are matched by ordinal.

  Older sources render identical events differently (e.g. `@全体成员` vs
  `*[系统消息 type=5588]*`), so text must not take part in the identity.
  """
  return (msg["date"], msg["time"], msg["sender"], msg.get("ord", 0))


def _seconds(clock: str) -> int:
  parts = [int(x) for x in clock.split(":")]
  while len(parts) < 3:
    parts.append(0)
  return parts[0] * 3600 + parts[1] * 60 + parts[2]


def render(conv: dict, *, account: str, exported_at: str) -> str:
  msgs = conv["msgs"]
  # stable chronological order; identical (date,time) keeps source order
  msgs = sorted(msgs, key=lambda m: (m["date"], _seconds(m["time"])))
  head = [
    "---",
    f"contract: {CONTRACT}",
    "platform: qq",
    f"account: {account}",
    f"kind: {conv['kind']}",
    f"conversation: {json.dumps(conv['title'], ensure_ascii=False)}",
    f"conversation_id: {json.dumps(f'{account}:{conv['native']}', ensure_ascii=False)}",
    f"exported_at: {json.dumps(exported_at)}",
    "---",
    f"# {conv['title']}",
    "",
  ]
  lines = list(head)
  current_date = ""
  for msg in msgs:
    if msg["date"] != current_date:
      current_date = msg["date"]
      lines += [f"## {current_date}", ""]
    lines += [f"**{msg['time']} · {msg['sender']}**", ""]
    if msg["mid"]:
      lines.append(f"> <!-- mid: {msg['mid']} -->")
    body = [("> " + ln) if ln.strip() else "> " for ln in msg["lines"]]
    lines += body or ["> [空]"]
    lines.append("")
  text = "\n".join(lines)
  return text if text.endswith("\n") else text + "\n"
