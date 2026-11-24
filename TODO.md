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
- [x] P4 · 前端：`backend.ts` 与 token；`NodePanel` 与 4 个新节点；`PreferencesWindow`；
  删除 `LoginScreen` 与旧宿主配置弹窗。
  - [x] P4.1 `lib/backend.ts`：壳里 `invoke("backend_info")` 取 `{base_url, token}`（`backend://ready` 后丢弃缓存），
    开发期走 `/api/*` 代理 + `NEXT_PUBLIC_KA_TOKEN`；所有请求 Bearer，下载 / PDF.js 用 `?access_token=`；
    删除 `LoginScreen`、cookie 与 `/api/auth|login|logout` 客户端。
  - [x] P4.2 `components/flow/NodePanel.tsx`（schema 驱动、PATCH 写入、按 consequence 提示）+ `SettingInput`
    （含 secret 后 4 位、model_ref 连接 + 模型两级选择）；`model.ts` 新增模型供应商 / 模型分配 / Notion / R2 节点，
    原 sync 节点拆成 Notion 与 R2（操作区迁自旧设置弹窗）；`ProvidersPanel`（测试连接、获取模型列表、增删改）。
    后端能力快照同步新增 `models` / `providers` / `notion` / `r2` 四个环节、删除 `sync`。
  - [x] P4.3 `PreferencesWindow` 六组（通用 / 外观 / 存储 / 本地模型 / 组件 / 诊断），替代 SettingModal；
    ModelsModal 并入「本地模型」，WorkflowModal 改为主窗口「数据流」页；`set_*` 文案 80 项（zh / en）；
    界面「重启插件」统一为「重启服务」。
  - [x] P4.4 欢迎页（导入旧版插件数据 / 从空库开始）与崩溃页（最后几行日志、重启服务、打开日志目录）；
    后台任务完成在窗口不在前台时发系统通知。
  - [x] P4.5 测试 + CHANGELOG。
  - 验证：全量 1286 passed / 4 skipped；ruff、mypy、前端 lint / tsc / build 通过；新增
    `test_settings_i18n.py`（每个 SettingSpec 标签都有 zh / en）。浏览器冒烟（真实后端 + next dev，Playwright）：
    欢迎页 → 从空库开始 → 数据流 11 个节点 → 添加连接（密钥显示 ••••1234、测试连接报「连接被拒绝」）→
    模型分配选连接 + 填模型名保存后节点转绿 → Notion token 保存后显示 ••••9876 → 偏好设置各组；
    欢迎页导入一份构造的旧版数据，服务自动重启换入，结果页列出需要确认的事项。
  - 落点调整：
    - 新文案集中在 `lib/i18nDesktop.ts`（`lib/i18n.ts` 已超红线），合并进 zh / en 表。
    - Zotero 节点保留专用面板（访问方式切换、密钥两段式换号、探针），保存时把旧「段.键」映射到 `flow.zotero.*`；
      其余节点全部由 schema 渲染。
    - 偏好设置里 owner=shell 的项同时写后端与壳（`set_shell_pref`）；数据目录只交给壳保存（重启应用生效）。
      owner=frontend 的语言 / 明暗 / 配色以 settings.json 为准，后端就绪后同步到本机界面。
    - 前端不再调用 `/api/config/update`；后端路由暂留，因为外部 agent skill 仍依赖它（见下方待决项）。
    - 删除无引用的 `Rail.tsx`、`PerfPanel.tsx`、`NotionSyncStatus.tsx`。
- [x] P5 · 桌面壳：Tauri 工程、sidecar、单实例、原生对话框、托盘、Python 打包脚本；本地出包。
  - [x] P5.1 `desktop/src-tauri`：`lib.rs`（单实例只聚焦已有窗口；菜单「偏好设置…」⌘, / Ctrl+,；托盘：显示窗口 /
    偏好设置 / 退出，`prefs.general.close_to_tray` 时关窗隐藏到托盘；macOS 点 Dock 图标恢复窗口；命令
    backend_info / backend_state / restart_backend / pick_folder / open_log_dir / notify / set_shell_pref /
    get_shell_prefs），`prefs.rs`（壳偏好 `shell.json`，数据目录与日志目录与 platformdirs 一致；开机启动经 autostart 插件）。
  - [x] P5.2 `sidecar.rs`：256 位随机 token 只经 `KA_TOKEN`；`python -I -m ka_server serve --port 0 --parent-pid
    --uv [--data-dir]`；读 `KA_READY`（60 s 超时）；运行中退出 / 超时 → `backend://exited`（带日志尾）；就绪 →
    `backend://ready`；退出时 `POST /api/shutdown`，10 s 后强制结束；数据目录被占用（退出码 3）给出专门提示。
  - [x] P5.3 `desktop/scripts/bundle_python.py`：uv 下载 python-build-standalone 3.12 → `resources/python/`，装
    requirements.txt、去掉 pip；源码 → `resources/ka/`；site-packages 写 `knowledge_arch.pth`（相对路径）；uv 放在
    解释器旁；`out/` → `resources/frontend/`（frontendDist）；预编译 .pyc；`-I` 导入冒烟。torch 不进安装包。
  - [x] P5.4 本地出包（Linux）：`npx tauri build --bundles deb` 得到 268 MiB 的 `.deb`；macOS / Windows 包需在各自平台
    或 CI 上打（P6 矩阵）。
  - 验证：`cargo test`（6）/ `cargo clippy` / `cargo fmt --check` 通过；新增 `test_bundle_python.py`（3）。
    Xvfb 中实测：`tauri dev` 与安装后的 `.deb` 都能拉起后端并显示欢迎页（真实 WebKitGTK、`tauri://localhost`
    Origin 通过 CORS）；第二个实例立即退出；杀掉后端 → 崩溃页显示日志尾，点「重启服务」后新进程就绪、界面恢复；
    「文件 › 退出」→ 后端日志 `shutdown: requested`、无残留进程；壳被 SIGTERM 时后端经 `--parent-pid` 退出。
  - 落点调整：
    - 菜单与托盘文案用中文（与默认界面语言一致）；Linux / Windows 用「文件 / 编辑」菜单，macOS 用应用菜单。
    - 不用 Tauri shell 插件起 sidecar：解释器路径与参数完全由壳决定，用 std::process 即可；/api/shutdown 用最小
      HTTP 请求实现，不引入 HTTP 客户端依赖。
    - `KA_SIDECAR_PYTHON` 可指定别的解释器（排障 / 开发用）；debug 构建自动给后端加 `--dev-origin`。
    - 前端崩溃页日志框默认滚到最后。
- [x] P6 · 发布：Actions 三平台矩阵、签名与公证、updater、文档改写、v2.0.0（远端操作另行批准）。
  - [x] P6.1 `.github/workflows/desktop-release.yml`：macOS（arm64 / x64）、Windows、Linux 矩阵出包；签名 / 公证 /
    updater 由仓库 secret 驱动，缺 secret 时产出未签名包（不生成、不伪造任何证书或密钥）。
  - [x] P6.2 updater：Cargo feature `updater`（tauri-plugin-updater），启动时按 `prefs.general.check_updates` 检查，
    界面提示安装并重启；发布配置脚本把公钥 / 端点写进 tauri.conf.json。
  - [x] P6.3 macOS：资源里的 Mach-O 逐个签名（hardened runtime + entitlements），否则无法公证。
  - [x] P6.4 版本 v2.0.0（`ka_server.APP_VERSION`、`tauri.conf.json`、`Cargo.toml`、两个 `package.json`）+ 一致性测试。
  - [x] P6.5 按实际功能重写 README；`docs/RELEASE.md` 列出所需 secret 与发布步骤；CHANGELOG。
  - [x] P6.6 Developer CI：前端 Node 22；新增 desktop 作业（`cargo fmt --check`、两套 feature 的 clippy、`cargo test`）。
  - 验证：`pytest` 1292 passed / 4 skipped；ruff / mypy；前端 lint / tsc / build；`cargo fmt --check`、
    `cargo clippy -D warnings`（默认与 `--features updater`）、`cargo test` 6 项；`test_release_config.py`
    覆盖无 secret / 有 secret 两种配置与版本一致性；两份 workflow YAML 可解析。
  - 未验证（需要仓库 secret 与远端 runner）：真实签名、公证、updater 端到端；三平台 CI 出包需在推送后手动触发确认。
  - 落点调整：
    - 空字符串的 secret 也会被 tauri CLI 当作「要签名 / 公证」，所以流水线只把非空 secret 导出到环境；公证需
      Apple ID 三项齐全，否则只签名。
    - macOS x64 用 `macos-15-intel` runner（`macos-13` 已退役）。
    - 未启用 updater 的构建里，偏好设置 › 通用的「检查更新」显示「此版本未启用自动更新」。
- [x] P7 · 界面打磨：默认英语、界面缩放、欢迎页沿用旧登录页视觉、英文字体 PT Sans。
  - [x] P7.1 默认界面语言 `en`（前端 `kr-lang` 缺省值、`<html lang>` 随语言同步、i18n context 默认、
    `prefs.general.language` 默认值）；已选中文的用户不受影响。
  - [x] P7.2 界面缩放：`prefs.appearance.ui_scale`（0.8–1.5，档位 80/90/100/110/125/150%），`lib/uiScale.ts` 经
    Tauri `setZoom` 应用（capability 加 `core:webview:allow-set-webview-zoom`）；Ctrl/⌘ + / − / 0；本机缓存首帧套用。
  - [x] P7.3 `components/desktop/LandingShell.tsx`：v1 登录页的网点建筑背景 + Bitcount 字标 + 流光卡片；欢迎页、
    启动画面、崩溃页共用；`styles/desktop.css` 删去旧整窗卡片样式。
  - [x] P7.4 `--font-sans` 改为 PT Sans（next/font 自托管 400 / 700 + 斜体），中文回落 PingFang / 微软雅黑 / Noto CJK。
  - 验证：`pytest` 1292 passed / 4 skipped；ruff / mypy；前端 lint / tsc / build；Playwright 截图确认欢迎页
    （宽 / 窄屏）、`lang=en`、PT Sans 生效、无页面错误。
  - 未验证（本机无 Rust 工具链）：壳内缩放与快捷键、`cargo test`；待 Windows 包实测。
  - 落点调整：
    - 不用 CSS `zoom`：会让 PDF.js 与数据流图的指针坐标错位；浏览器开发期交给浏览器自带缩放。
    - PT Sans 只有 400 / 700，界面中的 500 / 600 字重由浏览器就近取档。
- [x] P8 · 测试反馈：Milvus 唯一向量库、线上 Embedding 入口、顶栏选中态与分界线。
  - [x] P8.1 向量库只提供 Milvus Lite（内置）：数据流节点不再可切换（`capabilities` candidates 只剩 milvus、
    `switchable=False`，前端隐藏 backend 键），旧插件 lexical 设置导入时改回 Milvus 并提示；Embedding 未就绪时
    节点显示「等待 Embedding」，检索兜底 SQLite 词法召回（方案 A）。内部 `vector_db.backend=lexical` 保留给测试装配。
  - [x] P8.2 Embedding 来源改为「本地模型 / 线上 API」二选一（`EmbeddingSourceInput`）：只列 OpenAI 兼容连接，
    名字含 embed 的模型优先，可手填；无连接时提示先添加；线上模式隐藏本地模型名。
  - [x] P8.3 顶栏 tab 选中态去掉 3px 光圈；工作台视图顶栏不画分界线，面板四周统一 `--panel-gap`。
  - 验证：`pytest` 1294 passed / 4 skipped（新增 lexical→Milvus 迁移、线上 Embedding 解锁 Milvus 两项）；ruff /
    mypy；前端 lint / tsc / build；Playwright 截图核对 Embedding / 向量库节点与工作台顶栏。
- [x] P9 · 组件安装失败归类（参考旧宿主框架的 pip 失败分类）。
  - [x] P9.1 `kacore/extras/failure.py`：uv 输出 → disk_full / permission / network / no_wheel / conflict / build /
    unknown，附最相关的几行原文；`ExtrasManager.install` 失败结果带 `reason` 与 `detail`。
  - [x] P9.2 前端按 `reason` 显示本地化说明（`installFailureText`），偏好设置 › 组件在该行下方给出说明与可展开的原始
    报错；`installResultOf` 把后端 500 的结构化失败结果当作结果返回，不再丢成通用异常。
  - [x] P9.3 README 开发命令补 `--dev-origin http://localhost:3000`（缺少时浏览器开发页的写请求被 CORS 拒为 403）。
  - 验证：`pytest` 新增 `test_extras_failure.py`（9 项，覆盖六类典型 uv 报错、优先级、未知回退、manager 结果）；
    ruff / mypy；前端 lint / tsc；假 uv 模拟网络失败，Playwright 截图确认说明与原始报错显示。

- [x] P10 · 安装包瘦身（对齐旧宿主桌面版的打包方式）+ 开发环境对齐。
  - [x] P10.1 不再预编译 `.pyc`：`prune_runtime` 清掉字节码，构建期解释器一律 `-B`；`ka_server/bytecode_cache.py`
    在打包产物（`ka/.ka_bundle` 标记）里把 `sys.pycache_prefix` 指到用户缓存目录，首次启动编译一次。
  - [x] P10.2 删去 site-packages 里的 `tests/`、`include/`（解释器自身的 include/ 保留）。
  - [x] P10.3 裁掉 pyarrow 的 Flight / S3 扩展与 `_pyarrow_cpp_tests`；substrait 被核心 lib 直接链接，保留。
    打包冒烟测试新增 Milvus Lite 建库-写入-flush-带过滤检索。
  - [x] P10.4 Windows 改出 NSIS `setup.exe`（`installMode: currentUser`，中英文安装界面）；`configure_release.py`
    合并而非覆盖 `bundle.windows`。
  - [x] P10.5 开发 `.venv` 回到 pymupdf4llm 0.0.27，移除 pymupdf-layout / onnxruntime（PolyForm 非商用许可）。
  - [x] P10.6 Linux AppImage：发布流水线设 `NO_STRIP=true`（rc.1 的 linuxdeploy 失败连带 `.deb` 未上传）；
    rc.2 仍失败则改为只出 `.deb`。
  - 验证：本机（Linux）完整打包：未压缩 729→548 MB、gzip 224→174 MB，冒烟（含 Milvus 往返）通过，产物无 `.pyc`；
    运行时字节码写入用户缓存（首次导入 2.2 s，之后 0.9 s）；`pytest` 1308 passed / 4 skipped；ruff / mypy。
  - 未验证（需 CI）：NSIS 实际产物与体积；Windows / macOS 上 pyarrow 文件名的裁剪命中情况；AppImage 修复。
- [x] P11 · 线上 Rerank + Embedding / Rerank 默认线上 API。
  - [x] P11.1 `kacore/repository/reranker/remote.py`（`RemoteReranker`）：OpenAI 兼容连接的 `/rerank`，兼容
    `results`（Cohere / Jina / SiliconFlow）与 `data`（Voyage）；只发 model / query / documents；部分返回时缺失项
    按原序排后；失败只对本次按序回退并记录原因（不泄露 URL），下次照常请求；20 s 超时不重试。
  - [x] P11.2 设置：`rerank.provider` 增加 `remote`，新增 `flow.retrieval.rerank.remote`（连接 + 模型），设置桥注入
    地址与密钥；没选连接按 noop。数据流快照里线上 Rerank 不要求本地组件。
  - [x] P11.3 前端：`ConnectionModelInput`（Embedding / Rerank 共用，只列 OpenAI 兼容连接，embed / rerank 类模型
    优先，可手填）；选 remote 才显示连接，线上时隐藏本地模型名。
  - [x] P11.4 默认线上：`flow.embedding.source` 默认「线上、未选连接」，`rerank.provider` 默认 `remote`；未选连接时
    启动不做 Embedding 探针、节点提示先添加连接。旧版插件配置（含没写 embedding 的）迁移后保持本地。
  - 验证：`pytest` 1330 passed / 4 skipped（新增 `test_remote_reranker.py` 21 项、默认值 1 项）；ruff / mypy；
    前端 lint / tsc；Playwright 截图核对默认态与选择连接流程（临时连接测后已删除）。
  - 已知限制：只改连接的 API Key 不会热切换已建好的线上 Rerank，需重启服务（与线上 Embedding 一致）。
- [x] P12 · 全英文输出（保留界面中文选项）+ 顶栏字标。
  - [x] P12.1 顶栏字标改用 Bitcount、全大写（与落地页一致）。
  - [x] P12.2 后端日志改英文：Terminal 与日志文件启动后零中文行。
  - [x] P12.3 后端返回界面的文字改英文（约 490 条：报错、安装失败、导入确认项、诊断提示、Notion / R2 / Zotero 消息，
    含全角标点拼接）。不改：LLM 提示词、中文文本处理用的正则 / 关键词、Notion 列名「已做笔记」与旧版标记、离线占位里
    匹配「问题」的逻辑。随回答语言的文案保持中英两套：深度思考提示、生成状态提示原样保留；LLM 不可用 / 未找到内容 /
    摘录兜底、答案末尾「参考文献 / References」改为按回答语言选择（新增 `_answers_in_chinese`）。
  - [x] P12.4 桌面壳菜单、托盘、更新通知、后端崩溃 / 启动失败提示改英文（注释保持中文）。
  - [x] P12.5 前端去掉写死的中文（页面描述、两条请求错误、mock 数据）；`model.ts` 的中文词条表属中文选项，保留。
  - 验证：`pytest` 1330 passed / 4 skipped（约 70 处断言随文案更新，中文回答相关断言保持中文）；ruff / mypy；
    前端 lint / tsc；重启后端日志零中文；顶栏截图。
  - 未验证（本机无 Rust）：壳的 `cargo fmt --check` / `clippy` / `cargo test`，待 CI。
- [ ] P13 · 三平台安装包体积与 Linux AppImage（🚧）。rc.2 实测：Windows 81 MB（NSIS LZMA），macOS DMG 139/146 MB
  （zlib；同一 .app 用 xz 只有 83 MB），Linux AppImage 失败（linuxdeploy 解析不了 auditwheel 改名的 `.so`）。
  - [x] P13.1 `bundle_python.py`：POSIX 只留一个实体 `bin/python3`（Tauri 复制资源会把符号链接展开成三份）、
    删去未用的 `libpython` 共享库、tkinter / tcl / tk、idlelib、ensurepip、lib2to3、turtledemo、`*-config` 与
    idle / pydoc / 2to3 脚本。曾在 Linux strip site-packages 的 `.so`，CI（ubuntu-22.04）上导致导入失败，已撤回；
    Windows 的 glob 不区分大小写，POSIX 的 `lib/thread*` 误删 `Lib/threading.py`，已按平台拆分。
  - [ ] P13.2（代码已落地，待 CI 验证）macOS：出包后 `hdiutil convert -format ULMO` 把 DMG 改为 LZMA 压缩并替换 Release 资产。
  - [ ] P13.3（代码已落地，待 CI 验证）Linux AppImage 后注入：tauri 出 AppImage 时不带 python，之后解包注入、appimagetool 重打包、
    按需重签 updater `.sig`。
  - 验证：本机 Linux 完整打包，运行时（展开符号链接后）约 605→461 MB、xz 120→103 MB；冒烟（含 Milvus 往返）通过；
    瘦身后的解释器经 uv 安装 boto3 正常；`test_bundle_python.py` 新增 2 项。linuxdeploy 失败在本机用模拟 AppDir
    复现（`Could not find dependency: libquadmath-2284e583.so.0.0.0`）；不带 python 的 AppImage 可出包，
    注入脚本重打包后（zstd 19 级）解包导入后端成功。
  - 未验证（需 CI）：DMG 的 ULMO 实际体积与替换上传、tauri `--config` 去掉 python 资源、真实 AppImage 启动；
    启用 updater 时 `latest.json` 缺 Linux 条目（已在 docs/RELEASE.md 记为已知限制）。
- [ ] P14 · 由 developer 的 `v*` 标签自动生成干净的 `main`（🚧）。
  - [x] P14.1 `.release-exclude` 清单 + `tools/release/build_main.py`：按清单删除文件、生成精简 CHANGELOG、
    检查 README 死链、`git commit-tree` 接在现有 main 之后（不 force）。
  - [x] P14.2 `.gitattributes` 的 `export-ignore` 与清单同步（源码 zip 同样干净）。
  - [ ] P14.3（代码已落地，待 CI 验证）`desktop-release.yml`：`publish-main` → `build`，安装包从 main 提交构建。
  - [x] P14.4 README 拆为 `README.md`（中文）/ `README_EN.md`（英文），注明来源为旧插件 v1.2.0；
    贡献与 issue 走 developer。
  - [x] P14.5 `CLAUDE.md` §1.1、`docs/GIT_WORKFLOW.md`、`docs/RELEASE.md`：批准推 `v*` 标签即批准自动更新 main。
  - [x] P14.6 rc.3 反馈：main 默认英文、不带开发期 CHANGELOG、根目录精简。`README.md` 改为英文、中文版改名
    `README_ZH.md`；`CHANGELOG.md` 加入 `.release-exclude` / `export-ignore`，`build_main.py` 去掉精简 CHANGELOG，
    发布说明改为在 GitHub Release 用英文写；`logo.svg` 移到 `desktop/assets/`，删去未引用的 `logo.png` 与空的 `uv.lock`。
  - 保留：`.agents/skills/operate-knowledge-arch` 与 `requirements-codex-skill.txt` 是 Codex Skill 功能（有测试），
    不属于 agent 规范，留在 main。子 README 指向 ARCHITECTURE / dev 的链接改为 developer 分支的绝对链接；
    `test_no_legacy_host_references.py` 放行两份 README 的来源说明。
  - 验证：`pytest` 1339 passed / 4 skipped（新增 `test_build_main.py` 7 项）；ruff / mypy；actionlint；
    本地预演 main（447 个文件、无死链、父提交为旧 main `1640a57`）。
- [x] P15 · 首次启动 AI 辅助配置。决策：欢迎页顺序「语言 → 导入 / 空库 → AI 配置」；密钥只由用户在 app 里填
  （agent 发请求、app 弹框，服务端拦截 agent 写机密）；skill 随安装包内置，另提供一键安装到 Codex / Claude Code。
  - [x] P15.1 后端 agent 接入：`prefs.general.agent_access`；独立 agent token + 连接文件（仅当前用户可读、退出即删）；
    agent token 走路由白名单并拦截机密写入；`/api/agent/status`、`/api/agent/prompt`、密钥请求、skill 一键安装。
  - [x] P15.2 skill：客户端改为连接文件 + Bearer（去掉登录 / 密码 / keyring）；新增 setup-status、detect、
    provider-*、settings-*、component-install、request-secret；SKILL.md 按桌面版重写，新增 references/setup.md；
    随安装包打包。
  - [x] P15.3 欢迎页：语言选择 → 导入 / 空库 → AI 配置（prompt 复制、实时清单、密钥弹框）；偏好设置 › 通用可重新
    复制 prompt 与安装 skill。
  - [x] P15.4 测试与文档：后端白名单 / 连接文件 / 密钥请求；skill 客户端契约测试；前端 lint / tsc / 截图；README 中英。
  - 补充：后端启动时刷新本应用装过的 skill（AppImage 每次挂载路径不同，升级后路径与版本也会变）；只处理带
    `.knowledge-arch-skill` 标记的目录。`web/agent_routes.py` 登记进打包 SOURCES，新增守卫测试防止漏登。
  - 验证：`pytest` 1413 passed / 4 skipped（新增 `test_agent_access.py`、`test_knowledge_arch_skill_setup.py`、
    ka_server 进程级连接文件测试，skill 客户端测试改为连接文件 + Bearer）；ruff；新模块 strict mypy；前端 lint / tsc /
    build；本机打包冒烟（含 skill 定位）并用打包后的 Python 渲染 prompt；Playwright 走通「语言 → 空库 → 复制 prompt →
    模拟 agent 建连接并请求密钥 → 弹框填写 → 已连接」，密钥写入后 `api_key_set` 为真。
  - 路径脱敏：prompt 与已安装 SKILL.md 里家目录下的路径写成 `"$HOME/…"` / `~/…`；客户端所有输出把家目录替换为 `~`
    （写回 path 类设置前展开）；白名单去掉 `/api/system/info`（只有带用户名的路径），`detect` 只在本机探测。
    Windows 上 prompt 仍是绝对路径（Git Bash 对 $HOME 的展开与 PowerShell 不同），会带出用户名。
  - 未验证：真实 Codex / Claude Code 会话全程；Windows / macOS 上的连接文件位置与 prompt 引号（逻辑有测试，未上机）。
- [x] P16 · 首次使用引导（不用 AI 助手时）。决策：锁定其它区域，只有高亮处可点（弹窗 / 下拉在引导层之上，仍可操作）；
  向量化为推荐步骤，可「先用关键词召回」或「改用 Codex / Claude 检索」（skill 的 agentic 检索不需要应用内模型）。
  - [x] P16.1 `components/desktop/FirstRunTour.tsx`：四块遮罩 + 高亮环 + 提示卡，六步（数据流 → 模型连接（须有 Key 或本机
    免 Key 服务）→ 对话模型 → 向量化 → 第一篇文献 → 第一个问题），按能力快照 / 库是否为空 / 是否已提问自动推进，
    目标不在当前视图时先高亮切换按钮；数据流节点不在屏幕内时经 `ka:flow-focus` 让画布平移到中央。
  - [x] P16.2 触发：欢迎页「跳过，自己配置」；偏好设置 › 通用「重新查看引导」。状态在 `lib/tour.ts`（本机 localStorage）。
  - [x] P16.3 锚点 `data-tour`：顶栏两个视图按钮、数据流节点、上传按钮、Ask 输入框；`lib/zLayers.ts` 新增 `tour: 950`。
  - 验证：前端 lint / tsc / build；Playwright 走完六步与完成卡（遮罩下点上传按钮无效、节点自动居中、关键词召回回答带
    「Lexical retrieval」标记、完成后本机标记清除）。
- [x] 默认深色界面：next-themes `defaultTheme` 与 `prefs.appearance.theme_mode` 默认值改为 `dark`。验证：设置测试；
  Playwright 在系统浅色下打开新实例，`<html>` 为 `dark`。
- [x] Linux AppImage 的 updater 条目与 artifact 瘦身：`updater-manifest` 作业 + `updater_add_appimage.py`（只写
  `linux-x86_64-appimage`，通用键缺失才补）；artifact 排除 `*.AppDir`。验证：`test_release_config.py` 新增 3 项；actionlint。
  未验证（只在推 `v*` 标签时运行）：真实 Release 上的 latest.json 合并。
- [x] 死代码清理（rc.3 后审查，不含大文件拆分）。删去无调用方的后端接口（`/api/quota`、`/api/milvus/clear-lock`、
  `/api/files/list`、`/api/metrics`、`GET /api/graph`、`/api/graph/query`、`/api/graph/probe`、两个笔记 PATCH）及其
  `kacore` 实现、`kacore/metrics.py`、Milvus 清锁回调；只在测试里出现的方法；前端 `lib/api.ts` 的 24 个未调用函数、
  `?mock` 离线模式、`components/ds/index.ts`、`geist`、47 个未引用的 CSS 变量；6 份无人引用的 docs。
  `restart_plugin` / `/api/plugin/restart` 改名为 service；repository 的 `memory.py` 不进安装包。
  保留：source_store 的读方法（store 契约测试用它核对写入）、`MemoryLogHandler.get_lines`（测试读日志）、
  Zotero 账号合并（报错指路到它但没有 HTTP 入口，待定）。验证：pytest 1383 passed / 4 skipped；ruff；mypy；
  tsc；eslint；`next build`；knip 无未用文件 / 依赖。
- [ ] P17 · 卸载与数据（🚧）。决策：卸载 / 应用内删除时，自选数据目录只删应用自己的条目（选定时为空才整删）；
  应用内「删除全部数据」三平台都做。
  - [ ] P17.1（代码已落地，待 CI 验证）Windows / Linux 不挂菜单栏（顶部 File / Edit），macOS 保留；Ctrl+Q 由前端调用 `quit_app`。
  - [ ] P17.2（代码已落地，待 CI 出包与 Windows 实测）Windows 卸载器：`installerHooks` 的 `NSIS_HOOK_POSTUNINSTALL` 在勾选「删除应用程序数据」且非更新时
    删除 `Knowledge Arch` 数据 / 配置目录与自选数据目录（`desktop/src-tauri/windows/hooks.nsh`）。
  - [ ] P17.3（代码已落地，待 CI 验证）应用内「删除全部数据」：偏好设置 › 存储，二次确认 → 删钥匙串主密钥 → 停后端 → 删目录 → 退出
    （`desktop/src-tauri/src/wipe.rs`、`/api/secrets/forget-master-key`）。
  - [x] P17.4 README 两份加「卸载与数据」；测试。
  - 验证：pytest 1385 passed / 4 skipped（新增主密钥清除 2 项）；ruff；mypy；tsc；eslint；`next build`；
    `wipe.rs` 单独 `rustc --test` 10 项通过（含与 `hooks.nsh` 清单一致）、clippy -D warnings、rustfmt。
    未验证：`lib.rs` 整体编译（本机无 webkit 依赖，等 Developer CI 的 clippy / cargo test）、NSIS 钩子编译与 Windows 卸载实测。
- [x] 上传修复（rc.4 反馈）：桌面壳里拖入文件无效（Tauri 默认 `dragDropEnabled` 截走系统拖放，`tauri.conf.json` 关闭）；
  上传框支持多选 / 多文件拖入，逐个上传、失败的留在列表里；目标集合改用 ds `Select`（原生 `<select>` 的弹出列表不跟主题）；
  集合列表晚到时补上默认目标；去掉后端并不支持的 DOCX（会被当纯文本读成乱码），加上 `.markdown`。
  验证：tsc；eslint；`next build`；Playwright 对 dev 前端 + 临时数据目录：多选 3 个、模拟拖入 1 个、DOCX 被拒，
  三个文件依次上传成功并入库；暗色主题下拉截图。未验证：桌面壳里的真实系统拖放（需出包后实测）。
- [x] 文档列表刷新：Documents 面板搜索键旁加刷新键；上传成功后经 `lib/documentEvents.ts` 通知列表重新加载；
  未选集合（All Documents）时真正列出全部文档（此前不请求、恒为 0 篇）。上传框的目标集合改为派生值，去掉 effect 里的 setState。
  验证：tsc；eslint；`next build`；Playwright：All Documents 列出已有 3 篇，上传后不刷新页面即出现第 4 篇。
- [x] P18 · 多轮对话。决策：先把追问改写成独立问题（检索与生成都用改写结果，存库保留原话）；默认开；
  「使用英语召回」「启用上下文」「显示改写后的问题」三个开关记在本机。
  - [x] P18.1 `kacore/pipelines/conversation_context.py` + `ask(use_context=...)`：有前几轮时用 LLM 改写，失败回退原问题。
  - [x] P18.2 `/api/ask` 的 `use_context`；ChatPanel 开关与「理解为：…」显示；i18n。
  - [x] P18.3 测试：纯函数、首轮 / 关闭不改写、检索用改写结果、存库原话、失败回退。
  - 验证：pytest 1391 passed / 4 skipped（新增 `test_conversation_context.py` 6 项）；ruff；mypy；tsc；eslint；`next build`；
    Playwright：设置菜单三个开关，切换后重新加载仍保持。未验证：接真实模型的多轮问答效果。
    限制：「理解为：…」不存库，重新加载聊天记录后不显示（存库需迁移）。
- [x] P19 · 提示词按回答语言切换。决策：`zh` / `en` 直接用对应语言；`auto` 跟随问题语言。只翻译不改语义，
  JSON 字段名与枚举值保持英文；LightRAG 建图提示词不在范围内。
  - [x] P19.1 语言判定模块（`answer_language` + 问题 → `zh` / `en`）。
  - [x] P19.2 双语提示词：深度思考、增强召回、答案合成、`api.py` 内联提示词、追问改写、来源标签。
  - [x] P19.3 `ask()` → orchestrator / 合成函数串联 `lang`。
  - [x] P19.4 测试：两种语言齐全、占位符与 JSON 字段一致、按 lang 产出；既有测试保持通过。
  - 验证：pytest 1434 passed / 4 skipped（新增 `test_prompt_language.py` 43 项）；ruff；mypy。
    逐字对比改动前后：原中文提示词 → 新 zh 版一致，原英文提示词 → 新 en 版一致，差异只有英文里夹的中文标签
    （「来源」「证据部分支持」等示例）与 SEA 中文版末尾的动作说明改为中文。
    保留英文：英语召回的翻译提示词（只在中文提问时触发，输出必须是英文）。未做：接真实模型的效果评测（benchmark 后续）。
- [x] P20 · 2.0.2 前端修复。
  - [x] PDF「Invalid PDF Structure」：PdfViewer 带 token 向后端取文件字节（此前用相对路径，桌面壳里取到的是前端页面）；
    非 PDF 文档（库里存成 original.pdf 的文本副本）不再报 pdf 类型、不提供 PDF 阅读模式。
  - [x] PDF 全屏：「适应宽度」旁加全屏键，阅读器铺满整个应用窗口（不是系统 F11 全屏），Esc 退出。
  - [x] 聊天来源卡片去掉内部分块 ID（`LOCAL_…`），标题省略号截断。
  - [x] 版本号 2.0.2。
  - 验证：pytest 1435 passed / 4 skipped；ruff；mypy；tsc；eslint；`next build`；Playwright：真 PDF 渲染 3 页、
    全屏铺满并重新适应宽度、Esc 退出、.md 文档无 PDF 切换。未验证：桌面壳里实测（需出包）。
- [x] P21 · 2.0.3（含未单独发布的 2.0.2）。
  - [x] ① 聊天记录存 `meta`（思考过程、改写后的问题），超时补拉与重启后仍在；迁移 026。
  - [x] ② 聊天来源面板可折叠（默认收起）；知识范围标签显示文档标题而非内部 ID（新增 `GET /api/documents/{doc_id}`）。
  - [x] ③ 集合改名级联：documents / lightrag_index_status / 集合笔记 / 图谱构建记录 / 向量标签 / LightRAG 映射；
    构建中拒绝改名；启动时修复已损坏的归属；前端选中项跟随新名字。
  - [x] ④ 未就绪的 Zotero / R2 / LightRAG 按钮变灰，点击提示去数据流页配置；去掉 Zotero 区无操作的「+」。
  - [x] ⑤ Windows / Linux 去掉系统标题栏，窗口按钮并入顶栏 Preferences 右侧；macOS 红绿灯嵌入顶栏左侧（Overlay）。
  - [x] ⑥ 外框固定：html / body 不滚动、关闭回弹，只有面板内部滚动。
  - 验证：pytest 1445 passed / 4 skipped；ruff；mypy；tsc；eslint；`next build`；Playwright：改名后 5 篇仍在且选中跟随、
    未配置按钮变灰与提示、范围标签显示标题、滚轮下根节点不动而面板内部可滚。
    未验证：桌面壳里的自绘标题栏（三平台）、窗口拖动 / 双击最大化、真实模型下思考过程的存取（需出包实测）。
- [x] P22 · 2.0.4（2.0.3 之后的修复）。
  - [x] ① 聊天输入框写好内容后点「查询设置」直接把问题发出去：`components/ds/IconButton.tsx` 的按钮没写 `type`，
    在表单里默认是 submit；改为 `type="button"`。
    验证：tsc；eslint；Playwright：填好问题后开关设置、切检索模式、拨开关，均不发 `/api/ask`，输入保留；回车仍发送。
    撤掉修复时同一流程会发出 1 次请求并清空输入框。
  - [x] ② 聊天默认检索档位改为 enhanced（`ChatPanel.tsx` 的 `DEFAULT_MODE`）；模式失效时回退到它，设置键高亮也以它为基准。
    后端 `/api/ask` 未传 `retrieval_mode` 时仍是 default，只改界面默认。
    验证：tsc；eslint；Playwright：打开设置默认选中 Enhanced recall。
  - [x] ③ 清空聊天时打断进行中的回答：后端按 conversation_id 登记 ask 任务，`clear_chat_history` 先取消再删记录
    （清进度条目、运行事件记为 cancelled，不让任务跑完后写回）；前端清空时中止请求、停轮询与补轮询、恢复发送键。
    多轮改写与检索翻译并入同一个可中断任务；不限时（task_timeout_seconds=0）也走任务。
    验证：pytest 1449 passed / 4 skipped；ruff；mypy；tsc；eslint；`next build`；Playwright：`/api/ask` 挂起时点清空，
    问题消失、提示已中断、发出 DELETE，发送键恢复且能立刻再问。未验证：真实模型下卡住的深度思考（需实际复现）。
  - [x] ④ 版本号 2.0.4。
- [x] P23 · 2.0.5（2.0.4 之后的修复）。
  - [x] ① 聊天知识范围与实际检索不对应：选中文档时标签显示文档，但只有全文模式按文档检索，其余模式按集合（或全部集合）。
    标签改为可点的集合选择器（`ChatPanel.tsx`），显示的就是这次检索用的范围：全文模式是当前文档，其余是聊天自己的集合；
    空闲时跟随左侧点选的集合，中间切文档不影响；回答进行中范围锁定（标签变灰），左侧 / 中间怎么切都不变。
    每轮问答记下所属范围：后端 `ask` 写入聊天记录 `meta.scope` 并随结果返回 `scope`，气泡上显示；
    「加入笔记」挂到该轮的范围。
    验证：pytest 新增 2 项；tsc；eslint；`next build`；Playwright（真后端，ask 延迟 3s）：选择器列出全部集合与子集合缩进、
    选中后标签跟随；回答中标签禁用，左侧连点两个集合标签不变；请求体 collection 为所选集合；气泡标注所选集合；
    回答后左侧点选恢复跟随；刷新后从历史恢复气泡范围。未验证：选中文档 + 全文模式的标签（测试库无文档）。
  - [x] ② Notion 目标 404 时初始化死锁：`preflight(initialize=True)` 遇到已配置库 404 不再抛错，返回失效字段；
    `initialize_databases` 把失效库当未配置、在父页面下新建（结果带 `recreated_*` 与 warning；无父页面时报错且不写远端；
    QA 库保留时提示其 Citations 仍指向旧库）。`NotionSyncPipeline.initialize_databases` 新建 Articles 库后清掉文章推送账本，
    否则新库会因指纹相同被整批跳过。推送前的预检 404 照旧拦截，报错里加上「可运行建库重建」。前端初始化成功时按是否重建给提示。
    验证：pytest 1457 passed / 4 skipped（新增 6 项，旧实现下全部失败）；ruff；mypy。未验证：真实 Notion 工作区。
  - [x] ③ 版本号 2.0.5。
  - [x] ④ 聊天可关闭模型思考：查询设置弹窗加「模型思考」开关（默认开，记在本机），`/api/ask` 带 `model_thinking`；
    本轮所有模型调用（改写、翻译、增强召回 / 深度思考各步、生成）经 contextvar（`kacore/providers/thinking.py`）
    按连接换成各家的关闭参数，端点拒收（400 / 422）时去掉参数重试一次（OpenAI 兼容连接记住不再发）。
    Anthropic 按型号：Opus 5.5 / Opus 5 / Sonnet 5.5 / Fable 关不掉或与拒答回退冲突，改用 effort low；
    Haiku 5.5 / Sonnet 5 / Opus 4.8 / 4.7 发 `thinking: disabled`。不影响建图等后台任务与「深度思考」档位。
    验证：pytest 1482 passed / 4 skipped（新增 `test_model_thinking.py` 24 项与 ask 级 1 项）；ruff；mypy；tsc；eslint；
    `next build`；Playwright：默认请求带 `model_thinking: true`，关掉后为 false，刷新后开关保持关闭。
    未验证：各家真实端点是否真的按参数关闭思考（需各自的密钥实测；Moonshot / Ollama 参数依据第三方资料，LM Studio 无请求级参数）。
  - [x] ⑤ 图谱档位（仅图谱 / 图谱混合）只在聊天所选集合的图谱就绪时可选：以前靠左侧 LightRAG 区的 `lr:` 前缀，
    范围改为聊天自选后这个判断没了，①里临时改成「有集合即可选」不对。新增 `GET /api/lightrag/readiness`
    （`get_lightrag_readiness` 的只读出口），前端按所选集合查询，未就绪时两档变灰并显示原因。
    换集合或打开查询设置时重查；已选图谱档位而集合未就绪时回退到默认档位。
    验证：pytest 1483 passed / 4 skipped（新增路由测试 1 项）；ruff；mypy；tsc；eslint；`next build`；Playwright：
    无图谱的集合两档变灰并提示去左侧构建，就绪的集合（模拟 ready）可选，选着图谱档位换回无图谱集合时回到增强召回。
- [x] P24 · 2.0.5 之后的修复。
  - [x] ① 数据流页保存 Zotero API Key 后几秒又显示「未设置」：5 秒一次的自动刷新用 `/api/config/effective` 整个替换 config，
    冲掉了 `/api/zotero/config` 合并进来的 key 状态 / 用户名 / 连接状态（本地模式的连接灯同样会闪）。`FlowPageContent.tsx`
    记住最近一次 Zotero 探针结果，每次刷新合并回去（设置项以新拉到的为准）；定时刷新进行中时，保存 key 要求的 Zotero 刷新不再被丢掉。
  - [x] ② 删除集合后出现 `_uncategorized`：决策（A）——默认集合（`source_store.default_collection`）是收件箱，不可删除、不可改名
    （启动时会按名字补建，删掉只会多一个空集合）；删除其他集合时无归属的文档迁入默认集合，不再新建 `_uncategorized`；
    启动时 `merge_legacy_uncategorized` 把已有 `_uncategorized` 的文档并回默认集合并删掉它。按 key 删除时主归属指向被删集合、
    但还有别的归属的文档，主归属改到剩下的集合（以前留着已删除的名字）。`/api/collections` 多返回 `is_default`，界面隐藏删除 / 改名键；
    删除确认框的文案改为实际行为（以前写「永久删除所有文档」）。
  - [x] ③ 聊天可限定单篇文档：范围菜单顶部加「当前浏览的文档」，点一次钉住当时那篇；之后中间换文档、左侧点集合都不改它，
    菜单里同时列出「已选定的文档」与新打开的那篇，再点即换；选任一集合即取消。默认 / 增强召回 / 深度思考 / 全文都按这一篇，
    图谱两档变灰。后端新增 `document` 作用域（`retrieval_orchestrator.SCOPE_DOCUMENT`，只放行该 doc_id，屏蔽图谱通道）；
    `ask` 在非全文模式收到 `doc_id` 时按该文档的主归属集合召回再硬过滤，`meta.scope` 记文档标题；图谱档位带 `doc_id` 返回 400。
  - [x] ④ 在线模式没填 Zotero API key 时同步按钮仍是亮的、点了才由后端报错：`kacore/api_capabilities.py` 的
    `_overlay_zotero_config_state` 在 server 模式且本地没有 key 时把 zotero 环节降为 degraded（`detail.server_key_present`、
    `detail.reason`），文件面板按钮随之变灰、点击提示去数据流页（`lib/featureReadiness.ts` 原有逻辑）。本地模式不看 key。
    Zotero → 导入是虚线边，不向下游级联；整体健康灯变红，与「启用但缺 PDF 依赖」一致。
    验证：pytest 1497 passed / 4 skipped（新增 1 项）；ruff；mypy；真后端：启用 Zotero + 在线模式无 key → degraded，
    切回本地 → ready。未验证：填入真实 key 后恢复 ready（单测覆盖，需真实账号）。
  - [x] ⑤ 版本号 2.0.6。
  - 同类问题排查（①）：前端只有数据流页 Zotero 节点把两个接口的结果拼进同一份状态；Notion token（capabilities）、
    R2 密钥（settings schema）、模型连接 key（providers）每次刷新都整份来自单一接口，不会被覆盖。
  - 验证：pytest 1496 passed / 4 skipped（新增 `test_default_collection.py` 10 项、`test_retrieval_scope.py` 2 项、
    `test_conversation_context.py` 1 项）；ruff；mypy；tsc；eslint；`next build`。
    真后端 + `next dev` + Playwright 19 项全过：钉住 attn.md 后打开 mamba.md 范围不变、请求带 doc_id、选集合即取消；
    API 层同一问题全部集合召回 2 篇、钉住后只有 1 篇；default 无删除 / 改名键且接口拒绝；删除 papers 后文档进 default、
    界面无 `_uncategorized`；Zotero（探针返回已存 key）自动刷新 3 轮后仍显示 key 与用户名——还原旧 `FlowPageContent.tsx`
    时同一检查变成「Not set」。重启后端：预置的 `_uncategorized`（1 篇）并回 default 并消失。
    未验证：真实 Zotero Web API key 的保存（需真实账号）；接真实模型的单篇深度思考效果；桌面壳里实测。
- [x] P25 · 2.0.7（2.0.6 之后的修复）。
  - [x] ① 文件面板的 LightRAG Collection 区默认收起（`FilePanel.tsx` 的 `lightragOpen` 初值）。
  - [x] ② 偏好设置「本地模型」面板把线上 rerank 显示成「已卸载」的本地模型、不重排时显示未翻译的 `models_state_off`：
    `kacore/api_runtime_models.py` 的 `_rerank_runtime` 对线上 rerank 报 `external`（与线上 embedding 同义），noop 不列出；
    `ModelRuntimePanel.tsx` 对 `external` 行不显示卸载键。排查结论：选线上或不重排时不会构造本地 cross-encoder、
    不会 import sentence-transformers（新增回归测试）；只有显式 `cross_encoder` 或 `auto` 且已装本地组件时才会用本地模型。
  - [x] ③ 版本号 2.0.7。
  - 验证：pytest 1503 passed / 3 skipped（`test_model_runtime.py` 新增 5 项）；ruff；mypy；eslint；tsc；`next build`。
    未验证：桌面壳里实测面板（逻辑由单测覆盖）。
- [x] P26 · 2.1.0 识图与正文去污染（2.1.0-preview.1）。决策（已批准）：找图在本地、上传时自动建空位；识图描述类似 LightRAG 手动触发
  （集合级「补全图表描述（仅缺失）」，先估算）；识图模型是「模型分配」新角色 `vision`、不跟随对话模型，开关在 Upload/Chunk
  （`flow.import`）；截图不落盘、调用时按位置从原 PDF 现渲染；不做 OCR，扫描页只检测提示；图描述 2.1.0 不进 LightRAG；
  正文去污染三层（L1 文本规则 / L2 几何遮罩：图内文字挪进图位而非删除 / L3 评测守恒），`cleaning-v4` 升级后自动后台重抽取；
  乱码行直接删除。
  - [x] P1 PDF 关键词搜索：pdf.js 文字层（顺带可选中文字）+ 查找条（Ctrl/Cmd+F、Enter / Shift+Enter、n/m 计数、Esc）；
    没有文字层时提示。纯函数在 `lib/pdfSearch.ts`；单页渲染与文字层拆到 `PdfPage.tsx`，查找条 `PdfSearchBar.tsx`。
    验证：tsc；eslint；真后端 + `next dev` + Playwright：Attention 论文查 attention 97 处、Enter / Shift+Enter 计数 6/97→5/97、
    跨行短语 `scaled dot-product attention` 6 处、无结果提示、Esc 清除高亮、悬停时 Ctrl+F 打开；125% / 140% 截图高亮与原文对齐；
    纯图片 PDF 提示「没有可查找的文字层」；快速缩放无未处理的取消异常。
  - [x] P2 找图与空位：`kacore/domain/figures.py`、`kacore/managers/figure_detection.py`、迁移 `027_document_figures.sql`、
    `FigureRepository`（`source_store/figures.py`，SQLite / 内存实现各一个混入）；上传与重抽取时找图并接上同位置旧描述，
    `local_meta` 记 `scanned_pages` / `figure_count` / `text_empty`。
    检测规则经 4 篇论文调定：图标题在上方加罚、矢量候选阈值放低（必须配标题）、紧邻或同列的候选并入同一张图、
    带表标题且单元格多半有字的线框表切掉、正文段落（本页正文字号、≥2 行、平均行长 ≥50）把误连的绘图簇切开。
    实测：ResNet 7/7、Attention 5/5、ViT 12/12、ToT 5/6（图 4 内全是正文字号的长段，保守不收）；每篇 0.4–2.0 s。
    验证：`test_figures.py` 10 项（合成 PDF：图表 + 刻度、真表格、重复 logo、扫描页；两种仓储契约；摄入与重抽取保留描述）。
  - [x] 既有问题：没有文字的 PDF 每次列文档都反复重切 —— `chunk_needs_rebuild` 对 `text_empty` 的文档不再要求重切。
  - [x] P3 正文去污染：`kacore/managers/figure_noise.py`。L1（伪表格：ColN 表头且多为空格子 / 删除线 / `<br>` 堆叠 / 乱码；
    乱码行：拉丁扩展 / IPA / 修饰符占字母 ≥40%；连续 ≥3 行短数字的刻度）；L2（抽正文前在内存副本上逐行删图内文字、
    删完全落在图区域里的矢量线条，原 PDF 不改；图标题留在正文）。`clean_raw_pages` 让抽取与「从原始页重洗」共用一条路径。
    图块：`figure_chunk_text`（`Figure N (page p)` + 标题 + 描述 + 图内文字，≤1500 字），排在正文块之后，
    `chunk_kind=figure`、`anchor_labels` 带 `FIGURE N`；图块不续接相邻正文（`evidence_views.py`），不进 LightRAG（`api.py`）。
    `PROCESSING_VERSION` → `cleaning-v4`：启动时旧文档被标记重建，后台「数据清洗」阶段对原 PDF 还在的走重抽取（含找图），
    不在的从 provenance 原始页重洗。
    L3：`dev/eval_body_cleaning.py`（对比 v3，守恒检查）。实测（伪表格 / `<br>` / 乱码行，前→后）：ResNet 5/258/0→0/228/0，
    Attention 1/105/0→0/21/0，ViT 8/746/0→0/0/0，ToT 2/146/11→0/0/0；「正文少了却不在图位也不是垃圾」的词：0 / 0 / 5 / 4
    （ViT 为伪表格里的断词碎片；ToT 为图下方一个超链接的 URL 被遮罩连带去掉，链接文字保留）。每篇抽取 3.3–7.2 s（v3 2.6–8.8 s 的解析 + 找图）。
    验证：`test_figure_noise.py` 14 项；`test_ingest_evidence_upgrade.py` 的旧用例改为当前版本文档（过期文档改走重洗，另有用例）。
  - [x] P4 识图角色：`ROLE_VISION`（不跟随对话模型，`ROLES_FOLLOWING_CHAT` 只含 research / graph）；`ProviderClient.describe_image`
    （默认不支持；OpenAI 兼容发 `image_url` data URI，400/422 归类为 `no_image_input`；Anthropic 发 base64 图片块；
    两者都尝试关思考）；`RoleClient.describe_image`；设置 `flow.models.vision`、`flow.import.vision.{enabled,max_concurrency,timeout_sec}`
    （`VisionConfig`），中英文标签；技能客户端的配置策略快照补 `vision`。验证：`test_vision_provider.py` 5 项。
    「测试识图」放在 P5 的 `/api/vision/test`（按模型分配测，而不是按连接测）。
  - [x] P5 识图任务：`kacore/vision_job.py`（进度快照）、`kacore/pipelines/figure_caption_pipeline.py`（现渲染 150 dpi、≤1.6 MP →
    提示词 `figure-v1`：类型 / 坐标轴与单位 / 系列与图例 / 读得出的数值与趋势 / 流程图组件与连接 / 要点，80–200 词，不编造）、
    `kacore/api_vision.py`（`VisionApiMixin`：状态与估算、全局单任务、按文档并发、取消后不再发新请求；
    `no_image_input` / 密钥无效 / 模型不存在时整体停下，超时等写 failed 下次重试；每篇做完 `refresh_figure_chunks` 并通知自动重建索引；
    「测试识图」发一张现画的红色柱状图问颜色）。HTTP：`GET /api/vision/status`、`POST /api/vision/build`、
    `GET /api/vision/build/active`、`POST /api/vision/build/cancel`、`POST /api/vision/test`、`GET /api/documents/{id}/figures`。
    日志：`FigureVision` 记 DEBUG（每张图：尺寸、字节、状态、字数、token、耗时、模型），终端页分类 `vision`。
    验证：`test_vision_job.py` 7 项、`test_web_server.py` 新增 1 项。
  - [x] P6 前端：Upload/Chunk 节点出现「识图」开关与高级项（schema 自动渲染），节点操作区 `VisionActions`（统计 + 「测试识图模型」）；
    模型分配节点出现「识图模型」，空选项显示「未分配」而不是「跟随对话」（`SettingInput.tsx`）；文件面板顶部「补全图表描述」
    → `VisionBuildDialog`（范围 / 模型 / 文档 / 图数 / 待描述 / 估算 token / 扫描页提示；没开或没选模型时只给「去数据流设置」）；
    左下角进度栏 `vision_build` 行（文档 · 图 · 页、百分比、n/m、取消，结束时 toast + 桌面通知）；聊天来源卡片对图块显示「图标 Figure N · p.X」。
    `lib/visionApi.ts`（`lib/api.ts` 已超 600 行）。图块里的描述写明「generated by a vision model」，回答模型据此区分转述与原文。
    照片类图 PNG 超 600 KB 时改用 JPEG 上传（像素不变）：实测 ViT 图 14 由 1.27 MB 降下来。
    验证：tsc；eslint；真后端 + `next dev` + 本地假 OpenAI 兼容识图服务 + Playwright：关着时确认框只给提示；
    打开并分配模型后节点「测试识图模型」通过（≈310 ms）；文件面板开始 → 进度栏 41% 7/17 → 「17 张，失败 0」；
    ViT 12 个图块带描述，日志分类 `vision` 有每张图的 DEBUG 行与汇总（tokens in/out 18700/1020，4 s）。
  - [x] P7 检索整合与收尾：真后端关键词检索验证（无 Embedding 时的 SQLite 召回）：「Figure 3」同时命中两篇论文的图 3 图块，
    描述性查询（「ImageNet top-1 accuracy line chart」）前五里有 3 个图块；README / README_ZH 补识图、PDF 查找、四个模型角色，
    并改正「上传 / 分块」里「扫描件 OCR」的说法（该开关目前不起作用，见下）；版本号 2.1.0，标签 `v2.1.0-preview.1`（沿用 rc 先例：
    文件里写正式版本号，标签带预览后缀）；CHANGELOG。
  - 遗留 / 待决：`flow.import.ocr_enabled`（「扫描件 OCR」开关）自插件时代起只打印成本警告、不做任何 OCR，界面上仍可见；
    是否隐藏或改为未来的「识图转写扫描页」由用户决定。ToT 图 4 这类「图里全是正文字号长段落」的图保守不收。
    未验证：真实识图模型的描述质量与费用（本轮用本地假 OpenAI 兼容服务验证了协议、进度与写回）；Anthropic 图片块只有单测；
    桌面壳里实测；有 Embedding 时图块进 Milvus（走既有重建路径，单测覆盖 needs_reindex）。
- [x] P27 · 2.1.1（2.1.0-preview.1 之后的修复与 PDF 定位）。决策（已批准）：阅读模式全局一个、默认 PDF（记在本机），
  非 PDF 文档只用分块界面且不改全局；citation 在 PDF 模式下跳 PDF 原文并高亮（保留到下次跳转 / 换文档），定位不到时跳页并给「在 MD 中查看该段」；
  上传摄入进度条改为不确定进度 + X/Y；「重新抽取」只在 MD 模式；版本 2.1.1。
  - [x] ① 向量库节点变黄不自动恢复：打开旧分块文档时 `_ensure_document_chunks_current` 隐式重切 / 重抽取，IngestManager 直接写
    `needs_reindex` 而不经 `_mark_document_needs_reindex` 收口，自动重建调度器收不到信号。现在重切后补 `_notify_auto_reindex()`。
    验证：`test_processing_recovery.py` 新增 1 项（还原修复时失败）。
  - [x] ② 进度条：向量重建清洗阶段分母只有清洗篇数（清洗完 100% → 进入索引掉回一半），清洗阶段改按全部目标篇数估索引段；
    进度栏计数改为当前阶段（清洗 a/b、索引 c/d），与百分比一致。LightRAG 建图每次 LLM 调用成功就 +1 个分块、只封顶到全局总数，
    一个分块要调好几次 LLM，进度会跑到后面文档上再长时间卡住；改为封顶到本文档的分块终点。上传摄入去掉 20% / 70% 的假百分比，
    进行中显示滑动的不确定进度条 + 「第 X/Y 步」（`IngestJob.step` / `step_total`），文件面板一批上传时再加「文件 X/Y」（`lib/uploadBatch.ts`）。
    计划里提到的 LightRAG 前端兜底 `pctOf(0)`：后端 `progress_total` 恒 ≥1，百分比总是数字，兜底不会被用到，未改。
    验证：`test_api.py` 新增 2 项、`test_build_hardening.py` 新增 1 项（前两项还原修复时失败）。
  - [x] ③ PDF 模式去掉文档头（标题、作者、徽章、重新抽取）与工具栏里重复的标题，外边距 12px → 4px；阅读模式全局（`ConsoleContext.readingMode`，
    localStorage `ka-reading-mode`），默认 PDF。
  - [x] ④ citation 跳转：统一入口 `jumpToCitation`（带来源文字、页码、图框与 nonce）；引用文档不在左侧当前集合时用 `getDocument` 取回
    （404 回列表）；分块界面等分块加载完再定位，chunk_id 因重切换了修订号时按来源文字回找，定位不到也清掉状态（以前会卡住，列表里点别的文档又被拉回）。
    PDF 定位：`lib/pdfLocate.ts`（两边只留字母数字比较，分块首尾各一个 40 字符锚点，在分块页码 ±1 页内找，从锚点逐字外扩到对不上为止，可跨页）；
    图块按 `figure_bbox` 画框；都找不到时跳到起始页、整页闪一下并提示，可「在 MD 中查看该段」（只这一次，不改全局）。高亮用固定蓝色（主题强调色在部分配色下接近白色）。
    验证：两篇 arXiv 论文（Attention 15 页、ViT 22 页）用项目自带 pdf.js 抽文字，全部正文分块定位成功 Attention 40/40、ViT 53/53。
  - [x] ⑤ 版本号 2.1.1；README / README_ZH 补引用跳 PDF 与默认 PDF 视图。
  - 验证：pytest 1545 passed / 4 skipped（新增 4 项）；ruff；mypy；eslint；tsc；`next build`。真后端 + `next dev` + Playwright（`/api/ask` 用真分块拼的回答替身）15 项全过：
    打开 PDF 默认 PDF 视图、无文档头，第一页顶部 y=153；同文档跨页正文高亮在第 2、3 页；左侧选 papersA 时跳 papersB 的 ViT 并高亮；
    图块在第 7 页画框；文字对不上时提示并停在第 4 页，「在 MD 中查看该段」按 chunk_id 闪烁且不改全局；下一次跳转回到 PDF；
    MD 模式跳另一篇文档在分块加载后闪烁；跳转后回列表能正常打开别的文档；刷新后模式保持。进度栏：上传时显示「step 1/2 · 文件名」、
    滑动条、无百分比；文件面板一次传两份显示「file 1/2」「file 2/2」。
    未验证：真实 Milvus 环境下向量库节点自动恢复（本机测试环境无 Milvus，单测覆盖信号）；Milvus / LightRAG 进度在真实任务中的观感
    （单测覆盖算法）；索引阶段「step 2/2」（无 Milvus 时瞬间完成）；旋转页或带 CropBox 偏移的 PDF 上图框位置；桌面壳里实测。
- [x] P28 · 2.1.2（2.1.1 之后的修复）。决策（已批准）：Zotero 严格镜像不再全量重建向量库；文档列表不再等处理锁；
  列表加载失败显示错误与重试；版本 2.1.2，先推 developer，正式版本累计后再打标签。
  - [x] ① Zotero 同步后「文档爆炸」：strict_mirror 只要有新增 / 变更 / 删除就 `needs_milvus_rebuild=True`，同步收尾调
    `rebuild_vector_store()` 清空 Milvus 再把全库重灌一遍（真实日志：11-10 ×3、11-12、11-13、11-16 每次 new>0 都触发，一次约 6 分钟）。
    新文档在同步中已逐篇索引（`retain_document_chunks` 清掉旧分块），删除也已逐篇移出 Milvus，全量重建是多余的。
  - [x] ② 重建期间集合显示「0 篇文献」：`/api/documents` 的 `_document_dict` 调 `list_document_chunks`（`@serialized_processing`，
    还会顺手重切旧分块），全量重建持锁数分钟，列表请求排队到前端 30 s 超时，`catch` 把列表置空。
  - [x] ③ 前端列表请求失败时显示错误与重试，不再显示「0 篇 / 该集合暂无文档」。
  - [x] ④ 版本号 2.1.2；CHANGELOG。
  - 验证：pytest 1548 passed / 4 skipped（新增 3 项：`test_zotero_strict_pull_with_new_documents_does_not_rebuild_milvus`、
    `test_zotero_remove_index_failure_marks_milvus_incompatible`、`test_list_documents_does_not_wait_for_processing_lock`；
    后两项还原修复时失败，strict 全量重建由 `test_pull_strict_hard_deletes_removed` 的 `to_dict()` 断言兜住）；ruff；mypy；eslint；tsc；`next build`。
    未验证：桌面壳里真实 Zotero 同步 + Milvus 实测。用户本机 11-16 03:27 在全量重建中途退出，`index_compatibility.json` 仍是
    「重建进行中」，升级后首次启动会再完整重建一次（属正常恢复，期间列表不再显示 0 篇）。
    遗留：`XT7UNQSM` 每次同步 Web 下载 Not found，`MTA6FXYE` 每次以 0 分块「重建」，未查。
- [x] P29 · 2.1.2 第二批：「文件」「文档」两栏的选论文交互。方案页 `data/ka_doclist/ka_doclist_plan.html`（工作区本地，未入库），
  决策（已批准，11-17）：列表两行（标题 / 作者 · 年份）；父集合含子集合、按子集合分组可折叠；去掉「LightRAG 集合」区，
  图谱状态点在集合行上、构建进 ⋯ 菜单；阅读时左栏「论文」「笔记」两个页签、记住上次。评论：⋯ 菜单项文字左边加图标。
  - [x] ① 后端：`api.list_scope_documents(name)`（集合 + 全部子集合，按归属表）合并图谱就绪 / 建图 / 识图三处同一段逻辑；
    `/api/documents?collection=&descendants=1` 用它；`_document_dict` 改用 `document_biblio_meta`（Zotero 字段优先、文件名猜测补空）
    并带 `collection_keys`；`/api/collections` 带 `doc_count`（本级）与 `doc_count_total`（含子集合、按论文去重），由
    `api.collection_doc_counts()` 从全部 active 文档算出（不改 store 接口）；新路由 `GET /api/library/summary`（全库 active 篇数，
    放在 `web/desktop_routes.py`）。
  - [x] ② 前端纯函数 `lib/docList.ts`（第二行、查找、排序、问题标记、子树先序、按子集合分组）+ `tests/frontend/test_doc_list.py`。
  - [x] ③ 中栏：`DocList`（两行 / 一行两种密度、查找框、排序、只看有问题、组头折叠、↑↓ Enter）、`lib/useCollectionDocs.ts`
    （模块级 store，中栏与左栏共用一份列表、查找词与排序；排序记在本机）、阅读时栏头 ‹ n/m ›；去掉 PDF 标与无效查找键；
    返回列表时停在刚读的那篇。
  - [x] ④ 左栏：`CollectionTree` 从 `FilePanel` 拆出，对话框拆到 `CollectionDialogs.tsx` 与 `UploadDocumentDialog.tsx`（FilePanel
    1500 → 360 行）；「全部文档」行、含子集合篇数、选中项的祖先自动展开；图谱点只画建好 / 正在建 / 中断；⋯ 菜单（右键同样打开，
    每项带图标）收构建图谱（中断时为「继续构建图谱」）/ 补全图表描述 / 新建子集合 / 改名 / 删除；去掉 LightRAG 区、
    `ActiveBuildCard`（进度在 ProgressDock）、`data-mode="lightrag"` 与对应 CSS；栏头只留 R2；旧的 `lr:` 选择键自动换成树上的键。
  - [x] ⑤ 阅读时左栏 `ReadingSidebar`（‹ 集合名、论文 / 笔记页签，页签记在本机；从行上笔记键进来先开笔记）；`NotePanel`
    改按 doc_id 取文档（以前按集合名在列表里找，子集合的论文会一直「加载中」）、`embedded` 时不画外框与返回键。
  - [x] ⑥ i18n（zh / en 各 25 条）、README / README_ZH、CHANGELOG。
  - 验证：pytest 1556 passed / 4 skipped（新增：`test_web_server.py` 3 项、`tests/frontend/test_doc_list.py` 5 项；后端 3 项还原修复时失败）；
    ruff；mypy；eslint；tsc；`next build`。真后端（用户库 `knowledge_repository.db` 的副本，148 篇）+ `next dev` + Playwright 19 项全过：
    「[26.2]」显示 64 篇（含子集合）且按子集合分组、Yuk Hui 两本书出现、折叠 Sage Handbook 组少 38 行、查找「hui」2 行、
    Zotero 集合菜单 2 项 / 本地集合 5 项、打开论文后左栏两个页签与 41/64、下一篇 42/64、左栏点第一篇 1/64、笔记页签加载出来、
    返回后树可见且列表停在刚读的论文、「[25.1]」有问题 1 篇且筛出 Mediascapes、全部文档 148 篇、↓↓ Enter 打开第 2 篇；
    后端 110/148 篇带作者或年份（以前 6 篇）。
    未验证：桌面壳里实测；中文界面截图（测试机缺中文字体）；有真实 PDF 时的阅读器（副本没带 library 原件，PDF 为 404）。
- [ ] P30 · 向量检索内存（🚧）。起因：KA_Benchmark s01 全库评测（494 篇 / 52,477 片段）中 KA 进程被 10G 上限多次杀掉。
  根因：集合用 `index_type="FLAT"`，Milvus Lite 3.1.1 的暴力检索每搜一个分段都复制一份有效向量再算距离
  （`milvus_lite/index/brute_force.py:117`），全库一次 +0.59G，同时 4 个检索在纯向量库下就超 4G。
  实测（真实库副本）：HNSW（M=16、efConstruction=200、ef=64）检索无内存尖峰、单次 1073 → 约 400 ms，
  top-10 与 FLAT 重合 99.5%；已有库换索引 52 s（不重新嵌入），索引落盘、重新打开 4.2 s。
  决策（已批准，11-18）：先做 P1–P3；已有库升级时自动换索引。
  - [x] ① 配置：`VectorDbConfig` 增加 `index_type`（HNSW | FLAT，默认 HNSW）、`hnsw_m`、`hnsw_ef_construction`、
    `hnsw_ef_search`、`search_concurrency`（内部项，不进设置界面）。
  - [x] ② `MilvusLiteVectorStore`：新集合按配置建索引；已有集合索引不符时只标记待迁移、照常检索；
    `migrate_index()` 在后台把索引换成配置值（持 `_init_lock`，等在途操作结束；期间新操作排队），
    无索引（迁移中途崩溃）时开库即补建；HNSW 检索传 `ef`；检索并发上限 `search_concurrency`。
    `VectorStore.migrate_index()` 默认空操作（base 契约）。
  - [x] ③ 组合根：开库后有待迁移时起后台任务；teardown 先收走该任务再关库。
  - [x] ④ 测试：配置解析、索引参数、迁移状态与排队、teardown 顺序；真实 Milvus Lite 小库迁移回归。
  - [ ] ⑤ 长跑内存（P2）：换索引后用 KA_Benchmark 内存探针连续提问，确认是否仍只涨不落；再决定是否需要剖析。
  - 验证（①–④）：pytest 1566 passed / 4 skipped（新增 `tests/backend/test_milvus_index.py` 10 项，含真实 Milvus Lite 小库
    FLAT → HNSW 迁移；4 个文件里替身 Milvus 构造器补 `**kwargs`）；ruff；mypy。真实库副本（52,477 片段）实测见上。
  - [x] ⑥ 上游断连（P3）：已分配对话模型但合成调用失败时，`api.ask` 不再用离线占位文本冒充答案（评测断网期间 8 题拿到
    「This is an offline placeholder answer…」），改为检索摘录排版（`_excerpt_answer`，与未接模型时同一段），响应新增
    `generation_status`（失败为 `llm_unavailable`，正常为空）。离线占位只留给未分配模型。降级为 default 本身界面已有标识；
    重试再拉长扛不住十几分钟的断网，不改。验证：pytest 1568 passed / 4 skipped（`test_api.py` 新增 2 项）。
- [ ] P31 · 2.1.2 之后的修复（🚧）。
  - [ ] ① Intel macOS 安装包后端起不来：v2.1.1 x64 DMG 启动即 `ImportError: Library not loaded:
    /usr/local/opt/openssl@3/lib/libssl.3.dylib`（cryptography 的 `_rust.abi3.so`）。cryptography 49.0.0 起不再发布
    Intel macOS wheel，`macos-15-intel` runner 从源码构建并链接了 runner 的 Homebrew openssl；runner 上有这个库，冒烟没拦住。
    `bundle_python.py` 的 `install_requirements` 加 `--only-binary :all:`（uv 在 x86_64-apple-darwin 上自动解析到
    cryptography 48.0.1，其余依赖不变；四个平台 `uv pip compile --only-binary :all:` 均可解析），冒烟导入加
    `kacore.secret_store`。待 CI 出 x64 DMG 后在 Intel Mac 上验证。
  - [ ] ②（代码已落地，壳待 CI 编译、待 Windows 实测）Windows 后端「活着但不听」：asyncio Proactor 循环在 accept 出错（`WinError 64`，客户端在握手中途断开）时
    `sock.close()` 关掉监听 socket 且不再恢复（`proactor_events.py` `_start_serving`），进程不退出，壳不报崩溃，
    界面停在 Loading / 「Log endpoint is unavailable」，偏好里的「重启服务」走 HTTP 软重启同样发不出去。
    后端每秒检查监听 socket，被关就在原端口重新监听（`ka_server/lifecycle.py` 的 `keep_listening`）；
    桌面界面连续连不上后端时进入崩溃页，「重启服务」由壳重启进程（`BackendGate`）。
    托盘菜单加「Restart service」（进程级重启，`desktop/src-tauri/src/lib.rs` 的 `restart_sidecar` 与崩溃页共用）。
    验证：pytest 1575 passed（`test_ka_server.py` 新增 2 项：模拟 Proactor 关掉监听后原端口恢复、正常监听不重建）；
    ruff / mypy；前端 lint / tsc / build。未验证（本机无 Rust）：`cargo check` / `clippy` / `fmt`。
  - [x] ③ 深度思考不选集合也能用（Global or scoped）。现状：`api.ask` 已放行空集合，但界面把深度思考按钮置灰并自动退回
    default，`retrieve_agent_evidence` 与 skill 客户端要求 collection。另外召回编排器在集合为空时跳过向量召回（注释称 Milvus
    「按 workspace 分库」，实际是单表 `kb_chunks` + `collection_tag` 过滤），全局深度思考 / 增强只剩词法召回；选父集合时向量召回
    只按父集合自己的 tag 过滤，子集合文档召不到。
    - [x] 召回：有 scope 时向量检索不按 tag 过滤（由 doc_id 白名单约束），全局时不过滤并排除非 active 文档；
      `VectorStore.search` 契约写明空 collection = 不按集合过滤（`RetrievalOrchestrator._search_dense` / `_keep_active_chunks`）。
    - [x] default 路径删掉按集合名扇出再合并（`_resolve_ask_collections`）：召回一次即覆盖子树 / 全局，扇出只剩重复检索。
    - [x] 接口与界面：agent evidence 去掉 deep_thinking 必须带集合；删掉无人使用的 `STRICT_COLLECTION_MODES` /
      `STRICT_DOCUMENT_MODES`；`ChatPanel` 不再置灰 / 自动退回；skill 客户端与 `references/research.md`、README 中英同步。
    - 验证：pytest 1579 passed（`test_retrieval_orchestrator.py` 新增全局向量召回剔除 detached、父集合召回子集合文档 2 项；
      `test_vector_store.py` 新增内存 / 真实 Milvus Lite 空 collection 跨集合 2 项；`test_api.py` 扇出测试改为单次召回，
      `test_agent_evidence.py` 改为允许全局）；ruff；mypy；前端 lint / tsc / build。
  - [ ] ④ 无 embedding / rerank 时的检索（🚧）。没有向量库时只剩锚点 + 关键词两路，关键词通道（`LIKE` OR + 词频）排序差、
    会把常用词命中的整片分块拉进 Python；`.md` / `.txt` 上传被当成 PDF 抽取（原件统一存为 `original.pdf`，按后缀判断）。
    口径（用户 11-18）：不触发已有库的重新抽取 / 重新分块 / 重新嵌入——不改 `PROCESSING_VERSION`，旧的误抽文档不自动恢复
    （偏离原计划 Phase 1 的「启动时自动恢复」，理由：已有用户库不应因本次修复被整库重处理；需要时手动重处理）。
    - [x] 导入：按原件内容（前 1024 字节内有 `%PDF-`）判断 PDF（`ingest_manager._is_pdf_file`，与 `_prefers_reextract`、
      `source_pdf_path` 共用）；`_extract_artifact` 去掉不再使用的 `content_type` 参数，`reextract_document` 的文本恢复分支
      并入 `_extract_artifact`。旧的误抽文本文档可经手动重处理（`/api/documents/{id}/reextract`）恢复。
      验证：`test_ingest_manager.py` 新增 4 项（旧代码下 3 项失败：误抽的标志是 converter=pymupdf4llm；短中文样本旧代码
      并未丢正文，所以断言落在 converter 上）。
    - [x] 关键词通道换 SQLite FTS5（BM25，`porter unicode61`，中日汉字预拆二元组，查询去停用词）；迁移 028 只新增表与触发器。
      打包解释器（python-build-standalone 3.12.13）自带 SQLite 3.53.1，FTS5 / porter / contentless_delete 均可用。
      决定：① FTS 不挂 chunks 的隐式 rowid（TEXT 主键，VACUUM 可能重编号），经 `chunk_search_rows` 的 INTEGER 主键映射；
      ② 写入在两处写入点显式维护（`search_index.index_chunks`），删除 / 改文本用触发器（覆盖级联删除）；
      ③ 派生文本只能用 Python 算，迁移里不回填，改为启动时 `backfill_search_index` 补齐（也补上旧库导入 / R2 恢复进来的分块）；
      ④ `T55` 标题加权由锚点通道覆盖（`test_retrieval_orchestrator_prioritizes_thesis_heading_anchor` 未改仍过），不在新通道复刻。
      现有测试断言均未修改。验证：`test_search_text.py` 6 项、`test_keyword_recall.py` 23 项新增；retrieval / scope / api /
      agent_evidence / skill client 相关测试全过；ruff。未做（用户 11-18 要求先推送）：全量 pytest、mypy、5 万分块性能对比
      （首轮合成数据词频失真，作废；Zipf 数据那轮未跑完）、端到端实测。
    - [x] skill：`references/research.md` 增「Keyword-only recall」一节，`SKILL.md` 一句指针；`doctor` 新增 `retrieval_engines` /
      `vector_recall`（取自 `/api/capabilities` 的 retrieval 环节，服务端未改）。`_vector_store_stage` 泛化为 `_pipeline_stage`。
      验证（11-18）：全量 pytest 1612 passed / 1 failed（`test_legacy_import.py::test_api_import_end_to_end_through_soft_restart`，
      本机断网 DNS 解析失败；`0c1ee58` 上同样失败，与本次改动无关，联网后复跑）；ruff；mypy。端到端（全新数据目录、无 embedding /
      rerank，中英各一篇 Markdown）6 个用例全部符合：长句 Limitations 段排第一、中文长句命中正文、`Table 1` 锚点 + 关键词、
      全局 deep_thinking 中英都召回、`doctor` 显示 `vector_recall: false`。
    - [ ] 性能：5 万分块 LIKE vs FTS5 中位耗时、启动补索引耗时（批量写入后未测）。用户 11-18：先实现并推送，性能稍后。
- [ ] P32 · Deep Thinking 迭代（🚧）。计划：`docs/deep-thinking-iteration-plan.md`（用户 11-18：跑完汇报，可暂缓 push）。
  每个 Phase 一个提交，用 KA-Benchmark 的 mini（24 题）与基线运行对比；噪声约 3 题翻转。
  - [x] P0 · 基线：`ab9c749` 上跑两次 mini（18/24、19/24，噪声 3 题翻转；数字见计划文档「结果」）。
  - [x] P1 · 重排提速：`EvidenceSession.select` 各 query 池的重排并发（锁只护缓存）；「补候选」每个 query 只在
    首次出现时补一次、之后沿用同一批（不再随新块反复补、反复重打分），匹配词改用 `search_text.query_terms`（去停用词）。
    代码已落地：`kacore/pipelines/evidence_session.py`（`_padding_for` / `_rerank_missing`）；`test_evidence_session.py` 新增 3 项
    （旧代码下均失败）；全量 pytest 1616 passed、ruff、mypy。mini：重排 −34%、中位耗时 −10~25%，答对未降（见计划文档）。
  - [x] P2 · 证据稀释：**主次方面降权 + 18→12 撤回，只保留锚点修复**。试过：主方面（原问题 + PLAN 子问题）保底、次方面（第 2 轮起
    补检 query / 校验缺口）不保底且补位 × 0.5、`max_final_evidence` 18→12。mini 两次（`cc81663`，对 P1）：20/24、19/24（P1 22/24），
    稳定答对题 W2988278250 金标证据 2/2→0/2、来源从 2 篇散到 8 篇；W2560622558 没找到对的论文。结论：第 2 轮起的 query 是 SEA
    针对缺口生成的补检，金标常靠它们找到，不是偏题方面——假设不成立。保留：会话路径补回结构锚点保护（`_gather_round` 收集
    `anchor_hit` 进 `pinned_ids` 并传给 `select`；`test_structural_anchor_survives_tight_budget_in_session_path` 旧代码下失败）。
  - [x] P3 · 收敛条件：关键项都满足且 SEA 判充分即收敛，新发现的方面不再强制追一轮（以前 `converged` 要求本轮无新发现）。
    `deep_thinking_orchestrator.py`；测试 `test_sufficient_round_with_new_discovery_converges`（旧代码下失败）。
    mini 两次 21/24（基线 21/24）；ResearchQA 调用 −15~21%、中位耗时 −20~27%，MultiHop-RAG 不变。保留。
  - [x] P4 · 校验闭环：补检词只用 `info_gaps` + 硬缺失（不再用 `partial_claims` 断言句）；补检后的一稿只在不比上一稿差时
    替换它（上一稿合格而新稿不合格、或新稿硬缺失更多 → 退回上一稿，与它的证据一起返回；打平用新稿）；第二次合成或校验失败时
    返回已校验的上一稿（以前返回 None 交 api.ask 重合成）。`deep_thinking_orchestrator.py`；测试新增 3 项（旧代码下均失败）。
    mini 两次 22/24、20/24（P3 21/24），成本持平，保留。题集已饱和（15 题恒对、9 题不稳定），见计划文档。
  - [x] 换题集 mini_v2（用户 11-19：v1「有点不显著」）：KA-Benchmark 每场景 4 对 + 20 错共 48 题、干扰文献 100 篇，
    每个候选跑两次，用 `kabench mini pool` 按题合并答对率、给置换检验 p 值。`22f5fa7` 上两次 v2 基线：35/48、35/48，
    同一提交 10 题翻转（5 / 5）；48 题里恒对 30、恒错 8、不稳定 10。一次约 11 分钟。
  - [x] P5a · `deep_thinking.rerank_weight` 0.2 → 0.5（`9aaa181`）——**撤回**。理由原是：s01 里 ResearchQA 答错多是「找到了
    文献、没拿到关键段落」，重排分权重低时证据排序主要靠 RRF。v2 两次对两次：合计 −1.0（变好 5 / 变差 8，p = 0.81），
    ResearchQA −1.5；ResearchQA 关键段落命中 0.705 → 0.705 不变，假设不成立。成本持平。
  - [x] P5b · deep 合成篇幅随问题收放（`3706b97`）——**撤回**：窄问题（事实 / 数值 / 是非）先直答、几段说明，宽问题才分节。
    回答中位长度 −23~28%、小标题 1.5 → 0，但 v2 三次对三次：合计 −2.0（变好 6 / 变差 10，p = 0.29），ResearchQA 持平、
    MultiHop-RAG 三次都低于基线（−2.0，变差多在 inference_query）。没有收益，撤回；耗时上涨是 hub 时段（基线第 3 次同样变慢）。
  - [x] P5c · deep 合成先核对问题前提（`e2a8ca3`）：证据与前提矛盾、或问题假定的来源 / 实体在证据里没有时，先明说；
    「部分证据 → 有据推断」只用于前提成立的情况。理由：s01 假前提题 deep 多顺着前提作答，模板要求「不要以没有证据拒答」。
    v2 三次对三次：合计 +0.7（变好 10 / 变差 9，p = 0.87）；ResearchQA +1.7（5 / 2，p = 0.28）、MultiHop-RAG −1.0；
    假前提两类（adversarial、null_query）合计 +1.0。成本持平。**保留**（修的是规则矛盾、答对与成本持平，同 P4），
    mini 未证明有效，待全量切片确认。
  - 小结（11-19）：P5 三项在 mini_v2 上都没有显著变化。3 次对 3 次能分辨的大约是 ±4 题；48 题里 22 题时对时错，
    deep_thinking 自身的答案波动是目前最大的噪声来源。下一步：在全量切片（b02 + b03）上跑 P1–P5c 的累计效果。
- [ ] P33 · 3.0.0 回答引用外部文献、Zotero 回推与迁移（🚧）。计划：工作区本地 `data/ka_v3/ka_v3_plan.html`（未入库），
  交互原型 `data/ka_v3/ka_v3_demo.html`。决策（11-19 ~ 11-20 用户确认）：对话栏只在打开「联网补充」时查外部；skill 先问用户、
  同意后才查；外部服务只取数据（书目、标识、参考文献、撤稿标记），写检索词 / 排序 / 归类只用用户配置的模型；核验 = 持久标识 +
  拿编号去第二个索引直接查得到（P0 实测两家检索结果很少重合，故不比两份结果）；每个回答一个「来源」栏（照 ai-source-scraper 记账：capture、rank、去重、排名漂移；导出列兼容）；
  「体系」= 用户自己的集合树；图谱边只来自参考文献列表；外部全文读到多深先按「能拿到开放获取全文就读片段，否则摘要」实现，
  做成设置项，之后再定；入库文件名按用户现有风格（`{作者} ({年份}) - {标题}`，目录 = 集合路径）；Zotero 推送只走
  「KA 放文件 + Web API 建独立 linked_file 附件」，推送 = 移交（不留副本）；导出 `.kaarch`（密钥用口令另行加密），
  导入时本机库非空弹框选「完全覆盖 / 合并」；版本 3.0.0。
  - [ ] P0 验证：① 🚧→已测（11-20）：三个研究题上 Crossref / arXiv / OpenAlex 各返回 10 条，主题对口；前 10 条里
    Crossref 与 OpenAlex 同一 DOI 只重合 1–2 篇 → 核验改为「拿编号去另一家查」；Crossref 摘要覆盖 2–4 / 10、参考文献 3–6 / 10，
    OpenAlex 参考文献 8–10 / 10；arXiv 只有 1–3 / 10 带期刊 DOI；OpenAlex 无 key 每天 0.1 美元、一次检索 0.001 美元（约 100 次）；
    同一作品的预印本与会议版在 OpenAlex 里是两条（只靠索引给出的编号关联合并，不按标题猜）。② Zotero Web API 能否建带
    `path` 的独立 linked_file 附件（需用户的写权限 key，测试条目验完删除）。
  - [x] P1 数据层：迁移 `029_external_sources.sql` / `030_zotero_push_ledger.sql`；domain `web_sources`（标识规整、
    按编号归并、核验规则、排名漂移）、`file_style`（命名 / 目录风格、Windows 字符、风格统计）、`external_ledger`（记录类型）；
    `kacore/adapters/scholarly/`（Crossref、arXiv、OpenAlex、Semantic Scholar、DataCite、doi.org；限速、可注入请求函数、
    内存实现）；仓储契约 `source_store/external.py` 与 SQLite / 内存实现（混入源文档库）。作者姓名只从结构化来源取
    （Crossref family/given、DataCite familyName/givenName），整名不拆（沿用 `citation.normalize_creators` 的规则）。
    验证（11-20）：新增测试 4 个文件 55 项；全量 pytest 1671 passed / 4 skipped；ruff；mypy（domain 严格）。
    真实接口冒烟：三家检索、按编号互查（判为已核验）、DataCite 补姓氏、doi.org 分辨真假 DOI 均正常。
  - [x] P2 问答接入（后端）：`kacore/managers/external_evidence.py`（写检索词 → 并发检索 → 按编号归并 → 第二个索引按编号
    查证 + 补结构化作者 → 模型挑选 → 全文片段 / 摘要；`persist` 写账本，is_new 按同一对话计算）、`external_fulltext.py`
    （开放获取 PDF 临时缓存，按内容 sha256 命名，只收 `%PDF-`）、`pipelines/external_prompts.py`（plan / select 两次调用，
    research 角色；挑选时同一作品只留一个版本）、`adapters/scholarly/factory.py`；`ask(external=True)` 外部证据编号续接、
    deep / enhanced 有外部证据时重写一稿、已在库（按 DOI）的不作外部证据、结果与聊天记录带 `external` 摘要、引用段落入账；
    `POST /api/external/evidence`（skill 用：必须 `user_approved=true`，助手给检索词、KA 不调模型，进 agent 白名单）；
    skill 客户端 `external-evidence --user-approved`、SKILL.md 联网边界改为「先问再查」、research.md 新增 External literature；
    设置分组 `prefs.external`（索引开关、depth、篇数上限、两个 key、Crossref 邮箱）与中英文标签；`citation_rendering.cited_numbers`。
    外部来源条目与库内同形（`origin: "external"`），引用改写不改。索引同一实例一次一个请求、429 按 Retry-After 重试一次。
    验证（11-20）：新增测试 `test_external_evidence.py` 8 项、`test_ask_external.py` 6 项、客户端与适配器各 1 项；全量
    pytest 1688 passed / 4 skipped；ruff；mypy。真实接口冒烟（两个检索词、无 key）：38 篇、已核验 12 / 单源 26，Crossref
    并发时 429（已改为串行 + 重试）；同一作品的会议版与预印本各占一条（交给挑选模型去重）。对话栏开关在 P3。
  - [x] P2b 人文专著（用户 11-20：arXiv 对人文帮助有限，「openlib 可以」）：ISBN 列为可核验的持久标识
    （`normalize_isbn` 校验位、ISBN-10 转 13、一书多 ISBN 作别名）；`adapters/scholarly/openlibrary.py`（检索 + 按 ISBN 查）；
    Crossref 读 `ISBN` 并支持按 ISBN 查；ISBN 核验链 Open Library ↔ Crossref；设置 `prefs.external.openlibrary`（默认开）。
    限制：Open Library 不给结构化姓名（整名只作显示）、通常没有摘要，书多半只进来源栏供核验与入库，不作回答片段。
    访问权限（用户问能否配置）：按来源开关 + key 的机制已有；用学校账号自动访问付费库不做（机构统一登录 + 多重验证、出版社
    条款禁止程序批量下载）；可选折中「学校代理前缀 → 通过学校打开」放 P4，待用户确认。
    验证：离线测试（用户 11-20 关心真实请求的外露与费用，本步未发真实请求）；全量 pytest 通过。
  - [x] P2c 数据流「联网补充」节点（用户 11-20：「节点加」；OpenAlex「不默认关，走免费，用完拉倒」）：设置从
    `prefs.external.*` 改到节点分组 `flow.external.*`（目标键不变）；`capabilities.detect_pipeline` 新增 `external` 环节，
    `api_capabilities._overlay_external_runtime` 叠加各数据库最近成功 / 错误、OpenAlex 今日免费额度、两个 key 是否已填；
    `kacore/managers/external_status.py` 读 OpenAlex 响应头记额度，额度用完到重置时间前自动跳过（429 且额度为 0 时不重试），
    其余数据库照常；适配器 `attach_observer`。前端：`components/flow/model.ts`（节点、网格 col5 row4、虚线连问答）、
    `Icons.tsx`（globe）、`ExternalActions.tsx`（数据库状态表与额度）、`QuickConfigPanel.tsx`、文案与样式。
    验证：新增 `test_external_status.py` 3 项、编排 1 项、能力 1 项；全量 pytest、ruff、mypy、前端 tsc / lint 通过；
    真后端 + next dev + Playwright 截图：节点、开关、key 输入、状态表与连线均正常（未向外部数据库发请求）。
  - [x] P3 来源栏与对话栏开关（用户 11-20：「刚刚讨论中的前端检查一下是否已经完成」）：后端 `kacore/api_external.py`
    （`ExternalApiMixin`：一次联网补充的详情——每篇的核验、各数据库名次、同一对话里的排名历史与漂移、回答引用的段落与页码、
    字段来源；按用户集合树归类，第一次打开「体系」时才调用 research 模型、结果存回账本；导出 `ka-sources_<capture>.csv/.json`，
    前 15 列同 ai-source-scraper 1.7.0，后接 trust / pid / cited_passages / pages）；`kacore/domain/source_graph.py`
    （图谱边只来自参考文献列表：引用、共同引用、引用库内文档）；`pipelines/external_prompts.py` 归类提示；路由
    `GET /api/external/captures/{id}`、`POST …/placement`、`POST …/export`。前端：`components/panels/SourcesPanel.tsx`
    （列表 / 体系 / 图谱三栏 + 出处详情 + 导出；占左侧文件区，关闭后恢复）、`ChatPanel.tsx`（输入框「联网补充」开关，
    只对当前对话有效、新对话默认关；回答下方「来源 · 外部 N 篇」条；外部引用点开来源栏并定位）、`lib/externalApi.ts`、
    `ConsoleContext.tsx`、`app/(console)/layout.tsx`、文案与样式。「入库…」按钮先置灰，P4 接上。
    验证：新增 `test_source_graph.py` 1 项、`test_ask_external.py` 1 项（详情、归类、导出）；全量 pytest 1703 passed /
    4 skipped；ruff；mypy；前端 lint / tsc / build。真后端 + next dev + Playwright：用写进临时库的示例数据（不发外部请求）
    截图检查开关、来源条、列表、体系、图谱，无页面错误；图谱长作者名截断保留年份。
  - [x] P3b 图谱手感与显示（用户 11-20：图放大难、点也看不清）：图谱拆成 `components/panels/SourceGraph.tsx`，
    画布铺满左栏、按真实像素绘制；操作同数据流页（滚轮以光标为中心缩放、拖空白处平移、放大 / 缩小 / 适应窗口），
    另可拖节点、悬停或 Tab 聚焦显示信息卡并调暗不相连的节点、点圆点或标签都能选中、双击空白处适应窗口；
    `lib/sourceGraphLayout.ts` 布局改为按相连的簇分别力导向、孤立节点排成网格再逐行拼放（确定性，刷新不跳）；
    适应窗口按标签宽度留边；标签字号不小于 12px；节点超过 14 个时只常显引用的、选中的与悬停相邻的标签，
    放大到 1.5 倍以上全部显示；「只看回答引用的」开关；图例改为图形；库内节点显示文档标题
    （`api_external` 新增 `graph.library_titles`）。验证：`test_ask_external.py` 增加库内节点断言；全量 pytest
    1703 passed / 4 skipped；ruff；mypy；前端 lint / tsc / build；Playwright 用 20 篇示例（不发外部请求）走查
    适应窗口、悬停、滚轮放大、平移、拖节点、单击标签选中、只看引用的、小图。
  - [x] P3c 整理：联网功能改为工具调用（用户 11-22 批准，计划 `data/ka_v3/ka_v3_tools_plan.html`，未入库）。
    用户 11-22：代码要足够干净、不预设很多东西；人文专著这类特殊代码不合适；「将每个联网功能适配成类似 MCP 的方式，
    允许一个 distributor 通过任务内容调用」，便于之后调试与管理来源。取代 P1–P3b 里按索引写死的部分：
    逐索引适配器、DOI/arXiv/PMID/ISBN 规整与第二家互查、DataCite / doi.org、OpenAlex 额度模块、按库名写的配置与
    设置项、图谱与排名漂移、P4/P5 提前写的 `file_style.py` 与迁移 030。决策：内置 6 个工具（crossref / openalex /
    arxiv / semantic_scholar / openlibrary 检索 + read_open_pdf）；核验 = 可追溯（引用段落与书目字段逐字出现在本次
    运行记下的工具原文里）；distributor 用供应商原生工具调用，默认最多 4 轮（假设值：检索、读全文、登记引用、
    重试一次被拒引用）；skill 路径由助手自己挑工具，KA 只执行、核对、记账。
    - [x] T1 工具层：`domain/tool_calls.py`（类型 + `trace_check`）、`adapters/tools/`（base、registry、6 个工具；
      OpenAlex 额度只在 `openalex.py`）；设置项由工具声明生成（`tool_setting_specs`，home `flow.external.tools`），
      配置 `ToolsConfig`；删除 `adapters/scholarly/`、`domain/web_sources.py`、`managers/external_status.py`，
      `managers/external_fulltext.py` 并入 `adapters/tools/read_open_pdf.py`。
    - [x] T2 供应商工具调用：`ProviderClient.generate_with_tools`（默认 `ERROR_NO_TOOLS`）、OpenAI 兼容（tools /
      tool_calls，助手原始消息含 reasoning_content 原样回放，400 / 422 报 ERROR_NO_TOOLS）、Anthropic（tools / tool_use，
      思考块原样回放，相邻工具结果合进一条 user 消息）、`RoleClient` 委派。
    - [x] T3 distributor 与账本：`managers/tool_distributor.py`（`ToolSession` 执行并记账、cite 核对、可按 capture_id
      恢复；`ToolDistributor` 最多 `max_rounds` 轮，最后一轮只给 cite；`place` 归类）、`pipelines/external_prompts.py`
      重写、迁移 `029_tool_calls.sql`（DROP 旧 029 / 030 的表）、`ToolLedgerRepository`（SQLite / 内存）、`api.py`
      接入（`_collect_external` / `_finish_external`，已在库按 DOI 跳过）、`api_external.py` 重写（来源详情、归类、导出、
      工具接口）；删除 `external_evidence.py`、`file_style.py`、`source_graph.py`、`external_ledger.py`、迁移 030；
      `api_capabilities.py` 恢复 2.2.0 原样，`capabilities.py` 的节点只读开着几个工具。
    - [x] T4 界面：`components/flow/ToolsPanel.tsx`（开关、设置、试调用、最近调用；全部来自工具声明）；来源栏只留列表 /
      体系，来源标「工具 · 调用 · 块」，可看每次调用的参数与原文；对话栏来源条与「没有运行」的原因文案；删除
      `ExternalActions.tsx`、`SourceGraph.tsx`、`sourceGraphLayout.ts` 与相应样式和文案。
    - [x] T5 skill 与收尾：`GET /api/tools`、`GET /api/tools/calls`、`POST /api/tools/{name}/call`（须 user_approved）、
      `POST /api/tools/cite`，进 agent 白名单；skill 命令 `tools` / `tool-call` / `tool-cite`，SKILL.md、research.md；
      skill 的可写配置不含工具设置（工具开关由用户在面板里定）。
    - [x] T6 真实模型冒烟（用户 11-22「测试也给我做」）：用户库副本 + arc:chat（经 hub）+ 真实 Crossref / OpenAlex 等，
      三个问题各调用 8 次工具、登记 0 条引用。根因：最后一轮只给 cite 但模型照调检索工具、distributor 照样执行；
      模型不知道轮数，一直在搜。修：未开放的工具调用拒绝并记账；任务里写明轮数；最后一轮前追加「检索结束，现在登记」；
      Anthropic 连接把这条提示并进工具结果那条 user 消息；`normalize_text` 统一弯 / 直引号、去强调星号（被拒引用的主因）。
      修后三题各登记 6 条，正文用到 6 / 5 / 5 条。未处理：库里已有、但没有 DOI 的书（Massumi《Parables for the Virtual》）
      仍会作为外部来源重复出现——已在库只按 DOI 判断。
    - [x] T7 关着时与 2.2.0 一致（用户 11-23 重申四条：本地调用不变；联网 = MCP 式 distributor 协助检索；
      关掉时正常功能完全不变；skill 一并兼容）。逐文件对照 v2.2.0：问答路径的新代码都在 `if external` 之后；
      迁移 029 只新建三张账本表（DROP 的表 2.2.0 与用户库里都没有）；连接层 `_complete` 默认路径不变；新增 mixin 与
      已有方法无重名；新 CSS 只挂在新类名上；skill 原有命令不变、只加 tools / tool-call / tool-cite。去掉关着时仅剩的两处
      差别（返回结果的 `external` 键、请求里的 `external: false`）。始终可见的新增：数据流页「联网补充」节点（工具开关所在）、
      `/api/capabilities` 多一个节点、设置里多 `flow.external` 分区。
    - 验证（11-22）：新增 `test_tools.py` 11 项、`test_tool_distributor.py` 5 项、`test_provider_tools.py` 3 项，重写
      `test_ask_external.py` 6 项、`test_external_ledger_store.py` 9 项；删除只测已删规则的 6 个测试文件；全量 pytest
      1657 passed / 4 skipped；ruff；mypy；前端 tsc / lint / build。真后端 + next dev + Playwright：临时数据目录写入示例
      账本（不发外部请求、未按「调用」），走查工具面板（6 个工具、缺 key 的 Semantic Scholar 不提供给模型、最近调用含
      被拒 cite 与 OpenAlex 额度错误）、对话栏来源条、来源栏列表 / 调用原文 / 体系，无页面错误。真实模型的工具调用
      冒烟见 T6（11-22 已做）。
  - [x] 预览 3.0.0-preview.1（11-23，用户：「准备推 3.0.0 preview」）：只含 P0–P3c 的联网补充；版本号 3.0.0、标签
    `v3.0.0-preview.1`（沿用 2.1.0-preview.1：文件里写正式版本号，标签带预览后缀）；README / README_ZH 补「联网补充」节点
    与外部文献；CHANGELOG 版本段与升级说明。无用代码清理只查 2.2.0 之后为联网新增的代码（`18966e4`）。升级核对：用户装的是
    2.1.1，新代码在其数据副本上跑过——迁移 028 / 029 自动执行，补关键词索引 31245 段约 5 秒，向量指纹不变、无文档待重建。
    遗留：没有 DOI 的已在库书重复出现；Crossref `&amp;` 未还原；全文 / graph_only 下开关被忽略且不提示；Anthropic 工具调用、
    Semantic Scholar、skill 工具命令、面板试调用、关思考时的联网补充都还没在真实环境验证。
    同批修复（11-23）：顶栏字标对齐（`.kr-wordmark`）；偏好设置显示当前版本；「检查更新」改为手动检查 GitHub 最新正式版
    + 打开下载页（用户：不要自动检测），去掉自动检查设置项与新版本提示条。
  - 🚧 P4–P6 计划（11-23 用户批准，计划页 `data/ka_v3/ka_v3_rest_plan.html`，未入库）：四个决策全按默认——
    入库只试本次运行账本里记下的链接、不调模型；文件名 = 设置项模板（默认 `{authors} ({year}) - {title}`，
    入库框里可逐篇改）；做一次真实 Zotero 写入测试（临时集合 + 测试附件，验完删除）；合并时带文档笔记、不带对话。
    用户评论：找不到 PDF 时给出能在浏览器打开的链接（DOI 链接、原文页、试过的链接），让用户自己下载后补进来。
  - [x] P4 入库：
    - [x] P4a 后端：迁移 `030_paper_imports.sql`（入库记录 + 推送账本）、`domain/paper_import.py`（记录类型、
      文件名模板、PDF 候选链接、浏览器链接；`doi_key` 从 api_external 挪来）、仓储 `source_store/imports*.py`、
      `Tool.fetch_file` / `ToolBox.fetch_file`（只有 read_open_pdf 实现）、`managers/paper_importer.py`、
      `api_library_io.py`、`web/library_io_routes.py`、补 PDF 沿用 `POST /api/documents`（多带 import_id）、
      设置 `flow.external.import_filename`（skill 客户端的可写配置表同步加一项）。出处存在入库记录里
      （不写进 local_meta：文档界面改书目时 `update_document_meta` 会整体替换 local_meta）。
      验证（11-23）：新增 `test_paper_import.py` 14 项；全量 pytest 1678 passed / 4 skipped；ruff；mypy。
    - [x] P4b 界面：入库框（`ImportDialog.tsx`）、文档栏「待补 PDF」清单（`PendingPdfList.tsx`，浏览器链接、
      选文件 / 拖入补 PDF）、文档信息栏出处（`DocumentMeta.tsx` 的 `ImportOriginRows`）、来源栏的入库状态标记；
      外链统一用 `openExternalUrl`（壳的 opener 权限放宽到 http / https）。出处显示问题原文而不是「第 N 个回答」
      （msg_idx 把提问也算在内）。验证（11-23）：tsc / lint / build；真后端 + next dev + Playwright（临时数据目录、
      本机 HTTP 服务提供 PDF，不发外部请求）：入库下到 PDF、E1 标「已入库」、E2 无 PDF 进待补并列出 DOI 链接与原文页、
      清单里选文件补上、新文档的信息栏显示出处与 PDF 链接。
  - [ ] P5 Zotero 推送：
    - [x] P5a 后端：Web API 写方法（`item_template` / `create_items` 带 Zotero-Write-Token / `delete_item`）、
      `managers/zotero_pusher.py`（目标文件夹：集合里已有附件同在一个文件夹就用它，否则 ZotMoov 目录 / 集合路径；
      根目录取 `flow.zotero.base_dir`，空着时从已有附件推出；按 Zotero 给的模板建独立 linked_file 附件，
      title = 文件名、有 DOI 时 url = DOI 链接；写入失败移回）、拉取认领（`pipelines/zotero_push_claims.py`，
      `zotero_sync_pipeline.py` 只接三处）——严格镜像的删除判断用同一张表，否则认领后的文档会被当成已删除；
      入库目标可选 Zotero 集合（入库后立刻推送）；`api_library_io` 推送接口与路由。
      验证（11-23）：新增 `test_zotero_push.py` 7 项、`test_zotero_sync.py` 认领 2 项；全量 pytest 1687 passed /
      4 skipped；ruff；mypy。
    - 🚧 P5b 界面：推送框（`PushDialog.tsx`：选 Zotero 集合、文件会移到哪、附件 path、移交说明）、文档信息栏
      「Zotero」一行（未推送 / 已推送待拉取 / 已认领 / 失败可重试）、入库框的目标加 Zotero 集合（选它 = 入库后
      立刻推送）。验证（11-23）：tsc / lint；真后端 + next dev + Playwright（临时数据目录、假 Zotero 集合与 ZotMoov
      目录、占位 key，未按「推送」——那会请求 api.zotero.org）：推送框显示的目标文件夹与 path 正确（跟着集合里
      已有附件所在的文件夹，根目录由已有附件推出）。
      - [ ] 真实写入测试（用户 11-23 选「做」）：需要用户提供有写权限的 key；临时集合 + 测试附件，验完删除。
  - [x] P6 导出导入：
    - [x] P6a 后端：`utils/passphrase_box.py`（scrypt n=2^17 + AES-GCM）、打包函数从 R2 抽出共用
      （`managers/data_inventory.py`，R2 输出不变）、`managers/kaarch.py`（后台导出、读清单、完全覆盖 = 解进
      R2 暂存 + 待恢复标记 + 重启，密钥当场写入且原密钥文件另存）、`managers/kaarch_merge.py`（按路径对集合、
      按 content_hash 跳过已有文档、走上传管线重切、写回书目 / 标签 / 集合、带文档笔记；Zotero 文档与对话不带；
      设置与密钥只补本机没有的）、机密库 `list_names`、接口 `/api/kaarch/export|inspect|import|job`（不进
      AI 助手白名单）。验证（11-23）：新增 `test_kaarch.py` 4 项（导出内容、口令、读清单、覆盖后经 R2 恢复
      换库、合并只加缺的）；全量 pytest 1691 passed / 4 skipped；ruff；mypy。
    - [x] P6b 界面：偏好设置新分组「备份与迁移」（`components/prefs/BackupGroup.tsx`：导出勾选设置 / 密钥（口令）/
      集合与构筑、选文件夹、进度条与结果；导入先读清单——设置差几项、密钥几条、文档 / 集合 / 笔记数、Embedding
      是否一致（任一边没配模型时不显示）——本机库不空时选合并 / 完全覆盖，覆盖在页内再确认一次）；壳新增
      `pick_file` 命令（`desktop/src-tauri/src/lib.rs`，按扩展名过滤），浏览器开发模式下手填路径。
      验证（11-23）：tsc / lint / build；rustfmt（clippy 与 cargo test 由 developer CI 跑）；真后端 + next dev +
      Playwright：导出（11 个文件、设置 + 密钥 + 库）、读清单、合并（2 篇已在、0 新增）结果显示正确。
  - [ ] 用户 11-23 追加（三项做完后处理）：
    - [x] Bug：数据流「联网补充」节点的工具面板里填 OpenAlex 等 key 后点保存会卡住，也没有「已配置好」的提示。
      查明：后端保存与刷新各只要几毫秒；面板保存时没有任何状态——不显示保存中、不提示成功、输入框清空、已填的 key
      只在占位符里淡淡一行，缩放后几乎看不见。用户桌面版日志与 settings.json 里没有任何工具设置写入，说明那边的
      保存没到后端且界面没报错。修：每项设置原地显示保存中 / 已保存 ✓ / 没有保存（原因），同时发提示；已配置的
      key 常显掩码（✓ 已配置 ••••abcd）并可「更换 / 清除」；回车保存、Esc 取消；必填未填时写明「不会被调用」；
      面板拦下指针事件，避免在节点里点击被画布拖动吃掉。
    - [x] 工具面板界面重做：每个工具一张卡片（开关、名称、状态胶囊、两行描述、设置行），分区标题带「n / m 个可用、
      k 个缺设置」。验证（11-23）：tsc / lint；next dev + Playwright 走查保存（回车）、掩码显示、清除。
    - [x] 数据流 / 设置页加直接跳到各节点设置的快捷按钮（节点多了以后拖动体验变差）。用户 11-23 看过三版交互 demo 后批准：
      页头一排按钮（`components/flow/FlowJumpBar.tsx`），点击 = 画布平移并缩放到节点（≥90%）并选中，节点比视口高时顶部对齐；
      按钮按「来源 / 主干 / 模型 / 备份与同步」分组，「待处理」的节点集中到最左一组（不重复出现）；状态色与画布同一份计算
      （级联逻辑移到 `model.ts` 的 `effectiveStages`）；原生滚动条隐藏，两端淡出 + 箭头，滚轮横向滚动。不改后端与节点内设置。
      顺带修：画布的 translate 与 zoom 原本写在同一元素上，Chromium（WebView2）的 CSS zoom 会连 translate 一起缩放，
      平移 / 适应画布 / 引导聚焦都按缩放比偏移（90% 时右侧节点偏 150px）；拆成平移层 + 缩放层。
      验证（11-23）：tsc / lint / build；真后端（空数据目录）+ next dev + Playwright（Chromium）：12 个按钮逐个点击，节点水平
      居中偏差 0px、缩放 90%、高节点顶距 25px、按钮与节点同步选中；分组与画布状态一致；空白处点击取消选中；滑动中拖动可打断；
      窄窗（980px）滚轮横滚、两端淡出与箭头、点画布节点时按钮自动滚进可见区；中英文案；明暗主题；适应画布左右留白 176 / 177px。
  - [ ] 🚧 P7 收尾：版本 3.0.0、README 中英、CHANGELOG、全量 pytest / ruff / mypy / 前端三项、真后端 + Playwright 走查。
    用户 11-24：P4–P6 与 11-23 追加项先按 3.1.0 准备，推标签前改为 3.0.0-preview.2（按预览版惯例：文件里的版本号仍是 3.0.0，
    只在标签上带 -preview.N；Windows MSI 要求纯数字版本）。CHANGELOG 的 Unreleased 归入 `[v3.0.0-preview.2]`，README 标 3.0。验证（11-24）：全量 pytest
    1691 passed / 4 skipped；ruff；mypy；前端 lint / tsc / build。Playwright 只在各功能落地时分别走查过，发布前没有整体再走一遍。
    待：推 `v3.0.0-preview.2` 标签（须用户本轮批准），CI 出包后填 Release 正文（`data/ka_v3/release_notes_v3.0.0-preview.2.md`，
    工作区本地），发布时标为 Pre-release。
    用户 11-24：preview.2 已作为 Pre-release 公开（Apple Silicon 一次 DMG `hdiutil Resource busy` 偶发失败，重跑通过）。
    之后的来源栏重构（P35）、「查看来源」按钮、回答底部对齐、README 评测图作为 3.0.0-preview.3 发布（文件里版本号仍为 3.0.0）。
    - [x] 发布前修 3.0.0-preview.1 记下的三个已知问题（用户 11-24：三个都修，进 3.0.0-preview.2）。
      - [x] ① 没有 DOI 的书重复出现：「库里已有」只比 DOI。改为 DOI + ISBN（ISBN-10 换算成 13 并校验），来源为 Zotero 条目
        （`raw_zotero_json` 的 ISBN）、本地文档的 DOI、入库记录 `paper_imports` 的标识；回答跳过已在库与入库框提示两处共用。
        不做书名 + 年份猜测（默认不做，用户未要求）。
      - [x] ② Crossref 标题 / 期刊名 / 作者带 `&amp;` 与 `<i>` 标签：与摘要一样去标签并还原转义；不修时模型写对 `&` 会被核验拒掉。
      - [x] ③ 全文模式与只用图谱模式忽略联网补充开关：开着时结果与聊天记录带 `external: {status: unavailable, reason: mode}`，
        回答下显示原因；对话栏在这两种模式下开关变暗并提示。开关关着时不变。
      - 验证（11-24）：全量 pytest 1696 passed / 4 skipped；ruff；mypy；前端 lint / tsc / build。Crossref 真实请求 20 条无
        `&amp;` / `<i>`。真后端（经 hub 的 arc:chat）+ next dev + Playwright：全文模式下开关变虚线、提示正确，打开开关提问后
        回答下显示「联网补充没有运行：全文模式和只用图谱模式不支持」，聊天记录 meta 里有该原因。只用图谱模式的界面没有走查
        （测试库没建图谱），由后端测试覆盖。
- [x] P34 · 问答链路重构。用户 11-24：三个已知问题都出在「前提变了、旧代码没跟上」，要从数据流上改而不是打补丁；
  先重构再发版（3.0.0-preview.2）。计划在对话里批准，四个决策按推荐：① 只用图谱的返回补齐 `answer_notice` / `references` /
  `thinking_trace` / `generation_status`（只增不减）；② 工具文字在 `ToolBox.call` 出口统一还原转义、各工具去标签，再加约束测试；
  ③ 库内标识每次现算不缓存；④ 前端读后端的模式能力表。不改功能，release notes 不变。
  - [x] P0 基准：`tests/backend/test_ask_golden.py` + `tests/backend/golden/ask_modes.json`，17 个场景 × 联网开 / 关 = 34 条，
    录提示词、返回结果、聊天记录、运行事件、进度、工具调用数；在旧代码上录，连录三次逐字节相同。
  - [x] P1 模式能力表（`kacore/retrieval_modes.py` 的 `ModeSpec`），入口校验与 `EXTERNAL_SKIPPED_MODES` 从表里读。
  - [x] P2 拆成五个阶段（准备 → 取证据 → 补充证据 → 合成 → 收尾），`kacore/pipelines/answer_*.py`；`api.ask()` 只留校验、超时、取消。
  - [x] P3 工具输出出口：`ToolBox.call` 统一还原转义，`_plain` 进 `base.py`，约束测试覆盖全部内置工具。
  - [x] P4 库内标识移到 `kacore/managers/library_identity.py`。
  - [x] P5 `/api/capabilities` 加 `ask_modes`，对话栏照它显示。
  - [x] P6 验证（11-24）：全量 pytest 1737 passed / 4 skipped；ruff；mypy；前端 lint / tsc / build。真后端（hub 的 arc:chat）+
    next dev + Playwright：能力表取到后图谱两档在没建图谱时置灰、全文在打开文档时可选；默认与全文模式经界面提问 200，全文开着
    联网时开关变暗。深度思考、默认 + 联网补充在界面上被 next dev 代理 30 秒断开（socket hang up，开发代理的限制，桌面版直连
    后端不经这层），后端都正常跑完；直接调后端复核：默认 + 联网 1 条库内 + 5 条外部、5 条被正文引用，深度思考退到词法并带提示，
    增强召回正常。没走查：只用图谱 / 图谱混合的界面（测试库没建图谱），由基准与后端测试覆盖。然后回到发版（3.0.0-preview.2）。
- [x] P35 · 来源栏界面重构。用户 11-24：「体系」难懂、列表没有层级、块难读、分栏不能拖动。看过交互 demo 后批准计划：
  只改前端、后端不动。三种视图（按来源：引用 / 未引用两组、卡片三层；按检索过程：调用 → 被引用的块；按我的集合：完整集合树 +
  每篇理由 + 新方向）；右侧详情把原文块按我们自己的「标签: 值」格式排成字段表，被引用的块默认展开、句子高亮，可看原文；
  列表与详情之间可拖动分栏（宽度记在本机）。拆成 `SourcesPanel.tsx` / `SourcesViews.tsx` / `SourceDetail.tsx` +
  `lib/sourceBlocks.ts` + `components/ds/ResizeHandle.tsx`。不做：重新归类、改后端接口。
  - 走查中顺带改：「按检索过程」把模型登记引用的 `cite` 调用单列「登记引用」一组（显示接受为 E 几 / 没通过的原因），
    概况里的调用数只数检索，与对话栏「N 次工具调用」一致（以前一个 15、一个 9）。
  - 验证（11-24）：前端 lint / tsc / build；`lib/sourceBlocks.ts` 用 node 跑过（字段拆分、PDF 段落不拆、弯直引号与空白、集合路径
    与后端同法）。真后端（测试数据目录里那次真实联网补充：5 条外部来源、15 次调用）+ next dev + Playwright，亮 / 暗 × 中 / 英：
    三种视图；被引用的块默认展开且句子高亮，其余收起；展开另一块与看原文；换来源后展开状态重置；拖动 460 → 640 px 并存下，
    刷新后仍是 640，双击还原 460；「按我的集合」第一次打开经 hub 真模型归类，5 篇归到 Test、各带理由，空集合 default 显示「—」，
    点论文回到按来源并选中。
