# TODO

> 插件时代（v0.x – v1.2.0）的计划记录完整保留在本仓库首个提交 `89b251a` 中（`git show 89b251a:TODO.md`），不再随 v2 维护。

## v2.0.0 Knowledge Arch 独立桌面版（🚧 in progress）

- 计划：插件仓库工作区的 `docs/plans/knowledge-arch-standalone.html`（已批准，决策定稿；
  未入本仓库）。代码来源为插件仓库 developer `8e6f1af`（v1.2.0），本仓库为全新 git 历史；
  远端操作（push/tag/PR）另行批准。
- 决策：Tauri 2 + 现有 React 静态导出；LLM 角色集中在「模型分配」节点；删除宿主对话的
  RAG 注入与 research 工具（`research_skill.py` 删除）；Notion 直连 REST API（token 在节点填写）。
- 每个 Phase：先更新本节 → 写代码 → 测试全绿 → CHANGELOG 追加条目。
- [x] P0 · 基线：`git archive 8e6f1af` 导出全部已提交文件（826 个，绕过发布用 export-ignore）
  到本仓库并 `git init -b developer`；`.venv` 按插件 venv 的包版本复刻，`npm ci` 装前端依赖。
  基线：1289 passed / 2 skipped / 3 failed（既有三项：两项缺 torch 的 device 用例与
  `bad19` 测试桩；skip 为缺 lightrag 与 boto3）。
- [x] P1 · 后端脱钩：`ka_server` 薄壳与 `AppInitializer`；删除 `main.py`、`EventHandler`、
  astr Embedding、`kb_reader`、`research_skill.py`。验收：除 `kacore/legacy_import` 外
  对旧宿主框架名做 `grep -ri` 无结果。
  - [x] P1.1 删除宿主薄壳与专属模块：`main.py`、`kacore/main.py`、`kacore/event_handler.py`、
    `kacore/research_skill.py`、宿主托管的 Embedding 来源（`embedding/` 下两个适配模块）、`repository/kb_reader/`、
    `metadata.yaml`、`_conf_schema.json`（P2 由 SettingSpec 接替）及对应测试与替身。
  - [x] P1.2 组合根 `PluginInitializer` → `AppInitializer`（`kacore/app_initializer.py`）：去掉宿主
    context 与配置回写；不再持有 Web 控制台；配置暂沿用 `runtime_config.json`（P2 换 SettingsStore）。
  - [x] P1.3 `ka_server` 薄壳：`python -I -m ka_server serve --port 0 --data-dir --parent-pid`；
    token 只经 `KA_TOKEN` 环境变量；`AppPaths`（platformdirs）；`KA_READY port=…`；
    父进程消失 / `POST /api/shutdown` / SIGTERM 均有序 teardown（释放 Milvus Lite 锁）。
  - [x] P1.4 `web/server.py`：Bearer token 中间件 + CORS（仅 `tauri://localhost`、
    `http://tauri.localhost`，开发期经 `KA_DEV_ORIGINS` 追加）；删除密码登录/会话与静态托管；
    删除 `pages/`、`tools/sync_frontend.py` 等插件期构建同步与发布树工具。
  - [x] P1.5 检索：删除 `kb_reader` 与宿主 KB 回退引擎；`vector_db.backend` 的旧值归一为
    `lexical`；`actual_mode` 不再出现宿主值，deep 回退模式改名 `lexical_deep_fallback`。
  - [x] P1.6 LLM：`LLMAdapter` 去掉宿主调用链，改为注入 chat client（P2 由 ModelRouter 提供，
    未配置时沿用离线占位）；删除宿主人格读取（P3 由 `flow.answer.system_prompt` 接替）与
    ask 的 agent/research/persona 开关。
  - [x] P1.7 Embedding：删除 astr 来源（config / factory / 指纹 / capabilities）；旧值归一为
    local，指纹随之变化 → 走既有「索引不兼容 → 重建」路径。
  - [x] P1.8 文案与文档：日志名、诊断与依赖安装提示、requirements 注释、README / CLAUDE /
    ARCHITECTURE / docs / `.agents` skill；前端删除宿主配置弹窗及其 i18n 与入口（P4 的子集，
    因其后端接口在本阶段删除）；TODO / CHANGELOG 的插件期历史移出（保留在首个提交）。
  - [x] P1.9 守卫测试：除 `kacore/legacy_import` 外仓库文本不得出现宿主框架名。
  - [x] P1.10 验收：全量 pytest（仅允许基线三项既有失败）、ruff 无新增、前端 lint/tsc/build、grep。
  - 验证：全量 1190 passed / 4 skipped / 0 failed（基线三项既有失败已修：`bad19` 桩改为响应体
    解码失败；两项 device 用例缺 torch 时 skip）；`ruff check .`、mypy、前端 lint / tsc / build
    全部通过；仓库对旧宿主名 grep 无结果（含守卫测试）。`ka_server` 子进程端到端：
    KA_READY、401/403、第二实例退出码 3、`/api/shutdown` 与父进程消失均有序 teardown。
  - 例外记录：`migrations/001_source_store.sql` 只改了一行注释（为满足 grep 验收）；runner 按文件名
    记账、不校验内容，schema 与旧库兼容性不变。
  - 过渡期状态：对话模型未配置（LLMAdapter 走离线占位，P2 接 ModelRouter）；Notion 同步不可用
    （P3 接 REST）；前端仍是登录页 + cookie 调用，后端已改 Bearer token（P4 改 backend.ts）。
  - 待决：`.agents/skills/operate-knowledge-arch`（外部 agent skill，1.6k 行）依赖已删除的
    用户名/密码登录，连不上 token 化的桌面后端。去留与鉴权方式需用户决定（未在计划内）。
- [ ] P2 · 设置与模型：`SettingSpec`、`SettingsStore`、`legacy_keys`；`ProviderRegistry`、
  `ModelRouter`；`LLMAdapter` 改造。
- [ ] P3 · 外部能力：`NotionRestClient`、uv extras、`system_prompt`、旧数据导入器。
- [ ] P4 · 前端：`backend.ts` 与 token；`NodePanel` 与 4 个新节点；`PreferencesWindow`；
  删除 `LoginScreen` 与旧宿主配置弹窗。
- [ ] P5 · 桌面壳：Tauri 工程、sidecar、单实例、原生对话框、托盘、Python 打包脚本；本地出包。
- [ ] P6 · 发布：Actions 三平台矩阵、签名与公证、updater、文档改写、v2.0.0（远端操作另行批准）。
