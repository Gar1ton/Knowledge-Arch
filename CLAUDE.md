# CLAUDE.md

> 本文件是本项目对所有 coding agent（Claude Code / Cursor / Codex / Gemini 等）的**最高行为契约**。
> Agent 在执行任何任务前**必须**完整读完本文件，并按「必读顺序」加载其余规范。
> 本文件的指令**优先于** agent 的任何默认行为。

---

## 0. 这是什么

这是 **Knowledge Arch monorepo**——「一套核心，两个壳」的知识库系统唯一开发源头：

```
kacore/         共享业务核心（domain/repository/managers/pipelines/adapters/api/config）
                 ★ 框架中立：不 import astrbot，也不 import 任何壳
web/          Web 控制台（server.py + frontend/ Next.js 源码）
ka_migrations/   SQLite 编号迁移          pages/   前端构建产物（勿手改）
apps/plugin/     AstrBot 插件薄壳（v1.0.x 版本线，发布到 Astrbot_Knowledge_Repository）
  └ ka_plugin/   ★ AstrBot 专属代码（AstrBotLLMAdapter / AstrBotKnowledgeBaseReader）
standalone/   独立跨平台应用薄壳（v0.2.x 版本线，Linux/Windows/macOS）
tools/ release/  构建与发布脚本、发布 manifest      scripts/  一键安装/启动脚本
```

**依赖方向只能「壳 → 核心」**：两个壳各自持有自己的适配器并经组合根显式注入；
`kacore` 谁也不认识。这条边界由 `tests/backend/test_shell_boundaries.py` 守卫。

- **双版本线**：插件版本源 = `apps/plugin/metadata.yaml`；独立版版本源 = 根 `pyproject.toml`
  `[project].version`。经 `python tools/bump_version.py --target plugin|standalone` 统一推进，
  分别对齐 `apps/plugin/CHANGELOG.md` 与根 `CHANGELOG.md`。
- 业务逻辑只写在 `kacore/`；两个壳只做注册与委派（见 `ARCHITECTURE.md` §2 薄壳原则）。
- 本仓治理理念不变：**规范并约束「vibe coding」的代码可视性**——让任何人/任何 agent 一眼就知道
  「代码该放在哪一层、依赖该指向哪个方向、文档该怎么写」。

---

## 1. ABSOLUTE RULE — 工作目录边界

- **只允许在本工作区内修改代码。** 严禁改动本工作区以外的任何目录。
- 下列内容为**只读/禁改区**（除非用户明确指示）：
  - 任何位于本仓库根目录之外的路径；
  - `pages/`（前端构建产物，由 `tools/` 脚本生成，不要手改）；
  - 已标记 `(completed)` 的 `TODO.md` 计划段落的内部技术细节（仅可修正错误）。
- 非必要**不修改框架核心代码**（组合根、各层 `base` 接口）。确需改动时，必须在计划中单列理由。

### 1.1 Git 远端变更（绝对规则）

- 本仓（Knowledge-Arch）`main` 是**唯一开发真相源**。
- 插件发布链：`tools/publish_plugin.py` 从本仓生成**插件仓库形态源码树**，推送到
  `Astrbot_Knowledge_Repository` 的 `developer` 分支；该仓自身的 developer→main 发布机制
  （`tools/build_published_tree.py` + `release/published-files.txt`）保持不变。
  **禁止绕过 manifest 手工向旧仓提交。**
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
# 安装开发依赖
pip install -r requirements-dev.txt

# 运行测试（标 [x] 前必须绿）
python -m pytest

# 代码检查 / 类型检查
ruff check . && mypy

# 前端（Next.js App Router）
cd web/frontend && npm run build   # FRONTEND_BUILD_CMD（产物输出到 out/）
python tools/sync_frontend.py         # FRONTEND_SYNC_CMD（out/ → pages/）
python tools/sync_frontend.py --check # FRONTEND_VERIFY_CMD（CI 同款只读校验）

# 插件发布树（AstrBot 市场 main 形态，本地校验用）
python tools/build_published_tree.py --source HEAD

# 插件仓库同步树（旧仓 developer 形态；--push 需用户批准）
python tools/publish_plugin.py --check-only

# 独立应用（Phase 3 起）
python -m standalone serve --data-dir ~/.knowledge-arch
```

### 5.1 前端导出产物契约（强制）

只要本轮改动涉及 `web/frontend/` 中的源码、静态资源、构建配置或依赖锁文件，Agent 在将任务标记完成前**必须**完成以下闭环：

1. 构建当前前端源码，生成 `web/frontend/out/`；
2. 仅通过 `tools/sync_frontend.py` 将 `out/` 同步到 `pages/`，严禁手改 `pages/`；
3. 运行 `python tools/sync_frontend.py --check`，并在交付中报告通过结果；
4. 检查 `pages/` 的生成物变更已随对应前端源码纳入本地提交范围。用户未明确授权时，Agent 不得自行 commit 或 push。

Docker 开发环境的标准命令为：

```bash
bash rebuild.sh --build-only
python3 tools/sync_frontend.py --check
```

`bash rebuild.sh --build-only` 会执行生产构建并同步 `out/ → pages/`，但不会停止、启动或重启服务，适用于提交前、CI 前与发布前的构建产物刷新。
普通 `bash rebuild.sh` 也会构建并同步产物，但会停止现有进程、占用端口并重新启动后端和 Next 开发服务器；仅在用户明确需要重启开发环境时使用，不能替代最后的 `--check` 校验。

## 6. 语言约定

- 文档正文、设计讨论、注释：**中文**。
- 代码标识符、命令、`TODO.md` 状态值、`CHANGELOG.md` 版本号：**英文**。
