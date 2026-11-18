from __future__ import annotations

from aimem import db
from aimem.embedders import HashEmbedder
from aimem.index.indexer import Indexer
from aimem.ingest.jsonl import build_units
from aimem.ingest.markdown import parse_dir, parse_markdown
from aimem.search.pipeline import search
from aimem.vectors import VectorStore


def _md(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_bracket_format(tmp_path):
    p = _md(tmp_path, "a.md",
            "[2025-10-12 15:28] 张经理: 新报价已经发给你了\n"
            "[2025-10-12 15:31] 我: 收到\n")
    msgs = parse_markdown(p)
    assert len(msgs) == 2
    assert msgs[0].sender == "张经理"
    assert msgs[0].text.startswith("新报价")
    assert msgs[0].ts > 0


def test_bold_format(tmp_path):
    p = _md(tmp_path, "b.md",
            "# 设备采购群\n\n## 2025-10-12\n\n"
            "**张经理** 15:28\n新报价已经发给你了。\n\n"
            "**我** 15:31\n收到，按最终版更新。\n")
    msgs = parse_markdown(p)
    assert len(msgs) == 2
    assert msgs[0].conversation == "设备采购群"
    assert msgs[0].sender == "张经理"
    assert msgs[1].sender == "我"


def test_quote_format(tmp_path):
    p = _md(tmp_path, "c.md",
            "> 2025-10-12 15:28 · 张经理\n> 新报价已经发给你了\n\n"
            "> 2025-10-12 15:31 · 我\n> 收到\n")
    msgs = parse_markdown(p)
    assert len(msgs) == 2
    assert msgs[0].sender == "张经理"


def test_front_matter(tmp_path):
    p = _md(tmp_path, "d.md",
            "---\nplatform: wechat\nconversation: 新能源项目组\n---\n"
            "**王总** 10:00\n光伏试点先按 500kW 做。\n")
    msgs = parse_markdown(p)
    assert msgs[0].platform == "wechat"
    assert msgs[0].conversation == "新能源项目组"


def test_fallback_document(tmp_path):
    p = _md(tmp_path, "e.md", "# 一份普通文档\n没有任何聊天结构。\n")
    msgs = parse_markdown(p)
    assert len(msgs) == 1
    assert msgs[0].sender == "文件"


def test_markdown_reuses_engine(tmp_path):
    md_dir = tmp_path / "chats"
    md_dir.mkdir()
    (md_dir / "wechat.md").write_text(
        "# 设备采购群\n\n**张经理** 15:28\n新报价已经发给你了，之前那个版本别用了。\n\n"
        "**我** 15:31\n收到，设备采购方案我按最终版更新。\n",
        encoding="utf-8",
    )
    conn = db.connect(tmp_path / "t.db")
    store = VectorStore(tmp_path / "t.vectors").load()
    emb = HashEmbedder()
    indexer = Indexer(conn, store, emb, verbose=False)
    indexer.index_units(build_units(parse_dir(md_dir)), force_embed=True)

    res = search(conn, store, emb, "之前那个版本别用了", limit=5)
    assert res
    assert res[0].unit.source == "chat-md"


def test_qq_time_first_format(tmp_path):
    p = _md(tmp_path, "qq.md",
            "---\n# 无关风月\n\n- 类型：私聊\n\n## 2026-09-09\n\n"
            "**11:50:12 · 无关风月**\n\n> 这内容讲了吗\n\n"
            "**11:51:08 · 我**\n\n> 所以我这题写了 AI 回答\n")
    msgs = parse_markdown(p)
    assert len(msgs) == 2
    assert msgs[0].sender == "无关风月"
    assert msgs[0].text == "这内容讲了吗"
    assert msgs[1].sender == "我"


def test_reimport_with_prune_updates_and_deletes(tmp_path):
    md = tmp_path / "chat.md"
    md.write_text(
        "# 群\n## 2025-10-12\n\n**10:00:00 · A**\n\n> hello\n\n"
        "**10:01:00 · B**\n\n> world\n",
        encoding="utf-8",
    )
    conn = db.connect(tmp_path / "t.db")
    store = VectorStore(tmp_path / "v").load()
    emb = HashEmbedder()
    indexer = Indexer(conn, store, emb, verbose=False)
    indexer.index_units(build_units(parse_markdown(md)), prune=True)
    assert conn.execute("select count(*) from units").fetchone()[0] == 2

    # updated export: A's text changed, B deleted, C added, order changed
    md.write_text(
        "# 群\n## 2025-10-12\n\n**10:02:00 · C**\n\n> new\n\n"
        "**10:00:00 · A**\n\n> hello v2\n",
        encoding="utf-8",
    )
    stats = indexer.index_units(
        build_units(parse_markdown(md)), force_embed=False, prune=True
    )

    texts = [r[0] for r in conn.execute("select text from units")]
    assert len(texts) == 2
    assert any("hello v2" in t for t in texts)
    assert any("new" in t for t in texts)
    assert not any("world" in t for t in texts)
    assert stats.deleted == 1
    assert stats.embedded == 2  # changed A + new C
    assert len(store) == 2  # B vector removed


def test_account_and_conversation_id_scope(tmp_path):
    md = tmp_path / "chat.md"
    md.write_text(
        "---\n"
        "contract: chat-md/1\n"
        "platform: wechat\n"
        "account: wxid_a\n"
        "kind: group\n"
        "conversation: \"群\"\n"
        "conversation_id: \"123@chatroom\"\n"
        "---\n"
        "# 群\n\n## 2025-10-12\n\n**10:00:00 · A**\n\n> hi\n",
        encoding="utf-8",
    )
    msgs = parse_markdown(md)
    assert msgs[0].account == "wxid_a"
    assert msgs[0].conversation_id == "123@chatroom"
    u = build_units(msgs)[0]
    assert u.scope == "wxid_a:123@chatroom"
    assert u.meta["account"] == "wxid_a"
    assert u.meta["conversation_id"] == "123@chatroom"


def test_mid_comment_is_not_body_text(tmp_path):
    md = tmp_path / "mail.md"
    md.write_text(
        "---\ncontract: chat-md/1\nplatform: email\naccount: a\nkind: thread\n"
        "conversation: \"t\"\nconversation_id: \"t1\"\n---\n"
        "# t\n\n## 2026-01-01\n\n**10:00:00 · A**\n\n"
        "> <!-- mid: abc@example.com -->\n> hello body\n",
        encoding="utf-8",
    )
    msgs = parse_markdown(md)
    assert msgs[0].message_id == "abc@example.com"
    assert "mid:" not in msgs[0].text
    assert msgs[0].text == "hello body"
    u = build_units(msgs)[0]
    assert u.meta["message_id"] == "abc@example.com"
