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

## [v0.2.3] - 2026-07-25

### 修复 (Fixed)

- **同步 Notion Strict 归档账本迁移**：从旧插件仓移植归档状态约束升级为
  `ka_migrations/022_notion_archived_status.sql`，避免与本仓 `021_chat_sessions.sql` 编号冲突；
  迁移保留现有 `notion_entity_map` 记录并允许 Strict 模式写入 `archived` tombstone
  （`ka_migrations/022_notion_archived_status.sql`、`tests/backend/test_migration_022.py`）。

### 新增功能 (Added)

- **Codex 项目级 Knowledge Arch Skill**：新增 `.agents/skills/operate-knowledge-arch/`，由仅依赖 Python
  标准库的客户端通过 WebUI API 执行目录发现、紧凑检索、RRF 融合、去重、分页全文读取与就近引用，
  最终研究回答仍由 Codex 生成且绝不调用 `/api/ask`；配置操作按 `/api/auth.shell` 自动区分
  `plugin` / `standalone` 可写白名单，保留预览、显式应用、独立重启确认与禁止自动重建约束。

### 架构健康 (Refactor)

- **统一顶层目录与 Python 包命名**：一次性将 `ka_core/`、`ka_web/`、`ka_standalone/`
  硬切换为 `kacore/`、`web/`、`standalone/`，同步更新生产代码、测试、动态加载、CLI 入口、
  `pyproject.toml`、当前架构文档与工程说明；不保留旧导入兼容层。

### 测试 (Tests)

- **Skill 与发布契约覆盖**：新增 `tests/backend/test_knowledge_arch_skill_client.py`，覆盖 WebUI 鉴权、连接与
  401 故障、检索融合/截断、文档分页、双壳配置策略快照、敏感信息脱敏、应用/重启确认门、隔离进程
  端到端命令链，以及三份发布清单和两级发布校验器的完整 Skill 文件契约。

### 构建与工程 (Build/CI)

- **适配新目录的构建与发布链路**：更新 CI、发布清单、容器/重建脚本和前端同步路径；
  `tools/build_published_tree.py` 的 glob 改为从仓库根匹配，避免通用目录名 `web/` 误纳入嵌套设计资料；
  `tools/sync_frontend.py` 在 Windows 目录被占用、无法原子改名时支持安全的原地镜像回退。

- **Skill 纳入两级发布链路**：收窄 `.agents/` 忽略规则，仅追踪 `operate-knowledge-arch`；同步更新
  `release/published-files.txt`、`release/developer-files.txt`、`release/published-files.developer.txt`
  及 `tools/build_published_tree.py` / `tools/publish_plugin.py` 的必需文件校验，使 monorepo、旧仓
  developer 同步树与正式 AstrBot 插件安装树都包含该 Skill。

- **固化前端导出产物契约**：`CLAUDE.md` 要求 Agent 修改 `web/frontend/` 后，必须完成构建、
  `tools/sync_frontend.py` 同步与 CI 同款 `--check` 校验，并确认生成的 `pages/` 纳入本地提交范围；
  同时明确 Docker 中 `bash rebuild.sh --build-only` 与普通 `bash rebuild.sh` 的适用边界。

## [v0.2.2] — 2026-07-13

### 构建与工程 (Build/CI)

- **修复前端导出产物校验**：通过 `tools/sync_frontend.py` 将
  `ka_web/frontend/out/` 的 357 个构建产物同步至 `pages/`，使 Developer CI 的
  `Verify exported pages` 步骤重新通过（`pages/`、`tools/sync_frontend.py`）。

- **独立版版本线**：将 `pyproject.toml`、`README.md` 与
  `ka_standalone/__init__.py` 对齐为 `v0.2.2`。

## [v0.2.1] — 2026-07-13

### 架构健康 (Refactor)

- **两壳分离：`ka_core` 框架中立化**。此前核心里既住着 AstrBot 专属适配器、又住着独立版适配器，
  组合根还用 `else: <AstrBot 默认>` 把 AstrBot 当成核心的隐藏默认值——「哪块代码归哪个壳」无法一眼判断。
  本次改为：
  - **中立契约**：`ka_core/adapters/llm.py` 的 `LLMAdapter` 由 AstrBot 具体类改为
    **Protocol**（核心唯一依赖 `generate`；`chat_stream` 仍是 `hasattr` 探测的可选能力）；
    新增 `OfflineStubLLMAdapter` 承接「未配置主 LLM」的中立离线占位（`allow_mock=False` 显式抛错，
    占位内容永不混入证据）。`LMStudioLLMAdapter` 留在核心（LightRAG / Deep Thinking / 独立版主模型共用）。
  - **AstrBot 代码归位插件壳**：新建插件自有包 `apps/plugin/ka_plugin/adapters/`，
    收纳 `AstrBotLLMAdapter`（原 `LLMAdapter` 的 provider 实现，含**核心里最后一行 `import astrbot`**）、
    `AstrBotKnowledgeBaseReader`、`to_document_chunk`；从 `ka_core` 删除
    `repository/kb_reader/astrbot.py` 与 `adapters/astrbot_kb.py`。
  - **组合根中立、两壳对称**：删掉 `else: LLMAdapter(context)` / `else: AstrBotKnowledgeBaseReader(context)`
    两处 AstrBot 隐藏默认，改为中立默认（`OfflineStubLLMAdapter` / `InMemoryKnowledgeBaseReader`）；
    插件壳改为**显式注入**自己的适配器（`apps/plugin/main.py`），与独立壳
    （`ka_standalone/bootstrap.py`）完全对称——谁都不再是「默认壳」
    （`ka_core/plugin_initializer.py`）。
  - **结果**：`ka_core` 现在 **零 `import astrbot`、零壳 import**；独立版运行链路完全不再触碰任何
    AstrBot 代码（实跑验证：`kb_reader=InMemoryKnowledgeBaseReader`、
    `main llm=OfflineStubLLMAdapter`、配置后切 `LMStudioLLMAdapter`）。

### 测试 (Tests)

- 新增壳边界守卫 `tests/backend/test_shell_boundaries.py`：用 AST 扫描断言
  ①`ka_core` 不 import `astrbot`；②`ka_core` 不 import 任何壳；③两壳互不相识。
  架构一旦腐坏立刻红。原 `test_llm_adapter.py` / `test_notion_target.py` 随适配器改指
  `ka_plugin.adapters`；`test_standalone_bootstrap.py` 的「默认即 AstrBot」断言按新契约改为
  「默认即中立」（`pyproject.toml` 的 pytest `pythonpath` 增 `apps/plugin`）。

### 构建与工程 (Build/CI)

- `release/published-files.txt` 纳入 `apps/plugin/ka_plugin/` 各文件（`=>` 映射规则要求恰好命中
  一个文件，故逐个登记）；`apps/plugin/main.py` 的模块驱逐白名单 `_OWNED_TOPS` 增 `ka_plugin`。

## [v0.2.0] — 2026-07-11

### 新增功能 (Added)

- **统一问答入口（Phase 1–3）**：右侧「向知识库提问」成为普通 Ask、Research、引用、历史、
  Persona 与外部渠道共用的唯一对话入口。新增渠道无关的 `ConversationService`
  （`ka_core/conversation/service.py`、`prompts.py`）与领域模型 `ConversationTurn` /
  `ConversationSession`（`ka_core/domain/conversation.py`）；统一 SSE 事件流
  `stage|delta|assistant|error|done`，`assistant` 携带正文、引用、请求/实际模式、结构化召回
  范围、Research 标记、验证告警与思考摘要。新增 `POST /api/chat/turn` 与会话设置接口
  `GET|PATCH /api/chat/session/{conversation_id}`（仅允许改
  `research_enabled`/`context_enabled`/`persona_enabled`），保留 `/api/ask` 兼容
  （`ka_web/server.py`、`ka_core/api.py`）。前端主问答面板拆为消息列表、消息底标、Composer/
  设置浮层与 Research 状态组件（`ka_web/frontend/components/chat/`、`components/panels/ChatPanel.tsx`、
  `lib/api.ts`）；每条助手消息底标展示本轮实际 collection 召回范围（指定集合/全局/未召回/
  旧记录降级）。
- **上下文参与召回**：会话默认开启，用最近 12 条、最多 12,000 字符的有界历史改写出自包含检索
  意图；历史只用于改写与范围判断，**不作为事实证据**，改写失败显式降级到本轮原问题
  （`ka_core/conversation/service.py`）。
- **Research 自动编排**：输入框齿轮右侧新增 Research 开关；开启后由 Agent 自动探查范围、处理
  歧义并在现有五档中自动选择召回模式（低歧义直达、中高歧义跨轮澄清），Web 已选 collection 为
  服务器强制范围，probe/execute 均不越界；关闭时回落用户选择的 collection 与手动模式。统一服务
  保证同一会话严格串行、每轮恰好一次历史持久化，消除 Research 产生孤立/重复历史的隐患。
- **单一全局 Persona**：新增可按会话启用的全局 Persona 文本（最长 4,000 字符），仅作为 Agent
  澄清与最终合成的「风格层」注入，RAG 证据/范围/引用硬约束始终更高优先级；接入普通、Enhanced 与
  Deep 合成链（`ka_core/pipelines/answer_synthesis.py`、`enhanced_recall_orchestrator.py`、
  `deep_thinking_orchestrator.py`）。大设置页新增 Persona 编辑器（`SettingModal.tsx`）。
- **Discord 直连渠道（Phase 4）**：先定义平台无关的渠道接口 `ConversationChannel` /
  `IncomingMessage` / `ChannelStatus`（`ka_core/channels/base.py`），再实现 Discord adapter
  （`ka_core/channels/discord.py`）——仅响应私信、明确提及或回复 Bot；会话键按 guild/thread/DM
  隔离到用户×频道；`allowed_user_ids` 必填，guild/channel 白名单非空时进一步收窄；发送时显示
  typing、长 Research 用单条可更新进度消息、最终答案按句切分 ≤1,900 字符并禁用所有意外 mentions。
  Bot token 优先读 `KR_DISCORD_BOT_TOKEN`，否则读加密 secret store，API 只回 `token_present`，
  永不回显（`GET|PUT /api/channels/discord`、`PUT|DELETE /api/channels/discord/token`、
  `DiscordConfigCard.tsx`）。可选依赖 `discord.py>=2.7,<3` 登记到 extras、安装白名单与能力检测，
  缺依赖/凭据时独立版仍可启动并给出诊断（`ka_core/capabilities.py`、`pyproject.toml`）。
- **会话与消息持久化契约**：新增迁移 `021_chat_sessions.sql`（`chat_sessions` 会话开关表 +
  `chat_history.metadata` JSON 快照）；`SourceDocumentStore` ABC/SQLite/memory 同步增加会话 CRUD
  与消息 metadata，旧消息缺 metadata 时返回空对象并在底标显示「范围未知（旧记录）」
  （`ka_core/repository/source_store/{base,memory,sqlite}.py`）。

### 修复 (Fixed)

- **独立版脱 AstrBot 界面残留**（并入统一问答）：rebuild.sh 默认后端由内存测试桩改为真实独立
  应用（`--memory` 保留旧插件开发链路）；standalone 壳**恒装配**对话服务——主 LLM 经组合根惰性
  工厂 `_resolve_standalone_main_llm` 按最新持久化配置解析，设置页保存 main_llm 后**免重启**
  生效，未配置时自然语言通路回复配置指引、`/ka` 命令照常可用；`/api/auth` 增 `shell` 字段；前端
  按壳去 AstrBot 呈现（TopBar 隐藏 AstrBot 入口、登录副标题 STANDALONE、metadata 去 AstrBot
  措辞）（`ka_core/plugin_initializer.py`、`ka_web/server.py`、`TopBar.tsx`、`LoginScreen.tsx`、
  `app/layout.tsx`）。

### 架构健康 (Refactor)

- **移除独立全屏对话视图与专用接口**：删除 `AgentChatView.tsx` 全屏 Agent 视图、TopBar「对话」
  入口、独立会话 ID/视图切换状态与对应 localStorage/文案，改由统一主问答面板承载
  （`app/(console)/layout.tsx`、`TopBar.tsx`、`lib/api.ts`）；移除面板专用的
  `/api/chat/agent` 与 `/api/chat/agent/info`（能力由 `/api/chat/turn` 承接）。
- **完成 Agent 服务解耦**：旧 `ka_core/chat/agent_service.py`（`ChatAgentService`）已被
  `ka_core/conversation/` 与 `ka_core/channels/` 完全取代，删除该孤立包；`PluginInitializer` 统一
  装配 `ConversationService` 并管理 Discord 后台启动/诊断/teardown（`ka_core/plugin_initializer.py`）。

### 测试 (Tests)

- 新增迁移 021 幂等/旧库升级、会话 CRUD 与消息 metadata 回放、统一 SSE 与会话/Discord 设置接口、
  Discord 触发与用户×频道隔离、能力依赖清单等契约测试（`tests/backend/test_migration_021.py`、
  `test_source_store.py`、`test_sqlite_source_store.py`、`test_web_server.py`、
  `test_discord_channel.py`、`test_chat_agent.py`、`test_capabilities.py`）。

## [v0.1.0] — 2026-07-10

### 新增功能 (Added)

- **rebuild.sh 双壳化增强（Phase 8）**：保留原有开发链路（内存后端 tests/run_webui.py +
  next dev 热重载）不变，新增 `--standalone`（后端改为真实独立应用 `ka_standalone serve`，
  配 `--data-dir` 指定数据目录）、`--tests`（构建前先跑 `pytest tests/backend`，红了不拉起
  服务）、`--build-only`（只编译前端并同步 pages/，不杀不起任何服务，供 CI/发布前刷新产物）；
  进程清理清单纳入 `ka_standalone serve`（rebuild.sh）。

- **设置页按新功能整体重组（Phase 6）**：ConfigTab 重组为 8 分组——服务与访问 / 主模型
  （OpenAI 兼容，独立版专属）/ 对话与研究 Agent / Embedding 与检索 / 知识图谱 / Zotero 同步 /
  备份与镜像（R2+Notion）/ 系统与数据，底部保留有效配置核对。独立壳可编辑原由 AstrBot 面板
  管辖的节：`ConfigKeyPolicy` 新增 `standalone_writable`（web_console host/port/username、
  r2_sync account_id/access_key_id/bucket/阈值/间隔、notion_sync 库参数），api 按
  `attach_shell` 声明的运行壳放行（access_key_id 为 ID 类半机密仅独立壳可写；
  secret_access_key/password/api_key 任何壳仍仅环境变量）；`/api/capabilities` 暴露 `shell`
  与 `chat_agent_available`；前端半机密字段留空输入防掩码回写（ka_core/config.py,
  ka_core/api.py, ka_core/api_capabilities.py, ka_web/frontend/components/modals/SettingModal.tsx）。
- **uv 打包与一键部署（Phase 7）**：根 pyproject 定稿 `[project]` 打包——hatchling、必装依赖
  补 `cryptography`（独立版无 AstrBot 供给）、`pymilvus[milvus_lite]` 加
  `platform_system != "Windows"` 标记（Windows 装纯客户端 + 三条替代路）、extras
  `graph/local-embedding/backup/notion/all`、`knowledge-arch` 命令入口；`.python-version`
  钉 3.11；提交 `uv.lock`（144 包）；新增 `scripts/install.sh|.bat`（uv 引导、--cn 清华镜像、
  --minimal、装后自动 doctor）与 `scripts/start.sh|.bat`；README 增三平台部署文档
  （安装模型 = clone + uv sync editable，部署零 Node 依赖）（pyproject.toml, scripts/, README.md）。

- **对话 Agent 界面（Phase 5）**：独立版内置 AstrBot 使用模式的对话入口——
  自然语言消息走「agent 上下文注入（与 on_llm_request 同口径）+ research_scope_probe/
  research_execute/notion_push_note 三工具循环」，research 结果由系统直接推送给用户
  （工具回执告知模型勿复述，对齐插件后台回发模型）；`/ka` 命令与插件同一套 EventHandler
  命令体（webui 命令在独立壳被守卫）。组成：`ka_core/adapters/llm.py` 新增
  `LMStudioLLMAdapter.chat_stream`（OpenAI 流式 + tool-calls 增量聚合）、
  `ka_core/chat/agent_service.py`（事件流协议 stage/delta/assistant/error/done）、
  `ka_web/server.py` 新增 `/api/chat/agent/info` 与 `/api/chat/agent`（SSE）、前端
  `components/agent/AgentChatView.tsx`（复用登录页半调背景与报头美术）+
  layout/TopBar 的 agent ⇄ 控制台双视图切换；会话持久化复用 chat_history 表
  （ka_core/plugin_initializer.py 5.7 装配，主 LLM 支持 chat_stream 时启用，插件壳行为不变）。

- **独立版 Notion stdio 传输（Phase 4）**：`ka_standalone/notion_transport.py` 的
  `StdioNotionToolCaller` 用 mcp SDK 经 `npx -y @suekou/mcp-notion-server@1.2.x` 实现
  `NotionMCPAdapter` 的 ToolCaller 注入协议——工具契约与插件模式逐字对齐（`_unwrap` 直接解包
  mcp CallToolResult）；惰性建会话、调用失败重建会话重试一次（对齐宿主 call_tool_with_reconnect
  语义）；token 经 `KR_NOTION_API_TOKEN` 注入子进程。组合根 teardown 统一释放注入传输
  （ka_core/plugin_initializer.py）。
- **CLI 补齐 status/doctor/passwd**：`knowledge-arch status`（数据目录/存储文件/主 LLM 概览）、
  `doctor`（Python 版本、依赖 extras、npx、Windows 无 milvus-lite 原生 wheel 的三条替代路提示）、
  `passwd`（交互或 --set 更新 WebUI 登录密码，保持 0600）（ka_standalone/cli.py）。

- **独立应用薄壳 `ka_standalone/`（Phase 3）**：`python -m ka_standalone serve` 脱离 AstrBot
  启动全栈（WebUI + SQLite + Milvus + 嵌入索引）。组成：`cli.py`（serve 子命令，
  --data-dir/--host/--port，SIGINT/SIGTERM 优雅退出，Windows 事件循环兼容）、`paths.py`
  （数据目录解析：--data-dir > KR_DATA_DIR > ~/.knowledge-arch）、`config_bootstrap.py`
  （standalone_config.json 首启生成 + 随机登录密码 + 0600 权限）、`bootstrap.py`（构造壳差异
  注入件交给组合根，不建第二组合根）。
- **组合根壳差异注入点**：`PluginInitializer.__init__` 新增 keyword-only 可选参数
  `llm_adapter`/`kb_reader`/`notion_tool_caller`——缺省行为与插件壳完全一致（回归约束），
  独立壳注入 `LMStudioLLMAdapter`/`InMemoryKnowledgeBaseReader`/stdio tool_caller（Phase 4）
  （ka_core/plugin_initializer.py）。
- **主 LLM 配置节 `main_llm`**：新增 `MainLLMConfig`（base_url/model/api_key/timeout_seconds/
  max_retries；api_key 优先读环境变量 KR_MAIN_LLM_API_KEY），public dict 脱敏输出，
  CONFIG_KEY_POLICY 登记（api_key 机密不可 API 写）；独立壳在配置完整时以任意
  OpenAI-compatible 端点承担主 LLM（ka_core/config.py, ka_standalone/bootstrap.py）。

- **插件同步发布链（Phase 2）**：新增 `tools/publish_plugin.py` —— 按新 manifest
  `release/developer-files.txt` 从 monorepo 生成「插件仓库形态」源码树（apps/plugin/* 回写树根、
  剔除 monorepo 专属内容），支持 `--check-only`/`--diff-against`/`--push`（推送旧仓 developer，
  须用户批准）；`release/published-files.developer.txt` 以 `release/published-files.txt` 之名
  随树下发，保证旧仓 developer→main 现行发布机制不变。新增 `.github/workflows/plugin-parity.yml`
  （workflow_dispatch 防漂移比对）。
- **双版本线 bump**：`tools/bump_version.py` 重写为 `--target plugin|standalone`——插件线源 =
  `apps/plugin/metadata.yaml`，独立线源 = 根 `pyproject.toml [project].version`（本轮新增，
  version=0.1.0）；CHANGELOG 收口容忍无历史版本标题的新文件（tools/bump_version.py,
  pyproject.toml）。

### 架构健康 (Refactor)

- **治理文档对齐 monorepo（Phase 2）**：CLAUDE.md §0 重写为 monorepo 定位（目录图 + 双版本线
  规则）、§1.1 改为「本仓 main 唯一真相源 + publish_plugin 同步链」、§5 命令表更新（含
  ka_web/frontend 正确路径）；ARCHITECTURE.md §2 增加「一核两壳」对照表（两壳差异只允许表现为
  组合根注入参数）；docs/GIT_WORKFLOW.md 顶部增加 monorepo 迁移说明（CLAUDE.md, ARCHITECTURE.md,
  docs/GIT_WORKFLOW.md）。

### 构建与工程 (Build/CI)

- `tests.yml` 触发分支改为 `[main, developer]` 双仓共用；`published-verify.yml` 加
  `github.repository` 守卫防止在 monorepo 误跑发布树校验；`tests/backend/test_config.py`
  的 `_conf_schema.json` 路径改为双布局兼容（monorepo apps/plugin/ 与旧仓根）。

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
