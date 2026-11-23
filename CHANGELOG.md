# CHANGELOG

> 插件时代（v0.x – v1.2.0）的变更记录完整保留在本仓库首个提交 `89b251a` 中（`git show 89b251a:CHANGELOG.md`），不再随 v2 维护。

## [Unreleased]

### 新增 (Added)

- **外部论文入库（P33 P4a，后端）。** 来源栏的外部论文可以入库到本地集合（用户 11-23 批准计划
  `data/ka_v3/ka_v3_rest_plan.html`，四个决策按默认）。
  - 找 PDF 只试这次运行账本里已经记下的链接（段落所在调用的参数 → 引用的 url → 本次原文里提到这篇的块中的链接，
    最多 6 个，`kacore/domain/paper_import.py` 的 `pdf_candidates`），不再调用模型；下载交给开着的、实现了
    `Tool.fetch_file` 的工具（目前只有 `read_open_pdf`，`kacore/adapters/tools/base.py` / `registry.py` /
    `read_open_pdf.py`），核心不写工具名。
  - 找到 PDF：按模板起文件名（设置项 `flow.external.import_filename`，默认 `{authors} ({year}) - {title}`；
    1 位作者写姓、2 位「A and B」、3 位及以上「A et al.」，冒号写成逗号；姓氏只按「姓, 名」的逗号切），
    走现有上传管线入库（切块、向量、图谱待建与手动上传相同），书目写进文档的 local_meta
    （`kacore/managers/paper_importer.py`）。
  - 找不到 PDF：记成「待补 PDF」，给出能在浏览器打开的链接（DOI 链接、原文页、试过的链接，用户 11-23 评论）；
    用户下载后经 `POST /api/documents` 多带 `import_id` 补上，按同一个文件名入库（`web/server.py` 只多这一个分支）。
  - 账本：迁移 `migrations/030_paper_imports.sql`（`paper_imports` 入库记录，含出处；`zotero_pushes` 推送账本，
    P5 用），仓储 `kacore/repository/source_store/imports.py` / `imports_sqlite.py` / `imports_memory.py`。
  - 接口：`kacore/api_library_io.py`（入库框内容、可选本地集合、按 DOI 提示「库里已有」、待补清单、文档出处）、
    `web/library_io_routes.py`；来源栏详情每条来源多一个 `import` 状态（`kacore/api_external.py`，
    `doi_key` 挪到 domain）。skill 客户端的可写配置表加 `paper_import.filename_template`，命令不变。
  - 测试 `tests/backend/test_paper_import.py`（14 项：文件名、链接顺序、账本内存 / SQLite、入库 / 待补 / 补文件 /
    删掉、目标集合规则、工具箱下载接口、补 PDF 路由）。
- **外部论文入库（P33 P4b，界面）。** 来源栏的「入库…」按钮接上：入库框（`web/frontend/components/panels/ImportDialog.tsx`）
  显示书目、出处（问题原文 · E 编号 · 工具与调用 · 位置）、会试几个链接、目标本地集合与可改的文件名，库里已有同 DOI
  的文献时提示并不再入库；找不到 PDF 时在框里列出能在浏览器打开的链接与每个试过链接的失败原因。文档栏顶部的
  「待补 PDF」清单（`PendingPdfList.tsx`）可选文件或把 PDF 拖到那一行补上、可删掉、可打开当时的来源栏；
  来源栏每条来源标「已入库 / 待补 PDF」（`SourcesPanel.tsx`）；入库来的文档在信息栏显示出处与下载链接
  （`DocumentMeta.tsx` 的 `ImportOriginRows`，`NotePanel.tsx`）。接口封装 `web/frontend/lib/libraryIoApi.ts`；
  外链统一经 `openExternalUrl`（`web/frontend/lib/backend.ts`）用系统浏览器打开，壳的 opener 权限从只放行
  下载页改为放行 http / https 链接（`desktop/src-tauri/capabilities/default.json`）。文案 `imp_*`（中英）、
  样式 `.imp-*`（`styles/tokens.css`）。
- **Zotero 推送（P33 P5a，后端）。** 本地文档可以推送到 Zotero 集合（用户 11-20 定：只走「KA 放文件 + Web API」，
  建独立 linked_file 附件、不建父条目，推送 = 移交）。
  - `kacore/managers/zotero_pusher.py`：文件移进 ZotMoov 的目录——这个集合里已有的附件都在同一个文件夹就放那里，
    否则 ZotMoov 目录 / 集合路径（与 ZotMoov 按集合建子目录一致）；附件 path 写 `attachments:` 相对路径，
    根目录取新设置 `flow.zotero.base_dir`，空着时从已有附件推出（文件绝对路径去掉它的 attachments: 部分）。
    按 Zotero 给的模板（`items/new`）只填模板里有的键：title = 文件名、path、contentType、所在集合、有 DOI 时
    url = DOI 链接。重名加「 (2)」，不覆盖。写入失败把文件移回原处，账本记 failed 与原因；key 没有写权限、
    没配 ZotMoov 目录、文档不是本地的等前提不满足时什么都不动。
  - `kacore/adapters/zotero/web_api.py`：客户端加 `item_template` / `create_items`（带 Zotero-Write-Token）/
    `delete_item`（只给写入测试删测试附件），读取路径不变。
  - 拉取认领（`kacore/pipelines/zotero_push_claims.py`，`zotero_sync_pipeline.py` 只接三处）：附件 key 在推送账本
    里的，把原文档改成这条附件的镜像，文档编号、切块、向量不变，不再新建一篇；严格镜像的删除判断用同一张表
    （否则认领后的文档会被当成已删除——用户现在用的就是严格镜像）。没有推送过时拉取行为不变（有测试锁定）。
  - 入库框的目标可以选 Zotero 集合：入库后立刻推送，文件只放一次（`kacore/managers/paper_importer.py`）。
  - 接口 `GET/POST /api/documents/{id}/zotero-push`（`kacore/api_library_io.py`、`web/library_io_routes.py`）；
    设置 `flow.zotero.base_dir` 与中英标签，skill 客户端的可写配置表同步加一项。
  - 测试 `tests/backend/test_zotero_push.py`（7 项）、`tests/backend/test_zotero_sync.py`（认领 2 项）。
  - 未处理：认领后旧切块元数据里没有 Zotero 链接（「在 Zotero 中打开」要等下次全量拉取重新处理才有）；
    本地 LOCAL 镜像行留着不删（不影响检索与列表）。
- **Zotero 推送（P33 P5b，界面）。** 文档信息栏多一行「Zotero」：本地文档显示「未推送 · 推送到 Zotero…」，推送过的
  显示已推送待拉取 / 已由 Zotero 管理 / 失败原因与「重新推送…」（`web/frontend/components/panels/PushDialog.tsx`、
  `NotePanel.tsx`）。推送框选 Zotero 集合后预览文件会移到哪、附件 path，并说明推送是移交；前提不满足时显示原因。
  入库框的目标里加 Zotero 集合（选它 = 入库后立刻推送，推送失败另行提示，`ImportDialog.tsx`）。
  接口封装与类型 `web/frontend/lib/libraryIoApi.ts`，文案 `push_*` / `imp_zotero_note`（中英）。

## [v3.0.0-preview.1] — 2026-11-23

- **外部文献（联网补充）预览。** 对话栏「联网补充」开关（按对话分开记，默认关）打开时，本地检索之后由研究模型用原生工具调用
  去调 6 个形状照 MCP 的联网工具（Crossref、OpenAlex、arXiv、Semantic Scholar、Open Library、读开放获取 PDF），最多 4 轮、
  最后一轮只给 cite；引用的段落与书目字段必须逐字出现在本次运行记下的工具原文里才采用，接在本地来源编号后面。
  来源栏按「工具 · 调用 · 块」追溯并可看原文、按集合树归类、导出 CSV + JSON；数据流页「联网补充」节点是工具面板；
  skill 新增 `tools` / `tool-call` / `tool-cite`（先问用户再查）。关掉开关时问答与 2.2.0 逐项相同（有测试锁定）。
  真实模型冒烟（用户库副本 + arc:chat + 真实请求）：三题各登记 6 条，正文用到 6 / 5 / 5 条。详见下面各条。
- **升级说明。** 从 2.1.x / 2.2.0 直接升级，不需要重建向量库、不需要重新导入或重切分块：迁移 028（2.2.0 的关键词索引）与
  029（联网账本三张新表）在首次启动时自动执行，首次启动顺带给已有分块补关键词索引（用户库 31245 段约 5 秒，之后为 0）；
  向量库指纹不变。用过开发版的：迁移 029 会删掉开发期旧 029 / 030 的表；开发版里填过的 OpenAlex key 要在工具面板里重填。
- **尚未完成（不在本预览里）：** 入库（P4）、Zotero 回推（P5）、`.kaarch` 导出导入（P6）。已知问题：库里已有但没有 DOI 的书
  会作为外部来源重复出现；Crossref 返回的 `&amp;` 等 HTML 转义没有还原；全文模式与 graph_only 下开关被忽略且界面不提示。
  未在真实环境验证：Anthropic 连接的工具调用、Semantic Scholar、skill 的工具命令、工具面板的手动试调用、关闭模型思考时的联网补充。

### 移除 (Removed)

- **联网相关的无用代码（只查 2.2.0 之后为联网新增的代码，其他模块不动）。** 逐个查新增的函数、方法、常量、导出、
  文案键与样式类是否被用到：删掉没有调用方的 `ToolSession.citation`（`kacore/managers/tool_distributor.py`）、
  `RoleClient.describe`（`kacore/providers/router.py`），没被用到的常量 `SETTING_KINDS` / `ACTORS`
  （`kacore/domain/tool_calls.py`），没被用到的文案键 `chat_external_badge`（`web/frontend/lib/i18nDesktop.ts`）。
  已删的 P1–P3b 功能在代码与文档里没有残留引用。`ask()` 的说明改成实际行为：全文模式与 graph_only 在联网那一步之前
  就返回，开关被忽略。

### 修复 (Fixed)

- **顶栏字标比图标高。** Bitcount 的大写字母在 `line-height: 1` 的行框里偏上 0.06em（ascent 0.84 / descent 0.36 /
  cap 0.6 em），Windows WebView2 上实测比图标中心高约 2.5px。字标改用 `.kr-wordmark`（`web/frontend/styles/tokens.css`）：
  支持 `text-box` 时裁到大写字母高度再由 flex 居中，否则下移 0.06em；Chromium 实测两条路都与图标中心差 0.3px 以内
  （`web/frontend/components/layout/TopBar.tsx`）。
- **偏好设置看不到当前版本。** 原来只有点过「检查更新」才显示版本；现在「软件更新」一打开就显示（浏览器开发模式也显示），
  版本取自 `GET /api/system/info` 新增的 `app_version`（`web/server.py`、`web/frontend/lib/api.ts`、
  `web/frontend/components/prefs/PrefsGroups.tsx`；测试 `tests/backend/test_web_server.py`）。
- **「检查更新」一直不可用，改为手动检查 + 打开下载页（用户 11-23：不要自动检测，只要一个按钮手动检测、辅助更新）。**
  原来按钮走 Tauri updater，但仓库从没配过 updater 签名密钥，历次发布都没带 updater，按钮只会提示「未启用」。
  现在按钮请求新接口 `GET /api/update/check`（`web/update_routes.py`，并登记进 `desktop/scripts/bundle_python.py`）：
  后端查 GitHub 上最新的正式 Release（`releases/latest`，不含草稿与预发布版），按语义化版本与当前版本比较；有新版本时
  显示「打开下载页」，用系统浏览器打开最新 Release 页（`web/frontend/lib/backend.ts` 的 `openDownloadPage`，
  `desktop/src-tauri/capabilities/default.json` 只放行这一个地址），由用户自己下载安装。不在后台检查、不自动下载。
  去掉自动检测相关的界面：启动后检查到新版本时的提示条 `UpdateBanner.tsx` 与样式、「自动检查更新」设置项
  （`prefs.general.check_updates`）、壳的 `checkUpdate` / `installUpdate` 前端封装与相应文案。壳里 `updater` feature
  与发布流水线的 updater 步骤保持原样（没有公钥时不启用）。README / README_ZH「通用」一节同步。
  测试 `tests/backend/test_update_routes.py`（版本比较、接口返回、断网时报 502）。

- **联网补充关着时，问答与 2.2.0 逐项一致（用户 11-23 重申：本地调用和原来一样；关掉时正常功能完全不变）。**
  对照 v2.2.0 逐文件核对后，关着时只剩两处差别，都已去掉：`kacore/api.py` 的返回结果总带 `"external": None`
  （现在只有打开时才带这个键），每次回答都会算一遍外部引用编号（现在只在有联网补充时算）；
  `web/frontend/lib/api.ts` 提问请求总带 `external: false`（现在只在打开时带 `external: true`）。
  测试 `tests/backend/test_ask_external.py`：装了 distributor 但没打开，与根本没装时提示词、返回结果、聊天记录逐项相同，
  不发工具调用、不写账本。

- **联网补充在真实模型上一条引用也登记不上（P33 P3c T6，真实模型冒烟发现）。** 用户库副本 + arc:chat（经 hub）+ 真实
  Crossref / OpenAlex / arXiv / Open Library：三个问题各调用 8 次工具、登记 0 条。
  - `kacore/managers/tool_distributor.py`：最后一轮只开放 `cite`，但模型照调检索工具、原先照样执行；现在本轮没开放的
    工具调用不执行，按出错的调用记账（`ToolSession.refuse`）；最后一轮之前追加「检索结束，现在登记」的提示。
  - `kacore/pipelines/external_prompts.py`：任务里写明轮数与「最后一轮只给 cite」（`render_task` 多一个 `rounds`），
    新增 `FINAL_ROUND_NOTE`、`render_refused`。
  - `kacore/providers/anthropic.py`：紧跟工具结果的用户文字并进同一条 user 消息（排在工具结果之后）。
  - `kacore/domain/tool_calls.py`：`normalize_text` 两边同样把弯引号折成直引号、去掉 Markdown 强调星号；被拒的引用
    多半是原文 `novel’s` / `“Turn to Affect”` / `**In**terviewing` 被模型抄成直引号、去了星号。模型自拼的标识符
    （原文没有的 arXiv DOI）照旧拒绝。
  - 修后同样三题（角色扮演人格一致性 / Massumi 情动自主性的批评 / 大网络 ERGM 估计）：登记 6 / 6 / 6 条，回答正文用到
    6 / 5 / 5 条（第一、三题跑在引号规整之前）；那两次账本里被拒的 5 条重算，4 条排版差异通过、1 条自拼 DOI 仍拒。
  - 测试：`tests/backend/test_tool_distributor.py`（最后一轮拒绝其他工具、提示与轮数）、`test_provider_tools.py`、`test_tools.py`。

### 变更 (Changed)

- **联网补充改为工具调用（P33 P3c 整理，取代下方 P1–P3b 里按数据库写死的部分）。** 用户 11-22：代码要干净、不预设；
  「将每个联网功能适配成类似 MCP 的方式，允许一个 distributor 通过任务内容调用」。
  - 每个联网功能是一个形状照 MCP 的工具（名字、描述、参数格式、返回内容块）：`kacore/adapters/tools/`
    （`base.py` 接口与共用 HTTP、`registry.py` 内置清单与 `ToolBox`、`crossref.py`、`openalex.py`（免费额度判断只在这里）、
    `arxiv.py`、`semantic_scholar.py`、`openlibrary.py`、`read_open_pdf.py`（原 `managers/external_fulltext.py`））。
    设置项由工具声明生成（`kacore/settings/schema.py` 的 `tool_setting_specs`，键 `flow.external.tools.<工具>.<设置>`），
    配置改为通用的 `ToolsConfig`（`kacore/config.py`）；核心代码不再出现任何数据库名。
  - distributor：`kacore/managers/tool_distributor.py` 用 research 模型的原生工具调用（`ProviderClient.generate_with_tools`，
    OpenAI 兼容 / Anthropic / `RoleClient`），默认最多 4 轮（设置 `flow.external.max_rounds`），最后一轮只给 `cite`；
    研究模型不支持工具调用时回答照常、只用库内证据并说明原因。提示词在 `kacore/pipelines/external_prompts.py`。
  - 可追溯核验（取代「拿编号去第二个数据库查」与 DataCite / doi.org）：`kacore/domain/tool_calls.py` 的 `trace_check`
    逐字核对引用段落在所指调用的某一块里、书目字段在本次运行的调用原文里；页码取自内容块，不由模型填。
  - 账本：迁移 `029_tool_calls.sql`（运行 / 每次工具调用含 cite / 通过核验的引用），删除开发库上旧 029 / 030 的表；
    仓储 `kacore/repository/source_store/external*.py` 重写为 `ToolLedgerRepository`。
  - 界面：数据流节点的工具面板 `web/frontend/components/flow/ToolsPanel.tsx`（开关、设置、试调用、最近调用）；
    来源栏只留列表与体系，每条来源标出「工具 · 调用 · 块」并可打开调用原文（`SourcesPanel.tsx`、`lib/externalApi.ts`）。
  - skill：`external-evidence` 换成 `tools` / `tool-call` / `tool-cite`（助手自己挑工具，KA 只执行、核对、记账），
    路由 `GET /api/tools`、`GET /api/tools/calls`、`POST /api/tools/{name}/call`、`POST /api/tools/cite`。
  - 删除：`kacore/adapters/scholarly/`、`kacore/domain/web_sources.py`（DOI/arXiv/PMID/ISBN 规整与互查）、
    `kacore/managers/external_evidence.py`、`kacore/managers/external_status.py`、`kacore/domain/source_graph.py`
    与来源栏图谱（`SourceGraph.tsx`、`sourceGraphLayout.ts`）、排名漂移、`ExternalActions.tsx`；P4/P5 提前写的
    `kacore/domain/file_style.py`、`kacore/domain/external_ledger.py` 与迁移 030 等到那一步再写。
  - 测试：新增 `test_tools.py`、`test_tool_distributor.py`、`test_provider_tools.py`；重写 `test_ask_external.py`、
    `test_external_ledger_store.py`；删除只测已删规则的 6 个测试文件。全量 pytest 1657 passed / 4 skipped。

### 新增 (Added)

- **3.0.0 数据层（P33 P1）：回答引用外部文献的出处账本与学术索引适配器。**
  `kacore/domain/web_sources.py`：外部文献的标识规整（DOI / arXiv / PMID / OpenAlex / 净化 URL，跟踪参数表同
  ai-source-scraper 1.7.0）、按任一共同标识归并（不按标题猜）、字段级出处、核验规则（持久标识 + 第二个索引按编号查到
  = 已核验；撤稿 / doi.org 查不到 / 无持久标识不可引用）、排名漂移。`kacore/domain/file_style.py`：入库文件按用户现有
  风格命名（`{作者} ({年份}) - {标题}`，目录 = 集合路径，Windows 字符清理、同名加序号）与风格统计。
  `kacore/domain/external_ledger.py`：账本记录类型。迁移 `029_external_sources.sql`（capture / 文献 / 命中 / 引用段落 /
  字段出处）与 `030_zotero_push_ledger.sql`。`kacore/adapters/scholarly/`：Crossref、arXiv、OpenAlex、Semantic Scholar、
  DataCite、doi.org 适配器（只取书目数据，不取外部服务的分类与推荐；限速；请求函数可注入）。仓储契约
  `kacore/repository/source_store/external.py` 与 `external_sqlite.py` / `external_memory.py`（混入源文档库）。
  测试：`test_web_sources.py`、`test_file_style.py`、`test_migration_029_030.py`、`test_scholarly_adapters.py`、
  `test_external_ledger_store.py`。尚未接入问答与界面（P2 起）。
- **3.0.0 问答接入外部文献（P33 P2，后端）。** `ask(external=True)`：库内检索后去学术索引找外部文献、按编号核验，
  可引用的作为追加证据合成，Harvard 引用照常（来源条目 `origin: "external"`、带核验状态）；已在库（按 DOI）的不作外部证据；
  结果与聊天记录带 `external` 摘要，出处与引用段落写进账本。`kacore/managers/external_evidence.py`、
  `kacore/managers/external_fulltext.py`、`kacore/pipelines/external_prompts.py`、`kacore/adapters/scholarly/factory.py`、
  `kacore/api.py`、`kacore/app_initializer.py`、`web/server.py`（`/api/ask` 的 `external`，新 `POST /api/external/evidence`）、
  `web/agent_routes.py`。skill：`external-evidence --user-approved`，SKILL.md 联网边界改为先问用户再查。设置分组
  `prefs.external`（`kacore/settings/schema.py`、`kacore/config.py` 的 `ExternalEvidenceConfig`、`i18nDesktop.ts` 标签）。
  测试：`test_external_evidence.py`、`test_ask_external.py`，以及客户端 / 适配器 / 设置的相关断言。
- **外部文献支持只有 ISBN 的书（人文专著）。** ISBN 成为可核验编号；新增 Open Library 适配器
  （`kacore/adapters/scholarly/openlibrary.py`），Crossref 读取并可按 ISBN 查；设置 `prefs.external.openlibrary`。
- **数据流新增「联网补充」节点。** 各数据库开关、key、读到多深都在节点里配置（设置键改为 `flow.external.*`），节点显示
  各数据库最近是否成功与 OpenAlex 今日免费额度；OpenAlex 免费额度用完后当天自动跳过，不重试、不产生费用
  （`kacore/managers/external_status.py`、`kacore/capabilities.py`、`kacore/api_capabilities.py`、
  `web/frontend/components/flow/ExternalActions.tsx`、`model.ts`）。
- **回答的「来源」栏与对话栏「联网补充」开关（P33 P3）。** 输入框旁的开关只对当前对话有效，新对话默认关；用了外部文献的
  回答下方显示「来源 · 外部 N 篇」，点开占左侧文件区：列表（引用的 / 只查到的，带核验状态）、体系（按用户自己的集合树归类，
  第一次打开时才调用 research 模型，结果存回账本）、图谱（边只来自参考文献列表）、出处详情（各数据库名次、排名变化、
  引用段落与页码、字段来源）、导出 CSV / JSON（列兼容 ai-source-scraper 1.7.0）。`kacore/api_external.py`、
  `kacore/domain/source_graph.py`、`kacore/pipelines/external_prompts.py`、`web/server.py`
  （`/api/external/captures/{id}` 及 `placement`、`export`）、`web/frontend/components/panels/SourcesPanel.tsx`、
  `ChatPanel.tsx`、`lib/externalApi.ts`、`ConsoleContext.tsx`、`app/(console)/layout.tsx`。
  测试：`test_source_graph.py`、`test_ask_external.py`。
- **来源栏图谱好点、看得清。** 画布铺满、可滚轮缩放 / 拖动平移 / 拖节点 / 适应窗口（与数据流页同手感）；
  悬停显示信息卡并调暗无关节点；相连的簇分开排、孤立文献排成网格；标签不小于 12px，篇数多时只常显关键标签；
  「只看回答引用的」开关；库内节点显示文档标题。`web/frontend/components/panels/SourceGraph.tsx`、
  `web/frontend/lib/sourceGraphLayout.ts`、`kacore/api_external.py`（`graph.library_titles`）。

## [v2.2.0] — 2026-11-19

### 修复 (Fixed)

- **.md / .txt 上传不再被当成 PDF 抽取**：上传的原件一律存成 `original.pdf`，抽取时按后缀判断，文本文件也进了 PDF
  抽取器（英文被改写、中文正文可能丢失）。现在按原件内容判断（前 1024 字节内有 `%PDF-`），三处判断共用
  `kacore/managers/ingest_manager.py` 的 `_is_pdf_file`。已导入的文档不自动重处理（不触发重新分块 / 重新嵌入），
  需要时手动重处理即可恢复。测试：`tests/backend/test_ingest_manager.py`。
- **没有向量库时的关键词召回改用 SQLite FTS5（BM25）**：以前按空格拆词后 `text LIKE` 全量扫描、按词频打分，
  `the` / `of` 也加分，长句排序被拉偏，常用词会把整个范围的分块拉进 Python。现在分块写入时生成派生检索文本
  （`kacore/utils/search_text.py`：中日汉字拆二元组，其余小写；porter 词干让 limitation / limitations 互相匹配），
  查询去中英停用词后由 SQLite 按 BM25 排序截断（`kacore/pipelines/retrieval_orchestrator.py` 的 `_search_lexical_sqlite`）。
  新迁移 `migrations/028_chunks_fts.sql` 只新增索引表与触发器，不改分块、不动向量；已有分块在启动时一次补索引
  （`kacore/repository/source_store/search_index.py`，`kacore/app_initializer.py` 调用；写入点 `sqlite.py` 的
  `replace_chunks` 与 `processing.py`）。响应字段与 engine 名 `sqlite_lexical` 不变。
  测试：`tests/backend/test_search_text.py`、`tests/backend/test_keyword_recall.py`。

- **深度思考不选集合也能用（全局或限定范围）**：以前没选集合时深度思考按钮是灰的，外部 agent 取证据接口和 skill 客户端也要求
  `--collection`。现在三档（default / enhanced / deep_thinking）都可全局或按集合 / 范围检索（`kacore/api.py` 的
  `retrieve_agent_evidence`、`web/frontend/components/panels/ChatPanel.tsx`、`.agents/skills/operate-knowledge-arch/`，README 中英）。
- **全局与父集合检索补上语义召回**：集合为空时召回编排器跳过了向量检索，全局的深度思考 / 增强只剩关键词召回；选父集合时向量
  检索只按父集合自己的标签过滤，子集合与多归属文档召不到。向量库本是单表，现在有范围时按文档白名单约束、不再按集合标签过滤，
  全局时不过滤并剔除脱管（detached）文档（`kacore/pipelines/retrieval_orchestrator.py` 的 `_search_dense`；
  `kacore/repository/vector_store/` 的 `base.py` 契约：空 collection = 不按集合过滤，`milvus_lite.py` / `memory.py` 实现）。
  default 档因此不再按集合名逐个扇出再合并，召回一次覆盖整个范围（删去 `_resolve_ask_collections`）。
  测试：`tests/backend/test_retrieval_orchestrator.py`、`test_vector_store.py`、`test_api.py`、`test_agent_evidence.py`。

- **Windows 上后端「活着但连不上」**：客户端在连接握手中途断开时（`WinError 64`），asyncio 的 Proactor 循环会关掉监听
  socket 且不再恢复；进程不退出，壳不报崩溃，界面停在 Loading / 「Log endpoint is unavailable」，偏好里的「重启服务」
  走 HTTP 也发不出去，只能退出应用重开。现在后端每秒检查监听 socket，被关就在原端口重新监听
  （`ka_server/lifecycle.py` 的 `keep_listening`，`ka_server/__main__.py` 接入）；桌面界面每 5 秒探活，壳报告就绪却连续
  3 次连不上时进入崩溃页，「重启服务」由壳重启进程（`web/frontend/components/desktop/BackendGate.tsx`、
  `lib/backend.ts` 的 `backendReachable`）。测试：`tests/backend/test_ka_server.py`。

### 变更 (Changed)

- **深度思考合成前先核对问题前提**（P32 P5c）：合成模板原本无条件要求「证据部分相关时不要以没有证据拒答，给出有据推断」，
  假前提的问题（来源对发现 / 归属 / 比较的说法不同，或问题假定的报道 / 实体根本不在资料里）也被顺着前提作答。现在先核对前提：
  矛盾时开头指出、按来源实际说法作答；假定的来源不存在时直说资料中没有；有据推断只在前提成立时适用（中英模板同步）。
  `kacore/pipelines/answer_synthesis.py` 的 `_DEEP_INTRO`；测试 `tests/backend/test_deep_thinking_orchestrator.py`。
  KA-Benchmark mini_v2 三次对三次：答对与成本持平（未达显著），待全量确认。同轮试过并撤回：deep 重排权重 0.2 → 0.5（P5a）、
  窄问题不写分节报告（P5b），均无收益。
- **深度思考的校验补检用对检索词，且不再用更差的第二稿换掉第一稿**（P32 P4）：校验不合格后的补检以前把「部分有据的断言句」
  （`partial_claims`）也当检索词；现在只用信息缺口与硬缺失。补检后的第二稿以前总是替换第一稿；现在只有不比第一稿差才替换
  （第一稿合格而第二稿不合格、或第二稿硬缺失更多 → 用第一稿及其证据）；第二次合成 / 校验失败时返回已校验的第一稿。
  `kacore/pipelines/deep_thinking_orchestrator.py`；测试 `tests/backend/test_deep_thinking_orchestrator.py`。

- **深度思考证据充分时不再因「新发现」多跑一轮**（P32 P3）：以前收敛要求本轮 SEA 没有新发现的方面，而 SEA 几乎每轮都有发现，
  于是多跑一轮检索 + 审计。现在关键检查项都满足且 SEA 判充分即收敛；发现项仍并入检查清单与思考过程。
  `kacore/pipelines/deep_thinking_orchestrator.py`；测试 `tests/backend/test_deep_thinking_orchestrator.py`。

- **深度思考在会话路径上保住结构锚点**（P32 P2）：题目点名的表 / 节（锚点命中）无条件保留进证据——以前证据会话路径从不填
  `pinned_ids`，锚点块会被高分块挤掉。`kacore/pipelines/deep_thinking_orchestrator.py`；测试
  `tests/backend/test_deep_thinking_orchestrator.py`。（同批试过的「次方面降权 + 最终证据 18→12」在 mini 上丢了金标证据，已撤回，
  见 `docs/deep-thinking-iteration-plan.md`。）

- **深度思考 / 增强召回的证据选择少做重排**（P32 P1）：各 query 池的重排改为并发（锁只护打分缓存）；「补候选」每个 query
  只在首次出现时补一次、之后沿用（以前每轮按新进的块重补，旧方面的池不断变、新块逐个重打分）；补候选的匹配词改用
  `kacore/utils/search_text.py` 的 `query_terms`（去中英停用词，中文按二元组——以前整句中文几乎补不进候选）。
  `kacore/pipelines/evidence_session.py`；测试 `tests/backend/test_evidence_session.py`。

### 新增 (Added)

- **托盘菜单「Restart service」**：进程级重启后端，界面卡住时也能用（`desktop/src-tauri/src/lib.rs` 的 `restart_sidecar`，
  与崩溃页的 `restart_backend` 共用）。
- **skill：只有关键词召回时怎么写 query**：`references/research.md` 新增「Keyword-only recall」一节（关键词组而非整句、
  同义词与缩写全称、按文献语言写、编号原样保留、从首轮证据取原文术语、用满每轮 query 数）；`SKILL.md` 一句指针。
  `KA doctor` 新增 `retrieval_engines` 与 `vector_recall`，取自 `/api/capabilities` 的 retrieval 环节，服务端未改
  （`scripts/knowledge_arch_client.py` 的 `_pipeline_stage` 由原 `_vector_store_stage` 泛化，索引状态共用）。
  测试：`tests/backend/test_knowledge_arch_skill_client.py`。

## [v2.1.3] — 2026-11-18

### 变更 (Changed)

- **向量索引改为 HNSW，大库检索不再吃内存（P30）**：Milvus 集合原用 `FLAT`，Milvus Lite 每次检索都把有效向量整段复制一份再算距离，
  494 篇 / 52,477 片段的库单次检索 +0.6G，几个检索同时跑就能把进程内存撑爆。现在新集合建 HNSW（M=16、efConstruction=200、ef=64）；
  真实库副本上检索不再有内存尖峰，单次约 1.1 s → 0.4 s，前 10 条与 FLAT 重合 99.5%。
  - **已有的库自动换索引**：启动后在后台把 FLAT 换成 HNSW（不重新嵌入，5 万片段约 1 分钟，只做一次），期间检索与写入排队等待；
    换索引中途被打断，下次开库自动补建（`kacore/repository/vector_store/milvus_lite.py` 的 `MilvusIndexSpec`、`migrate_index`；
    `base.py` 增加默认空操作的 `migrate_index`；组合根 `kacore/app_initializer.py` 的后台任务与 teardown 顺序）。
  - 同时在跑的向量检索有上限（默认 4）；索引类型与 HNSW 参数是内部配置项 `vector_db.index_type` / `hnsw_*` / `search_concurrency`
    （`kacore/config.py`，不进设置界面）。测试：`tests/backend/test_milvus_index.py`。

### 修复 (Fixed)

- **Intel Mac 安装包后端起不来**：v2.1.1 的 x64 DMG 启动即报 `Library not loaded: /usr/local/opt/openssl@3/lib/libssl.3.dylib`。
  cryptography 49 起没有 Intel macOS wheel，CI 从源码构建并链接了 runner 上的 Homebrew openssl。打包时改为只装预编译 wheel
  （`desktop/scripts/bundle_python.py` 的 `install_requirements` 加 `--only-binary :all:`，Intel Mac 上 cryptography 解析为 48.0.1），
  冒烟测试加导入 `kacore.secret_store`。测试：`tests/backend/test_bundle_python.py`。
- **模型调用失败时不再给出假答案**：已分配对话模型、但这次合成调用失败（断网、5xx、超时、空响应）时，以前会把
  「This is an offline placeholder answer…」当作回答返回；现在改为「LLM 暂不可用」加检索到的原文摘录，响应带
  `generation_status: "llm_unavailable"`（`kacore/api.py` 的 `_excerpt_answer` 与通用合成分支）。离线占位只在未分配模型时出现。

## [v2.1.2] — 2026-11-18

### 变更 (Changed)

- **选论文的交互（2.1.2 第二批）**：不再照搬 Zotero，改成「左栏选范围、中栏选论文、右栏提问」。
  - **中栏列表 = 对话范围**：点一个集合，列出「集合 + 全部子集合」的论文，与对话、图谱、识图处理的是同一批（以前照 Zotero 只列本级，
    父集合显示「暂无文档」，对话却在搜子集合里的论文；Zotero 改过名的集合里的论文哪个集合都列不出来）。父集合按子集合分组，组头可折叠
    （`kacore/api.py` 的 `list_scope_documents`，图谱就绪 / 建图 / 识图三处同一段逻辑合并到它；`web/server.py` 的 `descendants=1`）。
  - **两行列表**：每篇一行标题、一行作者 · 年份；Zotero 独立附件用文件名猜出的作者与年份，与回答引用同一份（以前 Zotero 论文在列表里
    只剩标题）。常驻查找框（标题 / 作者 / 年份 / 标签）、排序（标题 / 作者 / 年份 / 加入时间，记在本机）、只看有问题的论文；
    正常论文不画状态图标，没切出文字或等待重建向量的才提示；↑↓ 移动、Enter 打开；去掉每行的 PDF 标与点了没反应的查找键
    （`web/frontend/lib/docList.ts`、`lib/useCollectionDocs.ts`、`components/panels/DocList.tsx`、`DocumentsPanel.tsx`；`_document_dict`
    改用 `document_biblio_meta` 并带 `collection_keys`）。
  - **左栏一棵集合树**：顶上「全部文档」，Zotero 与本地两组，每行是含子集合的篇数（`/api/collections` 的 `doc_count` /
    `doc_count_total`，新路由 `/api/library/summary`）；图谱是行上的状态点（建好 / 正在建 / 中断，没建不画）；构建图谱、补全图表描述、
    新建子集合、改名、删除收进行尾 ⋯ 菜单（右键同样打开，每项带图标）；去掉「LightRAG 集合」区、它的进度卡（进度在左下角进度栏）与
    紫色 LightRAG 模式；每个操作只出现一次（`components/panels/CollectionTree.tsx`、`FilePanel.tsx`，对话框拆到 `CollectionDialogs.tsx`、
    `UploadDocumentDialog.tsx`）。
  - **阅读时左栏**：「‹ 集合名」+「论文」「笔记」两个页签（记在本机），在「论文」里直接换一篇；阅读器栏头 ‹ n/m › 按当前查找与排序
    换上一篇 / 下一篇；返回列表时停在刚读的那篇（`components/panels/ReadingSidebar.tsx`、`app/(console)/layout.tsx`）。
    笔记面板改按 doc_id 取文档，子集合里的论文不再一直「加载中」（`NotePanel.tsx`）。

### 修复 (Fixed)

- **Zotero 同步后整库向量重灌（「文档爆炸」）**：严格镜像模式只要拉到一篇新增 / 变更 / 删除，同步收尾就清空 Milvus、把全库约 3 万分块重新写入
  （真实日志里每次 new>0 都触发，一次约 6 分钟）。新文档在同步中已逐篇索引并清掉旧分块，删除也已逐篇移出 Milvus，
  所以去掉这次全量重建；逐篇失败仍进待重建队列由自动重建补上。strict 删除时 Milvus 删不掉才把向量库标为需重建
  （`kacore/pipelines/zotero_sync_pipeline.py` 去掉 `needs_milvus_rebuild`、`kacore/api.py` 的 `_run_zotero_pull` 与 `_remove_document_index`）。
- **重建或同步期间集合显示「0 篇文献」**：文档列表为每篇文档取分块数时走了带处理锁的 `list_document_chunks`，全量重建持锁数分钟，
  列表请求排队到前端 30 秒超时，界面把失败当成空集合。列表改用只读、不取锁的 `count_document_chunks`
  （旧分块的重切留到打开文档或重建清洗时）；前端列表加载失败时显示「加载失败 + 重试」，不再显示 0 篇
  （`web/server.py` 的 `_document_dict`、`kacore/api.py`、`web/frontend/components/panels/DocumentsPanel.tsx`、`lib/i18n.ts`）。

### 变更 (Changed)

- 版本号 2.1.2。

## [v2.1.1] — 2026-11-13

### 新增 (Added)

- **引用跳到 PDF 原文**：PDF 视图里点回答中的引用（或来源卡片），在原 PDF 上高亮这段原文（可跨页），图块画出图的框；
  定位不到时跳到起始页、整页闪一下并提示，可「在 MD 中查看该段」。MD 视图仍定位并闪烁分块。
  定位只比字母与数字、用分块首尾锚点在分块页码 ±1 页内查找，两篇测试论文的正文分块全部命中
  （`web/frontend/lib/pdfLocate.ts`、`components/panels/PdfViewer.tsx`、`PdfPage.tsx`、`PdfPage.module.css`）。
- **默认 PDF 视图**：PDF 文档默认用 PDF 视图打开，MD / PDF 选择全局一个、记在本机；非 PDF 文档只有分块视图
  （`lib/ConsoleContext.tsx` 的 `readingMode`、`components/panels/DocumentsPanel.tsx`）。

### 修复 (Fixed)

- **向量库节点变黄不自动恢复**：打开旧分块文档时的隐式重切会把文档标成待重建，但没有通知自动重建调度器，只能手动点重建
  （`kacore/api.py` 的 `_ensure_document_chunks_current`）。
- **引用有时跳不过去**：引用的文档不在左侧所选集合里时没有反应；跳到另一篇文档时分块还没加载就定位，之后引用状态卡住，
  在列表里点别的文档又被拉回；分块重切后旧回答的 chunk_id 找不到。现在统一经 `jumpToCitation`，按需取回文档、等分块就绪、
  按来源文字回找（`lib/ConsoleContext.tsx`、`components/panels/ChatPanel.tsx`、`DocumentsPanel.tsx`）。
- **进度条不是真实进度**：向量重建清洗段做完显示 100% 后又掉回一半，计数与百分比对不上（`kacore/milvus_build.py`、`ProgressDock.tsx`）；
  LightRAG 建图按 LLM 调用次数累加分块，进度跑到后面文档上再卡住（`kacore/api.py`）；上传摄入的 20% / 70% 改为不确定进度条
  + 「第 X/Y 步」与批量上传的「文件 X/Y」（`kacore/ingest_job.py`、`lib/uploadBatch.ts`、`FilePanel.tsx`）。
- **PDF 视图上方留白过大**：PDF 视图不再显示文档头与重复标题，阅读区从工具栏下直接开始（`DocumentsPanel.tsx`、`PdfViewer.tsx`）。

### 变更 (Changed)

- 版本号 2.1.1。

- **分块版本升级**：`PROCESSING_VERSION` `cleaning-v4` → `cleaning-v5`（`kacore/managers/artifact_provenance.py`），
  `CHUNK_SCHEMA` `clean_md_structural_v3` → `clean_md_structural_v4`（`kacore/managers/chunking.py`）；已入库文档会被
  `IngestManager.chunk_needs_rebuild` 判为旧数据，重新切分并重建向量。本次只升版本号，未改分块代码本身。
  `tests/backend/test_ingest_manager.py` 改为引用 `CHUNK_SCHEMA` 常量，不再写死版本字符串。

## [v2.1.0-preview.1] — 2026-10-31

### 新增 (Added)

- **识图：给图表补描述**（手动、按集合、只补缺失）。「模型分配」新增**识图模型**角色（不跟随对话模型，须能看图）；
  「Upload / Chunk」节点新增**识图**开关（默认关）、并发与超时，以及「测试识图模型」（发一张现画的红色柱状图问颜色）。
  文件面板顶部「补全图表描述」先显示图数与估算 token，再在后台逐图从原 PDF 现渲染（150 dpi、≤1.6 MP，照片类大图改 JPEG）、
  交给识图模型写检索用描述（提示词 `figure-v1`：图的类型、坐标轴、系列、读得出的数值与趋势、流程图结构，不编造），
  写回图空位并重建该文档的图块；左下角进度栏显示文档 · 图 · 页与 n/m，可取消、可续跑；模型不收图片 / 密钥无效 / 模型不存在时整体停下，
  超时等单张失败下次重试。日志 `FigureVision`（终端页分类 `vision`）每张图一行 DEBUG。
  OpenAI 兼容连接发 `image_url` data URI，Anthropic 发 base64 图片块（`kacore/providers/*`、`kacore/vision_job.py`、
  `kacore/pipelines/figure_caption_pipeline.py`、`kacore/api_vision.py`、`web/server.py` 的 `/api/vision/*`、
  `web/frontend/lib/visionApi.ts`、`components/panels/VisionBuildDialog.tsx`、`components/flow/VisionActions.tsx`、`ProgressDock.tsx`）。
  图块里的描述标明「generated by a vision model」；聊天来源卡片给图块加「Figure N · p.X」标记。
- **PDF 阅读器关键词查找**：工具栏新增查找键（鼠标在阅读器上时 Ctrl/⌘+F），Enter / Shift+Enter 跳下一处 / 上一处，显示 n/m，
  Esc 关闭；不区分大小写，跨行短语也能找到；没有文字层的扫描件给出提示。页面叠加 pdf.js 文字层，顺带可以选中、复制文字
  （`web/frontend/lib/pdfSearch.ts`、`components/panels/PdfPage.tsx`、`PdfPage.module.css`、`PdfSearchBar.tsx`、`PdfViewer.tsx`）。
- **本地找图与图空位**：上传与重抽取 PDF 时按 PDF 结构找图（位图、矢量绘图簇、`Figure / 图 N` 标题），不调模型、不做 OCR；
  每张图存一个空位（位置、标题、图内文字），识图描述后补；扫描页只记页码。新迁移 `migrations/027_document_figures.sql`
  （`kacore/domain/figures.py`、`kacore/managers/figure_detection.py`、`kacore/repository/source_store/figures*.py`、`ingest_manager.py`）。
- **图块**：每张有标题或图内文字的图生成一个 `chunk_kind=figure` 的块（`Figure N (page p)` + 标题 + 描述 + 图内文字），
  可被向量、关键词与「Figure N」锚点召回；不续接相邻正文，不进 LightRAG 图谱（`evidence_views.py`、`api.py`）。

### 修复 (Fixed)

- **正文里的图表垃圾**：矢量图表被误判成的伪表格（`|Col1|Col2|…`、成堆 `<br>`、删除线）、自定义字体抽出的乱码行、
  坐标轴刻度碎行不再进正文；图内文字按位置挪进对应图的空位而不是删除（`kacore/managers/figure_noise.py`、`markdown_extractor.py`）。
  处理版本升到 `cleaning-v4`：升级后启动时旧文档在后台重新处理（左下角「数据清洗」进度），原 PDF 在的从原件重抽取并补上图空位，
  不在的从保存的原始页重洗。内容没变的块沿用 embedding 缓存。开发期评测脚本 `dev/eval_body_cleaning.py`。
- **没有文字的 PDF 反复重切**：扫描件切不出块，以前每次列文档都被判为需要重切；现在处理过一次就不再重复（`ingest_manager.py`）。

## [v2.0.7] — 2026-10-30

### 修复 (Fixed)

- **本地模型面板把线上 Rerank 显示成本地模型**：选了线上 rerank 时，偏好设置「本地模型」里有一行「Rerank · 已卸载」并带卸载键，
  看起来像在本机拉起了重排模型；不重排时这一行显示未翻译的状态名。`kacore/api_runtime_models.py` 对线上 rerank 报 `external`
  （界面显示「远端」，与线上 Embedding 一致，不计入驻留数），不重排时不列出；`components/panels/ModelRuntimePanel.tsx`
  对远端行不显示卸载键。排查确认：选线上或不重排时不会构造本地 cross-encoder、不会 import sentence-transformers，
  不会下载本地模型（`tests/backend/test_model_runtime.py` 新增回归）。

### 改进 (Changed)

- **LightRAG Collection 默认收起**：`components/panels/FilePanel.tsx`，点标题右侧箭头展开；构建进度仍在左下角进度栏显示。

## [v2.0.6] — 2026-10-29

### 新增 (Added)

- **聊天可限定单篇文档**：`components/panels/ChatPanel.tsx` 的范围菜单顶部加「当前浏览的文档」，点一次钉住中间当时打开的那篇；
  之后换文档、左侧点集合都不改范围，菜单里再点新打开的那篇即换，选任一集合即取消。默认 / 增强召回 / 深度思考 / 全文都只在这一篇里检索，
  图谱两档变灰并提示。后端 `kacore/pipelines/retrieval_orchestrator.py` 新增 `document` 作用域（只放行该 doc_id，屏蔽图谱通道）；
  `kacore/api.py` 的 `ask` 在非全文模式收到 `doc_id` 时按该文档的主归属集合召回再硬过滤，聊天记录 `meta.scope` 记文档标题；
  图谱档位带 `doc_id` 返回 400。

### 修复 (Fixed)

- **Zotero API Key 保存后又显示「未设置」**：数据流页每 5 秒的自动刷新用 `/api/config/effective` 整个替换配置，冲掉了
  `/api/zotero/config` 合并进来的 key 状态、用户名与连接状态（key 其实已保存、同步可用）。`components/panels/FlowPageContent.tsx`
  记住最近一次 Zotero 状态并在每次刷新时合并回去；定时刷新进行中时，保存 key 触发的 Zotero 状态刷新不再被丢掉。
- **删除集合后出现 `_uncategorized`**：删除集合时文档被迁进内部系统集合 `_uncategorized` 并以原名显示，而启动时又会重建一个空的 `default`。
  现在默认集合（`source_store.default_collection`）不可删除、不可改名，删除其他集合时无归属的文档迁入默认集合；
  启动时 `merge_legacy_uncategorized`（`kacore/api.py`，由 `kacore/app_initializer.py` 调用）把旧的 `_uncategorized` 并回默认集合并删掉。
  按 key 删除时，还有别的归属的文档主归属改到剩下的集合，不再留着已删除的名字。`/api/collections` 新增 `is_default`
  （`web/server.py`），`components/panels/FilePanel.tsx` 对默认集合隐藏删除 / 改名键；删除确认框的说明改为实际行为
  （此前写「永久删除该集合及其所有文档」）。
- **在线模式未填 Zotero API key 时同步按钮仍可点**：就绪判断只看 Zotero 是否启用与 PDF 依赖。`kacore/api_capabilities.py`
  在 server 模式且没有 key 时把 zotero 环节报为 degraded（附 `server_key_present` 与原因），文件面板的 Zotero 同步按钮随之变灰，
  点击提示去数据流页填 key。本地模式不受影响。

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
