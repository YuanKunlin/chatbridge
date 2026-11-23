# ChatBridge 接口契约 v1

> 版本：`aimem-spool/1` + `chat-md/1`
> 状态：P0 冻结草案（Lead 维护）
> 日期：2026-11-04
> 变更规则：破坏性变更升版本号；只有 Lead 能改本文件，适配器 agent 只能提 proposal。

---

## 0. 范围与非目标

本契约约束：

- 微信、QQ、邮箱三个适配器如何把历史写成统一文件；
- Hub 如何合并 manifest、校验、触发搜索；
- 搜索端如何读取多源 spool。

非目标（本阶段明确不做）：

- 不做中英文 UI 切换/语言输入；
- 不做其他通讯来源（Telegram/SMS/钉钉/飞书）；
- **不使用 qqcli 的搜索命令、DuckDB 索引或排序**；只使用它读取/导出 QQ 数据；
- 不把搜索逻辑放进适配器；适配器只生产数据。

---

## 1. 路径与运行平面

```
~/chatbridge/                 # 代码根（WSL，git）
  core/CONTRACT.md
  core/validate_spool.py
  core/sources.json
  adapters/{wechat,qq,email}/
  hub/
  tests/
  spool-dev/                          # 开发样例，gitignore

D:\aimem-spool\                       # 运行数据（Windows）
  manifest.json
  wechat\<account>\...
  qq\<account>\...
  email\<account>\...
  _state\

D:\aimem-tools\qqcli\                 # qq.exe + duckdb.dll + LICENSE
D:\archive\2026-11\                   # 旧导出/备份
```

- 代码在 WSL，运行在 Windows；Hub 使用 Windows venv 和同步后的 runtime 副本。
- 适配器只能写 `D:\aimem-spool\<自己的 source>\`、`.manifest.<source>.json`、`_state/<source>.json`。
- 不允许写搜索 DB、其他 source、`core/` 契约。

---

## 2. Spool 目录

```
aimem-spool/
├── manifest.json
├── .manifest.wechat.json
├── .manifest.qq.json
├── .manifest.email.json
├── wechat/
│   └── <account>/                 # wxid_xxx 或账号目录名
│       ├── private/*.md
│       ├── group/*.md
│       ├── media/
│       └── captions.json          # 可选：图片 md5 -> OCR 文本
├── qq/
│   └── <account>/                 # QQ 号
│       ├── private/*.md
│       └── group/*.md
├── email/
│   └── <account>/
│       └── threads/*.md
└── _state/
    ├── wechat.json
    ├── qq.json
    └── email.json
```

规则：

- 一个会话一个 `.md`；`index.md` 跳过；`-ALL.md` 不允许放进 spool。
- 账号目录必须存在，防止两个账号同名会话互相覆盖。
- 写入先写 `.tmp`，再 `os.replace`；不允许半成品。
- 文件路径必须落在自己的 source 目录内，禁止 `..` 跳出。

---

## 3. Manifest

### 3.1 适配器片段 `.manifest.<source>.json`

```json
{
  "contract": "aimem-spool/1",
  "source": "wechat",
  "adapter": "wechat4",
  "version": "1.0",
  "status": "ok",
  "complete": true,
  "root": "wechat",
  "accounts": [
    {"id": "wxid_xxx", "label": "主账号", "conversations": 53, "messages": 19164}
  ],
  "conversations": 53,
  "messages": 19164,
  "last_run": "2026-11-04T13:58:00+08:00",
  "errors": []
}
```

### 3.2 Hub 合并后的 `manifest.json`

```json
{
  "contract": "aimem-spool/1",
  "generated_at": "2026-11-04T14:00:00+08:00",
  "sources": {
    "wechat": {"adapter": "wechat4", "root": "wechat", "status": "ok", "accounts": [], "conversations": 53, "messages": 19164, "last_run": "..."},
    "qq": {"adapter": "qqcli", "root": "qq", "status": "ok", "accounts": [], "conversations": 34, "messages": 2234, "last_run": "..."},
    "email": {"adapter": "imap", "root": "email", "status": "ok", "accounts": [], "conversations": 120, "messages": 800, "last_run": "..."}
  }
}
```

规则：

- 适配器只写片段；Hub 是唯一写 `manifest.json` 的角色。
- 搜索端对 `ok`/`partial` 的 source 会按 scope 清理旧数据（partial 仅表示图片等非会话缺失）；`error`/`consent_required`/`setup_required` 不清理。
- `status`：`ok` / `partial` / `error` / `consent_required` / `setup_required`。
- `complete`：本次导出是否覆盖该 source 的完整账号/会话集合。
  - `true`：搜索端可以按 scope 清理旧数据；
  - `false`：只允许 upsert，**禁止**删除旧数据（防止部分导出覆盖完整导出）。
- 计数是“本次导出后的总数”，不是增量数。
- 原子写：`manifest.json.tmp` → `os.replace`。

---

## 4. 会话 Markdown `chat-md/1`

### 4.1 示例

```markdown
---
contract: chat-md/1
platform: wechat
account: wxid_xxx
kind: group
conversation: "示例群"
conversation_id: "123@chatroom"
exported_at: "2026-11-04T13:58:00+08:00"
---
# 示例群

## 2025-10-12

**15:28:00 · 张三**

> 正文第一行
> 正文第二行

**15:31:00 · 我**

> ![图片](<../media/abc.gif>)
> [图片文字] 课表 第8周
```

### 4.2 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `contract` | 是 | 固定 `chat-md/1` |
| `platform` | 是 | `wechat` / `qq` / `email` |
| `account` | 是 | 账号隔离键 |
| `kind` | 是 | `private` / `group` / `thread` |
| `conversation` | 是 | 会话/线程标题 |
| `conversation_id` | 是 | 账号内唯一的原生 id；搜索端 scope 会补 `account:` 前缀 |
| `exported_at` | 否 | ISO8601，带时区 |

### 4.3 硬性规则

1. UTF-8 无 BOM，LF 换行。
2. `## YYYY-MM-DD` 唯一且升序；消息时间升序。
3. 消息头固定 `**HH:MM:SS · 发言人**`。
4. 正文每行 `> ` 开头；空正文用占位 `> [空]`。
5. 图片 `![图片](<相对路径>)`；文件 `📎 [文件名](<相对路径>)` 或 `📎 文件名`。
6. 相对路径以会话文件所在目录为基准（`group/` 下一层 → `../media/x.jpg`）。
7. 图片必须落在 spool 的 `media/` 内；附件原文不复制进 spool 时只写 `📎 文件名`，禁止用 `../../..` 指向 `xwechat_files`/QQ 目录。
8. 会话文件内不放表格；表格只放 `index.md`。
9. 系统/撤回消息保留占位，不丢。
10. 同一 `(account, conversation_id)` 只能有一个文件。
11. 有原生消息 ID 时写 `<!-- mid: <id> -->`：消息头上一行，或正文第一行 `> <!-- mid: <id> -->` 都支持；没有则用 `(sender, second, occurrence)`。

### 4.4 长文本

- 邮件正文可以很长，Markdown 不截断。
- 搜索端 `text` 保留全文供 FTS；`embed_text` 取「主题 + 前 1200 字 + 关键段落」。
- 如果评测显示尾部召回差，P2 给邮件加 `chunk` 子单元，不改本契约。

---

## 5. 适配器 CLI

统一入口：

```bash
python -m adapters.wechat export --spool D:\aimem-spool --account auto --json
python -m adapters.qq     export --spool D:\aimem-spool --account auto --json
python -m adapters.email  export --spool D:\aimem-spool --since 2025-01-01 --folders INBOX,Sent --json
```

参数：

| 参数 | 说明 |
|---|---|
| `--spool` | spool 根目录 |
| `--account` | `auto` 或账号 id |
| `--since` | 邮箱/增量起始日期 |
| `--folders` | 邮箱文件夹，逗号分隔 |
| `--full` | 全量重建；默认增量 |
| `--json` | stdout 输出机器可读结果 |
| `--dry-run` | 只探测，不写数据 |

退出码：

| 码 | 含义 |
|---|---|
| `0` | 成功 |
| `2` | 需要用户授权（QQ consent） |
| `3` | 需要配置/修复（未登录、缺 key、qqcli 未安装） |
| `4` | 部分成功（有会话失败，仍写了可用数据） |

stdout 最后一个 JSON：

```json
{
  "contract": "aimem-spool/1",
  "source": "wechat",
  "status": "ok",
  "accounts": ["wxid_xxx"],
  "conversations": 53,
  "messages": 19164,
  "fragment": ".manifest.wechat.json",
  "errors": []
}
```

规则：

- 适配器只写自己的 source 目录、片段、`_state`。
- 不写 `manifest.json`；不调用搜索；不投递 DSH。
- 原子写；失败保留上一次可用数据。
- 日志脱敏：不打印 key、授权码、邮箱地址、聊天正文。

---

## 6. 各适配器约束

### 6.1 微信

- 账号：扫描 `%USERPROFILE%\Documents\xwechat_files\wxid_*`，用库页 1 HMAC 校验 key；多账号隔离。
- 图片：必须走 `wechat4_decode_images.py --map`；可解码图片输出 `![图片]`，不允许一律 `🖼 图片`。
- 动图：GIF 保留 `.gif`；WXGF 用 ffmpeg 转 PNG；OCR 取首帧。
- caption：`captions.json`（md5 → 文本）或消息内 `[图片文字]`。
- 写 `.manifest.wechat.json` + `_state/wechat.json`。
- 没有图片 key 时，允许占位，但必须在 `errors`/`status=partial` 里说明。

### 6.2 QQ

- 以 `qqcli` 的 `init` / `sessions` / `history` / `export` 为主；qqcli 只能覆盖私聊时，允许用现有 `ntqq_export_md.py` 补群聊与昵称，两路按消息 ID/内容合并去重。
- **禁止**调用 `qq search`，禁止导入 qqcli 的 DuckDB 索引/排序。
- 导出格式：优先 `--format jsonl`，由适配器归一化为 `chat-md/1`；也可以用其 Markdown，但必须补 front matter。
- consent：`status=consent_required` 时把 `consent.scope` 原样返回；用户同意后才执行 `consent.command_after_user_agrees`。
- 工具位置：`D:\aimem-tools\qqcli\qq.exe`；保留 LICENSE/NOTICE。
- 批量：`sessions` → 逐会话 `export`；失败记录会话 id，不中断其他会话。
- 写 `.manifest.qq.json` + `_state/qq.json`，并写 `complete`。
- **覆盖保护**：本次消息数低于上次 fragment/state/磁盘计数的 80% 时，适配器必须保留旧 spool、返回 `status=incomplete`，不得覆盖；仅 `--full` 可显式跳过。

### 6.3 邮箱

- IMAP 只读；不改变已读/删除标记。
- 文件夹：INBOX + Sent（可配置）；先 `LIST` 发现，处理 UTF-7 名称。
- 线程归并：`References` / `In-Reply-To` 根 Message-ID；没有则规范化主题 + 参与者。
- 附件只写文件名，不下载内容。
- 增量：每文件夹 UIDVALIDITY + last_uid；重复运行不产生重复。
- 写 `.manifest.email.json` + `_state/email.json`。
- export 模式不投递 DSH，不改变 router 的 PAUSED/loop 状态。

---

## 7. 搜索 API v2

### 7.1 `/api/search`

```http
GET /api/search
  ?q=...
  &source=wechat,qq,email
  &account=...
  &kind=message,file
  &ext=pdf,docx
  &conversation=...
  &sender=...
  &from=2025-01-01&to=2025-12-31
  &mode=auto|keyword|semantic|chat|file|email
  &limit=20&offset=0
  &rerank=1
```

响应：

```json
{
  "results": [
    {
      "id": "...",
      "source": "wechat",
      "kind": "message",
      "title": "张三 · 示例群",
      "path": null,
      "ts": 1760000000,
      "account": "wxid_xxx",
      "conversation": "示例群",
      "conversation_id": "123@chatroom",
      "sender": "张三",
      "snippet": "...",
      "score": 0.93,
      "reasons": ["关键词命中", "语义命中"],
      "links": [
        {"rel": "context", "label": "查看上下文", "href": "/api/context?id=..."},
        {"rel": "open_md", "label": "打开导出文件", "href": "/api/open?id=..."}
      ]
    }
  ],
  "total": 120,
  "facets": {
    "source": {"fs": 50, "wechat": 40, "qq": 20, "email": 10},
    "kind": {"message": 70, "file": 50},
    "ext": {"pdf": 10, "docx": 8},
    "account": {"wxid_xxx": 40},
    "conversation": [{"id": "...", "label": "示例群", "count": 12}]
  },
  "elapsed_ms": 85
}
```

### 7.2 其他端点

| 端点 | 作用 |
|---|---|
| `GET /api/sources` | 来源注册表、数量、上次导出、状态 |
| `GET /api/open?id=` | 打开文件/导出 md；路径必须在 allowlist |
| `GET /api/reveal?id=` | Explorer 定位 |
| `GET /api/context?id=` | 聊天上下文 / 文件命中片段 |

### 7.3 筛选策略

- 查询中显式的时间/扩展名/来源 → 召回前过滤。
- 来源/账号/会话/发件人 facet → 默认在当前结果集（候选池 ≤ 200）内后筛选。
- UI 提供“在全库中筛选” → 带 `source/account/...` 重新请求。
- 不允许只做后筛选而不给全库入口。

### 7.4 来源注册表

`core/sources.json`：

```json
{
  "fs":     {"label": "本地文件", "color": "#6b7280", "adapter": null},
  "wechat": {"label": "微信", "color": "#16a34a", "adapter": "wechat4"},
  "qq":     {"label": "QQ", "color": "#2563eb", "adapter": "qqcli"},
  "email":  {"label": "邮箱", "color": "#d97706", "adapter": "imap"}
}
```

UI chip、planner 来源词、`label()` 都从注册表生成。

### 7.5 结果链接与安全

- `/api/open`、`/api/reveal` 只接受索引内 unit id，不接受任意路径。
- 解析后的真实路径必须位于 allowlist 根目录（spool、文件索引根）内。
- 不使用 shell 拼接；Windows 用 `os.startfile` / `explorer /select,`。
- 聊天上下文用 `id` 查同 `(source, account, conversation_id)` ± N 条。
- 邮件“打开”优先 QQ 邮箱搜索 URL 或本地 `.eml`；不伪造精确消息链接。

---

## 8. 搜索端多源行为

- 启动时读 `%LOCALAPPDATA%\aimem\spool.json` 或 `D:\aimem-spool\manifest.json`。
- 按 `manifest.sources` 遍历每个 source root；每个 root 用对应 `platform` 解析。
- 仅当 source 的 `complete=true` 才做 source-level prune；`complete=false` 时只 upsert。
- `account` 进入 `Unit.meta`，`scope = account:conversation_id`。
- 删除会话/账号：prune 对应 scope；删除 vector。
- watch 指纹覆盖 manifest + 所有 md；稳定 8 秒后增量同步。
- UI 状态显示：来源、账号、上次导出、监听状态。

---

## 9. 验收

`python core/validate_spool.py D:\aimem-spool --strict` 必须通过：

- manifest 与片段 schema；
- front matter 必填；
- 日期唯一升序、消息头/引用行；
- 图片/附件相对路径存在（缺失为 warning，strict 为 error）；
- manifest 计数与文件计数一致；
- 无 BOM、无 CRLF、无 `-ALL.md`、无越界路径。

适配器交付前必须附：

- 提交说明：命令、计数、失败项；
- validator 输出；
- 至少一个样例 spool。

---

## 10. 写入范围

| 角色 | 可写 | 只读/禁止 |
|---|---|---|
| A（微信+QQ） | `adapters/{wechat,qq}`、`spool-dev/{wechat,qq}`、`D:\aimem-tools\qqcli`、`D:\aimem-spool\{wechat,qq}` | `core/`、搜索、邮箱、`~/qqmd` 只读 |
| B（邮箱） | `~/qqmail_router`、`adapters/email`、`spool-dev/sample/email`、`D:\aimem-spool\email` | `core/`、搜索、微信/QQ |
| Lead | `core/`、`~/AI-Advaned-File-Search`、`hub/`、`D:\aimem-spool\manifest.json`、归档 | — |

契约变更：agent 只提 proposal；Lead 改 `CONTRACT.md`/`sources.json`/validator。
