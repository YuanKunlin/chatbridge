# 开发与提交流程

## 唯一源码仓库

- GitHub：`git@github.com:YuanKunlin/chatbridge.git`
- 分支：`main`
- 本地源码副本：本仓库。

以后代码/文档变更都在本仓库进行，并立即提交、推送：

```bash
git add -A
git commit -m "..."
git push
```

## 运行时目录（部署目标，不是源码）

运行时目录：

```
$CHATBRIDGE_RUNTIME（默认 ~/chatbridge，已 symlink 到本仓库 chatbridge/）
$AIMEM_RUNTIME（默认 ~/AI-Advaned-File-Search）
```

`~/chatbridge` 现在就是本仓库 `chatbridge/` 的符号链接，改仓库即改运行时；
`engine/` 仍通过下面的脚本同步到搜索运行时。所有代码修改请回到本仓库，提交后：

```bash
./scripts/deploy_runtime.sh
```

同步到运行时。脚本只复制代码，不碰数据库、spool、模型。

## 数据与隐私

- 不提交真实聊天记录、数据库、向量、模型、截图。
- 样例只保留 `chatbridge/spool-dev/sample` 与 `chatbridge/spool-dev/email` 的合成数据。
- 提交前可跑：

```bash
./scripts/check.sh
```
