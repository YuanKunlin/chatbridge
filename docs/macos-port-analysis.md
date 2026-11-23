# ChatBridge / aimem 迁移 macOS 可执行性分析

> 报告类型：只读分析（未修改任何源码、未改动 `D:\aimem-spool` / `C:\Users\<user>\aimem-test`、未运行微信/QQ 解密）
> 分析范围：`~/chatbridge`、`~/AI-Advaned-File-Search`、`launcher/`、`~/qqmail_router`、`~/qqmd`（只读）
> 分析基线：2026-11-08；代码版本 = 工作区当前状态（chatbridge `git status` 除本报告新增文件外干净，无未提交的源码改动）
> 证据形式：`文件:行号`，全部为本次实际读取/执行所得；凡未在真机 macOS 上验证的结论均显式标注

---

## 0. 执行摘要

**一句话结论：这是一个"85% 可以直接搬到 Mac 上跑"的项目——搜索内核、文件索引、IMAP 邮箱、Spool/契约层、Hub/Web 控制台几乎零改动；真正的 macOS 阻塞只有两个：微信密钥获取（Windows 进程内存读取）和 QQ 的 `qq.exe`/`sqlcipher3` 依赖，两者都可以用"Windows 侧解码 → 同步 spool（2.2 MB）"绕开，而不必在 Mac 上重写解码器。**

| 指标 | 数值 | 口径 |
|---|---|---|
| 总体可移植比例（加权） | **87.5 %** | 13,139 行产品代码，A=1.0 / B=0.9 / C=0.3 / D=0.05 加权 |
| 零改动直接可跑 | **65.6 %**（8,619 行） | 分类 A |
| 零改动 + 小改（≤1 文件/模块） | **85.8 %**（11,271 行） | 分类 A+B |
| 必须换实现 / 不可移植 | **14.2 %**（1,868 行） | 分类 C+D |
| 若采用"Windows 解码 + spool 同步"方案 | Mac 侧需迁移的代码降到 **11,271 行（85.8%）**，其余 1,868 行**完全不迁移** | 见 §5 方案 B |
| 需要跨机器搬运的数据量 | **2.2 MB**（104 个 `.md`，21,163+2,204+28 = 23,395 条消息） | `/mnt/d/aimem-spool` 实测 |

**估算方法**：把"产品范围"内每个模块的 Python LOC 归入四类（A 原生可跑 / B 只改路径或平台分支 / C 核心逻辑需重写 / D 依赖 Windows 二进制或 API），用 A=1.0、B=0.9、C=0.3、D=0.05 加权除以总行数。行数用 `wc -l` 实测，分类依据是逐文件读取 + 平台关键词扫描（扫描器与完整分档表见 §10 附录 A，可复现）。分类"权重"是主观但可审计的：C 保留数据契约故给 0.3，D 完全不可复用得 0.05。

**三大阻塞**（全部集中在"聊天原始数据解码"，不在搜索与数据层）：

1. **微信 4.x 密钥只能从 Windows 进程内存读**：`wechat4_extract_keys.py:27` 用 `ctypes.WinDLL("kernel32")` + `ReadProcessMemory`；macOS 没有等价 API（`task_for_pid` 需 entitlement/关闭 SIP），这是**不可移植**项，且不能靠改代码解决。
2. **QQ 依赖 Windows 二进制**：`adapters/qq/pipeline.py:11,36` 找 `qq.exe`，上游 `qqcli` README 明确写"Linux and macOS are not supported installation targets yet"（`qqmd/qqcli_README.md:40`）；旧解码器 `qqmd/fmt/ntqq_export_md.py:32` 又硬依赖 `sqlcipher3` 绑定。
3. **微信 Mac 客户端的数据布局/加密方案未验证**：`~/Library/Containers/com.tencent.xinWeChat/...` 下是否与 Windows `xwechat_files/wxid_*/db_storage` 同构、是否同为 SQLCipher4(page=4096, reserve=80)，**本次无法在 Linux 上证实，报告中一律标"未知"**。

> 关键判断：**Everything 不是必需的，Spotlight/mdfind 也不是必需的。** 代码里 Everything 已经降级成可选叠加路径（`src/aimem/cli.py:291` 的 `--everything` 开关），真正的索引器是跨平台的 `scan_local`（`src/aimem/everything.py:160-189`，`src/aimem/index/indexer.py:159` 调用）。Mac 上"文件名/路径召回"由 FTS5 的 `path_tok` 列兜底（`src/aimem/db.py:36-39`、`src/aimem/db.py:158-161`）。

---

## 1. 方法与验证环境

### 1.1 本地实测（Linux，作为"非 Windows"的对照）

| 检查 | 命令 | 结果 |
|---|---|---|
| 搜索内核单测 | `.venv/bin/python -m pytest -q` | **33 passed** |
| chatbridge 单测（6 个模块） | `python3 -m unittest <mod>` | **28 passed**（2+2+4+2+15+3） |
| Spool 契约校验 | `python3 core/validate_spool.py spool-dev/sample --strict` | **OK**（三源各 1 会话 2 消息） |
| Hub 状态 | `python3 -m hub status --spool spool-dev/sample` | 三源 `ok`，`manifest: yes` |
| qqmail_router 单测 | `python3 tests/test_router.py` | **51 passed** |
| 运行时 | Python 3.13.11 / torch 2.14.1+cpu / sentence-transformers 6.1.0 / numpy 2.5.4 / SQLite 3.51.0（FTS5 OK） | — |

这组结果的意义：**项目从未在代码里写死 Windows 假设**——否则在 Linux 上跑不通。macOS 与 Linux 同属 POSIX，这 85.8% 的"零改动 + 小改"部分有很强的先验支持。

### 1.2 明确不在本次验证范围

- 任何 macOS 真机行为（无 Mac 可用），故凡是涉及 macOS 的回调/权限/模型后端，均写"预期 + 需实测命令"。
- 未打开/未修改 Windows 端的聊天数据库；对 `D:\aimem-spool`、`C:\Users\<user>\aimem-test` 只做了 `ls`/`stat`/`du`。

---

## 2. 兼容性矩阵

分类：**A**=原生可跑（零改动）、**B**=小改（改路径/平台分支，单模块 ≤ 1 文件）、**C**=需重写（核心逻辑换实现，数据契约可复用）、**D**=不可移植（依赖 Windows 二进制/API）。

| # | 模块 | LOC | 分类 | 关键证据（file:line） | macOS 预期 |
|---|---|---:|---|---|---|
| 1 | kernel 核心：`db.py` `models.py` `text.py` `planner.py` `vectors.py` `embedders.py` `rerank.py` `index/indexer.py` `ingest/*` `search/pipeline.py` `eval.py` `paths.py` | 2,506 | **A** | 纯 stdlib + numpy/torch；无 `os.startfile`、无盘符、无 `sys.platform` | 直接跑 |
| 2 | kernel `everything.py` | 189 | **B** | `everything.py:129` 注释"dev fallback on Linux and macOS"；`:160` `scan_local` 用 `os.walk`；`:132-137` 排除表已含 `library`/`windows`/`appdata` | 直接跑；可选加 `mdfind` 后端（~60 行） |
| 3 | kernel `server.py`（Web UI 770 行） | 770 | **B** | `server.py:365-371` 只有 `win32`→`explorer /select,`/`os.startfile`，非 Windows 一律 `xdg-open` | `xdg-open` 在 macOS 不存在；需加 `darwin`→`open` / `open -R`（~6 行） |
| 4 | kernel `cli.py` | 378 | **B** | `cli.py:368-374` `_auto_device()` 只返回 `cuda`/`cpu`；`cli.py:277` help 文案 `cpu\|cuda\|auto` | 需加 `mps`（~4 行） |
| 5 | kernel tests | 519 | **A** | 33 tests 全绿；`tests/test_everything.py:23,30,40` 的 `C:\` 只是字符串夹具 | 直接跑 |
| 6 | chatbridge `core/`（契约+校验器+合并） | 363 | **A** | 纯 stdlib；唯一 Windows 痕迹是默认参数 `merge_manifest.py:39` | 直接跑（默认值改一下更顺手） |
| 7 | chatbridge 根 `tests/` | 89 | **A** | 3 tests 通过 | 直接跑 |
| 8 | chatbridge `hub/`（CLI + Web 8766） | 403 | **B** | `hub/web.py:107-146` 用 `sys.executable` 起子进程；`hub/__main__.py:110-131` 默认 `D:\aimem-spool`；`hub/web.py:53` 硬编码搜索端 8765 | 直接跑，默认路径改成 `~/` 即可 |
| 9 | chatbridge `adapters/email`（+ 467 行测试） | 1,249 | **A** | `export.py:19-27` 只 import `argparse/hashlib/json/os/re/...`；IMAP 在 `mail_core.py:18` `imaplib`；凭据路径 `export.py:489-493` 用 `Path.home()/.config` | 直接跑 |
| 10 | chatbridge `adapters/wechat`：`cipher.py` `normalize.py` + 测试 | 715 | **A** | `cipher.py:1-11` 纯页级 SQLCipher4 实现（只依赖 `cryptography`），注释直言"Windows Python has no sqlcipher3 wheel"；`normalize.py:167-171` 原子写 | **解密算法本身跨平台**，可直接跑 |
| 11 | chatbridge `adapters/wechat/cli.py` | 223 | **B** | 仅 spool 落盘/计数/退出码 | 直接跑 |
| 12 | chatbridge `adapters/wechat/pipeline.py` | 387 | **C** | `pipeline.py:62-73` `find_win_python()`；`:76-90` `xwechat_files` 布局硬编码；`:120-122` 调 Windows 专用取密钥工具；`:204-217` 取图片密钥 | 需替换"密钥来源"与"数据根目录"（数据布局未知） |
| 13 | chatbridge `adapters/qq`：`mdio.py` `normalize.py` `snapshot.py` + 测试 | 379 | **A** | 纯文本解析 | 直接跑 |
| 14 | chatbridge `adapters/qq/decoder.py` | 163 | **B** | `decoder.py:30-40` 只扫 `Documents/Tencent Files`；`:44-49` 要求 `NTQQ_DB_KEY` | 改数据根即用，但被调用的解码器需 sqlcipher（见 #21） |
| 15 | chatbridge `adapters/qq/pipeline.py` + `cli.py` | 547 | **C** | `pipeline.py:11` `DEFAULT_TOOLS=D:\aimem-tools\qqcli`；`:36-38` `glob("**/qq.exe")`；`cli.py:199-205` `/mnt/c` 临时目录假设 | 需换数据源（qqcli 无 macOS 版） |
| 16 | qqmail_router（`mail_core.py` 455 + `router.py` 1385 + 测试 780） | 2,620 | **A** | `mail_core.py:14-24` 全 stdlib（`imaplib`/`email`/`html.parser`）；`router.py:34` `fcntl`（macOS 有）；路径 `router.py:57-82` 用 `Path.home()` | 直接跑（调度从 cron 换 launchd 即封顶） |
| 17 | `launcher` 启动器 | 292 | **C** | `aimem_demo.py` 默认已改为 `Path.home()`；仍含 Windows venv、`D:/C:/` 探测和 `cuda` | 需改配置/探测层（~80 行；工作量 **S**） |
| 18 | qqmd/fmt `wechat_media_index.py` + `validate_format.py` | 178 | **A** | `sqlite3` + `zstandard`，纯 Python | 直接跑 |
| 19 | qqmd/fmt `wechat4_decode_images.py` | 102 | **B** | `:88` `subprocess.run([ffmpeg, ...])` 仅 WXGF 动图需要 | `brew install ffmpeg` 即用 |
| 20 | qqmd/fmt `wechat4_decrypt.py` | 111 | **B** | `:16` `import sqlcipher3.dbapi2` | 用 chatbridge `cipher.py` 替代即可，无需 sqlcipher |
| 21 | qqmd/fmt `wechat4_export_md.py` | 313 | **B** | `:3-4` `sqlite3`+`zstandard`，无平台 API | 取决于 Mac 微信明文库 schema（**未知**） |
| 22 | qqmd/fmt `ntqq_export_md.py` | 362 | **C** | `:32` `import sqlcipher3.dbapi2 as sc`；`:108-116` 自定义 `kdf_iter`/`cipher_hmac_algorithm` | 需 macOS 版 sqlcipher 或自写页级解密（QQ 参数与微信不同） |
| 23 | qqmd/fmt `wechat4_extract_keys.py` | 170 | **D** | `:27` `ctypes.WinDLL("kernel32")`；`:30-32` `ReadProcessMemory`/`VirtualQueryEx`；`:55` `tasklist` | macOS 无等价 API |
| 24 | qqmd/fmt `find_image_key.py` | 110 | **D** | `:14` `ctypes.WinDLL`；`:17-19` 同上；扫描 `Weixin.exe` 内存 | 同上 |

**不计入产品范围**（避免虚增分母）：`AI-Advaned-File-Search/examples`（951 行，18 处 `cuda` + 47 处 Windows 路径的评测探针）、`qqmd` 早期迭代脚本与 `wxauto/`（5,418 行）、`qqmd/wxtool/wechat-decrypt-main`（3,788 行，第三方上游，产品未调用）。全量口径下加权比例会掉到 60.8%，但那不是"要迁移的产品代码"。

---

## 3. 分层结论

### 3.1 可以直接跑（A，8,619 行 / 65.6%）

- **搜索内核主体**：SQLite FTS5 + 向量 memmap + RRF 融合 + planner/eval/ingest。CJK 分词在 Python 侧完成（`src/aimem/text.py:39` `tokenize_cjk`），**不依赖 SQLite 的 tokenizer 能力**，所以 FTS5 只需支持 `unicode61 remove_diacritics 2`（`db.py:52`），这是所有 FTS5 构建的标配。
- **向量层**：`vectors.py:28` `np.load(..., mmap_mode="r")` 在 APFS 上正常；唯一的平台特判 `vectors.py:37-40` 是"Windows 不能覆盖仍被 mmap 的文件"，在 macOS 上是**多余但无害**的死分支。
- **文件索引**：`scan_local`（`everything.py:160`）用 `os.walk(followlinks=False)` + `os.stat`，`~/Library` 因为名字 `library` 已在 `DEFAULT_EXCLUDE_DIRS`（`everything.py:136`）里被自动排除。
- **邮箱整条链路**：`chatbridge/adapters/email`（IMAP→chat-md）与 `qqmail_router`（IMAP→邮件路由）都是纯 stdlib `imaplib`，Mac 上是"同一套 Python 代码 + 换调度器"。
- **契约与数据层**：spool / manifest / chat-md 是纯文件 + JSON（`core/CONTRACT.md:84-89` 原子写、`:186-196` UTF-8 无 BOM + LF）。本次在 Linux 上用 `validate_spool --strict` 与 `hub status` 实测通过 → **数据层与平台无关已被反证**。
- **Hub CLI/Web**：`http.server` + `sys.executable` 子进程，天然跨平台。

### 3.2 需要小改（B，2,652 行 / 20.2%，总计约 150–250 行改动）

| 文件 | 位置 | 改动 | 规模 |
|---|---|---|---|
| kernel `server.py` | `:363-372` | 加 `elif sys.platform == "darwin": subprocess.Popen(["open","-R",...])` / `["open", path]` | ~6 行 |
| kernel `cli.py` | `:368-374` | `_auto_device()` 加 `torch.backends.mps.is_available()` → `"mps"` | ~4 行 |
| kernel `server.py:218` + `cli.py:289,327` | — | 精排 dtype：`cuda`/`mps` 用 `float16`，**CPU 必须 `float32`**（否则 `CrossEncoder` 在 CPU 上会踩 half 运算不支持） | ~6 行 |
| kernel `everything.py` | `:53-125` 之后 | 可选：新增 `scan_spotlight()` 用 `mdfind -onlyin`（仅"实时叠加"，不进索引） | ~60 行（可选） |
| kernel `everything.py` | `:169` | `onerror=lambda e: None` 会**吞掉 macOS TCC 的 `EPERM`**，静默少索引；应收集并提示"需要完全磁盘访问权限" | ~10 行（重要） |
| kernel `cli.py`/`paths.py` | — | `--spool` / `--db` 默认值改用 `AIMEM_SPOOL` 环境变量，避免写死 `D:\` | ~5 行 |
| chatbridge `hub/*`, `core/merge_manifest.py` | `hub/__main__.py:110-131` 等 6 处 | 默认 spool 改环境变量 | ~6 行 |
| chatbridge `adapters/wechat/cli.py`、`adapters/qq/decoder.py` | `decoder.py:30-40` | 数据根目录改为"按平台探测 + 允许环境变量覆盖" | ~20 行 |
| qqmd `wechat4_decode_images.py` | `:56` | `--ffmpeg` 默认值在 macOS 上可由 `shutil.which` 解析 | ~3 行 |

> B 类里**没有一处是架构性改动**，全部是"分支 + 默认值"。

### 3.3 必须换实现（C，1,588 行 / 12.1%）

- `adapters/wechat/pipeline.py`（387 行）：它本身不是解码器，而是**"Windows 专用取密钥工具"的编排器**。Mac 上要重写的是"密钥从哪来" + "微信数据根在哪"，而不是解析逻辑。
- `adapters/qq/pipeline.py` + `cli.py`（547 行）：围绕 `qq.exe` 的编排（含 `/mnt/c` 临时目录、Windows 路径转换），Mac 上整体换数据源。
- `qqmd/fmt/ntqq_export_md.py`（362 行）：需要 sqlcipher 等价物，且 QQ NT 的 cipher 参数是定制的（`:108-116`）。
- `launcher/aimem_demo.py`（292 行）：启动器，Windows 路径探测占了大半；Mac 版可去掉 `D:/C:/` 探测。

### 3.4 不可移植（D，280 行 / 2.1%）

- `qqmd/fmt/wechat4_extract_keys.py`（170 行）与 `find_image_key.py`（110 行）：`ReadProcessMemory` 扫描 `Weixin.exe` 进程内存。macOS 上**没有任何用户态等价物**（`task_for_pid` 需要签名 entitlement，普通用户 + SIP 开启时不可用）。这不是"改写"，是**换一条获取密钥的路径**。

---

## 4. 逐主题分析

### 4.1 搜索内核

**Python 依赖**（`pyproject.toml`）：基础仅 `numpy>=1.24`、`requests>=2.28`（后者只服务 Everything HTTP 客户端，`everything.py:7`）；可选 `torch`/`sentence-transformers`（`embed`）、`pypdf`/`python-docx`/`openpyxl`（`docs`）、`pypinyin`（`pinyin`）。**全部在 macOS (arm64/x86_64) 有官方 wheel**，无需编译器。

**SQLite FTS5**：`db.py:49-53` 建 `units_fts`（external content + 三个触发器），查询用 `bm25()`（`db.py:284-299`）。风险与对策：

- macOS 系统自带 Python 的 `sqlite3` 是否启用 FTS5 **本次无法验证**；python.org / Homebrew / pyenv 的 Python 一律带 FTS5。
- 因为 CJK 分词在 Python 侧（`text.py:39`），Mac 上**不需要 trigram tokenizer**，也就避开了不同 SQLite 构建的能力差异。
- 一个真机检查命令见 §9.2。

**向量 memmap**：`vectors.py:9-14` 明确"brute-force float32 + memmapped .npy"。实测现有 `demo.vectors/vectors.npy` = 276,021,376 B / 134,776 条 = **512 维**（`vectors.npy` 行数 × dim × 4）。这是"整表 `@` 一次"的实现，Mac 上 134k×512 一次点积是毫秒级；上到 1M×512（2 GB）在 16 GB Mac 上也放得下。**APFS 对 mmap 无特殊限制**。

**BGE / reranker 在 CPU/MPS**：

- `BAAI/bge-small-zh-v1.5`（24M 参数/512 维）：CPU 单条 query 编码是个位数毫秒，MPS 更快；`embedders.py:60-113` 只是把 `device` 透传给 `SentenceTransformer`，**MPS 可以直接用 `device="mps"`**。
- `BAAI/bge-reranker-v2-m3`（568M，2.2 GB 磁盘，见 `/mnt/c/Users/<user>/aimem-test/bge-reranker-v2-m3`）：MPS 上 fp16 推理可用，但**首屏延迟会显著高于 5060 笔记本**；备选是降级为 `--no-rerank`（`cli.py` 有该开关）或换 0.3B 级小精排。
- **硬编码 `float16` 是最可能的第一个坑**：`server.py:218` `make_reranker(rerank_spec, device, "float16")`、`cli.py:289,327` `--rerank-dtype` 默认 `float16`。CPU 上 fp16 的若干 kernel 未实现 → 必须加 `dtype = "float16" if device in ("cuda","mps") else "float32"`。

**Windows 专用 API 清单（内核逻辑点实测只有 4 处 + 1 处注释）**：

| 位置 | 内容 | macOS 处理 |
|---|---|---|
| `server.py:367` | `subprocess.Popen(["explorer", "/select,", ...])` | `open -R <path>` |
| `server.py:369` | `os.startfile(str(target))` | `subprocess.Popen(["open", target])` |
| `server.py:371` | `subprocess.Popen(["xdg-open", ...])` | `xdg-open` 在 macOS 默认不存在 → 换成 `open` |
| `cli.py:372` | `torch.cuda.is_available()` | 加 `torch.backends.mps.is_available()` |
| `everything.py:14` | 注释里的 `C:\Users\me\` | 仅注释；但 `everything.py:45-50` 的 `_join` 与 `:118` 的 `norm_sep(...).replace("/", "\\")` 只在 `"\\" in folder` 时触发，Mac 上不会命中，无害 |

**盘符/反斜杠**：内核里**没有**任何盘符路径进入逻辑（只有注释 `everything.py:14` 与提示文案）。`paths.py` 的 `norm_sep`/`basename_any` 同时吃 `/` 和 `\`，天然兼容。

### 4.2 文件索引与 macOS 权限

- `scan_local` 是跨平台的：`os.walk` + `os.stat`，`followlinks=False`，`max_files` 保护（`everything.py:160-189`）。
- **Everything 非必需**（这是本报告最想强调的一点）：设计文档里 Everything 是"三路召回"之一（`AI-Advaned-File-Search/README.md:17`、`docs/技术路线分析.md:97`），但实现上它只是**可选叠加**——`cli.py:291` 的 `--everything` 开关、`cli.py:93` 只在开关打开时构造 client、`server.py` 里**完全没有** `EverythingClient`（本次 grep 确认）。真正的入库路径是 `index/indexer.py:159` → `scan_local`。所以："Everything 是否必需？→ 否。"
- 若想要"实时文件名召回"：Mac 的可选替代是 `mdfind -onlyin <root> '<query>'` 与 `mdls`（Spotlight 元数据），只需实现一个和 `EverythingClient` 同接口的 `SpotlightClient`（`probe()`/`search()`，`everything.py:66-125` 就是接口样例）。**注意**：`mdfind` 只返回被 Spotlight 索引的位置，`--onlyin ~/Library` 这类路径通常无结果，所以它只能做"叠加召回"，不能替代 `scan_local`。
- **macOS 权限（真实风险）**：`~/Documents`、`~/Desktop`、`~/Downloads` 受 TCC 保护；脚本被拒时 `os.walk` 抛 `PermissionError`，而 `everything.py:169` 的 `onerror=lambda e: None` **把错误吞掉** → 用户只会看到"只索引到很少文件"，无任何提示。对策：收集 `onerror` 的 errno==EPERM 并在 UI/CLI 提示"到 系统设置 → 隐私与安全性 → 完全磁盘访问权限 里勾选终端/Python"，或直接建议用户授予 Fully Disk Access。
- **APFS 相关**：APFS 默认大小写不敏感，`DEFAULT_EXCLUDE_DIRS` 用 `name.lower()` 比较（`everything.py:151-154`）→ 行为一致。APFS 文件名返回 **NFD 规范化**（分解式 Unicode）；对 CJK 无影响（CJK 无分解形式），但同一个带声调拉丁字母的文件名在 Windows 与 macOS 上 `stable_id` 会不同（`models.py` 用 path 哈希）。**影响面**：跨平台复用同一个 DB 时，文件类 unit 的 id 会变；聊天类 unit 的 id 基于 `(platform, account, conversation_id, source_id)`（`ingest/jsonl.py:180-181`），不受影响。
- **`.DS_Store` / 隐藏文件**：`everything.py:174` 跳过 `.` 开头文件，天然屏蔽。
- **Spotlight 会不会把索引搞脏**：不会，`mdfind` 只是读。

### 4.3 适配器 · 微信

现状拆解（这是全项目最需要分层看的地方）：

| 步骤 | 实现 | 平台性 |
|---|---|---|
| 找账号目录 | `wechat_root()`→`%USERPROFILE%\Documents\xwechat_files`，WSL 下探 `/mnt/c/Users`（`pipeline.py:76-90`） | Windows/WSL 专用路径逻辑，Mac 需换根 |
| **取 DB 密钥** | `wechat4_extract_keys.py` 扫 `Weixin.exe` 内存 + 页 1 HMAC 校验（`pipeline.py:120-140`、`cipher.py:25-33`） | **Windows 专用（D）** |
| 解密 DB | 优先 `sqlcipher3`，否则自写页级解密（`pipeline.py:161-196`、`cipher.py:74-89`） | **跨平台（A）**：`cryptography`+`hashlib/hmac` 即可 |
| WAL 合并 | `cipher.py:49-71` 手动解 WAL 帧 | 跨平台 |
| 图片索引 | `wechat_media_index.py`（sqlite3+zstandard） | 跨平台 |
| **图片密钥** | `find_image_key.py` 扫进程内存 | **Windows 专用（D）** |
| `.dat`/WXGF 解码 | `wechat4_decode_images.py` 纯 Python AES+XOR，动图调 ffmpeg | 跨平台（B，含 `brew install ffmpeg`） |
| 导出 Markdown | `wechat4_export_md.py`（sqlite3+zstandard） | 跨平台（B） |

要点：

1. **加密算法本身完全可移植**：`cipher.py` 是纯 Python 的 SQLCipher4 页级解密（salt 16 / reserve 80 / AES-CBC + HMAC-SHA512 / PBKDF2 2 轮 MAC key），注释 `cipher.py:3` 明确写"Windows Python has no sqlcipher3 wheel, so the adapter decrypts pages itself"——**这个"为了 Windows 而写的降级路径"恰好让 macOS 免装 sqlcipher**。
2. **不可移植的只有"密钥获取"**：进程内存读取在 macOS 上没有用户态方案。可行路径（**均未验证，需评估合规与可行性**）：
   - (a) 用户手工提供 key（`keys.json` 已有格式：`{relpath: hex}`，`pipeline.py:128-130` 读取）——适配成本最低；
   - (b) 通过 `lldb`/`vmmap` attach（需关闭 SIP 或带 `get-task-allow` 的签名，普通用户体验很差）；
   - (c) 在 Windows 机器上取 key + 解密后，把明文库/Markdown 同步到 Mac（推荐，见 §5 方案 B）。
3. **微信 Mac 客户端的数据格式是未知项**：`~/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat/<version>/<wxid>/db_storage` 这一布局、以及是否同为 `message_*.db` 分片 + `msg/attach/*.dat`，**本项目没有任何实测证据**（本地 `qqmd/` 全部围绕 Windows `xwechat_files`）。`docs/` 与 `CONTRACT.md:265` 也只描述 Windows 布局。→ 任何"Mac 版微信解码"都应是独立立项 + 实测探索，不能写进 P0/P1。
4. 顺带发现一个**现存缺陷**（在 Windows 上也会犯）：`pipeline.py:210` 调用 `wechat4_find_image_key.py`，但 `~/qqmd/fmt/` 里只有 `find_image_key.py`，**文件名不匹配**（本次 `ls` 确认），而 `find_image_key()` 会 `except SetupError: continue`（`pipeline.py:212-213`）→ 图片密钥永远取不到，图片只能落占位。
   **已被现场数据证实**：`/mnt/d/aimem-spool/.manifest.wechat.json` 的账号条目里就写着 `"未取得图片密钥：图片保留占位（请在微信中打开 2-3 张图片后重跑）"`，`media: 0`。迁移前应顺手修复这一行，否则会把"Mac 上图片不可解码"误判为新平台问题。
5. **合规风险（新）**：主流第三方微信解码项目 `sjzar/chatlog` 已于 2025-10-20 因微信官方函件下架全部代码（[仓库 README](https://github.com/sjzar/chatlog)）。这说明"在 Mac 上另做微信解码器"除了技术风险，还有**明确的合规/法律风险**，进一步支持"不在 Mac 上重新实现解码"的结论。

### 4.4 适配器 · QQ

- `qqcli` 是 Rust 实现（`qqcli_README.md:168` "Tech stack: Rust · rusqlite · DuckDB · SQLCipher"），但**官方发布只面向 Windows**，README/FAQ 两处明说 macOS 不受支持（`qqcli_README.md:40`、`:155-156`）。理论上"自己交叉编译 Rust 工程"可行，但依赖 `duckdb.dll` 与 Windows 专用解密工作流（`qqcli_README.md:150` "configure the required local decryption tool and SQLCipher"），**构建可行性未验证**，且合同规定不使用其搜索/DuckDB 部分（`CONTRACT.md:22`）。
- 旧解码器 `~/qqmd/fmt/ntqq_export_md.py` 本身**几乎没有平台 API**（只 import `argparse/datetime/html/json/os/pathlib/re/shutil/sqlite3/sys/unicodedata`），唯一硬依赖是 `sqlcipher3`（`:32`）。Mac 上的三条路：
  1. 装 `sqlcipher3-binary` 或 `pysqlcipher3`（需 brew `sqlcipher` + 编译，**arm64 上是否顺滑未验证**）；
  2. 照 `wechat/adapters/cipher.py` 的思路**自写 QQ 的页级解密**——但 QQ NT 用自定义 `kdf_iter`/`cipher_hmac_algorithm`/`cipher_kdf_algorithm`（`ntqq_export_md.py:108-116`），工作量 **M**；
  3. **换实现**：`WeQ` 项目公开支持 macOS（`qqmd/weq.md:53-55`：Windows x64 / macOS arm+intel / Linux x64+arm64，含 Web 版 `./start.sh`），可作为"Mac 侧原生导出"的候选，把产物对齐 `chat-md/1`；但它是 Electron 应用、导出格式需二次转换，**未验证**，且同属第三方解密工具，合规风险同上。
- QQ 在 Mac 上的数据目录猜测值（**未验证**）：`~/Library/Containers/com.tencent.qq/Data/Library/Application Support/QQ/nt_qq_<uin>/nt_db/nt_msg.db`。
- 结论：QQ 的**原生解码在 Mac 上属于"可能可行但需立项"**；P0/P1 阶段应直接走"Windows 解码 → 同步"。

### 4.5 适配器 · 邮箱

`chatbridge/adapters/email` 与 `qqmail_router` 都是纯 stdlib（`imaplib` + `email` + `html.parser`）。本次在 Linux 上 15+51 个测试全绿，路径全部走 `Path.home()`（`export.py:489-493`、`router.py:57-66`），**在 macOS 上是零改动**。唯一与平台相关的是调度方式：

- `qqmail_router` 用 `fcntl.flock` 做单实例锁（`router.py:1031-1036`）——macOS 有 `fcntl`，可用。
- 定时触发：Windows 用任务计划；macOS 用 `launchd`（`~/Library/LaunchAgents/*.plist`）或 `cron`。属于"部署方式"，不是代码改动。
- QQ 邮箱 IMAP 走 `imap.qq.com:993` + 授权码，与操作系统无关。

### 4.6 Hub / launcher / Web 控制台

- `hub` 的 `export`/`merge`/`validate`/`web` 全部用 `sys.executable -m adapters.x`（`hub/web.py:107-119`、`hub/__main__.py:59-97`），**没有 shell 拼接、没有盘符传参**，macOS 上原样工作；端口 8766（Hub）/ 8765（搜索）都不需要特权。
- `hub/web.py:53` 把搜索端地址硬编码成 `http://127.0.0.1:8765/`，Mac 上若改端口需一起改（一行）。
- `launcher` 是唯一的"启动体验"问题：它默认 Windows venv 的 `python.exe`、`D:/C:/` 探测、`cuda`。Mac 上建议：
  - **不要**用 PyInstaller 打包（模型 + torch 会让 .app 膨胀到 GB 级，且 Apple Silicon 要求签名）；
  - 改成"shell 启动脚本 + 可选 `.app` 包装（`automator`/`osacompile` 或 `py2app` 轻量壳）"，脚本里用 `open http://127.0.0.1:8765` 打开浏览器。
  - 现有 `aimem_demo.py` 的后半段（端口探测 `:154-181`、探活 `:175-181`、`webbrowser.open` `:279`、子进程管理 `:271-288`）**可以复用**，只需替换 `load()` 的默认值与 `discover_export()`。

### 4.7 契约与数据层

结论：**平台无关，且已被实测反证**。

- 目录规则：`<spool>/{wechat,qq,email}/<account>/...`、`_state/`（`CONTRACT.md:59-82`）。
- 写入原子性：`.tmp` + `os.replace`（`CONTRACT.md:88`、`merge_manifest.py:31-33`、`wechat/normalize.py:167-171`）——POSIX 与 APFS 上同样是原子的。
- 编码：UTF-8 无 BOM、LF（`CONTRACT.md:186`）；Python 文本模式在 POSIX 上不做换行转换，天然 LF。
- `validate_spool --strict` 在 Linux 上通过（§1.1），说明格式约束不依赖 Windows。
- 跨平台唯一需要注意的：**Windows 时代的 spool 里会有 `D:\` 绝对路径**吗？实测 `/mnt/d/aimem-spool` 的 markdown 用的是相对链接（`![图片](<../media/x.jpg>)`），attachment 是"仅文件名"策略（`CONTRACT.md:192`）→ **spool 可以整体搬到 Mac，无需改写**。
- 搜索 DB 则不同：`fs` 类 unit 的 `path` 是 `D:\...` 绝对路径，搬到 Mac 后这些 unit 的"打开文件"链接会失效，需要**重新跑文件索引**；聊天/邮件类 unit 可继续用（`scope` 是 `account:conversation_id`，与平台无关）。建议 Mac 上重建文件索引（111k 文件级别，实测索引耗时见 `docs/实测报告.md`）。

### 4.8 测试与打包

- **现有测试没有任何 macOS 阻塞**：全部 `unittest`/`pytest`，不碰 GUI、不碰真机、不碰网络（email 测试用 `FakeIMAP`，`adapters/email/tests/test_export.py:90`）。本次 33 + 28 + 51 个测试在 Linux 全绿，macOS 上属于"同一份 POSIX 语义"，预期一致。
- 唯一的**工程性缺口**：`chatbridge` 没有 `requirements.txt`/`pyproject.toml`，隐式依赖 `cryptography`（`wechat/cipher.py:19`）与 `zstandard`（仅 qqmd 工具）。当前环境 `pytest` 缺失导致 chatbridge 只能 `unittest` 跑；迁移前应补一个 `requirements.txt`（`cryptography`、`pytest`），否则 Mac 上装环境靠猜。
- **打包建议**：
  - 首选 **venv + 启动脚本**（不做 .app）。理由：模型缓存（HF 默认 `~/.cache/huggingface`）、向量文件、DB 都是外部数据，本来就要落在 `~/Library/Application Support/aimem/` 或用户目录；PyInstaller 只会增加签名负担。
  - 若一定要 `.app`：PyInstaller 可产出 `.app`，但 Apple Silicon 上未签名二进制**无法启动**，需 `codesign --force --deep -s -`（自签）；分发到别人机器还需 Developer ID + 公证（`notarytool`），属于**可选、非必须**（自用可跳过）。
  - 模型缓存：`HF_HOME`/`HF_ENDPOINT` 在 Mac 上照旧可用（`aimem_demo.py:240` 已用镜像）；预先下载 `BAAI/bge-small-zh-v1.5`（~100 MB）与 `BAAI/bge-reranker-v2-m3`（2.2 GB，可选）到本地目录，用 `ce:<本地路径>` 可完全离线（`rerank.py:57-58` 支持 `ce:<model>` 传路径）。
  - `sqlcipher3`：**Mac 上不需要**（微信路线走 `cipher.py` 纯 Python；QQ 路线走方案 B）。这消除了一大片打包/编译风险。

---

## 5. 推荐 macOS 架构

### 方案 A：Mac 原生解码（不推荐作为 P0/P1）

Mac 上重写微信 key 获取 + macOS 微信数据格式逆向 + QQ sqlcipher。技术不确定（数据格式未知）、合规风险高（chatlog 前车之鉴）、收益有限（聊天数据是"只增不改"的静态资产）。

### 方案 B：Windows 解码 → spool 同步（推荐）

```
┌──────────────────── Windows（保留现状，不动） ────────────────────┐
│ adapters/{wechat,qq,email}  →  D:\aimem-spool\                    │
│   （微信取密钥、.dat 解码、qqcli、ntqq 解码全部留在 Windows）      │
└───────────────────────────────────────────────────────────────────┘
                    │  单向同步（Syncthing / rsync / 坚果云 / 手动）
                    ▼  体积实测 2.2 MB，104 个 md
┌──────────────────── macOS ────────────────────────────────────────┐
│  aimem（搜索内核）+ hub web（8766）                                │
│  ① 文件索引：scan_local 扫 ~/Documents 等（首次耗时需实测估算）     │
│  ② 聊天索引：watch ~/aimem-spool（--watch，5 秒轮询/8 秒稳定）      │
│  ③ 邮箱：adapters/email 直接在 Mac 上跑 IMAP（A 类，零改动）       │
│  ④ 精排：bge-reranker-v2-m3 本地目录（可选，MPS/CPU）              │
└───────────────────────────────────────────────────────────────────┘
```

**为什么这条路线几乎零成本**：

1. 项目**本来就是为这件事设计的**：`CONTRACT.md` 的全部意义就是"适配器产出文件、搜索端消费文件"，两个平面可以是两台机器。
2. `aimem serve --watch <dir>` 已经支持"监听导出目录并增量导入"（`server.py:750-766`、`server.py:572-600` 目录指纹轮询 + 8 秒稳定期），Mac 上监听一个同步目录，行为与 Windows 完全一致。
3. 已有先例：QQ 适配器里的 `snapshot.py` 就是"读一个旧 Windows 导出目录"的兜底源（`snapshot.py:1-14`），证明"消费异地产物"是既有模式。
4. 数据量极小（2.2 MB / 23,395 条消息），同步、备份、迁移都无压力。

**代价**：聊天记录的时效性 = 同步周期（分钟级），不是秒级；这是唯一的取舍。若要求"Mac 上实时看微信新消息"，才需要方案 A 或方案 C。

### 方案 C：混合（P2 再议）

P0/P1 用方案 B 跑通；若之后确实需要 Mac 原生微信，则**单独立项**（独立仓库/分支），先做"macOS 微信数据布局 + 加密方案"的实测探索，并先确认合规边界。

---

## 6. 分阶段迁移计划

工作量口径：**S** ≤ 1 天、**M** 2–5 天、**L** ≥ 2 周。

### P0 — 让"搜索 + 文件 + 邮箱"在 Mac 上跑起来（S/M，3–6 天）

| # | 任务 | 文件 | 工作量 | 验收 |
|---|---|---|---|---|
| P0-1 | 建 venv（Python 3.12/3.13），装 `numpy requests cryptography pytest` + `torch sentence-transformers` | 新增 `requirements.txt` | S | `python -c "import torch;print(torch.backends.mps.is_available())"` |
| P0-2 | 补 `darwin` 分支：打开/定位文件 | `src/aimem/server.py:363-372` | S | Web UI 点"打开/所在文件夹"生效 |
| P0-3 | `_auto_device()` 支持 `mps`；rerank dtype 按 device 选 | `src/aimem/cli.py:368-374`、`server.py:218`、`cli.py:289,327` | S | `aimem search ... --device auto` 日志显示 mps |
| P0-4 | `scan_local` 的 `onerror` 不再静默，TCC/EPERM 明确提示 | `src/aimem/everything.py:169` | S | 未授权时输出"需要完全磁盘访问权限" |
| P0-5 | 授予终端/应用**完全磁盘访问权限**，索引 `~/Documents` | 系统设置 | S（人工） | `aimem stats` 文件数与 Finder 一致 |
| P0-6 | 校验 FTS5 与 `unicode61 remove_diacritics 2` | — | S | §9.2 的 sqlite 检查命令通过 |
| P0-7 | 邮箱：`adapters/email` 原样跑（`--spool ~/aimem-spool`） | 无需改 | S | 15 个测试 + 一次真实 IMAP dry-run |
| P0-8 | 契约校验 + Hub 起服务 | `core/validate_spool.py`、`hub web` | S | `--strict` OK；8766 可访问 |

**P0 结束态**：Mac 上可以按文件名/正文/语义搜索本地文件，可以拉取邮箱，数据契约校验通过。**此时已经能覆盖原始需求的大部分日常价值**。

### P1 — 接通聊天数据（方案 B）与体验闭环（S，2–4 天）

| # | 任务 | 工作量 | 验收 |
|---|---|---|---|
| P1-1 | 把 `D:\aimem-spool`（2.2 MB）同步到 Mac（`~/aimem-spool`，单向、Mac 只读） | S | 104 个 md 到位，`validate_spool --strict` OK |
| P1-2 | `aimem serve --watch ~/aimem-spool` 起监听；spool 模式按 manifest 自动遍历 wechat/qq/email，不需要按平台拆实例 | S/M | 新同步的会话自动出现在结果里，UI 显示"监听中" |
| P1-3 | 验证 spool 模式的 manifest 多源 watch（`server.py` 的 `sync_spool`）；`--watch-platform` 只影响旧单目录兼容路径 | S | 微信+QQ+邮箱三源同时可搜 |
| P1-4 | Hub 默认 spool 改环境变量（`AIMEM_SPOOL`），去掉 `D:\` 默认值 | S | `hub status` 无参数可直接用 |
| P1-5 | 精排模型预下载到本地目录，配 `ce:<path>`；测首屏延迟，决定是否默认关闭 | S/M | 首屏 p50 < 1.5 s（MPS）或 < 3 s（CPU） |
| P1-6 | macOS 启动脚本（替代 `launcher`）：venv python + `open http://127.0.0.1:8765` | S | 双击/一行命令可用 |
| P1-7 | Windows → Mac 同步任务（Syncthing 或 launchd + rsync over ssh） | S/M | 无人值守、失败可见 |

**P1 结束态**：搜索 + 文件 + 邮箱 + 微信/QQ 历史全部可用，聊天数据走 Windows 解码 + 同步。**这就是"85% 可移植"落地成产品的最小闭环。**

### P2 — 可选增强与"Mac 原生聊天解码"立项评估（M/L，需单独排期）

| # | 任务 | 工作量 | 风险 |
|---|---|---|---|
| P2-1 | Spotlight/mdfind 叠加召回（`SpotlightClient`，与 `EverythingClient` 同接口） | M | 中：覆盖面受索引范围限制 |
| P2-2 | macOS 微信数据布局 + 加密方案**可行性探索**（只读、只验证，不产出代码） | M | **高**：格式未知、TCC/权限、合规 |
| P2-3 | QQ：评估自编译 `qqcli-rs` 或集成 `WeQ`（macOS 版）产出，再转 `chat-md/1` | M/L | 高：第三方工具合规 + 格式漂移 |
| P2-4 | `.app` 包装 + 自签（`codesign -s -`）；如需分发再做 Developer ID + 公证 | M | 低（自用可跳过） |
| P2-5 | FSEvents（`watchdog`）替代 5 秒轮询，降低同步延迟 | S/M | 低 |
| P2-6 | 索引器性能：APFS + Apple Silicon 下重测 11 万文件索引耗时、FTS5/向量参数 | S | 低 |

---

## 7. 风险登记

| # | 风险 | 影响 | 概率 | 缓解 |
|---|---|---|---|---|
| R1 | 微信密钥获取在 macOS 无用户态方案 | 微信记录无法在 Mac 原生解码 | **高（确定）** | 方案 B（Windows 解码 + 同步）；或用户手工提供 keys.json |
| R2 | macOS 微信数据格式与 Windows 不同 | 即使拿到 key，解析器也可能不适用 | 未知（偏高） | P2-2 先做只读探索，未证实前不排期 |
| R3 | 第三方微信/QQ 解密工具的合规风险（chatlog 已被官方函件下架） | 项目可能被迫整体回退 | 中 | 只在 Windows 侧本地使用既有工具，产品不内置、不分发 |
| R4 | macOS TCC 权限导致文件索引"静默残缺" | 用户误以为索引坏了 | 中 | P0-4 显式报错 + 文档要求授予完全磁盘访问权限 |
| R5 | CPU 上 `float16` 精排报错/极慢 | 搜索首屏失败或超时 | 中（代码已埋雷） | P0-3 dtype 按 device 选择 |
| R6 | macOS 系统 Python 的 sqlite3 无 FTS5 | 完全不可用 | 低 | 用 Homebrew/python.org Python；§9.2 提供一行验证 |
| R7 | 模型下载/缓存（2.2 GB 精排） | 首次启动慢、离线不可用 | 中 | 预下载 + `ce:<本地路径>`；精排可关闭 |
| R8 | `demo.db`/`cdisk.db` 里 `fs` unit 的 `D:\` 绝对路径 | "打开文件"链接失效、重复索引 | 高（必然） | 迁移时**只搬 spool，不搬文件索引**；在 Mac 上重建文件索引 |
| R9 | APFS NFD 文件名 → 文件类 unit id 与 Windows 不一致 | 无法直接复用旧 DB 的文件 unit | 中 | 同上：Mac 重建文件索引 |
| R10 | `chatbridge` 无依赖清单 | Mac 环境装不起来/装错版本 | 高（必然） | P0-1 顺便补 `requirements.txt` |

---

## 8. 明确"未验证 / 未知"的地方（不做任何编造）

1. **macOS 微信客户端的数据目录与加密方案**：未验证。本报告只有 Windows 侧的实测证据（`/mnt/c/Users/<user>/Documents/xwechat_files/<wxid>` 存在，含 `db_storage`）；Mac 侧结构未知。
2. **macOS 微信 `.dat`/WXGF 图片格式**是否与 Windows 一致：未知。
3. **macOS QQ NT 的 `nt_msg.db` 位置、SQLCipher 参数、`NTQQ_DB_KEY` 获取方式**：未知。`WeQ` 宣称支持 macOS（`qqmd/weq.md:53-55`），但未实测。
4. **`qqcli-rs` 能否在 macOS 交叉编译并工作**：未知（上游明说不支持 macOS）。
5. **macOS 上 `torch.backends.mps` 对 `bge-reranker-v2-m3` 的完整算子覆盖与实测延迟**：未知（预期可用，可能个别算子回退 CPU）。
6. **Apple 自带 Python / Homebrew Python 的 sqlite3 是否启用 FTS5**：未在真机验证（Apple Command Line Tools 自带 Python 与 Homebrew Python 的 SQLite 构建可能不同）。
7. **目标 Mac 的硬件配置**（Apple Silicon vs Intel、内存、是否有独立显卡）**未知** → 本报告对"CPU 还是 MPS"只给条件性建议。
8. **模型 cache 路径在 macOS 的实际默认值**：预期 `~/.cache/huggingface`（与 Linux 一致，HF 库行为），未实测。
9. **`sqlcipher3-binary`/`pysqlcipher3` 在 macOS arm64 的可安装性**：未验证（本报告已尽量绕开该依赖）。
10. **`open -R` 在沙盒/无 Finder 权限场景的行为**：未验证（默认预期正常）。

---

## 9. 可复现的检查命令

### 9.1 Linux/WSL 侧（本次已执行，结果见 §1）

```bash
# 搜索内核测试（不需要 torch 的重活，hash embedder 即可）
cd ~/AI-Advaned-File-Search && .venv/bin/python -m pytest -q

# chatbridge 全部测试（6 个模块，注意 email 目录无 __init__.py，discover 会漏）
cd ~/chatbridge
for m in adapters.wechat.tests.test_export adapters.wechat.tests.test_images \
         adapters.wechat.tests.test_normalize adapters.qq.tests.test_normalize \
         adapters.email.tests.test_export tests.test_validate_spool; do
  python3 -m unittest $m || echo "FAIL $m"
done

# 契约 + Hub
python3 core/validate_spool.py spool-dev/sample --strict
python3 -m hub status --spool spool-dev/sample

# 邮箱路由器测试
cd ~/qqmail_router && python3 tests/test_router.py

# 平台关键词扫描（本报告 §2 的分母来源）
# （把 §10 附录 A 的扫描脚本存成 /tmp/wincount.py 再执行）
python3 /tmp/wincount.py chatbridge AI-Advaned-File-Search/src AI-Advaned-File-Search/tests \
        launcher qqmail_router qqmd/fmt
```

### 9.2 macOS 上必须补跑（迁移前，"未验证"项的验证清单）

```bash
# ① Python / FTS5 / 编码能力（R6）
python3 - <<'PY'
import sqlite3, sys
c = sqlite3.connect(":memory:")
c.execute("CREATE VIRTUAL TABLE t USING fts5(a, tokenize='unicode61 remove_diacritics 2')")
c.execute("INSERT INTO t VALUES ('宿舍停电了')")
print("sqlite", sqlite3.sqlite_version, "FTS5+unicode61 OK", sys.version)
PY

# ② 设备与 MPS（P0-3）
python3 -c "import torch;print(torch.__version__, 'mps', torch.backends.mps.is_available())"

# ③ 精排模型在 CPU/MPS 上能否跑（R5/R7）
python3 - <<'PY'
from sentence_transformers import CrossEncoder
import torch, time
dev = "mps" if torch.backends.mps.is_available() else "cpu"
dtype = torch.float16 if dev == "mps" else torch.float32
m = CrossEncoder("BAAI/bge-reranker-v2-m3", device=dev, model_kwargs={"torch_dtype": dtype})
t = time.time(); print(m.predict([("课表", "第8周课表")], show_progress_bar=False), "sec", round(time.time()-t, 2))
PY

# ④ 文件索引 + 权限（R4）
mkdir -p /tmp/aimem-probe && cd /tmp/aimem-probe
python3 -m aimem --db probe.db index ~/Documents --embed hash --no-chunks 2>&1 | tail -5
#   -> 若文件数远小于 `find ~/Documents -type f | wc -l`，去授予"完全磁盘访问权限"

# ⑤ 打开/定位（P0-2）
python3 -m aimem --db probe.db serve --port 8765 &   # 点 UI 的"打开/所在文件夹"

# ⑥ 契约层（数据层与平台无关的再确认）
rsync -av --dry-run /mnt/d/aimem-spool/ ~/aimem-spool/   # 先看清单，2.2 MB
cp -r /mnt/d/aimem-spool ~/aimem-spool && cd ~/chatbridge && python3 core/validate_spool.py ~/aimem-spool --strict

# ⑦ 微信/QQ 现状探测（只读，确认 Mac 侧到底有什么）
ls -la ~/Library/Containers/com.tencent.xinWeChat/Data 2>/dev/null
ls -la ~/Library/Containers/com.tencent.qq/Data 2>/dev/null
#   -> 具体子路径与格式属"未知"，需人工探索（P2-2），不要据此改代码

# ⑧ 打包/签名（P2-4，可选）
codesign --force --deep -s - YourApp.app && spctl --assess --type execute YourApp.app
```

---

## 10. 附录 A：量化口径与扫描脚本

**分档表**（LOC 与分类的完整对应，`wc -l` 实测）：

```text
A  2,506  kernel: db/models/text/planner/vectors/embedders/rerank/indexer/ingest/search
B    189  kernel: everything.py
B    770  kernel: server.py
B    378  kernel: cli.py
A    519  kernel tests
A    363  chatbridge core/（契约+校验器+合并）
A     89  chatbridge 根 tests/
B    403  chatbridge hub/
A  1,249  chatbridge adapters/email
A    715  chatbridge adapters/wechat — cipher/normalize/tests
B    223  chatbridge adapters/wechat — cli.py
A    379  chatbridge adapters/qq — mdio/normalize/snapshot/tests
B    163  chatbridge adapters/qq — decoder.py
C    387  chatbridge adapters/wechat — pipeline.py
C    547  chatbridge adapters/qq — pipeline.py+cli.py
A  2,620  qqmail_router
C    292  launcher
A    178  qqmd/fmt wechat_media_index + validate_format
B    102  qqmd/fmt wechat4_decode_images
B    111  qqmd/fmt wechat4_decrypt
B    313  qqmd/fmt wechat4_export_md
C    362  qqmd/fmt ntqq_export_md
D    170  qqmd/fmt wechat4_extract_keys
D    110  qqmd/fmt find_image_key
（另有 1 行包 __init__ 计入 A）
---
A 8,619 / B 2,652 / C 1,588 / D 280；合计 13,139
加权 = (8619*1.0 + 2652*0.9 + 1588*0.3 + 280*0.05) / 13139 = 11,496.2 / 13,139 = 87.5%
A+B = 11,271 / 13,139 = 85.8%；A = 65.6%
非产品范围（不计入）：examples 951（B）、qqmd 早期脚本+wxauto 5,418（C）、qqmd/wxtool 3,788（D）
```

**平台关键词扫描器**（本报告 §2 的证据来源；本次在 Linux 上执行）：

```python
import re, sys
from pathlib import Path
PATS = {
    "startfile": r"os\.startfile",
    "explorer":  r'"explorer"|xdg-open|/select,',
    "platform":  r"sys\.platform|platform\.system|\bwin32\b",
    "mnt":       r"/mnt/",
    "drive":     r"[A-Za-z]:\\+",
    "ctypes":    r"ctypes|windll|WinDLL|ReadProcessMemory|VirtualQueryEx|tasklist",
    "exe":       r"\.exe\b",
    "cuda":      r"\bcuda\b|torch\.cuda|CUDA",
    "winenv":    r"USERPROFILE|LOCALAPPDATA|APPDATA",
    "ffmpeg":    r"\bffmpeg\b",
    "sqlcipher": r"sqlcipher",
}
for root in sys.argv[1:]:
    files = [p for p in Path(root).rglob("*.py") if "__pycache__" not in p.parts]
    loc = sum(len(p.read_text("utf-8", "ignore").splitlines()) for p in files)
    cnt = {k: 0 for k in PATS}
    for p in files:
        for line in p.read_text("utf-8", "ignore").splitlines():
            if "json.dumps" in line:      # 去掉 ensure_ascii 噪声
                continue
            for k, pat in PATS.items():
                if re.search(pat, line):
                    cnt[k] += 1
    print(f"{root:<34} files={len(files):>3} LOC={loc:>6} " +
          " ".join(f"{k}={cnt[k]}" for k in PATS))
```

实测输出（节选）：

```text
module                         files    LOC   startfile   explorer   platform   mnt   drive   ctypes   exe   cuda   winenv   ffmpeg  sqlcipher
chatbridge                        34   4519           0          0          7    13      21        0     6      0        3        0          8
AI-Advaned-File-Search/src        21   3843           1          2          1     0       2        0     0      2        0        0          0
launcher                         1    292           0          0          0     0       7        0     1      1        2        0          0
qqmail_router                      3   2620           0          0          0     0       1        0     0      0        0        0          2
qqmd/fmt                           8   1346           0          0          0     0       2       27     5      0        0        2          7
qqmd/wxtool                       10   3788           0          0          0     0       7       43    10      0        0        0          0
```

> 说明：`drive` 计数包含 docstring/帮助文本里的 `D:\aimem-spool`，不代表逻辑依赖；chatbridge 的 21 处里只有 6 处是 `argparse` 默认值（`hub/__main__.py:110-131` 等），其余是注释与 `--help` 文案。`sqlcipher` 在 chatbridge 的 8 处全是"可选/替代方案"的注释与分支（`cipher.py:1,3`、`pipeline.py:163,168,170,176,184,192`），**不是硬依赖**。

## 11. 附录 B：已核实的现存数据规模（迁移搬运量）

| 项 | 路径 | 实测 |
|---|---|---|
| Spool（**要搬的只有这个**） | `/mnt/d/aimem-spool` | **2.2 MB**；wechat 53 md/1.8 MB、qq 34 md/272 KB、email 17 md/100 KB；`manifest.json` 三源 `status`: email ok / wechat partial / qq partial |
| 消息量 | `manifest.json` | wechat 21,163 + qq 2,204 + email 28 = **23,395**；三源 `status` = email `ok`、wechat `partial`、qq `partial` |
| 搜索 DB（不建议搬） | `/mnt/c/Users/<user>/aimem-test/demo.db` | 225,947,648 B；`demo.vectors/vectors.npy` 276,021,376 B / 134,776 条 / **512 维** |
| 模型 | `/mnt/c/.../aimem-test/bge-reranker-v2-m3` | 2.2 GB（可选） |
| HF 缓存 | `/mnt/c/Users/<user>/.cache/huggingface` | 2.0 GB（含 bge-small-zh 等） |
| 整个测试目录 | `/mnt/c/Users/<user>/aimem-test` | 4.4 GB |
| Windows 聊天原始数据（**不搬**） | `/mnt/c/Users/<user>/Documents/xwechat_files/<wxid>`、`.../Tencent Files/{<account>,nt_qq}` | 仅确认存在（只读 ls） |

**结论**：Mac 侧首次部署需要搬运的数据 = **2.2 MB 的 spool**（外加可选地预下载模型）。这是"方案 B"成本极低的直接原因。

---

## 12. 一页纸结论

- **能搬的**：搜索内核（SQLite FTS5 + 向量 memmap + BGE/reranker）、文件索引、IMAP 邮箱（email 适配器 + router）、Spool/manifest/chat-md 契约、Hub CLI/Web —— 共 8,619 行零改动 + 2,652 行小改。
- **搬不了的**：微信"从进程内存取密钥"（280 行，2 个脚本）与 QQ 的 `qq.exe`/`sqlcipher3` 编排（1,588 行需换实现）。
- **推荐**：P0 先把"搜索 + 文件 + 邮箱"在 Mac 跑起来（3–6 天）；P1 用"Windows 解码 → 同步 2.2 MB spool → Mac `--watch` 增量导入"接通微信/QQ（2–4 天）；P2 才评估 Mac 原生解码（合规风险，需单独立项）。
- **不要做的事**：不要在 Mac 上重造微信解码器再谈移植；不要搬 Windows 的 `demo.db` 文件索引（`D:\` 路径会全部失效）；不要用 PyInstaller 打大包（模型外部化 + 启动脚本更省事）。
