from __future__ import annotations

import unittest

from adapters.qq import normalize

SESSION = {"id": "12345", "name": "uid_12345", "chat_type": "private", "is_group": False}

ROWS = [
  {"message_id": "2", "timestamp_ms": 1793772971000, "sender_id": "999",
   "sender_name": "uid_999",
   "segments": [{"type": "文本", "text": "后到的"}]},
  {"message_id": "1", "timestamp_ms": 1793772000000, "sender_id": "12345",
   "sender_name": "uid_12345",
   "segments": [{"type": "文本", "text": "先到的\x14带控制符"},
                {"type": "文件", "file_name": "方案.docx"}]},
  {"message_id": "3", "timestamp_ms": 1793772971000, "sender_id": "999",
   "sender_name": "uid_999", "segments": [{"type": "图片"}]},
]


class QQNormalizeTest(unittest.TestCase):
  def test_build(self) -> None:
    text, meta, n = normalize.build(SESSION, ROWS, account="12345",
                                    exported_at="2026-11-04T15:00:00+08:00")
    self.assertEqual(n, 3)
    self.assertEqual(meta["conversation_id"], "12345:12345")
    self.assertIn("contract: chat-md/1", text)
    self.assertIn('platform: qq', text)
    # local time (+08) ordering: earlier message first
    self.assertLess(text.index("先到的"), text.index("后到的"))
    # self messages are labelled 我
    self.assertIn("**", text)
    self.assertIn("· 我**", text)
    # control character stripped
    self.assertNotIn("\x14", text)
    # attachment is name-only, no external path
    self.assertIn("> 📎 方案.docx", text)
    self.assertNotIn("](<", text)
    # native message id hint inside the body
    self.assertIn("> <!-- mid: 1 -->", text)
    # image placeholder
    self.assertIn("🖼 图片", text)
    # ascending clock inside a day
    body = text.split("\n")
    stamps = [ln for ln in body if ln.startswith("**")]
    self.assertEqual(stamps, sorted(stamps, key=lambda s: s[2:10]))

  def test_empty_segments(self) -> None:
    text, _, _ = normalize.build(SESSION, [{"message_id": "9",
                                            "timestamp_ms": 1793772971000,
                                            "sender_id": "1", "sender_name": "a",
                                            "segments": []}],
                                 account="1", exported_at="2026-11-04T15:00:00+08:00")
    self.assertIn("> [空]", text)


if __name__ == "__main__":
  unittest.main()
