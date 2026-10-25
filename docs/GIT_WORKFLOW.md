# Git 分支与远端操作手册

## 1. 分支

- `developer`：本仓库唯一开发真相源。历史从「导入插件仓库 developer `8e6f1af`」的首个提交开始；
  插件时代的提交、TODO 与 CHANGELOG 只保留在插件仓库与本仓库首个提交里。
- 新功能可从 developer 创建 `feature/*`，完成后合回 developer。
- 发布产物（安装包）由 CI 从指定 developer commit 构建（P6），不维护单独的「发布树」分支。

## 2. 远端操作审批（绝对规则）

下列行为都属于远端变更，执行前必须单独取得用户明确批准：

- `git push`、`git push --tags`；
- 创建、删除或改写远端分支/tag；
- force push（默认禁止）；
- 创建 PR 或 GitHub Release；
- 切换 GitHub 默认分支、替换远端 `main`。

批准修改、测试或本地 commit 不代表批准 push。申请批准时必须列出：

```bash
git remote -v
git branch --show-current
git log --oneline @{upstream}..HEAD   # 尚无 upstream 时改为 git log --oneline
git status --short
```

## 3. 首次推送到 Gar1ton/Knowledge-Arch

远端仓库已有旧实现。首次推送用**新分支**（例如 `standalone/v2`），不覆盖远端 `main`；
是否以及何时用新历史替换 `main`，需要用户单独批准。

## 4. 提交信息

- 不加 `Co-Authored-By: Claude` 之类的 AI 署名行。
- 每个 Phase 至少一个本地 commit，信息说明 Phase 与要点。
