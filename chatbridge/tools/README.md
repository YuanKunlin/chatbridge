# 本地解码器

这些脚本是当前微信/QQ 适配器的实际依赖，从原 `~/qqmd/fmt` 收进仓库，避免源码与运行时脱节。

- `wechat/`：微信 4.x 取 key、解密、媒体索引、图片解码、导出 Markdown。
- `qq/`：QQ NT 数据库 -> Markdown 导出器（需要 Windows + `sqlcipher3-binary`）。

依赖见 `chatbridge/requirements.txt`。数据目录（微信库、QQ `nt_db`、`qqmd/out`）不在仓库。
