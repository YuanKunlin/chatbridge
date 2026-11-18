"""Synthetic WeChat plain databases for adapter tests (no real data)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import zstandard as zstd

Z = zstd.ZstdCompressor()


def md5(text: str) -> str:
  return hashlib.md5(text.encode()).hexdigest()


def _content(text: str) -> bytes:
  return Z.compress(text.encode("utf-8"))


def build_account(root: Path, account: str, *, wxid: str,
                  conversations: list[dict], media: dict[str, Path] | None = None) -> Path:
  """Create a decrypted-db directory shaped like wechat4_decrypt output.

  conversations: [{username, title, kind, messages:[{type, text|xml, sender}]}]
  """
  root.mkdir(parents=True, exist_ok=True)
  msg_db = sqlite3.connect(root / "message__message_0.db")
  msg_db.execute("CREATE TABLE Name2Id(user_name TEXT, is_session INTEGER)")
  senders: dict[str, int] = {}

  def sender_id(name: str) -> int:
    if name not in senders:
      cur = msg_db.execute("INSERT INTO Name2Id(user_name, is_session) VALUES (?, 1)", (name,))
      senders[name] = cur.lastrowid
    return senders[name]

  ts = 1760000000
  for conv in conversations:
    table = f"Msg_{md5(conv['username'])}"
    msg_db.execute(
      f"CREATE TABLE `{table}`(local_id INTEGER, server_id INTEGER, local_type INTEGER,"
      " sort_seq INTEGER, real_sender_id INTEGER, create_time INTEGER, message_content BLOB)")
    for i, m in enumerate(conv["messages"], start=1):
      body = m.get("text") if m.get("type", 1) == 1 else m["xml"]
      if conv["kind"] == "group":
        body = f"{m.get('sender_wxid', wxid)}: {body}"
      msg_db.execute(
        f"INSERT INTO `{table}` VALUES (?,?,?,?,?,?,?)",
        (i, 1000 + i, m.get("type", 1), (ts + i) * 1000, sender_id(m.get("sender_wxid", wxid)),
         ts + i, _content(body)))
  msg_db.commit()
  msg_db.close()

  session = sqlite3.connect(root / "session__session.db")
  session.execute("CREATE TABLE SessionTable(username TEXT)")
  session.executemany("INSERT INTO SessionTable VALUES (?)",
                      [(c["username"],) for c in conversations])
  session.commit()
  session.close()

  contact = sqlite3.connect(root / "contact__contact.db")
  contact.execute("CREATE TABLE contact(id INTEGER, username TEXT, nick_name TEXT,"
                  " remark TEXT, alias TEXT)")
  contact.execute("CREATE TABLE chat_room(id INTEGER, username TEXT, owner TEXT, ext_buffer BLOB)")
  for i, c in enumerate(conversations, start=1):
    contact.execute("INSERT INTO contact VALUES (?,?,?,?,?)",
                    (i, c["username"], c.get("title", c["username"]), "", ""))
    if c["kind"] == "group":
      contact.execute("INSERT INTO chat_room VALUES (?,?,?,?)", (i, c["username"], wxid, None))
  contact.commit()
  contact.close()

  hard = sqlite3.connect(root / "hardlink__hardlink.db")
  hard.execute("CREATE TABLE image_hardlink_info_v4(md5 TEXT, file_name TEXT, dir1 INTEGER,"
               " dir2 INTEGER, type INTEGER)")
  hard.execute("CREATE TABLE dir2id(username TEXT)")
  hard.execute("CREATE TABLE video_hardlink_info_v4(md5 TEXT, file_name TEXT, dir1 INTEGER,"
               " dir2 INTEGER, type INTEGER)")
  hard.execute("CREATE TABLE file_hardlink_info_v4(md5 TEXT, file_name TEXT, dir1 INTEGER,"
               " dir2 INTEGER, type INTEGER)")
  hard.commit()
  hard.close()

  if media:
    manifest = root / "media-map.json"
    manifest.write_text(json.dumps({k: str(v) for k, v in media.items()}), "utf-8")
  return root
