# ChatBridge · 个人信息记忆检索

把**本地文件、微信、QQ、邮箱**统一成一种可检索单元，提供：

- 文件/文档内容搜索（SQLite FTS5 + 向量语义）
- 微信 / QQ / 邮箱历史导入（适配器）
- 统一 spool + `chat-md/1` 契约
- Web 搜索 UI、filter/facet、结果上下文链接
- Hub 控制台：数据源状态、manifest 合并、适配器更新

> 本仓库只包含代码、契约和**合成样例**；不含真实聊天记录、数据库、模型权重。

## 目录

```
chatbridge/                 # 数据总线
  adapters/wechat/          # 微信本地库 -> chat-md
  adapters/qq/              # QQ(qqcli + 解码器) -> chat-md
  adapters/email/           # IMAP -> 线程 Markdown
  core/                     # CONTRACT / validator / manifest merge
  hub/                      # CLI + Web 8766
  spool-dev/sample/         # 三源合成样例
engine/                     # 搜索内核 aimem
  src/aimem/                # FTS5 + 向量 + RRF + rerank + server
  tests/
launcher/                   # Windows 本地启动器
```

## 快速开始（Linux/macOS）

```bash
cd engine
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
pip install -r ../chatbridge/requirements.txt
python -m aimem.cli init --db a.db --store a.vectors
python -m aimem.cli serve --db a.db --store a.vectors --port 8765
```

导入合成样例：

```bash
cd chatbridge
python3 core/validate_spool.py spool-dev/sample --strict
python3 core/merge_manifest.py spool-dev/sample
python -m aimem.cli serve --db ../a.db --store ../a.vectors --watch spool-dev/sample
```

Hub：

```bash
cd chatbridge
python -m hub web --spool spool-dev/sample --port 8766
```

## 数据契约

- spool / manifest：`chatbridge/core/CONTRACT.md`
- 会话 Markdown：`chat-md/1`
- 校验：`python3 chatbridge/core/validate_spool.py <spool> --strict`

## 隐私

- 适配器只读本机数据；搜索全部本地。
- 仓库不包含真实账号、聊天库、图片或模型。
- 微信/QQ 解码涉及本地客户端，请自行确认合规与账号风险。

## 状态

这是从个人项目中整理出的研究与工程实现，包含已知限制：

- 微信图片 key 依赖 Windows 进程内存；缺失时图片为占位。
- QQ 群聊/昵称依赖现有解码器与快照；需要 `NTQQ_DB_KEY` 时才能补全。
- 搜索暂无“无证据/低置信”阈值。
- macOS 可移植性分析见 `docs/macos-port-analysis.md`（如存在）。

搜索测试：`cd engine && pytest -q`。

## 开发与提交

源码以本仓库为准，变更后直接 `git push`；运行时同步见 [docs/WORKFLOW.md](docs/WORKFLOW.md)。
