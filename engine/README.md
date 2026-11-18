# aimem 搜索内核

本目录是独立可运行的检索层：SQLite FTS5 + 向量 + RRF + 可选 cross-encoder 精排。

## 安装

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

可选依赖：

```bash
pip install "sentence-transformers" torch   # BGE / reranker
pip install pypdf python-docx openpyxl      # 文档正文抽取
```

## 命令

```bash
aimem init  --db a.db --store a.vectors
aimem index --db a.db --store a.vectors ~/Documents
aimem search "合同 违约金" --db a.db --store a.vectors
aimem serve --db a.db --store a.vectors --port 8765
aimem stats --db a.db --store a.vectors
```

聊天 Markdown：

```bash
aimem import-markdown <dir> --platform wechat --db a.db --store a.vectors
```

## 设计

- `Unit`: `file | chunk | message`
- 文件：file 身份 + chunk 正文，chunk 命中折叠到父 file 再 RRF
- 查询：FTS5 + 向量 + RRF + 启发式 + 可选精排
- 文件过滤：时间、扩展名、大小、来源

## 测试

```bash
pytest -q
```
