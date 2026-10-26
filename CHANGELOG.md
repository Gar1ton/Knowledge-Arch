# CHANGELOG

> 插件时代（v0.x – v1.2.0）的变更记录完整保留在本仓库首个提交 `89b251a` 中（`git show 89b251a:CHANGELOG.md`），不再随 v2 维护。

## [Unreleased]

## [v2.0.5] — 2026-10-26

### 新增 (Added)

- **聊天可关闭模型思考**：查询设置弹窗新增「模型思考」开关（默认开，记在本机；`components/panels/ChatPanel.tsx`），
  关掉后 `/api/ask` 带 `model_thinking: false`。`kacore/api.py` 在这一问自己的任务里设开关
  （`kacore/providers/thinking.py` 的 contextvar），改写、检索翻译、增强召回 / 深度思考各步与最终生成都按连接换成关闭参数：
  DashScope / SiliconFlow `enable_thinking`、智谱 / DeepSeek / Moonshot `thinking.type=disabled`、OpenRouter `reasoning.effort=none`、
  OpenAI 与 Ollama `reasoning_effort=none`，其余（vLLM / LM Studio / 自定义）`chat_template_kwargs.enable_thinking`；
  Anthropic 按型号，Opus 5.5 / Opus 5 / Sonnet 5.5 / Fable 无法关闭（或与拒答回退冲突）时降为 effort low。
  端点拒收这些参数时去掉重试一次（`openai_compat.py`、`anthropic.py`），开关只会不起作用，不会让提问失败。
  建图等后台任务与「深度思考」检索档位不受影响。

### 修复 (Fixed)

- **聊天知识范围与实际检索不对应**：选中文档时范围标签显示该文档，但只有全文模式按文档检索，其余模式按集合
  （未选集合即全部集合），标签与答案来源对不上。`components/panels/ChatPanel.tsx` 的范围标签改为可点的集合选择器
  （全部集合 + 按树序缩进的集合），显示的就是这次检索用的范围：全文模式是当前文档，其余是聊天自己选的集合；
  空闲时跟随左侧点选的集合，中间切换文档不影响。回答进行中范围锁定，左侧 / 中间怎么切都不变。
  每轮问答都有归属：`kacore/api.py` 的 `ask` 把范围写进聊天记录 `meta.scope` 并随结果返回 `scope`，
  气泡上显示该轮范围（重启后从历史恢复），「加入笔记」挂到该轮范围。
  图谱档位（仅图谱 / 图谱混合）以前靠左侧 LightRAG 区的选中前缀判断能否选，范围改为聊天自选后改为按图谱就绪度：
  新增 `GET /api/lightrag/readiness`（`web/server.py`），所选集合的图谱未就绪时两档变灰并提示去左侧构建，
  已选图谱档位而换到未就绪集合时回退到默认档位。
- **Notion 库 404 后无法通过「初始化数据库」恢复**：初始化前的预检对已配置库仍做 404 拦截，初始化本身永远跑不到。
  `kacore/repository/sync_targets/notion.py` 的 `preflight(initialize=True)` 对 404 不再抛错而是报告失效的库，
  `initialize_databases` 把它当作未配置、在父页面下新建（结果带 `recreated_articles` / `recreated_qa` 与说明；
  没有父页面时报错且不写远端）。`kacore/pipelines/notion_sync_pipeline.py` 新建 Articles 库后清掉文章推送账本，
  否则旧账本的指纹会让下一轮推送把所有文章判为已同步、新库一直是空的。推送前预检的 404 报错补充了重建的提示；
  数据流页初始化成功时区分「已重建」（`components/flow/SyncActions.tsx`）。

## [v2.0.4] — 2026-10-26

### 修复 (Fixed)

- **改查询设置时把已写好的问题发出去**：聊天框里的设置键（`components/ds/IconButton.tsx`）没写按钮类型，在表单里默认是提交键，
  点开设置就会发送输入框里的内容。`IconButton` 改为 `type="button"`，输入与设置互不干扰。
- **深度思考卡住时清空记录无效**：回答在后台任务里跑，清空只删了已存记录，任务跑完又把问答写回，前端补轮询也会把它拉回来，
  发送键一直转圈。现在 `kacore/api.py` 按会话登记进行中的回答（`cancel_ask`），`clear_chat_history` 先中断再删记录：
  任务取消后不再落库，进度条目（`ask_progress.ProgressStore.discard`）即删，运行事件记为 cancelled；多轮改写与检索翻译
  也并入同一个可中断任务。被中断的 `/api/ask` 返回 409 `ask_cancelled`，`DELETE /api/chat/history` 多返回 `cancelled`。
  前端（`ChatPanel.tsx`、`lib/api.ts`）清空时中止请求与超时补轮询、恢复发送键并提示已中断。

### 改进 (Changed)

- **聊天默认检索档位改为 enhanced**：`components/panels/ChatPanel.tsx` 的 `DEFAULT_MODE`；取消选中文档或集合导致当前模式不可用时
  也回退到 enhanced。后端 `/api/ask` 不传模式时的默认值不变。

## [v2.0.3] — 2026-10-26

### 修复 (Fixed)

- **思考过程在答案完成后消失**：深度思考超时后前端从聊天记录补拉答案，而记录里不存思考过程。迁移 `026_chat_history_meta.sql`
  给聊天记录加 `meta`，回答时存下思考过程与「理解为」，补拉与重启后恢复。
- **集合改名后文件从列表消失、按该集合检索为空**：归属与索引都按集合名字记录，改名只改了集合本身。`rename_collection` 现在
  在一个事务里级联文档、LightRAG 索引状态、集合笔记、图谱构建记录（`rename_collection_references`），向量库重新打标签，
  LightRAG workspace 映射跟随；图谱构建中拒绝改名。启动时 `repair_renamed_collections` 修复旧版改名留下的断链。
- **聊天知识范围标签显示内部 ID 并溢出**：改为显示文档标题（新增 `GET /api/documents/{doc_id}`），过长省略。
- **界面像网页一样整体被拖动**：`app/globals.css` 固定 html / body、关闭回弹，只有面板内部滚动。

### 改进 (Changed)

- **聊天来源可折叠**：与思考过程一样默认收起，标题显示条数。
- **未配置的功能按钮变灰**：Zotero 同步、R2 备份、LightRAG 构建按 `/api/capabilities` 显示就绪状态，点击时现查，
  仍未就绪就提示去数据流页设置（`lib/featureReadiness.ts`）；去掉 Zotero 区无操作的「+」。
- **自绘标题栏**：Windows / Linux 去掉系统标题栏（`tauri.windows.conf.json` / `tauri.linux.conf.json`），最小化 / 最大化 / 关闭并入顶栏
  Preferences 右侧（`components/layout/WindowControls.tsx`），顶栏空白处可拖动、双击最大化；macOS 用 Overlay 标题栏，红绿灯嵌入顶栏左侧。

## [v2.0.2] — 2026-10-26

### 修复 (Fixed)

- **PDF 显示「Invalid PDF Structure」**：`components/panels/PdfViewer.tsx` 改为带 token 向后端取原件字节再交给 pdf.js
  （此前用相对 URL，桌面壳里取到的是前端页面）；不是 PDF 时提示改用 MD 模式。
- **文本原件被当成 PDF**：本地上传的原件都存成 `original.pdf`，`web/server.py` 的 `_document_ext` 按文件头与标题后缀
  给出真实类型；非 PDF 文档的列表图标正确，阅读时不再提供 PDF 模式（`DocumentsPanel.tsx`）。
- **聊天来源卡片溢出**：去掉内部分块 ID（`LOCAL_…`），标题过长时省略。

### 新增 (Added)

- **PDF 全屏阅读**：「适应宽度」旁加全屏键，阅读器铺满整个应用窗口，Esc 或再点一次退出；「适应宽度」换用左右箭头图标。

## [v2.0.1] — 2026-10-26

### 改进 (Changed) — P19 提示词按回答语言切换

- **`kacore/pipelines/prompt_language.py`**：回答语言 zh / en 直接决定提示词语言，auto 跟随问题语言。
- **双语提示词**：深度思考的规划 / 证据审计 / 校验（`deep_thinking_prompts.py`）、增强召回（`enhanced_recall_prompts.py`）、
  答案合成模板（`answer_synthesis.py`）、`api.py` 的默认 / 图谱内联提示词（改用共享的 `rag_rules` / `context_prompt`）、
  追问改写、来源标签、JSON 重试提醒与证据动作说明都有中英两版；只翻译不改语义，JSON 字段名与枚举值不变。
  英文回答时深度思考暴露的信息点、缺口与补检查询也随之是英文。
- 英语召回的翻译提示词保持英文。

### 新增 (Added) — P18 多轮对话

- **追问结合上下文**：`kacore/pipelines/conversation_context.py` 取本会话最近 3 轮（答案截断），`ask(use_context=True)`
  在已有问答时先让 LLM 把追问改写成独立问题，检索与生成都用改写结果，六种模式通用；聊天记录保存原话，
  结果带 `standalone_question`；首轮不改写，改写失败退回原问题。`/api/ask` 增加 `use_context`。
- **聊天设置**：`components/panels/ChatPanel.tsx` 在「使用英语召回」下加「启用上下文（多轮对话）」与「显示改写后的问题」，
  三个开关记在本机（默认开）；答案下方显示「理解为：…」。

### 新增 (Added) — P17 卸载与数据

- **Windows 卸载器真正删除数据**：`desktop/src-tauri/windows/hooks.nsh`（`tauri.conf.json` 的 `installerHooks`）在勾选
  「删除应用程序数据」且不是更新时删除 `%LOCALAPPDATA%\Knowledge Arch`、`%APPDATA%\Knowledge Arch` 与自选数据目录；
  Tauri 自带的勾选框只删以应用标识命名的 WebView 目录。
- **应用内「删除全部数据」**：偏好设置 › 存储（`components/prefs/WipeDataField.tsx`），输入确认词后经
  `/api/secrets/forget-master-key` 删除钥匙串主密钥，壳命令 `wipe_data_and_quit` 停后端、关闭开机自启、删除数据 / 日志 /
  缓存 / 壳配置目录并退出；macOS / Linux 卸载前用它清除数据。
- **删除规则**：`desktop/src-tauri/src/wipe.rs`。平台默认目录整删；自选数据目录选定时为空才写 `.knowledge-arch-owned`
  并可整删，否则只删 `KNOWN_ENTRIES`；壳把当前数据目录写进 `data-dir.txt`（UTF-16LE）供卸载器读取，测试核对两边清单一致。

### 修复 (Fixed) — 上传

- **桌面壳里拖入文件无效**：`desktop/src-tauri/tauri.conf.json` 关闭 `dragDropEnabled`，系统拖放交给网页的 `onDrop`。
- **批量上传**：`components/panels/FilePanel.tsx` 的上传框支持多选与多文件拖入，逐个上传并显示进度，失败的文件留在列表里重试。
- **目标集合下拉跟随主题**：改用 ds `Select`；集合列表晚到时补上默认目标。
- **上传格式与后端一致**：去掉后端没有解析器的 DOCX（会被当纯文本读入），加上 `.markdown`。
- **文档列表刷新**：`components/panels/DocumentsPanel.tsx` 在搜索键旁加刷新键；上传成功后经 `lib/documentEvents.ts`
  自动重新加载；未选集合时（All Documents）列出全部文档，此前恒为空。

### 改进 (Changed)

- **Windows / Linux 不再显示 File / Edit 菜单栏**：菜单只在 macOS 挂；Ctrl+Q 由前端调用 `quit_app`，Ctrl+, 照旧。

### 移除 (Removed) — 死代码清理

- **无调用方的后端接口**：`web/server.py` 删去 `/api/quota`、`/api/milvus/clear-lock`、`/api/files/list`、`/api/metrics`、
  `GET /api/graph`、`/api/graph/query`、`/api/graph/probe` 与文档 / 集合笔记的 PATCH；`kacore/api.py` 删去对应实现，
  `kacore/metrics.py`（只写不读的延迟统计）与 `AppInitializer.clear_stale_milvus_lock` 一并移除。
- **只在测试里出现的方法**：Zotero 本地 API 的 `get_item` / `get_status_schema` / `get_file_view_url`、`LLMAdapter.extract_graph`
  及其离线桩、`Config.apply_override`、`SyncTarget.stat_backup_object`、`EncryptedSecretStore.masked_secret` 等；
  orchestrator 只写不读的 `_rerank_cfg`、Notion `upsert_document` 的 `force_body`、未读的图谱重试退避配置。
- **前端**：`lib/api.ts` 24 个未调用的函数与只被它们用到的类型、`?mock` 离线模拟模式（MOCK 数据与 `isMock()` 分支）、
  `components/ds/index.ts`、`geist` 依赖、47 个未引用的 CSS 变量（`--font-geist-mono` 改用 `--font-mono`）。
- **docs**：删去 6 份无人引用的旧设计稿与图片。

### 改进 (Changed) — 命名与打包

- **重启服务**：`restart_plugin` → `restart_service`，`/api/plugin/restart` → `/api/service/restart`（AI 助手白名单与 skill 同步），
  前端 `restartService` / `ServiceRestartResult`。
- **安装包不带测试替身**：`desktop/scripts/bundle_python.py` 的 `_TEST_ONLY` 排除 repository 的三个 `memory.py`。

### 改进 (Changed) — rc.3 反馈：main 精简

- **README 默认英文**：英文版改为 `README.md`，中文版改名 `README_ZH.md`；两份的版本记录链接改指 GitHub Releases。
- **main 不带 CHANGELOG**：`CHANGELOG.md` 加入 `.release-exclude` 与 `.gitattributes` 的 `export-ignore`，
  `.github/scripts/build_main.py` 去掉精简 CHANGELOG 的逻辑；发布说明在 GitHub Release 草稿里用英文补写（`docs/RELEASE.md`）。
- **根目录精简**：`logo.svg` 移到 `desktop/assets/`（`npm run icons` 与 README 同步改路径），删去未被引用的 `logo.png`
  与不含任何包的 `uv.lock`。

### 改进 (Changed)

- **Linux AppImage 自动更新**：`.github/workflows/desktop-release.yml` 新增 `updater-manifest` 作业（全部平台出包后运行），
  经 `desktop/scripts/updater_add_appimage.py` 把重签后的 AppImage 写进 `latest.json`；workflow artifact 排除 `*.AppDir`。
- **默认深色界面**：`web/frontend/app/layout.tsx` 的 `defaultTheme` 改为 `dark`，`kacore/settings/schema.py` 的
  `prefs.appearance.theme_mode` 默认值改为 `dark`；仍可在偏好设置 › 外观切换跟随系统或浅色。已选过明暗模式的用户不受影响。

### 新增 (Added) — P16 首次使用引导

- **`web/frontend/components/desktop/FirstRunTour.tsx`**：不用 AI 助手时的游戏式引导（遮罩锁定其它区域、高亮目标、提示卡与进度点），
  六步按真实状态自动推进到「收到第一条回复」；向量化可跳过（关键词召回）或改用 Codex / Claude 检索。
- `lib/tour.ts`（本机状态与事件）；`FlowDiagram.tsx` 响应 `ka:flow-focus` 把节点移到中央；`ChatPanel.tsx` 提问时派发 `ka:ask-sent`；
  偏好设置 › 通用「重新查看引导」（`components/prefs/AgentPrefsField.tsx` 的 `TourReplayField`）；`lib/zLayers.ts` 新增 `tour` 层。

### 新增 (Added) — P15 首次启动 AI 辅助配置

- **欢迎页**：`web/frontend/components/desktop/WelcomeScreen.tsx` 改为「选择语言 → 导入旧数据 / 从空库开始 → 用 AI 助手完成配置」；
  `components/desktop/AgentSetup.tsx` 提供 Codex / Claude Code / 其他三种用法提示、可复制的 prompt（复制即打开接入）、
  实时的「已连接」状态与十个数据流节点进度、一键安装 skill。
- **AI 助手接入**：`ka_server/agent_access.py` 在「偏好设置 › 通用 › 允许 AI 助手连接」（`prefs.general.agent_access`）
  打开时写出连接文件（地址 + 每次启动生成、与界面 token 无关的 agent token，仅当前用户可读，关闭或退出即删）。
- **权限与机密**：`web/agent_routes.py` 的白名单只放行配置、检索、问答记录与图谱接口；agent 写 provider 的 api_key 或
  kind=secret 的设置一律 403。需要密钥时 agent 创建密钥请求，`components/desktop/SecretRequestDialog.tsx` 弹框由用户填进应用，
  agent 只能看到状态。
- **prompt 与一键安装**：`kacore/agent_skill.py` 按界面语言渲染 skill 自带的 `assets/setup-prompt.<lang>.md`（含应用自带 Python
  与 skill 的绝对路径）；一键安装到 `~/.codex/skills` 与 `~/.claude/skills` 并改写 SKILL.md 的命令块，启动时自动刷新，
  不覆盖用户同名的 skill。偏好设置 › 通用新增入口（`components/prefs/AgentPrefsField.tsx`）。

- **路径不外泄**：prompt 会被粘贴给 AI 服务商，家目录下的路径写成 `"$HOME/…"` / `~/…`（POSIX）；skill 客户端的全部
  输出把家目录替换为 `~`；agent 白名单不含 `/api/system/info`。

### 改进 (Changed) — skill 适配桌面版

- **`.agents/skills/operate-knowledge-arch`**：客户端改为读连接文件 + Bearer token（去掉 WebUI 登录、密码与 keyring，
  删除 `requirements-codex-skill.txt`）；新增 `scripts/knowledge_arch_setup.py`（setup-status、detect、providers、provider-add /
  test / models、settings-show / set、component-install、request-secret、secret-status，变更一律先预览、`--apply` 才写入）；
  SKILL.md 按桌面版重写，新增 `references/setup.md` 首次配置流程，references 的命令统一为 `KA` 占位。
- **打包**：`desktop/scripts/bundle_python.py` 把 skill 放进 `ka/agent_skill/`，补登 `web/agent_routes.py`，冒烟测试检查 skill 可定位。

### 测试 (Tests)

- `tests/backend/test_agent_access.py`：连接文件权限与开关、白名单、机密拦截、密钥请求往返不泄露值、prompt、一键安装与启动刷新、
  白名单里每条路由都真实存在。
- `tests/backend/test_knowledge_arch_skill_setup.py`：后台线程跑真实后端，同步客户端走完配置流程；用 prompt 里的
  `-I --connection-file` 命令形式起子进程。
- `tests/backend/test_ka_server.py`：真实进程就绪后写连接文件、agent token 不能关机、退出后文件删除、token 不进日志。
- `tests/backend/test_knowledge_arch_skill_client.py` 改为连接文件 + Bearer；`test_bundle_python.py` 守护 web 模块与 skill 都已打包。

### 新增 (Added) — P14 发布分支 main 自动生成

- **`.github/scripts/build_main.py`**：由 developer 的发布提交生成干净的 main 提交（临时 index，不碰工作区）：
  按 `.release-exclude` 删去治理文档、内部设计稿、`dev/`、`tools/` 与仅 developer 使用的 CI，CHANGELOG 换成
  精简版（只留正式版本与每条首句），相对链接指向被删文件即失败，`git commit-tree` 线性接在现有 main 之后；`--list` 预演。
- **`.github/workflows/desktop-release.yml`**：新增 publish-main 作业（只接受 developer 上的标签，重跑同一标签不重复追加），
  build 作业从该 main 提交出包。
- **`.gitattributes`**：与 `.release-exclude` 同步的 `export-ignore`，tag 源码包同样干净（测试核对两者一致）。
- **README 拆为 `README.md`（中文）/ `README_EN.md`（英文）**：徽章互相跳转，注明项目承接自旧插件 v1.2.0，
  新增「参与贡献」（issue / PR 走 developer）；`kacore/README.md`、`web/frontend/README.md` 的内部文档链接改指 developer 分支。
- `CLAUDE.md` §1.1、`docs/GIT_WORKFLOW.md`、`docs/RELEASE.md`：批准推送 `v*` 标签即批准自动更新 main，不再手动 push main。

### 改进 (Changed) — P13 安装包体积与 Linux AppImage

- **`desktop/scripts/bundle_python.py`**：新增 `slim_interpreter`（POSIX 只留一个实体 `bin/python3`，Tauri 复制资源会把
  符号链接展开成三份解释器；删去未引用的 `libpython`、Tk、idlelib、ensurepip、lib2to3、turtledemo、`config-3.*`，
  POSIX 与 Windows 分开匹配）。冒烟失败时输出解释器的 stderr。本机 Linux 运行时约 605→461 MB。
- **macOS DMG 改为 LZMA（ULMO）**：出包后 `hdiutil convert` 并替换 Release 资产，有签名身份时重签。
- **Linux AppImage 后注入 Python**：`desktop/src-tauri/tauri.appimage.conf.json` 让 AppImage 那一遍不带 python，
  `desktop/scripts/appimage_inject_python.sh` 解包注入、冒烟、appimagetool（zstd 19 级）重打包，updater 签名重签；
  tauri-action 只出 `.deb`。Linux 依赖加 `desktop-file-utils`。

### 测试 (Tests)

- `tests/backend/test_build_main.py`：CHANGELOG 精简、清单删除与父提交、死链检查、export-ignore 与清单一致。
- `tests/backend/test_bundle_python.py`：`slim_interpreter` 保留唯一实体解释器、按是否引用决定删 libpython。

### 改进 (Changed) — P12 全英文输出

- **后端日志与界面消息改英文**：`kacore/`、`ka_server/`、`web/server.py` 约 490 条日志、报错、提示与诊断文案；
  提示词、中文文本处理正则、Notion 列名与旧版标记不变。
- **中文回答适配保留**：`kacore/api.py` 深度思考 / 生成状态提示的中文分支原样保留；新增 `_answers_in_chinese`、
  `_reference_heading`，让「LLM 不可用」「未找到内容」「摘录兜底」与答案末尾参考文献标题跟随回答语言。
- **桌面壳**：`desktop/src-tauri/src/lib.rs`（菜单 / 托盘）、`sidecar.rs`（崩溃与启动失败提示）、`updater.rs`（更新通知）改英文。
- **前端**：`components/layout/TopBar.tsx` 字标改用 Bitcount；`lib/api.ts` 请求错误与 mock 数据、`app/layout.tsx` 描述改英文。
- `kacore/api_capabilities.py`：线上 Embedding 未选连接时，向量库不可用的原因说明改为准确提示。

### 新增 (Added) — P11 线上 Rerank

- **`kacore/repository/reranker/remote.py`**：`RemoteReranker` 调用 OpenAI 兼容连接上的 `/rerank`（Cohere / Jina /
  SiliconFlow / Voyage 响应格式），失败只对本次回退；`build_reranker` 支持 `provider="remote"`。
- **设置**：`kacore/settings/schema.py` 新增 `flow.retrieval.rerank.remote`，`rerank.provider` 增加 `remote`；
  `kacore/settings/bridge.py` 注入连接地址与密钥；`kacore/config.py` 的 `RerankConfig` 增加 `base_url` / `api_key`；
  `kacore/capabilities.py` 线上 Rerank 不要求本地组件。
- **前端**：`components/settings/SettingInput.tsx` 抽出 `ConnectionModelInput`（Embedding / Rerank 共用），
  `components/flow/NodePanel.tsx` 按 Rerank 来源切换显示字段。

### 改进 (Changed)

- **Embedding 与 Rerank 默认线上 API**：未选连接时不做 Embedding 探针、Milvus 等待配置、Rerank 不重排；
  `kacore/settings/legacy_keys.py` 让旧版插件配置迁移后保持本地 Embedding。

### 改进 (Changed) — P10 安装包瘦身

- **`desktop/scripts/bundle_python.py`**：新增 `prune_runtime`（去字节码、site-packages 的 `tests/` 与 `include/`、
  pyarrow 的 Flight / S3 / C++ 自测库），不再 `compileall`，构建期解释器带 `-B`；冒烟测试加 Milvus Lite 往返；
  `ka/.ka_bundle` 标记。本机实测运行时未压缩 729→548 MB，gzip 224→174 MB。
- **`ka_server/bytecode_cache.py`**：打包产物里把 `sys.pycache_prefix` 指向用户缓存目录（`ka_server/__init__.py`
  最先调用），应用目录只读也只需首次启动编译。
- **Windows 安装包改为 NSIS `setup.exe`**：`tauri.conf.json` 的 `bundle.windows.nsis`（当前用户安装、中英文界面），
  `.github/workflows/desktop-release.yml` 的 Windows 矩阵出 `nsis`；`desktop/scripts/configure_release.py` 签名配置
  改为合并写入；README / `docs/RELEASE.md` / `desktop/README.md` 同步。
- 开发环境对齐安装包：pymupdf4llm 0.0.27，不装 pymupdf-layout / onnxruntime。
- `.github/workflows/desktop-release.yml`：打包步骤设 `NO_STRIP=true`，避免 linuxdeploy 的 strip 处理内置依赖的
  `.so` 失败导致 AppImage（及同任务的 `.deb`）出不来。

### 新增 (Added) — P9 组件安装失败归类

- **`kacore/extras/failure.py`**：把 uv 输出归为磁盘不足 / 权限 / 网络 / 无适用安装包 / 依赖冲突 / 编译失败 / 未知，
  `kacore/extras/manager.py` 的失败结果新增 `reason` 与 `detail`（输出末尾 400 行内取最相关的几行）；
  `kacore/extras/memory.py` 的替身支持模拟输出。
- **前端**：`lib/settingsApi.ts`（`InstallFailureReason`、`installFailureText`）、`lib/api.ts`（`installResultOf`：
  后端 500 的结构化失败结果按结果返回）、`components/prefs/PrefsGroups.tsx`（组件行下方显示说明与原始报错）、
  `components/panels/FlowPageContent.tsx`（数据流页安装失败提示本地化）。

### 修复 (Fixed)

- README 开发命令补 `--dev-origin http://localhost:3000`：缺少时浏览器开发页的写请求被 CORS 拒绝（403）。

### 改进 (Changed) — P8 测试反馈

- **向量库只提供 Milvus Lite**：`kacore/capabilities.py`（向量库节点 candidates 只剩 `milvus`、不可切换，新增
  `waiting_for_embedding`）；`kacore/settings/legacy_keys.py`（旧插件「仅 SQLite 词法召回」导入时改回 Milvus 并提示）；
  `kacore/app_initializer.py`（Embedding 未就绪时的日志改为「Milvus Lite 已内置，等待 Embedding」）；前端
  `components/flow/model.ts`（`hiddenSettingKeys`、「内置」角色标签）、`FlowNode.tsx`（等待提示，等待期间不提示重建）。
- **Embedding 线上 API 入口**：`components/settings/SettingInput.tsx` 新增 `EmbeddingSourceInput`（本地模型 / 线上 API，
  只列 OpenAI 兼容连接，embed 类模型优先、可手填）；`components/flow/NodePanel.tsx` 线上模式隐藏本地模型名。
- **顶栏**：`components/ds/Button.tsx` 的 tab 选中态去掉光圈；`components/layout/TopBar.tsx` 工作台视图不画分界线，
  `app/(console)/layout.tsx` 工作台四周统一 `--panel-gap`。

### 改进 (Changed) — P7 界面打磨

- **默认界面语言改为英语**：`web/frontend/app/(console)/layout.tsx`（`kr-lang` 缺省 `en`，`<html lang>` 随语言同步）、
  `app/layout.tsx`、`lib/i18n.ts`（context 默认）、`kacore/settings/schema.py`（`prefs.general.language` 默认 `en`）。
- **英文默认字体 PT Sans**：`app/layout.tsx` 用 next/font 自托管 PT Sans；`styles/ds-tokens.css` 的 `--font-sans`
  中文回落到 PingFang / 微软雅黑 / Noto CJK。
- **落地页沿用 v1 登录页视觉**：新增 `components/desktop/LandingShell.tsx` / `.module.css`（网点建筑背景、Bitcount
  字标、流光卡片）；`WelcomeScreen`、`CrashScreen`、启动画面（`BackendGate`、`ReadyShell`）改用它；删去
  `styles/desktop.css` 中的旧整窗卡片样式。

### 新增 (Added)

- **界面缩放**：设置项 `prefs.appearance.ui_scale`（0.8–1.5）；`lib/uiScale.ts` 在桌面壳内经 Tauri webview
  `setZoom` 应用，并接管 Ctrl/⌘ + / − / 0；偏好设置 › 外观新增「界面缩放」；
  `desktop/src-tauri/capabilities/default.json` 加 `core:webview:allow-set-webview-zoom`。

## [v2.0.0] — 2026-10-25

### 新增 (Added) — P6 发布

- **`.github/workflows/desktop-release.yml`**：macOS（Apple Silicon / Intel）、Windows（`.msi`）、Linux（`.deb` /
  `.AppImage`）矩阵出包；手动触发只上传 artifact，推送 `v*` 标签时创建草稿 Release。签名、公证、updater 全由仓库
  secret / variable 驱动，缺哪项跳过哪步，产出未签名包；只把非空 secret 导出给 tauri CLI。
- **自动更新**：`desktop/src-tauri/src/updater.rs`（Cargo feature `updater`，tauri-plugin-updater）提供
  `check_update` / `install_update` 命令，`prefs.general.check_updates` 开启时启动 10 秒后检查，有新版本发
  `update://available` 事件并弹系统通知；前端 `UpdateBanner`（顶部横幅「安装并重启」）与偏好设置 › 通用的检查更新项。
- **`desktop/scripts/configure_release.py`**：CI 中按 `TAURI_UPDATER_PUBKEY`、`WINDOWS_CERTIFICATE_THUMBPRINT`
  补全 `tauri.conf.json`（updater 公钥与端点、`createUpdaterArtifacts`、Windows 签名）并输出构建 feature。
- **macOS 公证准备**：`desktop/src-tauri/entitlements.plist`、hardened runtime；`desktop/scripts/macos_sign_resources.sh`
  在打包前逐个签名内置 Python 的 Mach-O。
- **`docs/RELEASE.md`**：产物矩阵、所需 secret / variable 清单、发版步骤。

### 改进 (Changed)

- 版本统一为 2.0.0：`ka_server/__init__.py`、`desktop/src-tauri/tauri.conf.json`、`Cargo.toml`、
  `desktop/package.json`、`web/frontend/package.json`。
- **README 按实际功能重写**：安装、首次启动（欢迎页 / 导入旧版插件数据）、数据流节点、偏好设置、可选组件、数据位置、开发。
- `.github/workflows/tests.yml`：前端改用 Node 22；新增 desktop 作业（rustfmt、默认与 `updater` feature 的 clippy、
  `cargo test`）。

### 测试 (Tests)

- `tests/backend/test_release_config.py`：无 secret 时不改配置；有公钥 / 指纹时写入 updater 与 Windows 签名；
  五处版本号一致。

## [v2.0.0-dev.5] — 2026-10-25

### 新增 (Added) — P5 桌面壳

- **`desktop/src-tauri`（Tauri 2）**：`lib.rs` 单实例（第二个实例只聚焦已有窗口）、菜单「偏好设置…」（⌘, / Ctrl+,，
  发 `menu://preferences`）、托盘（显示窗口 / 偏好设置 / 退出；`prefs.general.close_to_tray` 时关窗隐藏到托盘）、
  原生选择文件夹、系统通知、打开日志目录、开机启动（autostart 插件）；`prefs.rs` 壳偏好 `shell.json`（数据目录覆盖、
  关闭到托盘、开机启动、检查更新），默认数据 / 日志目录与后端 platformdirs 一致。
- **`sidecar.rs`**：随机 token 只经 `KA_TOKEN`；`python -I -m ka_server serve --port 0 --parent-pid --uv [--data-dir]`；
  `KA_READY` 60 秒超时；就绪 / 退出事件（`backend://ready` / `backend://exited`，带最后 80 行日志）；退出时
  `POST /api/shutdown`、10 秒后强制结束；重启递增 generation，旧进程的线程自动失效。
- **`desktop/scripts/bundle_python.py`**：经 uv 下载 python-build-standalone 3.12，安装核心依赖（去掉 pip），复制后端
  源码，写入相对路径的 `knowledge_arch.pth`（`-I` 下仍可导入），uv 放在解释器旁，前端静态导出作为 frontendDist，
  预编译字节码并做 `-I` 导入冒烟。
- `desktop/package.json`（`@tauri-apps/cli`）、`desktop/README.md`、由 `logo.svg` 生成的图标。

### 改进 (Changed)

- `ARCHITECTURE.md` / `CLAUDE.md` / `docs/PROJECT_STRUCTURE.md` 补充桌面壳；ruff 排除打包产物目录；`.gitignore` 忽略
  `desktop/src-tauri/{target,resources,gen}`。
- 崩溃页日志框默认滚动到最后几行。

### 测试 (Tests)

- Rust：`cargo test` 6 项（token、资源布局、壳偏好、日志目录、退出码提示）；Python：`test_bundle_python.py`。
- Linux 实机（Xvfb）：`.deb` 安装后启动、单实例、崩溃页与重启服务、菜单退出的有序停止均验证通过。

## [v2.0.0-dev.4] — 2026-10-25

### 新增 (Added) — P4 前端

- **`web/frontend/lib/backend.ts`**：后端地址与 token 的唯一来源。桌面壳里 `invoke("backend_info")`，
  `backend://ready` / `backend://exited` 事件刷新缓存；开发期走 `/api/*` 代理 + `NEXT_PUBLIC_KA_TOKEN`。
  所有请求带 `Authorization: Bearer`，下载与 PDF.js 用 `?access_token=`。另封装壳命令：`backend_state`、
  `restart_backend`、`pick_folder`、`open_log_dir`、`notify`、`set_shell_pref` / `get_shell_prefs`（P5 实现）。
- **`lib/settingsApi.ts`**：设置 schema / PATCH、连接 CRUD / 测试 / 模型列表、组件安装 / 卸载、旧数据导入的客户端。
- **数据流节点面板**：`components/flow/NodePanel.tsx` 按 `GET /api/settings/schema?home=flow.<node>` 渲染，
  basic 项在节点内、advanced 项进「高级」折叠，`PATCH /api/settings` 写入后按 consequence 提示重启服务 / 重建；
  `components/settings/SettingInput.tsx` 覆盖 9 种 kind（secret 只显示后 4 位，留空不改、可清除；model_ref 为
  「连接 + 模型」两级选择，research / graph 可选「跟随对话模型」）。新增节点：模型供应商（`ProvidersPanel`：
  预设、增删改、测试连接、获取模型列表、被引用时禁删）、模型分配、Notion、R2（`SyncActions.tsx`：初始化 / 立即推送 /
  账本状态；立即备份 / 强制全量 / 恢复）。
- **`components/prefs/PreferencesWindow.tsx`**：通用、外观、存储、本地模型、组件、诊断六组；菜单 Preferences… 与
  ⌘, / Ctrl+, 打开。组件页显示各组件安装状态与大小，可安装 / 卸载并提示重启服务；诊断页含日志、运行环境、重启服务、
  打开日志目录。
- **欢迎页 / 崩溃页**：`components/desktop/WelcomeScreen.tsx`（空库首启：导入旧版插件数据——选目录、预览、导入进度、
  结果与「需要你确认」事项——或从空库开始）；`BackendGate.tsx` + `CrashScreen.tsx`（启动失败或后端退出时显示最后几行日志，
  可重启服务、打开日志目录）。
- 后台任务完成 / 失败时，窗口不在前台则发系统通知（`ProgressDock`）。
- `lib/i18nDesktop.ts`：新增界面文案与全部 80 个 `set_*` 设置项标签（zh / en）。

### 改进 (Changed)

- 主窗口：顶栏「工作台 / 数据流」两页切换（原 WorkflowModal 改为页面），「显存」按钮打开偏好设置 › 本地模型。
- 后端能力快照新增 `models`、`providers`、`notion`、`r2` 四个环节，删除合并的 `sync`；设置桥额外给出连接数量。
- 数据流节点头部开关改写设置键（`flow.*.enabled` / `flow.vector_store.backend`）；Zotero 面板的保存映射到 `flow.zotero.*`。
- 界面文案「重启插件」统一为「重启服务」，去掉「插件配置 / 环境变量配置密钥」等过时说法；窗口标题改为 Knowledge Arch。
- 开发期代理改用 `KA_API_HOST` / `KA_API_PORT`；`dev/README.md`、`web/frontend/README.md` 按 token 化后端改写。
- 新增依赖 `@tauri-apps/api`。

### 移除 (Removed)

- `LoginScreen`（及样式）、`SettingModal`、`WorkflowModal`、`ModelsModal`、无引用的 `Rail` / `PerfPanel` / `NotionSyncStatus`；
  前端的登录 / 注销 / cookie 逻辑与 `updateConfigValue`（`/api/config/update` 客户端）。

### 测试 (Tests)

- 新增 `test_settings_i18n.py`；`test_capabilities.py` 改测四个新环节。全量 1286 passed / 4 skipped；前端 lint / tsc / build 通过；
  Playwright 冒烟覆盖欢迎页、数据流 11 节点、连接增删测、模型分配、Notion token、偏好设置与旧数据导入。

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
