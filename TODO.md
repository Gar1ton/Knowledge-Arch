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
- [x] P2 · 设置与模型：`SettingSpec`、`SettingsStore`、`legacy_keys`；`ProviderRegistry`、
  `ModelRouter`；`LLMAdapter` 改造。
  - [x] P2.1 `kacore/settings/`：`SettingSpec` + `SPECS`（flow.* 节点项与 prefs.* 应用项，一项只登记一次，
    含 kind/default/home/tier/consequence/options/bounds/label_key/portable）；`get_schema(home)`。
  - [x] P2.2 `SettingsStore`（`settings.json` 原子写 + 校验 + Secret 只存引用、值进加密库）与
    `to_config_raw()` 桥：既有 `XxxConfig` dataclass 保留，改从 SettingsStore 读值；删除
    `ConfigKeyPolicy` 表与 `RuntimeConfigStore`。
  - [x] P2.3 `legacy_keys.translate()`：旧插件配置 / `runtime_config.json` → 新键（RENAMED、TO_PROVIDER、
    其余丢弃并报告）；数据目录里只有旧文件时首启自动迁移。
  - [x] P2.4 `kacore/providers/`：`ProviderProfile` / `ModelRef`、`ProviderRegistry`（预设、增删改、
    测试连接、获取模型列表，错误按原因分类）、`OpenAICompatChat`、`AnthropicChat`（官方 SDK）、
    `ModelRouter.client_for(role)`（chat / research / graph，后两者为空跟随 chat，按调用现取）。
  - [x] P2.5 组合根接线：三个角色经 ModelRouter；外部 Embedding 来源改为 ModelRef（密钥从连接读）；
    `LMStudioLLMAdapter` 并入 `OpenAICompatChat`；R2 备份的可携配置改用新键。
  - [x] P2.6 `EncryptedSecretStore` 的 master.key 改存系统钥匙串（keyring），不可用时回退文件。
  - [x] P2.7 HTTP：`GET /api/settings/schema?home=`、`PATCH /api/settings`、`/api/providers*`
    （含 `POST /api/providers/{id}/test` 与模型列表）；旧 `/api/config/update` 映射到新键（P4 前过渡）。
  - [x] P2.8 `ka_server` 读取 prefs.diagnostics（日志级别、保留天数）。
  - [x] P2.9 测试全绿 + CHANGELOG。
  - 验证：全量 1246 passed / 4 skipped / 0 failed；ruff、mypy 通过（P2 未改前端）。新增
    `test_settings.py`（27）、`test_providers.py`（25）、`test_settings_api.py`（8）。
  - 落点调整：旧键翻译放在 `kacore/settings/legacy_keys.py`（计划路径），只做白名单翻译，不在
    代码里列出旧宿主专属键名（未识别的一律丢弃并报告），因此不违反 P1 的 grep 守卫。
  - 行为说明：research / graph 角色改为经 ModelRouter 现取，改选模型、替换密钥无需重启；
    master.key 在有系统钥匙串时迁入钥匙串——换机用 R2 恢复后需要重新填写各连接的密钥。
  - 修正：P1 提交的 CHANGELOG 有一行写出了已删除组件名（守卫测试在其后才写入），本阶段已改正。
- [x] P3 · 外部能力：`NotionRestClient`、uv extras、`system_prompt`、旧数据导入器。
  - [x] P3.1 `kacore/adapters/notion_rest.py`：aiohttp 直连 Notion REST（`Notion-Version: 2026-03-11`），
    token 每次请求从 `flow.notion.token` 现取；公开方法与 `NotionMCPError` 语义沿用旧 adapter；
    3 req/s 频控，401 / 403 / 404 / 429（按 Retry-After 重试）/ 5xx 分类；组合根改用新客户端，
    删除 `notion_mcp.py` 与客户端注册表依赖；重写 `docs/NOTION_SYNC.md`。
  - [x] P3.2 `kacore/extras/`：可选组件经 `uv pip install --target <data>/extras/<pack>` 安装
    （先装到临时目录，成功后原子换入，失败回滚）；启动时把已装组件加入 `sys.path`；
    uv 位置可配置（`--uv` / `KA_UV`）。
  - [x] P3.3 `api.ask` 读取 `flow.answer.system_prompt`（default / graph / fulltext 路径，非空时放在
    RAG 约束之前；deep / enhanced 不变）。
  - [x] P3.4 `kacore/legacy_import/`：复制旧插件数据 → 改写绝对路径 → 迁移 → 翻译旧配置与机密；
    staging + 软重启时换入、失败回滚；`/api/legacy-import*` 接口。
  - [x] P3.5 测试全绿 + CHANGELOG。
  - 验证：全量 1282 passed / 4 skipped / 0 failed（前端 TS 纯函数用例在装好 node_modules 后不再 skip；
    skip 仍为缺 torch / lightrag / boto3 的可选用例）；ruff、mypy 通过。新增 `test_notion_rest.py`（23）、
    `test_extras.py`（17）、`test_answer_system_prompt.py`（6）、`test_legacy_import.py`（14）；
    `test_notion_preflight.py` 按 REST 语义重写，`test_notion_adapter.py` 删除。手动冒烟：
    `ka_server` 起服务 → `POST /api/components/r2/install` 真实调用 uv 装上 boto3 → 软重启后组件激活。
  - 落点调整：
    - 组件安装不用约束文件钉住运行时版本（实测 botocore 要求 `python-dateutil<3`，钉死会无解）；
      改为「组件目录追加在 sys.path 末尾、运行时自带的包优先」。
    - 本地 Embedding 组件显式带上 `torch` 并传 `--torch-backend auto`（uv 按显卡选 CPU / CUDA 构建）；
      Milvus 属核心依赖（随安装包），不在组件页出现，但保留经同一接口补装的修复路径。
    - 旧数据导入只允许导入空库；换入与 R2 恢复同一套 pending → applied → commit | rollback 协议，
      组合根新增 `initialize_or_rollback()`（软重启与进程首启共用）。
    - HTTP 新路由放在 `web/desktop_routes.py`（`web/server.py` 已超红线，不再往里加）。
    - 服务重启的提示文案由「插件」改为「服务」（P4 的一部分，后端消息先改）。
- [ ] P4 · 前端：`backend.ts` 与 token；`NodePanel` 与 4 个新节点；`PreferencesWindow`；
  删除 `LoginScreen` 与旧宿主配置弹窗。
- [ ] P5 · 桌面壳：Tauri 工程、sidecar、单实例、原生对话框、托盘、Python 打包脚本；本地出包。
- [ ] P6 · 发布：Actions 三平台矩阵、签名与公证、updater、文档改写、v2.0.0（远端操作另行批准）。
