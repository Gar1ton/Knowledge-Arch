# Git 分支与远端操作手册

## 1. 分支

- `developer`：本仓库唯一开发真相源。历史从「导入插件仓库 developer `8e6f1af`」的首个提交开始；
  插件时代的提交、TODO 与 CHANGELOG 只保留在插件仓库与本仓库首个提交里。
- 新功能可从 developer 创建 `feature/*`，完成后合回 developer。
- `main`：发布分支，**只由 CI 写入**。在 developer 的提交上推 `v*` 标签后，`desktop-release.yml` 的
  publish-main 作业运行 `.github/scripts/build_main.py`：按 `.release-exclude` 删去治理文档（CLAUDE / AGENTS /
  CONVENTIONS / TODO / ARCHITECTURE）、开发期 CHANGELOG、内部设计稿、`dev/`、`tools/` 与仅 developer 使用的 CI，
  检查 README 等文档没有指向被删文件的链接，再以现有 main 为父提交生成一个提交并推送（线性、不 force）。
  安装包从这个 main 提交构建；tag 的源码包由 `.gitattributes` 的 `export-ignore` 剔除同一批文件。
- 外部贡献者的 issue 与 PR 都提到 developer；main 只放调试好的发布版本。

## 2. 远端操作审批（绝对规则）

下列行为都属于远端变更，执行前必须单独取得用户明确批准：

- `git push`、`git push --tags`；
- 创建、删除或改写远端分支/tag；
- force push（默认禁止）；
- 创建 PR 或 GitHub Release；
- 切换 GitHub 默认分支、替换远端 `main`。

推送 `v*` 标签会让 CI 自动更新 `main`（见 §1）：申请推标签时一并说明这一点，批准推标签即批准这次 main 更新。

批准修改、测试或本地 commit 不代表批准 push。申请批准时必须列出：

```bash
git remote -v
git branch --show-current
git log --oneline @{upstream}..HEAD   # 尚无 upstream 时改为 git log --oneline
git status --short
```

## 3. 预演 main 与首次切换

推标签前可在本地预演 main 的文件清单（不生成提交、不推送）：

```bash
git fetch origin main
python .github/scripts/build_main.py --tag v2.0.0-rc.3 --parent origin/main --list
```

远端 `main` 目前是插件时代的旧实现（v0.1.0）。第一次由标签生成的 main 提交以旧 main HEAD 为父提交，
旧历史保留在下面，不需要 force push。

## 4. 提交信息

- 不加 `Co-Authored-By: Claude` 之类的 AI 署名行。
- 每个 Phase 至少一个本地 commit，信息说明 Phase 与要点。
