from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from urllib.parse import quote
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db, planner
from .embedders import make_embedder
from .index.indexer import Indexer
from .ingest.jsonl import build_units
from .ingest.markdown import parse_dir
from .rerank import make_reranker
from .search.pipeline import search
from .text import size_label
from .vectors import VectorStore

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>个人信息记忆检索</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --ink:#1b1f24; --sub:#6b7280; --line:#e5e7eb;
          --accent:#2563eb; --fs:#0f766e; --wx:#15803d; --qq:#1d4ed8; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.6 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }
  header { position:sticky; top:0; background:rgba(246,247,249,.92); backdrop-filter:blur(8px);
           border-bottom:1px solid var(--line); padding:18px 0 12px; z-index:5; }
  .wrap { max-width:960px; margin:0 auto; padding:0 20px; }
  h1 { font-size:19px; margin:0 0 2px; letter-spacing:.2px; }
  .sub { color:var(--sub); font-size:13px; margin-bottom:12px; }
  .bar { display:flex; gap:10px; }
  input[type=search] { flex:1; padding:13px 16px; font-size:16px; border:1px solid var(--line);
        border-radius:12px; background:#fff; outline:none; }
  input[type=search]:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(37,99,235,.12); }
  button { border:1px solid var(--line); background:#fff; border-radius:12px; padding:0 18px;
           font-size:15px; cursor:pointer; }
  button:hover { border-color:var(--accent); color:var(--accent); }
  .filters { display:flex; gap:8px; margin-top:10px; flex-wrap:wrap; align-items:center; }
  .chip { border:1px solid var(--line); background:#fff; border-radius:999px; padding:5px 12px;
          font-size:13px; cursor:pointer; color:var(--sub); }
  .chip.on { border-color:var(--accent); color:var(--accent); background:#eff6ff; }
  label.toggle { margin-left:auto; font-size:13px; color:var(--sub); display:flex; gap:6px; align-items:center; }
  main { padding:18px 0 60px; }
  .meta { color:var(--sub); font-size:13px; margin:0 0 12px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px 18px;
          margin-bottom:12px; }
  .card:hover { border-color:#c7d2fe; box-shadow:0 2px 10px rgba(15,23,42,.05); }
  .title { font-size:16px; font-weight:600; margin:0 0 4px; }
  .badges { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:6px; }
  .badge { font-size:12px; padding:2px 8px; border-radius:999px; background:#f3f4f6; color:#374151; }
  .badge.fs { background:#ecfdf5; color:var(--fs); }
  .badge.wechat { background:#f0fdf4; color:var(--wx); }
  .badge.qq { background:#eff6ff; color:var(--qq); }
  .path { font:12px/1.5 ui-monospace,Consolas,monospace; color:var(--sub); word-break:break-all; }
  .snippet { margin-top:8px; color:#374151; font-size:14px; }
  .why { margin-top:8px; font-size:12px; color:var(--sub); }
  .why span { background:#f9fafb; border:1px solid var(--line); border-radius:6px; padding:1px 6px; margin-right:4px; }
  .empty { color:var(--sub); text-align:center; padding:48px 0; }
  .score { margin-left:auto; font:12px ui-monospace,monospace; color:#9ca3af; }
  .hub { font-size:12px; font-weight:500; color:#2563eb; background:#eff6ff; border:1px solid #dbeafe; border-radius:999px; padding:2px 9px; text-decoration:none; vertical-align:middle; }
  #chips { display:inline-flex; gap:6px; flex-wrap:wrap; }
  #facets select { border:1px solid var(--line); background:#fff; border-radius:8px; padding:5px 8px; font-size:13px; color:#374151; }
  .actions { margin-top:8px; display:flex; gap:8px; }
  .action { font-size:12px; color:#2563eb; text-decoration:none; border:1px solid #dbeafe; background:#eff6ff; border-radius:6px; padding:2px 8px; }
</style>
</head>
<body>
<header><div class="wrap">
  <h1>个人信息记忆检索 <a class="hub" href="http://127.0.0.1:8766/" target="_blank" rel="noopener">数据源</a></h1>
  <div class="sub" id="stats">加载中…</div>
  <div class="bar">
    <input id="q" type="search" placeholder="例如：上周张经理发的采购方案 / 课表 第8周 / 显卡过热掉帧" autofocus>
    <button id="go">搜索</button>
  </div>
  <div class="filters">
    <span id="chips"><span class="chip on" data-src="">全部</span></span>
    <label class="toggle"><input id="rerank" type="checkbox"> AI 精排</label>
  </div>
  <div class="filters" id="facets">
    <select id="fkind"><option value="">全部类型</option></select>
    <select id="fext"><option value="">全部扩展名</option></select>
    <select id="faccount"><option value="">全部账号</option></select>
    <select id="fconv"><option value="">全部会话</option></select>
  </div>
</div></header>
<main><div class="wrap">
  <div class="meta" id="meta"></div>
  <div id="list"></div>
</div></main>
<script>
const $ = s => document.querySelector(s);
let source = "", seq = 0;
const fmtDate = ts => ts ? new Date(ts*1000).toLocaleString('zh-CN',{hour12:false}) : '';

async function stats(){
  const r = await fetch('/api/stats'); const d = await r.json();
  const parts = [];
  for (const [k,v] of Object.entries(d.by_source||{})) parts.push(`${k} ${v}`);
  const w = d.watch || {};
  let extra = '';
  if (w.status === 'watching' && w.last_sync) extra = `  ·  监听中（${w.last_sync} 同步 ${w.messages} 条）`;
  else if (w.status === 'path not found') extra = '  ·  监听目录不存在';
  else if (w.status && String(w.status).startsWith('error')) extra = `  ·  ${w.status}`;
  $('#stats').textContent = `索引 ${d.units.toLocaleString()} 个单元  ·  ${parts.join('  ·  ')}${extra}`;
  const counts = d.by_source || {};
  const order = ['', 'fs', 'wechat', 'qq', 'email'];
  const keys = [...order.filter(k => k === '' || counts[k]), ...Object.keys(counts).filter(k => !order.includes(k))];
  $('#chips').innerHTML = keys.map(k => {
    const n = k ? (counts[k] || 0) : 0;
    return `<span class="chip${k===source?' on':''}" data-src="${k}">${esc(k?label(k):'全部')}${n?` <b>${n}</b>`:''}</span>`;
  }).join('');
  document.querySelectorAll('#chips .chip').forEach(c => c.addEventListener('click', ()=>{
    source = c.dataset.src; stats(); run();
  }));
}

async function run(){
  const q = $('#q').value.trim();
  if(!q){ $('#list').innerHTML=''; $('#meta').textContent=''; return; }
  const my = ++seq;
  $('#meta').textContent = '搜索中…';
  const kind = $('#fkind').value, ext = $('#fext').value;
  const account = $('#faccount').value, conversation = $('#fconv').value;
  const url = `/api/search?q=${encodeURIComponent(q)}&source=${source}` +
    `&kind=${encodeURIComponent(kind)}&ext=${encodeURIComponent(ext)}` +
    `&account=${encodeURIComponent(account)}&conversation=${encodeURIComponent(conversation)}` +
    `&rerank=${$('#rerank').checked?1:0}`;
  const t0 = performance.now();
  const r = await fetch(url); const d = await r.json();
  if(my !== seq) return;
  fillFacets(d.facets || {});
  $('#meta').textContent = `${d.total ?? d.results.length} 条结果  ·  ${d.elapsed_ms} ms`;
  if(!d.results.length){ $('#list').innerHTML = '<div class="empty">没有找到，试试更短的关键词</div>'; return; }
  $('#list').innerHTML = d.results.map(r => {
    const b = r.source || 'fs';
    const title = r.title || r.path || r.id;
    const path = r.path ? `<div class="path">${esc(r.path)}</div>` : '';
    const why = (r.reasons||[]).map(x=>`<span>${esc(x)}</span>`).join('');
    return `<div class="card">
      <div class="badges"><span class="badge ${b}">${label(b)}</span>
        <span class="badge">${esc(r.kind||'')}</span>
        <span class="badge">${fmtDate(r.ts)}</span>
        ${r.size?`<span class="badge">${r.size_label||''}</span>`:''}
        <span class="score">${(r.score??0).toFixed(3)}</span></div>
      <div class="title">${esc(title)}</div>
      ${path}
      <div class="snippet">${esc(r.snippet||'')}</div>
      ${why?`<div class="why">${why}</div>`:''}
      ${(r.links||[]).length?`<div class="actions">${r.links.map(l=>`<a class="action" href="${esc(l.href)}" target="_blank" rel="noopener">${esc(l.label)}</a>`).join('')}</div>`:''}
    </div>`;
  }).join('');
}
function label(s){ return {fs:'本地文件',wechat:'微信',qq:'QQ',email:'邮箱'}[s] || s; }
function setOptions(sel, entries, allLabel){
  const cur = sel.value;
  sel.innerHTML = `<option value="">${allLabel}</option>` +
    entries.map(([k,v])=>`<option value="${esc(k)}">${esc(k?label(k):k)} ${v?`(${v})`:''}</option>`).join('');
  if ([...sel.options].some(o=>o.value===cur)) sel.value = cur;
}
function fillFacets(f){
  const src = Object.entries(f.source||{});
  const ext = Object.entries(f.ext||{}).slice(0,20);
  const acc = Object.entries(f.account||{});
  const conv = (f.conversation||[]).map(x=>[x.id, x.count]);
  setOptions($('#fkind'), Object.entries(f.kind||{}), '全部类型');
  setOptions($('#fext'), ext, '全部扩展名');
  setOptions($('#faccount'), acc, '全部账号');
  setOptions($('#fconv'), conv, '全部会话');
}
$('#fkind').addEventListener('change', run);
$('#fext').addEventListener('change', run);
$('#faccount').addEventListener('change', run);
$('#fconv').addEventListener('change', run);
function esc(s){ return (s||'').replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

$('#q').addEventListener('input', ()=> setTimeout(run, 280));
$('#go').addEventListener('click', run);
$('#q').addEventListener('keydown', e=>{ if(e.key==='Enter') run(); });
$('#rerank').addEventListener('change', run);
document.querySelectorAll('.chip').forEach(c => c.addEventListener('click', ()=>{
  document.querySelectorAll('.chip').forEach(x=>x.classList.remove('on'));
  c.classList.add('on'); source = c.dataset.src; run();
}));
stats();
setInterval(stats, 30000);
</script>
</body>
</html>
"""


class App:
    def __init__(
        self,
        db_path: str,
        store_path: str,
        embed_spec: str,
        rerank_spec: str | None,
        device: str | None,
        caption_archive: dict | None = None,
    ) -> None:
        self.conn = db.connect(db_path)
        self.store = VectorStore(store_path).load()
        self.embedder = make_embedder(embed_spec, device)
        self.reranker = (
            make_reranker(rerank_spec, device, "float16") if rerank_spec else None
        )
        self.lock = threading.RLock()
        self.indexer = Indexer(self.conn, self.store, self.embedder, verbose=False)
        self.watch_path: str | None = None
        self.watch_status = "off"
        self.last_sync: str | None = None
        self.last_sync_count = 0
        self.last_sync_sources: dict[str, int] = {}
        self._watch_scopes: dict[str, set[str]] = {}
        self.caption_archive = caption_archive or {}
        print(
            f"[serve] db={db_path} units={db.stats(self.conn)['units']} "
            f"vectors={len(self.store)} embed={embed_spec} rerank={rerank_spec}",
            flush=True,
        )

    def stats(self) -> dict:
        with self.lock:
            data = db.stats(self.conn)
        data["watch"] = {
            "path": self.watch_path,
            "status": self.watch_status,
            "last_sync": self.last_sync,
            "messages": self.last_sync_count,
            "sources": self.last_sync_sources,
        }
        return data

    def search(
        self, q: str, source: str, use_rerank: bool, filters: dict | None = None
    ) -> dict:
        filters = filters or {}
        with self.lock:
            t0 = time.time()
            plan = planner.plan(q)
            srcs = [s for s in str(source or "").split(",") if s]
            if srcs:
                plan.sources = srcs
            kinds = [k for k in filters.get("kind", []) if k]
            if kinds:
                plan.kinds = kinds
            exts = [e for e in filters.get("ext", []) if e]
            if exts:
                plan.exts = exts
            if filters.get("ts_from") is not None:
                plan.ts_from = filters["ts_from"]
            if filters.get("ts_to") is not None:
                plan.ts_to = filters["ts_to"]

            results = search(
                self.conn,
                self.store,
                self.embedder,
                q,
                limit=200,
                plan=plan,
                use_semantic=filters.get("mode") != "keyword",
                reranker=self.reranker if (self.reranker and use_rerank) else None,
            )

            account = filters.get("account")
            conversation = filters.get("conversation")
            sender = filters.get("sender")

            def keep(r) -> bool:
                u = r.unit
                if account and u.meta.get("account") != account:
                    return False
                if sender and u.meta.get("sender") != sender:
                    return False
                if conversation:
                    cid = u.meta.get("conversation_id") or u.scope or ""
                    name = u.meta.get("conversation") or ""
                    if conversation not in (cid, name):
                        return False
                return True

            filtered = [r for r in results if keep(r)]
            facets: dict = {
                "source": Counter(),
                "kind": Counter(),
                "ext": Counter(),
                "account": Counter(),
                "conversation": Counter(),
            }
            for r in filtered:
                u = r.unit
                facets["source"][u.source] += 1
                facets["kind"][u.kind] += 1
                if u.ext:
                    facets["ext"][u.ext] += 1
                acct = u.meta.get("account") or ""
                if acct:
                    facets["account"][acct] += 1
                cid = u.meta.get("conversation_id") or u.scope or ""
                if cid:
                    facets["conversation"][f"{u.meta.get('conversation') or cid}\x1f{cid}"] += 1

            limit = int(filters.get("limit") or 20)
            offset = int(filters.get("offset") or 0)
            out = []
            for r in filtered[offset : offset + limit]:
                d = r.to_dict()
                d["size_label"] = size_label(r.unit.size)
                d["links"] = self._links(r.unit)
                out.append(d)

            conv_facets = []
            for key, count in facets["conversation"].most_common(20):
                label, _, cid = key.partition("\x1f")
                conv_facets.append({"id": cid, "label": label, "count": count})

            return {
                "results": out,
                "total": len(filtered),
                "limit": limit,
                "offset": offset,
                "facets": {
                    "source": dict(facets["source"].most_common()),
                    "kind": dict(facets["kind"].most_common()),
                    "ext": dict(facets["ext"].most_common()),
                    "account": dict(facets["account"].most_common()),
                    "conversation": conv_facets,
                },
                "elapsed_ms": int((time.time() - t0) * 1000),
            }

    @staticmethod
    def _links(u) -> list[dict]:
        if not u.path and u.source not in ("wechat", "qq", "email"):
            return []
        q = quote(u.id, safe="")
        links: list[dict] = []
        if u.path:
            links.append({"rel": "open", "label": "打开", "href": f"/api/open?id={q}"})
            links.append(
                {"rel": "reveal", "label": "所在文件夹", "href": f"/api/reveal?id={q}"}
            )
        if u.source in ("wechat", "qq", "email"):
            links.append(
                {"rel": "context", "label": "查看上下文", "href": f"/api/context?id={q}"}
            )
        return links

    def open_path(self, path: str, *, reveal: bool = False) -> dict:
        target = Path(path)
        if sys.platform == "win32":
            if reveal:
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(target.parent if reveal else target)])
        return {"ok": True, "path": str(target), "reveal": reveal}

    def context(self, unit) -> dict:
        if unit.source not in ("wechat", "qq", "email"):
            return {"unit": self._unit_dict(unit), "messages": []}
        rows = self.conn.execute(
            "SELECT * FROM units WHERE source=? AND scope=? ORDER BY ts",
            (unit.source, unit.scope),
        ).fetchall()
        items = [db.row_to_unit(r) for r in rows]
        idx = next((i for i, x in enumerate(items) if x.id == unit.id), -1)
        if idx < 0:
            return {"unit": self._unit_dict(unit), "messages": []}
        lo = max(0, idx - 3)
        hi = min(len(items), idx + 4)
        return {
            "unit": self._unit_dict(unit),
            "messages": [
                {
                    "id": x.id,
                    "title": x.title,
                    "text": x.text,
                    "ts": x.ts,
                    "sender": x.meta.get("sender", ""),
                    "conversation": x.meta.get("conversation", ""),
                }
                for x in items[lo:hi]
            ],
        }

    @staticmethod
    def _unit_dict(unit) -> dict:
        return {
            "id": unit.id,
            "source": unit.source,
            "kind": unit.kind,
            "title": unit.title,
            "path": unit.path,
            "ts": unit.ts,
            "scope": unit.scope,
            "account": unit.meta.get("account", ""),
            "conversation": unit.meta.get("conversation", ""),
            "sender": unit.meta.get("sender", ""),
            "text": unit.text,
        }

    def sync_spool(self, spool: Path) -> None:
        """Sync every source listed in <spool>/manifest.json.

        Source-level prune only runs when the adapter reports status=ok; a
        partial export keeps the previous data for missing accounts.
        """
        manifest_path = spool / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(manifest_path)
        manifest = json.loads(manifest_path.read_text("utf-8-sig"))
        sources = manifest.get("sources") or {}
        with self.lock:
            self.watch_status = "syncing"
            try:
                total = 0
                removed = 0
                changed = False
                detail: dict[str, int] = {}
                for source, spec in sources.items():
                    root = spool / str(spec.get("root") or source)
                    if not root.exists():
                        continue
                    caps = None
                    cap_file = root / "captions.json"
                    if cap_file.exists():
                        caps = json.loads(cap_file.read_text("utf-8-sig"))
                    msgs = parse_dir(root, platform=source)
                    units = build_units(
                        msgs, captions=caps, caption_archive=self.caption_archive
                    )
                    new_scopes = {u.scope for u in units if u.scope}
                    complete = spec.get("complete") is True

                    # If the adapter died mid-write, the old fragment still
                    # advertises the last good counts. Skip the source instead
                    # of importing/deleting a half-written export.
                    if complete:
                        expected_msgs = spec.get("messages")
                        expected_convs = spec.get("conversations")
                        if expected_msgs is not None and len(units) != int(expected_msgs):
                            print(
                                f"[watch] skip {source}: messages {len(units)} != "
                                f"manifest {expected_msgs}",
                                flush=True,
                            )
                            continue
                        if expected_convs is not None and len(new_scopes) != int(expected_convs):
                            print(
                                f"[watch] skip {source}: conversations {len(new_scopes)} != "
                                f"manifest {expected_convs}",
                                flush=True,
                            )
                            continue

                    # Only a complete export may delete old scopes. A partial
                    # adapter run (missing key / fallback) must never wipe the
                    # good data; it may only upsert.
                    if complete:
                        rows = self.conn.execute(
                            "SELECT id, scope FROM units WHERE source=?", (source,)
                        ).fetchall()
                        stale = [r["id"] for r in rows if r["scope"] not in new_scopes]
                        if stale:
                            with self.conn:
                                removed += db.delete_units(self.conn, stale)
                            self.store.remove(stale)
                            changed = True

                    if units:
                        st = self.indexer.index_units(
                            units, force_embed=False, prune=complete
                        )
                        changed = changed or st.embedded > 0
                    total += len(units)
                    detail[source] = len(units)

                if changed:
                    self.store.save()
                self.last_sync = time.strftime("%Y-%m-%d %H:%M:%S")
                self.last_sync_count = total
                self.last_sync_sources = detail
                self.watch_status = "watching"
                print(
                    f"[watch] spool synced messages={total} removed={removed} "
                    f"sources={detail}",
                    flush=True,
                )
            except Exception as exc:  # keep serving the previous index
                self.watch_status = f"error: {exc}"
                print("[watch] spool sync failed:", exc, flush=True)

    def sync_dir(self, root: Path, platform: str, captions: dict | None) -> None:
        """Re-import an extractor output directory; called by the watcher."""
        with self.lock:
            self.watch_status = "syncing"
            try:
                msgs = parse_dir(root, platform=platform)
                units = build_units(
                    msgs, captions=captions, caption_archive=self.caption_archive
                )
                old = self._watch_scopes.get(platform, set())
                new = {u.scope for u in units if u.scope}
                removed = 0
                for scope in old - new:
                    rows = self.conn.execute(
                        "SELECT id FROM units WHERE source=? AND scope=?",
                        (platform, scope),
                    ).fetchall()
                    ids = [r["id"] for r in rows]
                    if ids:
                        with self.conn:
                            removed += db.delete_units(self.conn, ids)
                        self.store.remove(ids)
                if units:
                    self.indexer.index_units(units, force_embed=False, prune=True)
                if self.store is not None:
                    self.store.save()
                self._watch_scopes[platform] = new
                self.last_sync = time.strftime("%Y-%m-%d %H:%M:%S")
                self.last_sync_count = len(units)
                self.watch_status = "watching"
                print(
                    f"[watch] synced {len(units)} messages, removed {removed}, "
                    f"scopes={len(new)}",
                    flush=True,
                )
            except Exception as exc:  # keep serving the previous index
                self.watch_status = f"error: {exc}"
                print("[watch] sync failed:", exc, flush=True)


class _Watcher(threading.Thread):
    def __init__(
        self,
        app: App,
        root: Path,
        platform: str,
        interval: float,
        settle: float,
        captions: dict | None,
    ) -> None:
        super().__init__(daemon=True)
        self.app = app
        self.root = root
        self.platform = platform
        self.interval = interval
        self.settle = settle
        self.captions = captions
        self._stop = threading.Event()
        self._synced_fp: dict | None = None
        app.watch_path = str(root)
        app.watch_status = "watching" if root.exists() else "path not found"
        self.is_spool = (root / "manifest.json").exists()

    def fingerprint(self) -> dict:
        fp: dict[str, tuple[int, int]] = {}
        self.is_spool = (self.root / "manifest.json").exists()
        if self.is_spool:
            files = list(self.root.rglob("*.md"))
            files.append(self.root / "manifest.json")
            for p in files:
                if p.name.lower() == "index.md" or p.name.lower().endswith("-all.md"):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                fp[str(p)] = (st.st_mtime_ns, st.st_size)
            return fp
        for sub in ("private", "group"):
            d = self.root / sub
            if not d.exists():
                continue
            for p in d.rglob("*.md"):
                if p.name.lower() == "index.md" or p.name.lower().endswith("-all.md"):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                fp[str(p)] = (st.st_mtime_ns, st.st_size)
        return fp

    def run(self) -> None:
        last = None
        stable_since = None
        while not self._stop.is_set():
            try:
                fp = self.fingerprint()
                now = time.time()
                if fp != last:
                    last = fp
                    stable_since = now
                elif (
                    stable_since is not None
                    and now - stable_since >= self.settle
                    and fp != self._synced_fp
                ):
                    if self.is_spool:
                        self.app.sync_spool(self.root)
                    else:
                        self.app.sync_dir(self.root, self.platform, self.captions)
                    self._synced_fp = fp
                self._stop.wait(self.interval)
            except Exception as exc:
                print("[watch] loop error:", exc, flush=True)
                self._stop.wait(self.interval)


def _date_ts(value: str, *, end: bool = False) -> float | None:
    if not value:
        return None
    try:
        d = dt.datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    if end:
        d += dt.timedelta(days=1)
    return d.timestamp()


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            u = urlparse(self.path)
            if u.path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            if u.path == "/api/stats":
                self._send(
                    200,
                    json.dumps(app.stats(), ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
                return
            if u.path == "/api/search":
                qs = parse_qs(u.query)
                q = (qs.get("q") or [""])[0]
                source = (qs.get("source") or [""])[0]
                use_rerank = (qs.get("rerank") or ["0"])[0] == "1"
                filters = {
                    "kind": [x for x in (qs.get("kind") or [""])[0].split(",") if x],
                    "ext": [x for x in (qs.get("ext") or [""])[0].split(",") if x],
                    "account": (qs.get("account") or [""])[0],
                    "conversation": (qs.get("conversation") or [""])[0],
                    "sender": (qs.get("sender") or [""])[0],
                    "mode": (qs.get("mode") or [""])[0],
                    "limit": int((qs.get("limit") or ["20"])[0] or 20),
                    "offset": int((qs.get("offset") or ["0"])[0] or 0),
                    "ts_from": _date_ts((qs.get("from") or [""])[0]),
                    "ts_to": _date_ts((qs.get("to") or [""])[0], end=True),
                }
                try:
                    payload = app.search(q, source, use_rerank, filters)
                except Exception as exc:  # keep the demo alive
                    payload = {"results": [], "elapsed_ms": 0, "error": str(exc)}
                self._send(
                    200,
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
                return
            if u.path in ("/api/open", "/api/reveal", "/api/context"):
                qs = parse_qs(u.query)
                uid = (qs.get("id") or [""])[0]
                unit = db.get_unit(app.conn, uid) if uid else None
                if unit is None:
                    self._send(
                        404,
                        json.dumps({"error": "not found"}, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8",
                    )
                    return
                if u.path == "/api/context":
                    payload = app.context(unit)
                else:
                    if not unit.path or not Path(unit.path).exists():
                        self._send(
                            404,
                            json.dumps({"error": "path missing"}, ensure_ascii=False).encode("utf-8"),
                            "application/json; charset=utf-8",
                        )
                        return
                    payload = app.open_path(unit.path, reveal=(u.path == "/api/reveal"))
                self._send(
                    200,
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
                return
            self._send(404, b"not found", "text/plain")

        def log_message(self, *args) -> None:
            return

    return Handler


def serve(
    db_path: str,
    store_path: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    embed: str = "st:BAAI/bge-small-zh-v1.5",
    rerank: str | None = None,
    device: str | None = None,
    watch: str | None = None,
    watch_platform: str = "wechat",
    watch_interval: float = 5.0,
    watch_settle: float = 8.0,
    captions: str | None = None,
    caption_archive: str | None = None,
) -> None:
    archive = None
    if caption_archive:
        ap = Path(caption_archive)
        if ap.exists():
            archive = json.loads(ap.read_text("utf-8-sig"))
            print(f"[watch] caption archive={len(archive)} from {ap}", flush=True)
        else:
            print(f"[watch] caption archive not found: {ap}", flush=True)

    app = App(db_path, store_path, embed, rerank, device, caption_archive=archive)

    if watch:
        caps = None
        if captions:
            cp = Path(captions)
            if cp.exists():
                caps = json.loads(cp.read_text("utf-8"))
                print(f"[watch] captions={len(caps)} from {cp}", flush=True)
            else:
                print(f"[watch] captions file not found: {cp}", flush=True)
        _Watcher(
            app,
            Path(watch),
            watch_platform,
            float(watch_interval),
            float(watch_settle),
            caps,
        ).start()

    server = HTTPServer((host, port), make_handler(app))
    print(f"[serve] http://{host}:{port}", flush=True)
    server.serve_forever()
