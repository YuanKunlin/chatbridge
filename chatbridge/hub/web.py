#!/usr/bin/env python3
"""Minimal ChatBridge Hub web console (stdlib only).

    python -m hub.web --spool D:\\aimem-spool --port 8766

It shows adapter fragments/manifest and can merge them. It deliberately does
not run adapters yet; export buttons come after the Windows runtime layout is
stabilized.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"

PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ChatBridge Hub</title>
<style>
:root{--line:#e5e7eb;--sub:#6b7280;--ink:#111827}
body{margin:0;font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#f6f7f9;color:var(--ink)}
.wrap{max-width:920px;margin:0 auto;padding:32px 20px}
h1{font-size:24px;margin:0 0 4px}
.sub{color:var(--sub);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-top:22px}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px}
.card h3{margin:0 0 8px;font-size:16px}
.badge{display:inline-block;border-radius:999px;padding:1px 8px;font-size:12px;background:#f3f4f6;color:#374151}
.ok{background:#ecfdf5;color:#047857}.partial{background:#fffbeb;color:#b45309}.err{background:#fef2f2;color:#b91c1c}
.row{display:flex;justify-content:space-between;border-top:1px dashed var(--line);margin-top:10px;padding-top:8px;font-size:13px}
.muted{color:var(--sub);font-size:12px;word-break:break-all}
.bar{display:flex;gap:10px;margin-top:18px;flex-wrap:wrap}
button,a.btn{border:1px solid var(--line);background:#fff;border-radius:8px;padding:7px 12px;font-size:13px;cursor:pointer;text-decoration:none;color:var(--ink)}
button.primary{background:#111827;color:#fff;border-color:#111827}
pre{background:#0b1020;color:#d1d5db;padding:12px;border-radius:8px;overflow:auto;font-size:12px;max-height:220px}
</style></head><body><div class="wrap">
<h1>ChatBridge Hub</h1>
<div class="sub" id="sub">加载中…</div>
<div class="bar">
  <button class="primary" onclick="refresh()">刷新</button>
  <button onclick="merge()">合并 manifest</button>
  <a class="btn" href="http://127.0.0.1:8765/" target="_blank">打开搜索</a>
</div>
<div class="grid" id="grid"></div>
<pre id="log" style="margin-top:16px">就绪。</pre>
<script>
const $=s=>document.querySelector(s);
const esc=s=>(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const label={fs:'本地文件',wechat:'微信',qq:'QQ',email:'邮箱'};
async function refresh(){
  try{
    const d=await (await fetch('/api/status')).json();
    $('#sub').textContent=`spool ${d.spool}  ·  ${d.sources.length} 个来源  ·  ${d.generated_at||''}`;
    $('#grid').innerHTML=d.sources.map(s=>`
      <div class="card"><h3>${esc(label[s.source]||s.source)} <span class="badge ${s.status==='ok'?'ok':s.status==='partial'?'partial':'err'}">${esc(s.status||'?')}</span></h3>
      <div class="row"><span>会话</span><b>${s.conversations??'-'}</b></div>
      <div class="row"><span>消息</span><b>${s.messages??'-'}</b></div>
      <div class="row"><span>上次导出</span><span class="muted">${esc(s.last_run||'-')}</span></div>
      ${(s.accounts||[]).map(a=>`<div class="muted">${esc(a.id)} · ${a.messages??'-'} 条</div>`).join('')}
      ${(s.errors||[]).length?`<div class="muted">! ${esc(JSON.stringify(s.errors)).slice(0,180)}</div>`:''}
      <div class="row"><button onclick="run('${esc(s.source)}')">更新</button></div>
      </div>`).join('');
  }catch(e){ $('#sub').textContent='Hub API 不可用: '+e; }
}
async function run(source){
  try{
    const r=await fetch('/api/run?source='+encodeURIComponent(source));
    const d=await r.json();
    if(!r.ok){ $('#log').textContent=JSON.stringify(d); return; }
    $('#log').textContent=source+' 运行中…';
    const t=setInterval(async()=>{
      try{
        const j=await (await fetch('/api/job?source='+encodeURIComponent(source))).json();
        $('#log').textContent='['+(j.status||'?')+'] '+(j.output||'');
        if(j.status!=='running'){ clearInterval(t); refresh(); }
      }catch(e){ clearInterval(t); }
    },1500);
  }catch(e){ $('#log').textContent='启动失败: '+e; }
}
async function merge(){
  $('#log').textContent='合并中…';
  try{
    const r=await fetch('/api/merge'); const d=await r.json();
    $('#log').textContent=JSON.stringify(d,null,2);
    refresh();
  }catch(e){ $('#log').textContent='合并失败: '+e; }
}
refresh(); setInterval(refresh,15000);
</script></div></body></html>
"""


RUN: dict[str, dict] = {}


def _run_adapter(source: str, spool: Path) -> None:
    state = RUN[source]
    cmd = [
        sys.executable,
        "-m",
        f"adapters.{source}",
        "export",
        "--spool",
        str(spool),
        "--account",
        "auto",
        "--json",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    state.update({"status": "running", "code": None, "output": "", "started": time.strftime("%H:%M:%S")})
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            state["output"] = (state["output"] + line)[-12000:]
        state["code"] = proc.wait()
        state["status"] = "ok" if state["code"] == 0 else "error"
        # Merge fragments after a successful export; Hub is the only writer.
        if state["code"] == 0:
            subprocess.run(
                [sys.executable, str(CORE / "merge_manifest.py"), str(spool)],
                capture_output=True,
                text=True,
                timeout=30,
            )
    except Exception as exc:
        state["status"] = "error"
        state["output"] = (state["output"] + f"\n{exc}")[-12000:]


def read_status(spool: Path) -> dict:
    sources = []
    manifest = spool / "manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text("utf-8-sig"))
            for source, spec in (data.get("sources") or {}).items():
                sources.append(
                    {
                        "source": source,
                        **{k: spec.get(k) for k in ("status", "accounts", "conversations", "messages", "last_run", "errors")},
                    }
                )
            return {
                "spool": str(spool),
                "generated_at": data.get("generated_at"),
                "sources": sources,
            }
        except Exception:
            pass
    for frag in sorted(spool.glob(".manifest.*.json")):
        try:
            data = json.loads(frag.read_text("utf-8-sig"))
        except Exception:
            continue
        sources.append(
            {
                "source": data.get("source"),
                **{k: data.get(k) for k in ("status", "accounts", "conversations", "messages", "last_run", "errors")},
            }
        )
    return {"spool": str(spool), "generated_at": None, "sources": sources}


def make_handler(spool: Path):
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
            if u.path == "/api/status":
                data = read_status(spool)
                self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
                return
            if u.path == "/api/merge":
                try:
                    r = subprocess.run(
                        [sys.executable, str(CORE / "merge_manifest.py"), str(spool)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    data = {
                        "ok": r.returncode == 0,
                        "stdout": r.stdout[-2000:],
                        "stderr": r.stderr[-2000:],
                    }
                except Exception as exc:
                    data = {"ok": False, "stderr": str(exc)}
                self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
                return
            if u.path == "/api/run":
                source = (parse_qs(u.query).get("source") or [""])[0]
                if source not in ("wechat", "qq", "email"):
                    self._send(400, b'{"error":"bad source"}', "application/json")
                    return
                if RUN.get(source, {}).get("status") == "running":
                    self._send(409, b'{"error":"already running"}', "application/json")
                    return
                RUN[source] = {"status": "running", "code": None, "output": "", "started": time.strftime("%H:%M:%S")}
                threading.Thread(target=_run_adapter, args=(source, spool), daemon=True).start()
                self._send(200, json.dumps({"started": True, "source": source}).encode(), "application/json")
                return
            if u.path == "/api/job":
                source = (parse_qs(u.query).get("source") or [""])[0]
                self._send(200, json.dumps(RUN.get(source, {}), ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
                return
            self._send(404, b"not found", "text/plain")

        def log_message(self, *args) -> None:
            return

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spool", default="D:\\aimem-spool")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    args = ap.parse_args(argv)
    srv = HTTPServer((args.host, args.port), make_handler(Path(args.spool)))
    print(f"hub http://{args.host}:{args.port} spool={args.spool}", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
