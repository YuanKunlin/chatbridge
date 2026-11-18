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

旧的工作目录仍供本机运行：

```
$CHATBRIDGE_RUNTIME（默认 ~/chatbridge）
$AIMEM_RUNTIME（默认 ~/AI-Advaned-File-Search）
```

它们属于 **部署副本**，不要在上面做长期开发；修改请回到本仓库，然后用：

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
