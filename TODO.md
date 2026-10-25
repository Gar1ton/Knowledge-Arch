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
