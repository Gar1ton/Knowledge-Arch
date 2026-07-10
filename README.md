# Knowledge Arch（Monorepo）

[![standalone](https://img.shields.io/badge/standalone-v0.1.0-orange)](pyproject.toml)
[![plugin](https://img.shields.io/badge/plugin-v1.0.3-blueviolet)](apps/plugin/metadata.yaml)

**一套核心，两个壳**：原件中心的知识库系统（PDF 原件管理、五档检索/RAG、LightRAG 知识图谱、
Zotero/R2/Notion 同步备份、独立 Web 控制台），同一业务核心同时驱动——

- **AstrBot 插件**（v1.0.x）：发布于 [Astrbot_Knowledge_Repository](https://github.com/Gar1ton/Astrbot_Knowledge_Repository)，
  在 AstrBot 中以 `/ka` 命令 + LLM 工具 + WebUI 使用；
- **独立跨平台应用**（v0.1.x，开发中）：脱离 AstrBot 独立运行，Linux/Windows/macOS，
  自带对话 Agent 界面与 OpenAI 兼容 LLM 接入，一键安装脚本部署。

## 仓库结构

```
ka_core/            共享业务核心（domain/repository/managers/pipelines/adapters/api）
ka_web/             Web 控制台：server.py（aiohttp）+ frontend/（Next.js 源码）
ka_migrations/      SQLite 编号迁移
pages/              前端构建产物（tools/sync_frontend.py 生成，勿手改）
apps/plugin/        AstrBot 插件薄壳（main.py / metadata.yaml / _conf_schema.json）
ka_standalone/      独立应用薄壳（Phase 3 落地：cli / bootstrap / doctor）
tools/              构建与发布脚本（build_published_tree / publish_plugin / bump_version / sync_frontend）
release/            发布 manifest（published-files.txt 等）
scripts/            一键安装/启动脚本（Phase 7 落地）
tests/  docs/       测试与文档
```

## 开发快速上手

```bash
pip install -r requirements-dev.txt      # 后端测试工具链
python -m pytest                         # 全量测试（标 [x] 前必须绿）
ruff check . && mypy                     # 静态检查

cd ka_web/frontend && npm run build      # 前端构建（产物输出到 out/）
python tools/sync_frontend.py            # out/ → pages/
```

首次进入本仓库的 agent/开发者请先读 `CLAUDE.md`（最高行为契约）→ `ARCHITECTURE.md` →
`CONVENTIONS.md` → `TODO.md`。

## 版本与发布

- **双版本线**：插件版本源 = `apps/plugin/metadata.yaml`；独立版版本源 = 根 `pyproject.toml`。
  统一经 `python tools/bump_version.py` 推进。
- **插件同步**：`tools/publish_plugin.py` 从本仓生成插件仓库形态源码树，推送到
  Astrbot_Knowledge_Repository 的 `developer` 分支；其 developer→main 发布机制保持不变。

## 许可

见 [LICENSE](LICENSE)。
