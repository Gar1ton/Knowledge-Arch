# Project Structure — 目录与治理指引

## 一句话定位

Knowledge Arch 是本地优先的学术知识库桌面应用：PDF 原件管理与清洗、Zotero 同步、
六档检索问答（带 Harvard 引用）、LightRAG 知识图谱、Notion / R2 同步备份。
安装一个桌面应用即可使用，所有数据都在本机数据目录里。

## 必读入口

1. [`CLAUDE.md`](../CLAUDE.md) — agent 行为契约与工作闭环。
2. [`ARCHITECTURE.md`](../ARCHITECTURE.md) — 分层、组合根、依赖方向。
3. [`CONVENTIONS.md`](../CONVENTIONS.md) — 命名、docstring、文件大小红线。
4. [`TODO.md`](../TODO.md) / [`CHANGELOG.md`](../CHANGELOG.md) — 路线图与变更记录。

## 目录结构一览

```
.
├── desktop/            # Tauri 2 桌面壳（src-tauri/）与后端打包脚本（scripts/bundle_python.py）
├── ka_server/          # 后端进程薄壳：python -I -m ka_server serve
├── kacore/             # 全部业务逻辑（组合根 app_initializer.py、api 门面、分层子包）
├── web/server.py       # HTTP 路由（token 鉴权 + CORS），委派 kacore/api
├── web/frontend/       # Next.js 界面源码，静态导出到 out/，由桌面壳加载
├── migrations/         # 编号 SQL 迁移（幂等 runner 按文件名应用）
├── tests/              # pytest：backend/ 与 frontend/（TS 纯函数）
├── tools/              # 维护脚本（切片预览、重处理等）
├── dev/                # 本地联调说明
├── docs/               # 设计与运维文档、计划
├── requirements*.txt   # 核心依赖 / 可选组件 / 开发依赖
└── .agents/skills/     # 外部 agent 操作 KA 的 skill（见 TODO 中的待决项）
```

## 运行时数据目录

| 平台 | 位置 |
|------|------|
| macOS | `~/Library/Application Support/Knowledge Arch` |
| Windows | `%LOCALAPPDATA%\Knowledge Arch` |
| Linux | `~/.local/share/Knowledge Arch` |

目录内的 `knowledge_repository.db`、`library/`、`vector_store.db`、`lightrag_workspaces/`
与插件版同一 schema；`--data-dir` 可指定其他位置（开发 / 测试）。另有：

- `settings.json` + `secrets/`：设置与加密机密库；
- `extras/<pack>/`：应用内安装的可选组件（本地模型 / 知识图谱 / R2），由 uv 安装，启动时加入 `sys.path`；
- `.legacy_import/`：导入旧版插件数据时的 staging、回滚目录与最近一次结果。

## 分层职责速查

| 层 | 职责 | 依赖约束 |
|----|------|----------|
| `ka_server` | 进程生命周期：目录、锁、信号、HTTP 起停 | 可依赖 aiohttp；不写业务 |
| `web` | HTTP ↔ `kacore/api` 翻译 | 不 import managers 内部实现 |
| `kacore/api` | 业务门面 | 框架无关，不含 HTTP 概念 |
| `managers` / `pipelines` | 用例编排 / 多步骤流水线 | 依赖 repository 接口 + domain |
| `repository` | 持久化抽象 + 实现 | 只依赖 domain |
| `domain` | 纯数据模型 | 零依赖 |

## 维护规则摘要

- 先更新 TODO，再动代码；测试通过才标 `[x]`；收尾追加 CHANGELOG。
- `migrations/` 只追加新文件，不改已有迁移的语义。
- 除 `kacore/legacy_import/`（旧插件数据导入器）外，仓库不出现旧宿主框架名；
  `tests/backend/test_no_legacy_host_references.py` 守住这条线。
