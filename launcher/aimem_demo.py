#!/usr/bin/env python3
"""aimem 演示启动器：启动本机搜索服务并打开浏览器。

同目录下的 aimem-demo.json 可修改路径。exe 本身不包含模型或数据。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = (
    Path(sys.executable).parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
CONFIG = HERE / "aimem-demo.json"

DEFAULTS = {
    "venv_python": str(Path.home() / "aimem-win" / ".venv" / "Scripts" / "python.exe"),
    "app": str(Path.home() / "aimem-test" / "app"),
    "db": str(Path.home() / "aimem-test" / "demo.db"),
    "store": str(Path.home() / "aimem-test" / "demo.vectors"),
    "embed": "st:BAAI/bge-small-zh-v1.5",
    "rerank": "ce:" + str(Path.home() / "aimem-test" / "bge-reranker-v2-m3"),
    "device": "cuda",
    "port": 8765,
    "watch": "",
    "captions": str(Path.home() / "aimem-test" / "captions.json"),
    "caption_archive": str(Path.home() / "aimem-test" / "caption-archive.json"),
    "open_browser": True,
}


def load() -> dict:
    cfg = DEFAULTS.copy()
    if CONFIG.exists():
        try:
            cfg.update(json.loads(CONFIG.read_text("utf-8-sig")))
        except Exception as exc:  # keep the demo alive
            print("配置读取失败，使用默认值:", exc)
    return cfg




def _has_chat(d: Path) -> bool:
    try:
        if not d.is_dir():
            return False
        for sub in ("private", "group"):
            p = d / sub
            if p.is_dir() and any(p.glob("*.md")):
                return True
    except OSError:
        pass
    return False


def _chat_mtime(d: Path) -> float:
    best = 0.0
    for sub in ("private", "group"):
        try:
            for p in (d / sub).glob("*.md"):
                best = max(best, p.stat().st_mtime)
        except OSError:
            pass
    return best


def discover_export() -> Path | None:
    """Find the current spool or the newest legacy export.

    Priority: spool handshake/manifest -> decoder handshake -> legacy dirs.
    """
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local")))

    # ChatBridge spool first: a manifest is the single source of truth.
    spool_handshake = local / "aimem" / "spool.json"
    try:
        if spool_handshake.exists():
            data = json.loads(spool_handshake.read_text("utf-8-sig"))
            spool = Path(str(data.get("spool") or ""))
            if spool and (spool / "manifest.json").exists():
                return spool
    except Exception:
        pass
    for spool in (
        Path("D:/aimem-spool"),
        home / "Documents" / "aimem-spool",
        Path("C:/aimem-spool"),
    ):
        if (spool / "manifest.json").exists():
            return spool

    candidates: list[Path] = []

    handshake = local / "aimem" / "wechat-out.txt"
    try:
        if handshake.exists():
            raw = handshake.read_text("utf-8-sig").strip()
            if raw:
                candidates.append(Path(raw))
    except OSError:
        pass

    candidates += [
        home / "Documents" / "wechat-markdown",
        home / "Desktop" / "wechat-markdown",
        home / "wechat-markdown",
        Path("C:/wechat-markdown"),
        Path("D:/wechat-markdown"),
        Path("C:/chat/wechat"),
        Path("D:/chat/wechat"),
    ]

    for root in (home / "Desktop", home / "Documents", Path("D:/"), Path("C:/")):
        try:
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                candidates.append(child / "wechat-markdown")
                if child.name.lower() in ("wechat-markdown", "wechat"):
                    candidates.append(child)
        except OSError:
            continue

    best = None
    best_mtime = 0.0
    seen: set[str] = set()
    for c in candidates:
        key = str(c).lower()
        if key in seen:
            continue
        seen.add(key)
        if not _has_chat(c):
            continue
        m = _chat_mtime(c)
        if m > best_mtime:
            best, best_mtime = c, m
    return best


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", int(port))) == 0


def stats(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url + "/api/stats", timeout=2) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def free_port(start: int) -> int:
    for p in range(start, start + 20):
        if not port_open(p):
            return p
    return start


def wait_up(url: str, timeout: int = 150) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if stats(url):
            return True
        time.sleep(1)
    return False


def fail(msg: str) -> None:
    print("\n" + "=" * 64)
    print(msg)
    print("=" * 64)
    try:
        input("\n按 Enter 退出...")
    except Exception:
        pass
    sys.exit(1)


def main() -> None:
    cfg = load()
    print("=" * 64)
    print(" aimem 个人信息记忆检索 · 演示启动器")
    print("=" * 64)

    if not cfg.get("watch"):
        found = discover_export()
        if found:
            cfg["watch"] = str(found)
            print(f"[auto] 检测到最新导出: {found}")
            cp = found / "captions.json"
            if cp.exists():
                cfg["captions"] = str(cp)
                print(f"[auto] 使用 caption: {cp}")
            else:
                cp = found.parent / "captions.json"
                if cp.exists():
                    cfg["captions"] = str(cp)
                    print(f"[auto] 使用 caption: {cp}")

    required = ["venv_python", "app"]
    if not cfg.get("watch"):
        # 没有 watch 时必须用已建好的库；有 watch 时允许空库自动建索引
        required += ["db", "store"]
    for key in required:
        if not Path(cfg[key]).exists():
            fail(f"找不到 {key}:\n  {cfg[key]}\n\n请检查同目录下的 aimem-demo.json。")

    port = int(cfg["port"])
    url = f"http://127.0.0.1:{port}"

    if port_open(port):
        if stats(url):
            print(f"服务已在运行: {url}")
            if cfg.get("open_browser"):
                webbrowser.open(url)
            print("按 Enter 关闭本窗口（不会停止已运行的服务）")
            input()
            return
        port = free_port(port + 1)
        url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env["PYTHONPATH"] = cfg["app"] + os.pathsep + env.get("PYTHONPATH", "")
    env["HF_ENDPOINT"] = "https://hf-mirror.com"
    env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    cmd = [
        cfg["venv_python"],
        "-m",
        "aimem.cli",
        "--db",
        cfg["db"],
        "serve",
        "--store",
        cfg["store"],
        "--device",
        cfg["device"],
        "--port",
        str(port),
        "--embed",
        cfg["embed"],
    ]
    if cfg.get("watch"):
        cmd += ["--watch", cfg["watch"]]
        if cfg.get("captions"):
            cmd += ["--captions", cfg["captions"]]
        if cfg.get("caption_archive"):
            cmd += ["--caption-archive", cfg["caption_archive"]]
    if cfg.get("rerank"):
        cmd += ["--rerank", cfg["rerank"]]

    print(f"启动服务: {url}")
    print("首次加载模型约 20–40 秒，请稍候...\n")
    proc = subprocess.Popen(cmd, env=env, cwd=str(HERE))

    try:
        if not wait_up(url):
            proc.terminate()
            fail("服务启动超时。请查看上面的错误信息，或修改 aimem-demo.json 后重试。")
        print(f"演示已就绪: {url}\n")
        if cfg.get("open_browser"):
            webbrowser.open(url)
        print("关闭此窗口即停止服务。")
        proc.wait()
    except KeyboardInterrupt:
        print("\n正在停止...")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
