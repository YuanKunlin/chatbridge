# Hub

统一入口的 P3 骨架。当前提供 CLI，后续加 Web 控制台/启动器。

```bash
python -m hub status   --spool D:\aimem-spool
python -m hub merge    --spool D:\aimem-spool
python -m hub validate --spool D:\aimem-spool --strict
python -m hub web      --spool D:\aimem-spool --port 8766
python -m hub export wechat --spool D:\aimem-spool --json
python -m hub export email  --spool D:\aimem-spool --since 2025-11-04 --folders INBOX,Sent --json
```

Hub 的职责：

- 页面按钮可触发适配器导出（后台任务 + 日志轮询）；

- 调适配器（微信/QQ/邮箱）；
- 合并 `.manifest.*.json` → `manifest.json`；
- 跑 validator；
- 后续：启动搜索、显示来源状态、打开浏览器。

Hub 不做：搜索排序、聊天解析、解密。
