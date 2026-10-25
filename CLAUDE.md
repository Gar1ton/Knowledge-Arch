# CLAUDE.md

> 本文件是本项目对所有 coding agent（Claude Code / Cursor / Codex / Gemini 等）的**最高行为契约**。
> Agent 在执行任何任务前**必须**完整读完本文件，并按「必读顺序」加载其余规范。
> 本文件的指令**优先于** agent 的任何默认行为。

---

## 0. 这是什么

这是 **Knowledge Arch** 桌面应用（Win / macOS / Linux）：Tauri 桌面壳 + 本地 Python 后端
（`ka_server` → `kacore`）+ Next.js 静态导出的界面。本仓库沿用「结构与约定先行」的治理框架：

> **规范并约束「vibe coding」的代码可视性**——让任何人/任何 agent 一眼就知道
> 「代码该放在哪一层、依赖该指向哪个方向、文档该怎么写」。

---

## 1. ABSOLUTE RULE — 工作目录边界

- **只允许在本工作区内修改代码。** 严禁改动本工作区以外的任何目录。
- 下列内容为**只读/禁改区**（除非用户明确指示）：
  - 任何位于本仓库根目录之外的路径；
  - 已标记 `(completed)` 的 `TODO.md` 计划段落的内部技术细节（仅可修正错误）。
- 非必要**不修改框架核心代码**（组合根、各层 `base` 接口）。确需改动时，必须在计划中单列理由。

### 1.1 Git 远端变更（绝对规则）

- `developer` 是唯一开发真相源；正式 `main` 是由指定 developer commit 生成的发布分支，禁止手改。
  推送 `v*` 标签后，CI（`desktop-release.yml` 的 publish-main）按 `.release-exclude` 删去治理文档、开发期 CHANGELOG、
  内部设计稿与开发脚本，生成新的 main 提交并推送；**批准推送 `v*` 标签即同时批准这次 main 更新**。
  不要手动 push main。新增只在开发期使用的文件时，同步更新 `.release-exclude` 与 `.gitattributes`。
- 任何 `git push`、tag push、远端分支创建/删除、force push、PR 创建或 GitHub Release 发布，
  **执行前必须单独向用户报告 remote、源分支、目标分支与提交范围，并取得本轮明确批准**。
- 用户批准修改、测试或本地 commit，**不等于**批准任何远端操作。
- force push 默认禁止；只有用户明确点名该操作并确认风险后才允许。
- 详细人工流程见 `docs/GIT_WORKFLOW.md`。

## 2. 必读顺序（首次进入本框架的 agent 必读）

按顺序加载，读完即具备「在本项目正确落位代码」的全部知识：

1. **`ARCHITECTURE.md`** — 分层、组合根、依赖方向、接口先行、生命周期。理解「代码放哪、依赖指哪」。
2. **`CONVENTIONS.md`** — 命名、docstring、分隔符、单一职责、文件大小红线。理解「怎么写才可视」。
3. **`TODO.md`** — 当前路线图与进行中任务。理解「现在该做什么」。
4. （需要时）**`CHANGELOG.md`** — 历史变更，理解「过去做过什么、为什么」。

## 3. 执行协议（Plan-First）

任何**非平凡**任务（多文件 / 改接口 / 加子系统）按以下流程，**未获批准不得改代码**：

1. **先读相关测试**与现有实现，理解契约与边界。
2. 产出一份**按 Phase 划分**的计划，每个 Phase 说明动作 + 技术理由。
3. **等待用户批准**。批准前只做只读探索。
4. 批准后逐 Phase 执行，**边写边跑测试**确保功能完整。

平凡任务（改错别字、单行修复、重命名）可直接执行，无需走完整流程。

## 4. 每轮工作闭环（强制）

> 经验法则：**先更新 TODO，再动代码；测试通过，才标完成；收尾追加 CHANGELOG。**

1. **动代码前**：在 `TODO.md` 对应计划里勾 `🚧`（进行中）或追加子项 `[ ]`。
2. **写代码时**：遵循 `CONVENTIONS.md`；新增子系统遵循 `ARCHITECTURE.md` 的「新增子系统清单」。
3. **测试通过后**：才能把 `TODO.md` 中的条目标为 `[x]`（`[x]` = 代码已落地且相关测试已过，不是「我以为写完了」）。
4. **任务收尾**：在 `CHANGELOG.md` 顶部按其格式追加本次变更条目（点名涉及的文件/模块）。

## 5. Build & Test 命令

```bash
# 安装依赖
pip install -r requirements-additional.txt

# 运行测试（标 [x] 前必须绿）
python -m pytest

# 代码检查 / 类型检查
ruff check . && mypy

# 前端（Next.js App Router，静态导出到 web/frontend/out/，由桌面壳加载）
cd web/frontend && npm run lint && npx tsc --noEmit && npm run build

# 本地后端（开发）：token 只经环境变量传入
KA_TOKEN=dev-token python -m ka_server serve --port 26618 --data-dir .dev_data

# 桌面壳（Tauri 2，见 desktop/README.md）：先构建前端，再打包后端资源，最后出包
python desktop/scripts/bundle_python.py && (cd desktop && npm ci && npx tauri build)
cd desktop/src-tauri && cargo test && cargo clippy && cargo fmt --check
```

## 6. 语言约定

- 文档正文、设计讨论、注释：**中文**。
- README 分两份：`README.md`（英文，默认）与 `README_ZH.md`（中文），内容同步修改。
- 代码标识符、命令、`TODO.md` 状态值、`CHANGELOG.md` 版本号：**英文**。
