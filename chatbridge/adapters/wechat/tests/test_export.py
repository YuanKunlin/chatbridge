from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from adapters.wechat import normalize, pipeline
from adapters.wechat.tests import fixtures

ROOT = Path(__file__).resolve().parents[3]
VALIDATOR = ROOT / "core" / "validate_spool.py"
PNG = bytes.fromhex(
  "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
  "890000000a49444154789c6360000002000154a24f610000000049454e44ae426082")


def _export_account(spool: Path, account_id: str, wxid: str, conversations: list[dict]) -> None:
  work = spool / "_state" / "tmp" / account_id
  work.mkdir(parents=True, exist_ok=True)
  plain = fixtures.build_account(work / "plain", account_id, wxid=wxid,
                                 conversations=conversations)
  md = work / "md"
  (md / "media").mkdir(parents=True, exist_ok=True)
  image = md / "media" / "demo.png"
  image.write_bytes(PNG)
  image_md5 = fixtures.md5("demo-image")
  image_map = work / "image-map.json"
  image_map.write_text(json.dumps({image_md5: str(image)}), "utf-8")

  account = pipeline.Account(id=account_id, wxid=wxid, db_dir=plain)
  pipeline.export_markdown(plain, md, account, None, image_map, pipeline.DEFAULT_TOOLS)
  items = pipeline.collect_markdown(md, account, "2026-11-04T15:00:00+08:00")
  target = spool / "wechat" / account_id
  conversations_n, messages_n = pipeline.place(items, target)
  (target / "media").mkdir(parents=True, exist_ok=True)
  (target / "media" / "demo.png").write_bytes(PNG)
  assert conversations_n and messages_n


class ExportLayoutTest(unittest.TestCase):
  def test_two_accounts_contract(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      spool = Path(tmp)
      img_xml = f'<msg><img md5="{fixtures.md5("demo-image")}"/></msg>'
      _export_account(spool, "wxid_a", "wxid_a", [
        {"username": "a@chatroom", "title": "群A", "kind": "group", "messages": [
          {"type": 1, "text": "你好", "sender_wxid": "wxid_bob"},
          {"type": 3, "xml": img_xml, "sender_wxid": "wxid_bob"},
        ]},
        {"username": "wxid_friend", "title": "好友", "kind": "private", "messages": [
          {"type": 1, "text": "私聊内容", "sender_wxid": "wxid_friend"},
        ]},
      ])
      _export_account(spool, "wxid_b", "wxid_b", [
        {"username": "a@chatroom", "title": "群A", "kind": "group", "messages": [
          {"type": 1, "text": "另一个账号", "sender_wxid": "wxid_carol"},
        ]},
      ])

      files = sorted(p.relative_to(spool).as_posix() for p in (spool / "wechat").rglob("*.md"))
      self.assertTrue(all(f.startswith(("wechat/wxid_a/", "wechat/wxid_b/")) for f in files))
      self.assertEqual(sum(f.startswith("wechat/wxid_a/") for f in files), 2)
      self.assertEqual(sum(f.startswith("wechat/wxid_b/") for f in files), 1)

      group_a = next((spool / "wechat/wxid_a/group").glob("*.md"))
      text = group_a.read_text("utf-8")
      self.assertNotIn("🖼 图片", text)
      self.assertIn("![图片](<../media/demo.png>)", text)
      self.assertIn('conversation_id: "wxid_a:a@chatroom"', text)
      self.assertIn("contract: chat-md/1", text)

      group_b = next((spool / "wechat/wxid_b/group").glob("*.md"))
      self.assertIn('conversation_id: "wxid_b:a@chatroom"', group_b.read_text("utf-8"))

      (spool / "manifest.json").write_text(json.dumps({
        "contract": "aimem-spool/1",
        "sources": {
          "wechat": {
            "root": "wechat", "adapter": "wechat4", "status": "ok",
            "accounts": [{"id": "wxid_a"}, {"id": "wxid_b"}],
            "conversations": 3, "messages": 4,
          }
        },
      }), "utf-8")

      r = subprocess.run([sys.executable, str(VALIDATOR), str(spool), "--strict"],
                         capture_output=True, text=True)
      self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

  def test_normalise_is_idempotent(self) -> None:
    text, _, _ = normalize.normalise(
      "---\nplatform: wechat\nkind: private\nconversation: \"x\"\nconversation_id: \"y\"\n---\n"
      "# x\n\n## 2025-01-01\n\n**00:00:00 · 我**\n\n> hi\n",
      account="acc", exported_at="2026-11-04T15:00:00+08:00")
    again, meta, _ = normalize.normalise(text, account="acc",
                                         exported_at="2026-11-04T15:00:00+08:00")
    self.assertEqual(meta["conversation_id"], "acc:y")
    self.assertEqual(again.count("---"), 2)


if __name__ == "__main__":
  unittest.main()
