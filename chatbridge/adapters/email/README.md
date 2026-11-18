# email adapter · IMAP → `chat-md/1`

只读导出 QQ 邮箱历史，按线程写 `chat-md/1` Markdown。契约见
[core/CONTRACT.md](../../core/CONTRACT.md) 第 4/5/6.3 节。

## 用法

```bash
# 运行数据（WSL 写 D）
python3 -m adapters.email export \
  --spool /mnt/d/aimem-spool \
  --since 2025-11-04 \
  --folders INBOX,Sent \
  --json

# Windows 运行数据
py -m adapters.email export \
  --spool D:\aimem-spool \
  --since 2025-11-04 \
  --folders INBOX,Sent \
  --json
```

参数：

| 参数 | 说明 |
|---|---|
| `--spool` | spool 根目录；数据写到 `<spool>/email/<account>/threads/` |
| `--since` | 起始日期 `YYYY-MM-DD`；缺省 365 天（首次运行后写入 state） |
| `--folders` | 逗号分隔，默认 `INBOX,Sent`；`Sent` 自动匹配 `Sent Messages` / `已发送` |
| `--account` | `auto`（取凭据邮箱）或自定义目录名 |
| `--credentials` | 默认 `~/.config/qqmail/credentials.json`，也可用 `QQMAIL_CREDENTIALS` |
| `--full` | 忽略 `last_uid`，在 `--since` 范围内重扫（仍按 Message-ID 去重） |
| `--dry-run` | 只连接、LIST、SEARCH，不写任何文件 |
| `--json` | stdout 最后一行输出机读结果 |

环境变量：`QQMAIL_ROUTER_DIR`（`mail_core.py` 所在目录，默认 `~/qqmail_router`）、
`QQMAIL_IMAP_HOST` / `QQMAIL_IMAP_PORT` / `QQMAIL_IMAP_TIMEOUT`。

退出码：`0` 成功、`3` 配置/登录/文件夹错误、`4` 部分成功（已写可用数据，`errors` 非空）。

## 行为

- **只读**：始终 `SELECT ... (readonly)`，不 STORE/EXPUNGE/标记已读。
- **线程**：优先 `References` / `In-Reply-To` 的根 Message-ID；没有则
  `规范化主题 + 参与者集合`（去掉本账号）。`Re:` / `Fwd:` / `回复:` 前缀会归一化。
- **附件**：只写 `📎 文件名`，不下载内容。
- **长正文**：全文写入，不截断。
- **增量**：`_state/email.json` 保存每个文件夹的 `UIDVALIDITY + last_uid`，以及线程的
  Message-ID 列表；重复运行不重复、不重复拉取。
- **原子写**：Markdown/片段/state 都是 `.tmp` + `os.replace`。
- **无变化不写盘**：本次没有新消息、没有错误、state 也没变时，不改写 fragment/state
  （`last_run` 表示最近一次有数据变化的运行，重复运行可逐字节对比）。
- **隐私**：日志只写脱敏账号、文件夹、计数、错误；不写正文、附件名或授权码。

## 输出

```
<spool>/
  .manifest.email.json
  _state/email.json
  email/<account>/threads/thread-<hash>.md
```

`conversation_id = "<account>:<root-message-id>"`；无引用时为
`"<account>:subject:<hash>"`。每封消息带 `> <!-- mid: <message-id> -->` 作为稳定去重键
（契约 4.3.11 允许“消息头上一行”或“正文首行”两种位置；当前使用正文首行形式，validator 两种都接受）。

> `spool-dev/email/` 是仓库内的**合成样例**（1 thread / 2 messages，长正文 + 附件名），
> 不要用真实导出覆盖它；真实数据写 `D:\aimem-spool`。

## 测试

```bash
cd ~/chatbridge
python3 -m unittest discover -s adapters/email/tests -p 'test_*.py' -v
```

离线：fake IMAP + 临时 spool，不连接真实邮箱。

## 已知限制

- 根消息早于 `--since`、回复在范围内时，回复会先以 `root:<id>` 建线程；之后回填根消息会自动并入。
- 主题 + 参与者线程在“回复时新增/删除收件人”时会分裂；优先依赖 References。
- `Sent` 为空时分片计数为 0，不生成空目录。
