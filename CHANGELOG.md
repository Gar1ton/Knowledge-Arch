# CHANGELOG

> 本文件记录 **monorepo / 独立应用（v0.1.x 版本线）** 的所有重要变更；AstrBot 插件（v1.0.x
> 版本线）的历史与后续变更见 `apps/plugin/CHANGELOG.md`。
> **所有参与者（含 AI Agent）在写入时必须遵守下方「写入规范」。**

## 写入规范

- **倒序排列**：最新版本永远在最上方；`[Unreleased]` 在所有已发布版本之上。
- **版本标题格式**：`## [vX.Y.Z] — YYYY-MM-DD`，遵循 [SemVer](https://semver.org/)；版本号与根 `pyproject.toml` 的 `version` 对齐。
  - MAJOR：不兼容的接口/数据结构变更；MINOR：向后兼容的新功能；PATCH：向后兼容的修复。
- **子分区**（按需出现，无内容则省略，顺序固定）：
  - `新增功能 (Added)` / `修复 (Fixed)` / `性能优化 (Performance)` / `架构健康 (Refactor)` / `测试 (Tests)` / `构建与工程 (Build/CI)`
- **每条变更点名涉及的文件或模块**，强化可追溯。
- **写入时机**：任务收尾时追加（见 `CLAUDE.md` §4 工作闭环）；先进 `[Unreleased]`，发布时整体改写为版本标题 + 日期。
- 语言：正文中文，标识符/路径/版本号英文。

---

## [Unreleased]

### 新增功能 (Added)

- **Monorepo 初始化（Phase 1）**：从 Astrbot_Knowledge_Repository `developer@65ec405`（v1.0.3）
  迁入全部跟踪源码（剔除 data/、deliverables/、dev/），确立「一核两壳」布局——插件壳文件
  （main.py/metadata.yaml/_conf_schema.json/README/CHANGELOG/logo）移入 `apps/plugin/`，
  `bump_version.py` 移入 `tools/` 并对齐插件版本线路径；`TODO.md` 置顶 v0.1.0 移植计划。

### 修复 (Fixed)

- **修复发布树构建脚本的陈旧校验清单**：`tools/build_published_tree.py` 的 REQUIRED 仍含
  v1.0.2 改名前的 `web/server.py`（导致对当前源构建必失败）、FORBIDDEN 含 `core/main.py`；
  更新为 `ka_web/server.py` / `ka_core/main.py`，并新增 `apps/`、`ka_standalone/`、`scripts/`
  禁止前缀（tools/build_published_tree.py）。
- **修复 Developer CI 前端任务的旧路径**：`.github/workflows/tests.yml` frontend job 仍指向
  v1.0.2 改名前的 `web/frontend`，更新为 `ka_web/frontend`。

### 架构健康 (Refactor)

- **核心包改名 `knowledge_arch/` → `ka_core/`**：全仓 import/字符串/monkeypatch 路径/工具与
  CI 清单/文档路径统一改写（118 个文件）；`apps/plugin/CHANGELOG.md` 与 `TODO.md` 已完结
  段落作为历史记录保留原名。发布树经重命名感知校验：对旧仓 developer@65ec405 基线
  482 文件 missing=0/extra=0，64 处内容差异全部为纯包名替换。
- **发布 manifest 适配 monorepo 布局**：`release/published-files.txt` 重写为
  `apps/plugin/* => 根` 映射 + `ka_core/**` 白名单（release/published-files.txt）。

### 测试 (Tests)

- 迁移后全量后端测试 `python -m pytest tests/backend` → **608 passed**；
  `ruff check .`（含 rename 后 2 处 I001 自动修复）与 `mypy` 全绿。
