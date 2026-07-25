# Knowledge Arch（Monorepo）

[![standalone](https://img.shields.io/badge/standalone-v0.2.2-orange)](pyproject.toml)
[![plugin](https://img.shields.io/badge/plugin-v1.0.3-blueviolet)](apps/plugin/metadata.yaml)

**一套核心，两个壳**：原件中心的知识库系统（PDF 原件管理、五档检索/RAG、LightRAG 知识图谱、
Zotero/R2/Notion 同步备份、独立 Web 控制台），同一业务核心同时驱动——

- **AstrBot 插件**（v1.0.x）：发布于 [Astrbot_Knowledge_Repository](https://github.com/Gar1ton/Astrbot_Knowledge_Repository)，
  在 AstrBot 中以 `/ka` 命令 + LLM 工具 + WebUI 使用；
- **独立跨平台应用**（v0.2.x，开发中）：脱离 AstrBot 独立运行，Linux/Windows/macOS，
  统一「向知识库提问」面板（普通 Ask、Research 自动编排、Persona 风格、历史与引用）、
  OpenAI 兼容 LLM 接入与可选 Discord 直连渠道，一键安装脚本部署。

## 仓库结构

```
kacore/            共享业务核心（domain/repository/managers/pipelines/adapters/api）
                    ★ 框架中立：不 import astrbot，也不 import 任何壳
web/             Web 控制台：server.py（aiohttp）+ frontend/（Next.js 源码）
ka_migrations/      SQLite 编号迁移
pages/              前端构建产物（tools/sync_frontend.py 生成，勿手改）
apps/plugin/        AstrBot 插件薄壳（main.py / metadata.yaml / _conf_schema.json）
  └ ka_plugin/      ★ AstrBot 专属适配器（AstrBotLLMAdapter / AstrBotKnowledgeBaseReader）
standalone/      独立应用薄壳（cli / bootstrap / doctor / notion_transport）
tools/              构建与发布脚本（build_published_tree / publish_plugin / bump_version / sync_frontend）
release/            发布 manifest（published-files.txt 等）
scripts/            一键安装/启动脚本
tests/  docs/       测试与文档
```

**一核两壳的边界**：两个壳各自持有自己的适配器，经组合根 `PluginInitializer` 显式注入；
`kacore` 只认中立接口（如 `kacore/adapters/llm.py` 的 `LLMAdapter` Protocol），
既不 import `astrbot` 也不 import 任何壳。依赖方向只能 **壳 → 核心**，两壳互不相识。

## 部署独立应用（Linux / Windows / macOS）

安装模型：**clone 仓库 + uv 同步依赖**（不走 PyPI；`pages/` 前端产物与迁移文件随源码就位，
部署全程无需 Node.js）。Python 版本由 `.python-version`（3.11）钉定，uv 会自动安装。

```bash
# Linux / macOS
git clone https://github.com/Gar1ton/Knowledge-Arch.git && cd Knowledge-Arch
bash scripts/install.sh          # 可加 --cn 用清华镜像；--minimal 跳过重型 extras
bash scripts/start.sh            # = uv run knowledge-arch serve
```

```bat
:: Windows（PowerShell 或 cmd）
git clone https://github.com/Gar1ton/Knowledge-Arch.git && cd Knowledge-Arch
scripts\install.bat
scripts\start.bat
```

- **首次启动**：在 `~/.knowledge-arch`（Windows 为 `%USERPROFILE%\.knowledge-arch`）生成
  `standalone_config.json` 与**随机登录密码**（打印在控制台，`knowledge-arch passwd` 可改）。
  默认 WebUI 地址 `http://127.0.0.1:26618`。
- **主模型**：登录后在「设置 → 主模型」配置任意 OpenAI 兼容端点（OpenAI/DeepSeek/Ollama/
  LM Studio/vLLM…）；API Key 经环境变量 `KR_MAIN_LLM_API_KEY` 注入。配置后即可在右侧
  **「向知识库提问」** 面板对话——普通 Ask、`/ka` 命令、开启 Research 后自动探查范围与选择召回
  模式；设置浮层可切换上下文参与召回、Persona 风格与回答语言（保存 main_llm 后免重启生效）。
- **Windows 向量检索**：Milvus Lite 无 Windows 原生 wheel，三选一——① 连接外部 Milvus 服务；
  ② 在 WSL 内运行本应用；③ 什么都不做，自动回落 SQLite 词法检索（功能降级）。
  `uv run knowledge-arch doctor` 可随时体检。
- **可选能力 extras**：`graph`（LightRAG 图谱）/ `local-embedding`（本地嵌入，含 PyTorch）/
  `backup`（R2）/ `notion`（stdio MCP，另需 Node.js 提供 npx）。默认脚本全装，
  `--minimal` 后可按需 `uv sync --extra <name>` 补装。
- 常用命令：`knowledge-arch serve|status|doctor|passwd`（经 `uv run` 前缀执行）。

## Discord 渠道（独立版可选）

独立版可把「向知识库提问」直连到 Discord，让 Bot 在私信、被提及或被回复时用知识库回答。
首版仅支持文本，不涉及附件、语音、审核或 Slash Commands；配置变更后需重启生效。

1. **建 App 与 Bot**：在 [Discord Developer Portal](https://discord.com/developers/applications)
   新建 Application → Bot，复制 **Bot Token**。
2. **开启 Intents**：在 Bot 设置里打开 **MESSAGE CONTENT INTENT**（并保留 Server Members 之外的
   默认项）。本应用启用 `GUILDS`/`GUILD_MESSAGES`/`DIRECT_MESSAGES`/`MESSAGE_CONTENT` 四个 intent，
   以可靠支持「回复但未再次提及」的触发；缺 MESSAGE CONTENT 会导致读不到消息正文。
3. **邀请到服务器**：OAuth2 → URL Generator 勾选 `bot` scope，Bot Permissions 至少给
   **View Channels / Send Messages / Read Message History**，用生成的链接邀请进目标服务器。
4. **注入 Token**：优先用环境变量 `KR_DISCORD_BOT_TOKEN`；也可经设置页写入加密 secret store。
   API 只返回 `token_present`，**永不回显、记录或备份** token。
5. **配置 allowlist 并启用**：在「设置 → Discord」填 `allowed_user_ids`（**至少一个**，否则禁止
   启用）；`allowed_guild_ids` / `allowed_channel_ids` 非空时进一步收窄服务器与频道范围。私信必须
   命中用户白名单。
6. **触发方式**：仅响应私信、`@提及` Bot 或**回复** Bot 的消息；忽略自身与其他 Bot 的消息。
   会话按 guild/thread/DM 隔离到「用户 × 频道」；长 Research 会先发一条可更新的进度消息，最终
   答案按句切分为 ≤1,900 字符的多条消息并禁用所有意外 mentions。

- **依赖**：需可选依赖 `discord.py`（`uv sync --extra discord`）；未安装或未配置 token/白名单时
  独立版仍正常启动，仅 Discord 渠道保持诊断状态。
- **故障排查**：`knowledge-arch doctor` 查看渠道状态；读不到消息→检查 MESSAGE CONTENT INTENT；
  Bot 不回复→确认已被提及/回复且发送者/频道在 allowlist 内；启用被拒→确认 `allowed_user_ids` 非空。

## 开发快速上手

```bash
pip install -r requirements-dev.txt      # 后端测试工具链
python -m pytest                         # 全量测试（标 [x] 前必须绿）
ruff check . && mypy                     # 静态检查

cd web/frontend && npm run build      # 前端构建（产物输出到 out/）
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
