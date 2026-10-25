# CHANGELOG

> 插件时代（v0.x – v1.2.0）的变更记录完整保留在本仓库首个提交 `89b251a` 中（`git show 89b251a:CHANGELOG.md`），不再随 v2 维护。

## [v2.0.0-dev.3] — 2026-10-25

### 新增 (Added) — P3 外部能力

- **`kacore/adapters/notion_rest.py`**：`NotionRestClient` 直连 Notion REST API（`Notion-Version: 2026-03-11`），
  token 每次请求从 `flow.notion.token` 现取；3 req/s 全局频控；401 / 403 / 404 / 429 / 5xx 分类成带中文提示
  的 `NotionMCPError`（错误码 `unauthorized` / `restricted_resource` / `object_not_found` / `rate_limited` /
  `server_error` / `network_error` / `not_configured` / `incompatible_response`）；429 按 Retry-After、5xx 与
  网络错误按指数退避有界重试；超过 100 个子块的追加自动分批；批次前用 `GET /users/me` 校验 token。
  公开方法与错误语义沿用旧适配层，同步语义（preserve / strict、QA outbox）不变。
- **`kacore/extras/`**：可选组件（本地 Embedding + rerank：sentence-transformers + torch；知识图谱：lightrag-hku；
  R2 备份：boto3）经 `uv pip install --no-config --target <data>/extras/<pack> --python <当前解释器>` 安装；
  先装进临时目录、成功后原子换入，失败或被占用时回滚、旧版本保留；标记文件记录 Python 版本，版本不符的组件
  不激活。`ka_server` 新增 `--uv`（或环境变量 `KA_UV`），启动时在组合根之前把已装组件加入 `sys.path`。
  HTTP：`GET /api/components`、`POST /api/components/{key}/install`、`DELETE /api/components/{key}`；
  既有 `/api/dependencies/install` 改走同一安装器。
- **回答系统提示**：`api.ask` 读取 `flow.answer.system_prompt`（每次调用现取），非空时放在 RAG 约束之前，
  覆盖 default / graph_mixed / graph_only / fulltext；为空时与旧版「人格关闭」完全一致；deep / enhanced 不变
  （`answer_synthesis.with_user_system_prompt`，门面 mixin `kacore/api_answer.py`）。
- **`kacore/legacy_import/`**：旧版插件数据导入器。选择旧安装根目录（也接受 `data/` 或插件数据目录）→ 预览 →
  复制（SQLite 用 backup API，旧目录只读）→ 改写 `documents.file_path` / `zotero_attachments.resolved_path`
  的绝对路径前缀 → 在副本上跑迁移 → 翻译旧配置（叠加 `runtime_config.json`）并解密旧机密 → 软重启时在连接
  数据库前换入，被替换的内容进 rollback 目录；初始化成功提交、失败回滚。只允许导入空库。返回 attention
  （需要用户确认的事项）与 dropped（丢弃的旧键）。HTTP：`POST /api/legacy-import/inspect`、
  `POST /api/legacy-import`、`GET /api/legacy-import`、`DELETE /api/legacy-import`（门面 `kacore/api_legacy_import.py`，
  路由 `web/desktop_routes.py`）。

### 改进 (Changed)

- 组合根：Notion 目标注入 `NotionRestClient`；`AppInitializer(settings, data_dir, *, extras=None)`；初始化前应用
  待换入的旧数据导入、结束时提交；新增 `initialize_or_rollback()`，失败时撤回本轮 R2 恢复 / 旧数据导入并重试
  （软重启与进程首启共用，首启撤回后服务照常起来）。
- `OptionalDependency` 增加 `installer_args` 与 `component`；依赖状态多出 `component` 字段。
- 服务软重启的提示与运行事件文案由「插件」改为「服务」。
- 文档：重写 `docs/NOTION_SYNC.md`（配置步骤、错误码对照、请求消耗）；`kacore/README.md`、`web/README.md`、
  `docs/PROJECT_STRUCTURE.md` 补充新目录。

### 移除 (Removed)

- `kacore/adapters/notion_mcp.py`（MCP 客户端注册表依赖）与 `tests/backend/test_notion_adapter.py`。

### 测试 (Tests)

- 新增 `test_notion_rest.py`、`test_extras.py`、`test_answer_system_prompt.py`、`test_legacy_import.py`（含经组合根
  软重启的端到端导入）；`tests/mocks/notion_tools.py` 改为把 REST 请求映射回操作名的 transport，同步层既有用例
  场景不变；`test_notion_preflight.py` 按 REST 语义重写。全量 1282 passed / 4 skipped。

## [v2.0.0-dev.2] — 2026-10-25

### 新增 (Added) — P2 设置与模型

- **`kacore/settings/`**：`SettingSpec` 登记表（80 项，`flow.*` 节点项 + `prefs.*` 偏好项，替代
  `_conf_schema.json` 与 `ConfigKeyPolicy` 表）；`SettingsStore`（`settings.json` 原子写、逐项校验、
  secret 只存 `{"ref"}` 引用、明文进加密库、批量写先校验后落盘、可携文档导出 / 合并）；`bridge.py`
  按 target 桥接回既有 `Config`（local/external embedding 指纹逐字节不变）；`legacy_keys.py`
  把旧插件配置 / `runtime_config.json` 翻译为新键、连接与密钥，并报告丢弃项与需确认项。
- **`kacore/providers/`**：`ProviderProfile` / `ModelRef`；`OpenAICompatChat`（并入原
  `LMStudioLLMAdapter`，新增列模型 / ping / 错误分类，4xx 不重试）；`AnthropicChat`（官方 `anthropic`
  SDK，不发 sampling 参数，`refusal` → content_filter，官方地址上的当前代模型默认开启服务端拒答回退）；
  `ProviderRegistry`（预设 10 个、增删改、密钥只显示后 4 位、被引用时拒删、测试连接先列模型再 ping）；
  `ModelRouter` / `RoleClient`（chat / research / graph，后两者跟随 chat，按调用现取分配与密钥）。
- **HTTP**：`GET /api/settings/schema?home=`、`PATCH /api/settings`、`GET|POST /api/providers`、
  `PATCH|DELETE /api/providers/{id}`、`POST /api/providers/{id}/test`、`POST /api/providers/{id}/models`
  （业务门面在新 mixin `kacore/api_settings.py`）。
- **主密钥**：`EncryptedSecretStore` 优先把 master.key 存进系统钥匙串（keyring），已有文件在回读校验
  成功后迁入；无钥匙串（如 WSL / 无桌面会话）回退文件。
- **`ka_server`**：启动时打开 SettingsStore；数据目录只有旧 `runtime_config.json` 时首启自动翻译
  （旧文件保留）；日志级别与保留天数读 `prefs.diagnostics.*`（按天滚动）。
- **向量缓存上限**：`prefs.storage.embedding_cache_mb`（0 = 不限），超出按写入顺序淘汰。

### 改进 (Changed)

- 组合根 `AppInitializer(settings, data_dir)`；三个 LLM 角色都经 ModelRouter；外部 Embedding 来源改为
  连接上的模型、密钥从连接读取（本机服务可无密钥，不再发送空 Bearer 头）。
- 旧 `/api/config/update` 改为映射到 SettingSpec 写入（P4 前前端过渡用）。
- R2 快照的可携配置改为 `config/portable_settings.json`（新键，不含机密、本机路径与 R2 凭据）；
  恢复时与当前 `settings.json` 合并并随其它恢复项一起可回滚；插件版快照的 `portable_config.json`
  经旧键翻译后恢复。
- `requirements.txt` 新增 `keyring`、`anthropic`。

### 移除 (Removed)

- `kacore/runtime_config.py`（`RuntimeConfigStore`）、`ConfigKeyPolicy` 表及派生函数、
  `GraphConfig.lightrag_llm_*` 与 `DeepThinkingConfig.llm_*`（由「模型分配」接替）、
  `LMStudioLLMAdapter`。

### 修正 (Fixed)

- P1 CHANGELOG 中一行写出了已删除组件的文件名，已改为不含旧宿主名的描述。

### 测试 (Tests)

- 新增 `test_settings.py`、`test_providers.py`、`test_settings_api.py`；`tests/mocks/settings_helpers.py`
  让组合根测试沿用旧式配置 dict（经真实翻译器生成 SettingsStore）。全量 1246 passed / 4 skipped。

## [v2.0.0-dev.1] — 2026-10-25

### ⚠️ 破坏性变更 (Breaking) — P1 后端脱钩

- **删除宿主薄壳与专属模块**：`main.py`、`kacore/main.py`、`kacore/event_handler.py`（`/ka` 命令）、
  `kacore/research_skill.py`（对话 research 工具与 RAG 注入）、宿主托管的 Embedding 来源、
  `kacore/repository/kb_reader/`（宿主知识库回退通道）、`metadata.yaml`、`_conf_schema.json`。
- **组合根**：`kacore/plugin_initializer.py` → `kacore/app_initializer.py`（`AppInitializer(raw_config,
  data_dir)`，无宿主 context）；不再持有 Web 控制台；teardown 后 `api` 置空，软重启窗口内 HTTP 回 503。
- **新增 `ka_server/` 进程薄壳**：`python -I -m ka_server serve --port 0 --data-dir --parent-pid`；
  `KA_TOKEN` 只经环境变量；`AppPaths`（platformdirs 三平台目录）；数据目录独占锁
  （`DataDirLockedError`，退出码 3）；`KA_READY port=…`；`/api/shutdown`、SIGTERM/SIGINT、父进程消失
  三种退出均有序 teardown（释放 Milvus Lite 锁）；stderr / 滚动日志文件固定 INFO 级。
- **`web/server.py`**：密码登录与会话 cookie 删除（`/api/auth|login|logout` 下线），改为
  `Authorization: Bearer <token>`（下载类 GET 可用 `?access_token=`）；新 CORS 中间件只放行
  `tauri://localhost` 与 `http://tauri.localhost`（开发期 `KA_DEV_ORIGINS` 追加），其余 Origin 403；
  不再托管静态文件；新增 `/api/health`、`/api/shutdown`；`build_app` 支持 `api_provider`（随软重启现取门面）。
- **检索**：宿主知识库回退引擎删除；`vector_db.backend` 取值改为 `milvus | lexical`（旧值 `astr`
  读取时归一为 lexical）；`actual_mode` 不再出现宿主值；deep 回退模式改名 `lexical_deep_fallback`。
- **LLM**：`LLMAdapter(client=None, timeout_seconds)` 改为注入 chat client（`ChatClient` 协议；
  reasoning-only 重试一次、超时闸与离线占位保留）；宿主人格读取与 `ask(persona_enabled=…)` 删除；
  `ask` 段的 agent/research/persona 开关与 `web_console` 段删除。
- **Embedding**：来源只剩 `local | external`，旧值归一为 local → 指纹变化走既有「索引不兼容 → 重建」；
  local/external 指纹与缓存 namespace 逐字节不变（冻结值测试保留）。
- **删除插件发布链**：`pages/`、`tools/sync_frontend.py`、`tools/watch_frontend.sh`、
  `tools/build_published_tree.py`、`release/`、`bump_version.py`、`rebuild.sh`、`tests/run_webui.py`、
  `.github/workflows/published-verify.yml`；`.gitattributes` 只保留行尾规范化。

### 前端 (Frontend)

- 删除宿主配置弹窗组件及其入口、i18n 键（原属 P4，因其后端接口在本阶段删除而提前）；
  向量库候选改为 `milvus | lexical`（lexical 可选）；修既有 ESLint 报错（`ModelRuntimePanel` effect 内
  同步 setState、未用常量）。

### 文档与治理 (Docs)

- README / CLAUDE / ARCHITECTURE / `docs/PROJECT_STRUCTURE.md` / `docs/GIT_WORKFLOW.md` / 目录 README
  改为桌面应用结构；删除 `docs/FRAMEWORK_GUIDE.md`（模板说明，已被 ARCHITECTURE/CONVENTIONS 覆盖）、
  `docs/Claude_Design_Reconfiguration/`、`docs/research_flow.md`、`deliverables/`。
- TODO / CHANGELOG 的插件期历史移出，保留在首个提交 `89b251a`。
- `requirements.txt` 显式加入 `cryptography`（原由宿主提供）与 `platformdirs`。
- `migrations/001_source_store.sql` 仅改一行注释（runner 按文件名记账，schema 不变）。

### 测试 (Tests)

- 新增 `test_ka_server.py`（目录、锁、退出信号、就绪协议、子进程端到端）、
  `test_no_legacy_host_references.py`（守卫）、`tests/mocks/static_retriever.py`（替代内存 KB 替身）；
  `test_embedding_wiring.py` 只保留通用不变量；`test_llm_adapter.py` 改测 chat client 契约。
- 既有三项失败修复：`bad19` 桩改为响应体解码失败（传输层异常本应重试）；两项 torch device 用例缺 torch 时 skip。
- 全量 1190 passed / 4 skipped；ruff、mypy、前端 lint / tsc / build 通过。

## [v2.0.0-dev.0] — 2026-10-25

### 基线 (Baseline)

- **Knowledge Arch 独立仓库建立**：从插件仓库 developer `8e6f1af`（v1.2.0）用 `git archive`
  导出全部已提交文件（826 个；通过临时 `info/attributes` 关闭发布用 `export-ignore`，
  使 tests/docs/tools/web/frontend 等开发资产一并导入），全新 git 历史，首个提交即该快照。
  工作区未提交改动未带入。
- 测试基线：1289 passed / 2 skipped / 3 failed（既有：`test_embedding.py` 两项 device 用例
  缺 torch，`test_external_embedding_contract.py::...[bad19]` 测试桩问题）；skip 为缺 lightrag 与 boto3。
- `TODO.md` 新增 v2.0.0 P0–P6 路线图。
