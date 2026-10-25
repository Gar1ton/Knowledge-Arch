# CHANGELOG

> 插件时代（v0.x – v1.2.0）的变更记录完整保留在本仓库首个提交 `89b251a` 中（`git show 89b251a:CHANGELOG.md`），不再随 v2 维护。

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

- 删除宿主配置弹窗 `AstrBotModal.tsx` 及其入口、i18n 键（原属 P4，因其后端接口在本阶段删除而提前）；
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
