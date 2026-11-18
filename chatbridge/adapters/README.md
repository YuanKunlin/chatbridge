# Adapters

每个适配器只做三件事：读取源数据 → 写 `chat-md/1` → 写 manifest 片段。

禁止：

- 调用搜索、写搜索 DB、复用 qqcli 的搜索/索引；
- 写别的 source、`core/`、`manifest.json`；
- 把真实聊天数据提交到 git。

统一 CLI 与退出码见 [core/CONTRACT.md](../core/CONTRACT.md) 第 5 节。

## 目录约定

```
adapters/<source>/
  __init__.py
  __main__.py        # python -m adapters.<source>
  export.py
  tests/
```

开发样例写到 `spool-dev/<source>/`；运行数据写到 `D:\aimem-spool\<source>\<account>\`。
