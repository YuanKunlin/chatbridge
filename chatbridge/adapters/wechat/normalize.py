"""Normalise the legacy wechat4 exporter output into `chat-md/1`.

The existing exporter already knows how to parse messages and images; it only
lacks the three contract fields (`contract`, `account`, `exported_at`) and uses
the native conversation id without the account prefix.  Rewriting the front
matter is cheaper and safer than forking the parser.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CONTRACT = "chat-md/1"
META_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$")
MSG_RE = re.compile(r"^\*\*(\d{1,2}:\d{2}(?::\d{2})?)\s*·\s*(.+?)\*\*\s*$")
DATE_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$")


def _unquote(value: str) -> str:
  if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
    return value[1:-1]
  return value


def split_front_matter(text: str) -> tuple[dict[str, str], str]:
  """Return (meta, body). Tolerates files without front matter."""
  lines = text.split("\n")
  if not lines or lines[0].strip() != "---":
    return {}, text
  meta: dict[str, str] = {}
  for i in range(1, len(lines)):
    line = lines[i]
    if line.strip() == "---":
      return meta, "\n".join(lines[i + 1:])
    m = META_RE.match(line)
    if m:
      meta[m.group(1).lower()] = _unquote(m.group(2))
  return {}, text


def conversation_id(account: str, native: str) -> str:
  native = (native or "").strip()
  if not native:
    return account
  return native if native.startswith(f"{account}:") else f"{account}:{native}"


SEPARATORS = {"\u2028": "\n", "\u2029": "\n", "\u0085": "\n"}


def _requote(body: str) -> str:
  """Guarantee every line inside a message body starts with `> `.

  Chat text can contain U+2028 and friends, which `str.splitlines()` treats as
  line breaks; without this pass the validator sees an unquoted continuation.
  """
  out: list[str] = []
  in_body = False
  for line in body.split("\n"):
    if DATE_RE.match(line):
      in_body = False
      out.append(line)
      continue
    if MSG_RE.match(line):
      in_body = True
      out.append(line)
      continue
    if in_body and line.strip():
      if line.startswith("> "):
        out.append(line)
      elif line.startswith(">"):
        out.append("> " + line[1:].lstrip())
      else:
        out.append("> " + line)
    else:
      out.append(line)
  return "\n".join(out)


def _seconds(clock: str) -> int:
  parts = [int(x) for x in clock.split(":")]
  while len(parts) < 3:
    parts.append(0)
  return parts[0] * 3600 + parts[1] * 60 + parts[2]


def _order(body: str) -> str:
  """Sort date sections ascending and messages by clock time (stable)."""
  sections: list[tuple[str, list[list[str]]]] = []
  prelude: list[str] = []
  current: list[list[str]] | None = None
  for line in body.split("\n"):
    if current is None and not DATE_RE.match(line) and not MSG_RE.match(line):
      prelude.append(line)
      continue
    if DATE_RE.match(line):
      current = []
      sections.append((line, current))
      continue
    if MSG_RE.match(line):
      if current is None:
        current = []
        sections.append(("", current))
      current.append([line])
      continue
    if current:
      current[-1].append(line)

  out: list[str] = list(prelude)
  while out and not out[-1].strip():
    out.pop()
  if out:
    out.append("")
  for date_line, messages in sorted(sections, key=lambda s: s[0]):
    if date_line:
      out.append(date_line)
      out.append("")
    ordered = sorted(messages, key=lambda block: _seconds(MSG_RE.match(block[0]).group(1)))
    for block in ordered:
      while block and not block[-1].strip():
        block.pop()
      if len(block) == 1:                      # header only -> contract placeholder
        block.append("> [空]")
      out.extend(block)
      out.append("")
  return "\n".join(out)


def count_messages(body: str) -> int:
  return sum(1 for line in body.split("\n") if MSG_RE.match(line))


def normalise(text: str, *, account: str, exported_at: str, default_kind: str = "",
              default_id: str = "") -> tuple[str, dict[str, str], int]:
  """Rewrite front matter to chat-md/1 and return (text, meta, message count)."""
  meta, body = split_front_matter(text)
  kind = meta.get("kind") or default_kind or "private"
  native = meta.get("conversation_id") or default_id
  conversation = meta.get("conversation") or ""
  contract_meta = {
    "contract": CONTRACT,
    "platform": "wechat",
    "account": account,
    "kind": kind if kind in ("private", "group", "thread") else "private",
    "conversation": conversation,
    "conversation_id": conversation_id(account, native),
    "exported_at": exported_at,
  }
  head = ["---"]
  for key in ("contract", "platform", "account", "kind"):
    head.append(f"{key}: {contract_meta[key]}")
  head.append(f"conversation: {json.dumps(contract_meta['conversation'], ensure_ascii=False)}")
  head.append(f"conversation_id: {json.dumps(contract_meta['conversation_id'], ensure_ascii=False)}")
  head.append(f"exported_at: {json.dumps(exported_at)}")
  head.append("---")
  for bad, good in SEPARATORS.items():
    body = body.replace(bad, good)
  body = _requote(body.lstrip("\n"))
  body = _order(body)
  out = "\n".join(head) + "\n" + body
  if not out.endswith("\n"):
    out += "\n"
  return out, contract_meta, count_messages(body)


def write_atomic(path: Path, data: bytes) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_name(path.name + ".tmp")
  tmp.write_bytes(data)
  tmp.replace(path)
