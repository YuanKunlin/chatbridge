from __future__ import annotations

from aimem import db
from aimem.embedders import HashEmbedder
from aimem.index.indexer import Indexer
from aimem.ingest.jsonl import Message, build_units
from aimem.server import App
from aimem.vectors import VectorStore


def test_search_filters_facets_and_links(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    store = VectorStore(tmp_path / "v").load()
    msgs = [
        Message(
            platform="wechat",
            conversation="群A",
            sender="张三",
            ts=1760000000,
            text="课表 第8周",
            source_id="a1",
            account="wxid_a",
            conversation_id="123",
        ),
        Message(
            platform="email",
            conversation="项目",
            sender="李四",
            ts=1760000100,
            text="课表附件",
            source_id="e1",
            account="demo",
            conversation_id="t1",
        ),
    ]
    Indexer(conn, store, HashEmbedder(), verbose=False).index_units(build_units(msgs))
    app = App(str(tmp_path / "t.db"), str(tmp_path / "v"), "hash", None, None)
    out = app.search(
        "课表", "", False, {"kind": ["message"], "account": "wxid_a", "limit": 10}
    )
    assert out["total"] == 1
    assert out["results"][0]["account"] == "wxid_a"
    assert out["results"][0]["links"][0]["rel"] == "context"
    assert out["facets"]["source"] == {"wechat": 1}
    assert out["facets"]["account"] == {"wxid_a": 1}
