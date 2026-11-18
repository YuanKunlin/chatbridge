"""Build spool-dev/wechat: a two-account synthetic spool for validator tests.

Run: python -m adapters.wechat.tests.make_dev_spool
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from adapters.wechat import pipeline
from adapters.wechat.tests import fixtures

ROOT = Path(__file__).resolve().parents[3]
SPOOL = ROOT / "spool-dev" / "wechat"
PNG = bytes.fromhex(
  "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
  "890000000a49444154789c6360000002000154a24f610000000049454e44ae426082")


def export(account_id: str, wxid: str, conversations: list[dict]) -> tuple[int, int]:
  work = SPOOL / "_state" / "tmp" / account_id
  if work.exists():
    shutil.rmtree(work)
  work.mkdir(parents=True)
  plain = fixtures.build_account(work / "plain", account_id, wxid=wxid,
                                 conversations=conversations)
  md = work / "md"
  (md / "media").mkdir(parents=True)
  image = md / "media" / "demo.png"
  image.write_bytes(PNG)
  image_md5 = fixtures.md5("demo-image")
  image_map = work / "image-map.json"
  image_map.write_text(json.dumps({image_md5: str(image)}), "utf-8")

  account = pipeline.Account(id=account_id, wxid=wxid, db_dir=plain)
  pipeline.export_markdown(plain, md, account, None, image_map, pipeline.DEFAULT_TOOLS)
  items = pipeline.collect_markdown(md, account, "2026-11-04T15:00:00+08:00")
  target = SPOOL / "wechat" / account_id
  convs, msgs = pipeline.place(items, target)
  (target / "media").mkdir(parents=True, exist_ok=True)
  (target / "media" / "demo.png").write_bytes(PNG)
  return convs, msgs


def main() -> None:
  if SPOOL.exists():
    shutil.rmtree(SPOOL)
  (SPOOL / "wechat").mkdir(parents=True)
  image_md5 = fixtures.md5("demo-image")
  img_xml = f'<msg><img md5="{image_md5}"/></msg>'

  a = export("wxid_demo_a", "wxid_demo_a", [
    {"username": "a@chatroom", "title": "示例群", "kind": "group", "messages": [
      {"type": 1, "text": "课程表更新了", "sender_wxid": "wxid_bob"},
      {"type": 3, "xml": img_xml, "sender_wxid": "wxid_bob"},
    ]},
    {"username": "wxid_friend", "title": "示例好友", "kind": "private", "messages": [
      {"type": 1, "text": "私聊示例", "sender_wxid": "wxid_friend"},
    ]},
  ])
  b = export("wxid_demo_b", "wxid_demo_b", [
    {"username": "a@chatroom", "title": "示例群", "kind": "group", "messages": [
      {"type": 1, "text": "第二个账号的群", "sender_wxid": "wxid_carol"},
    ]},
  ])

  manifest = {
    "contract": "aimem-spool/1",
    "generated_at": "2026-11-04T15:00:00+08:00",
    "sources": {
      "wechat": {
        "adapter": "wechat4", "root": "wechat", "status": "ok",
        "accounts": [{"id": "wxid_demo_a"}, {"id": "wxid_demo_b"}],
        "conversations": a[0] + b[0], "messages": a[1] + b[1],
        "last_run": "2026-11-04T15:00:00+08:00",
      }
    },
  }
  (SPOOL / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8")
  fragment = {
    "contract": "aimem-spool/1", "source": "wechat", "adapter": "wechat4",
    "version": "1.0", "status": "ok", "root": "wechat",
    "accounts": [
      {"id": "wxid_demo_a", "label": "wxid_demo_a", "conversations": a[0], "messages": a[1]},
      {"id": "wxid_demo_b", "label": "wxid_demo_b", "conversations": b[0], "messages": b[1]},
    ],
    "conversations": a[0] + b[0], "messages": a[1] + b[1],
    "last_run": "2026-11-04T15:00:00+08:00", "errors": [],
  }
  (SPOOL / ".manifest.wechat.json").write_text(
    json.dumps(fragment, ensure_ascii=False, indent=2) + "\n", "utf-8")
  shutil.rmtree(SPOOL / "_state" / "tmp", ignore_errors=True)
  print(f"built {SPOOL}: {a[0] + b[0]} conversations, {a[1] + b[1]} messages")


if __name__ == "__main__":
  main()
