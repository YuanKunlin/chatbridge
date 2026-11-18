from __future__ import annotations

import unittest

from adapters.wechat import normalize

LEGACY = """---
platform: wechat
kind: group
conversation: "示例群"
conversation_id: "123@chatroom"
---
# 示例群

## 2025-10-12

**10:00:00 · 张三**

> 课程表更新了
> ![图片](<../media/demo.png>)

**10:01:00 · 我**

> 收到
"""


class NormaliseTest(unittest.TestCase):
  def test_rewrites_contract_fields(self) -> None:
    text, meta, n = normalize.normalise(LEGACY, account="wxid_demo",
                                        exported_at="2026-11-04T15:00:00+08:00")
    self.assertEqual(meta["contract"], "chat-md/1")
    self.assertEqual(meta["platform"], "wechat")
    self.assertEqual(meta["account"], "wxid_demo")
    self.assertEqual(meta["kind"], "group")
    self.assertEqual(meta["conversation"], "示例群")
    self.assertEqual(meta["conversation_id"], "wxid_demo:123@chatroom")
    self.assertEqual(n, 2)
    self.assertFalse(text.startswith("\ufeff"))
    self.assertNotIn("\r\n", text)
    self.assertTrue(text.startswith("---\ncontract: chat-md/1\n"))
    self.assertIn("# 示例群", text)

  def test_does_not_double_prefix(self) -> None:
    src = LEGACY.replace('conversation_id: "123@chatroom"',
                         'conversation_id: "wxid_demo:123@chatroom"')
    _, meta, _ = normalize.normalise(src, account="wxid_demo", exported_at="2026-11-04T15:00:00+08:00")
    self.assertEqual(meta["conversation_id"], "wxid_demo:123@chatroom")

  def test_orders_by_clock_and_fills_empty_body(self) -> None:
    src = ("---\nplatform: wechat\nkind: private\nconversation: \"x\"\n"
           "conversation_id: \"y\"\n---\n# x\n\n## 2025-01-01\n\n"
           "**10:05:00 · 我**\n\n> 后到的\n\n"
           "**09:00:00 · 我**\n\n> 先到的\n\n"
           "**10:05:00 · 我**\n\n> 同一秒 B\n\n"
           "**10:05:00 · 我**\n")
    text, _, n = normalize.normalise(src, account="acc",
                                     exported_at="2026-11-04T15:00:00+08:00")
    body = text.split("\n")
    times = [line for line in body if line.startswith("**")]
    self.assertEqual(times[0], "**09:00:00 · 我**")
    self.assertEqual(times[-1], "**10:05:00 · 我**")
    self.assertIn("> 同一秒 B", text)
    self.assertIn("> 后到的", text)
    self.assertEqual(n, 4)
    # the message with no body gets the placeholder
    idx = text.rindex("**10:05:00 · 我**")
    self.assertIn("> [空]", text[idx:])

  def test_counts_messages(self) -> None:
    _, body = normalize.split_front_matter(LEGACY)
    self.assertEqual(normalize.count_messages(body), 2)


if __name__ == "__main__":
  unittest.main()
