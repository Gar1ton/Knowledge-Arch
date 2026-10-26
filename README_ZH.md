<div align="center">

<img src="./desktop/assets/logo.svg" width="96" alt="Knowledge Arch Logo" />

# Knowledge Arch

**本地优先的学术知识库桌面应用（macOS / Windows / Linux）**

*PDF 原件库 · Zotero 同步 · 六档检索问答 · Harvard 引用 · LightRAG 图谱 · Notion / R2*

[![version](https://img.shields.io/badge/版本-v2.0.2-blueviolet)](https://github.com/Gar1ton/Knowledge-Arch/releases)
[![license](https://img.shields.io/badge/license-AGPL--3.0-green)](LICENSE)
[![en](https://img.shields.io/badge/English-README.md-blue)](README.md)

</div>

---

> **项目来源**：Knowledge Arch 是 AstrBot 插件 [**AstrBot KA v1.2.0**](https://github.com/Gar1ton/Astrbot_Knowledge_Repository/tree/8e6f1af) 的独立桌面版。整体实现承接自该版本，并与聊天机器人宿主解耦；首次启动可直接导入旧版插件数据。

Knowledge Arch 以 PDF 等原件为中心管理学术资料：上传与清洗、集合树与标签、Zotero 镜像、多档检索问答（答案附带由代码确定性生成的 Harvard 引用）、LightRAG 知识图谱，以及 Notion / Cloudflare R2 同步备份。安装一个独立桌面应用即可使用；文献原件、索引与加密保存的密钥均留在本机，只有你主动配置的模型供应商、Notion 与 R2 端点会收到对应请求。

## 安装

从 [Releases](https://github.com/Gar1ton/Knowledge-Arch/releases) 下载对应平台的安装包：

| 平台 | 安装包 | 说明 |
|------|--------|------|
| macOS 11+（Apple Silicon / Intel） | `.dmg` | 拖入「应用程序」 |
| Windows 10/11（x64） | NSIS `setup.exe` | 装在当前用户目录，**不需要管理员权限**（支持中英文安装界面）；缺少 WebView2 时自动下载 |
| Linux（x64） | `.deb` / `.AppImage` | `.deb` 依赖 `libwebkit2gtk-4.1-0`、`libayatana-appindicator3-1` |

安装包内置独立 Python 3.12 运行时与全部核心依赖，**无需另行安装 Python 或 Node.js**。未签名构建在 macOS 上首次打开需到「系统设置 › 隐私与安全性」放行，Windows 上 SmartScreen 会提示「更多信息 › 仍要运行」。

## 首次启动与快速开始

1. **启动本机服务**：应用启动本机后端（只监听 `127.0.0.1`、端口由系统分配、每次启动生成新的 256 位访问令牌），空库首次启动进入欢迎页。
2. **选择界面语言**：中文或 English，之后可在「偏好设置 › 通用」更改。
3. **欢迎页二选一**：
   - **从空库开始**：新建空白知识库。
   - **导入旧版插件数据**：选择旧版插件安装根目录（含 `data/plugin_data/`）。应用会**复制**（不修改、不删除旧目录）SQLite 数据库、原件库、向量索引、图谱 workspace 与密钥，自动改写文档路径并迁移设置：
     - **保持本地 Embedding**：从旧版插件迁移时，Embedding 来源默认保持为**本地模型**（`local`，除非旧配置显式使用 `external` 外部接口）。导入后请到「偏好设置 › 组件」安装「本地 Embedding 与 Rerank」，或改选线上 Embedding API（改选后会重建向量索引）。
     - **向量库统一为内置 Milvus Lite**：若旧配置为「仅 SQLite 词法召回」，导入时会自动改回内置 Milvus Lite，并在导入完成后的「需要你确认」列表中提示。
4. **用 AI 助手完成配置（推荐）**：欢迎页最后一步给出一段 prompt，复制后粘贴到 Codex 或 Claude Code。
   助手会逐项询问你用哪些模型服务、要不要接 Zotero / Notion / R2，然后替你配置整条数据流；页面上实时显示连接状态与各节点进度。
   - 复制 prompt 即打开「偏好设置 › 通用 › 允许 AI 助手连接」：应用把本机地址与每次启动生成的 token 写进只有当前用户可读的连接文件，关闭开关或退出应用即删除。
   - AI 助手只能使用配置、检索与问答相关的接口，**不能写入任何 API Key 或 token**：需要密钥时应用弹出输入框，由你直接填进应用，助手看不到。
   - 也可以在偏好设置里一键把 skill 安装到 Codex / Claude Code，之后直接说「帮我配置 Knowledge Arch」即可。
5. **或手动配置模型并开始使用**（在「数据流」页；在欢迎页选「跳过，自己配置」时会出现分步引导，偏好设置 › 通用可重新查看）：
   - 点开「模型供应商」节点，添加至少一个 **OpenAI 兼容**连接（对话/研究/图谱也可添加 Anthropic）。
   - 点开「模型分配」节点，选择**对话模型**（研究与图谱模型留空则自动跟随对话模型）。
   - 点开「向量化」节点（及「检索编排」节点的 Rerank），选择刚添加的 OpenAI 兼容连接与模型；若使用本地模型，先到「偏好设置 › 组件」安装。
   - 切回「工作台」上传 PDF 或同步 Zotero，即可在右侧 **Ask** 面板提问。

若后端启动失败或意外退出，界面会切换到服务停止页：展示最后几行日志，可点击「重启服务」或「打开日志目录」。

## 语言与输出说明

| 范围 | 行为 |
|------|------|
| **界面语言** | 默认**英文（English）**，可在「偏好设置 › 通用」切换为**中文**。 |
| **Terminal 日志与系统提示** | 后端 Terminal 与文件日志、桌面壳菜单/托盘/通知，以及后端返回的诊断与报错提示均为**英文**。 |
| **回答语言** | 在「数据流 › 问答 Ask」节点可选 `auto`（随提问语言）/ `zh`（中文）/ `en`（英文）。中文回答完整支持；兜底说明与答案末尾的「参考文献 / References」标题随回答语言切换。由于目前的提示词系统是按中文设计的，深度思考过程中暴露的提示暂时都是中文。 |

## 配置：数据流页

主窗口顶栏可在「工作台」与「数据流」之间切换。数据流页将整条处理链路展示为节点图，点击节点即可在侧边面板修改配置；每项设置都会标明修改后果（立即生效 / 需重启服务 / 需重建索引）。

| 节点 | 作用与默认行为 |
|------|----------------|
| **Zotero 文献库** | 镜像本地 Zotero 数据目录或 Web API（user ID + key），collection 树同步为集合树。 |
| **上传 / 分块** | PDF 清洗（`pymupdf4llm`）、扫描件 OCR 开关、默认集合。 |
| **向量化（Embedding）** | **默认使用线上 API**：需先在「模型供应商」添加 OpenAI 兼容连接，再在此选择连接与 Embedding 模型（如 `text-embedding-3-small`）。未选连接时 Embedding 不启用，检索退回 SQLite 关键词召回。本地模型为可选组件。 |
| **向量库** | **只使用内置的 Milvus Lite**（随包内置，无需另装，界面不再切换其它后端）。**没配好 Embedding 前**，向量库节点显示等待 Embedding，**检索自动退回 SQLite 关键词召回**；配好 Embedding 后自动建库并维护索引。 |
| **检索编排** | 自动将 Milvus Lite 向量召回与 SQLite 关键词召回做 RRF 融合。**Rerank 默认使用线上 API（`remote`）**：选择 OpenAI 兼容连接与模型，**兼容 Cohere / Jina / SiliconFlow / Voyage 的 `/rerank` 接口**（支持 `results` 与 `data` 两种响应格式）；未选连接时不重排；单次请求失败只对当次查询按序回退。 |
| **LightRAG 图谱** | 按集合构建图谱 workspace 并执行图谱检索（需安装「知识图谱」可选组件且 Embedding 已就绪）。 |
| **模型供应商** | 内置 OpenAI、Anthropic、DeepSeek、DashScope（通义）、Moonshot（Kimi）、智谱 GLM、SiliconFlow、OpenRouter、Ollama、LM Studio 预设及自定义 OpenAI 兼容端点；API Key 加密保存（仅显示后 4 位），支持测试连接与拉取模型列表。 |
| **模型分配** | 为**对话（chat）**、**研究（research）**、**图谱（graph）**三个角色分配模型；研究与图谱留空时跟随对话模型。 |
| **问答 Ask** | 设置回答语言（`auto` / `zh` / `en`）、自定义系统提示词与超时时间；点击「打开 Ask」进入对话。 |
| **Notion** | 经 Notion REST API 单向同步文章元数据与问答记录（集成 token 加密保存，详见 [`docs/NOTION_SYNC.md`](./docs/NOTION_SYNC.md)）。 |
| **R2 备份** | 将整个数据目录生成内容寻址快照并上传至 Cloudflare R2，可跨设备恢复（需安装「R2 备份」可选组件）。 |

> **API Key 修改提示**：仅修改已有连接的 API Key 不会热切换已初始化的线上 Embedding 或线上 Rerank，修改后请点击「重启服务」使其生效。

## 功能与六档检索模式

- **原件优先**：保留 PDF 原件、逐页抽取与清洗后的 clean markdown，脚注单独保存并可按需回读；支持集合树、标签、批量入库与重新清洗。
- **按证据作答与 Harvard 引用**：问答基于检索片段生成，正文短引与文末参考文献表由代码确定性渲染（杜绝模型编造作者与年份），点击短引即可跳回原件片段。
- **索引自动维护**：新文档入库或更换 Embedding 后，Milvus Lite 向量索引自动按需重建。

### 六档检索模式

| 模式 | 适用场景 | 成本 | 范围要求 |
|------|----------|------|----------|
| `default` | 查证单点事实，最快（保留 6 条证据） | ★ | 可全局 |
| `enhanced` | 分析、对比、机制类问题：一次拆解 + 宽召回 + 自检纠偏 | ★★ | 可全局 |
| `deep_thinking` | 综述、系统梳理级任务：多轮迭代推演 + 校验 | ★★★★ | 需绑定集合 |
| `graph_mixed` | 语义/词法证据 + LightRAG 图谱上下文混合 | ★★★ | 需绑定集合 |
| `graph_only` | 纯图谱检索，只用实体关系上下文 | ★★ | 需绑定集合 |
| `fulltext` | 单篇精读：整篇正文直接送入 LLM，不截断 | 随篇幅 | 需选定一篇文档 |

## 偏好设置与可选组件

通过菜单 **Preferences…**（macOS `⌘,`，Windows / Linux `Ctrl+,`）或顶栏按钮打开偏好设置：

- **通用（General）**：界面语言（默认英文 `English`，可切换 `中文`）、关闭窗口时最小化到托盘、登录时启动、自动检查更新。
- **外观（Appearance）**：明暗模式（默认深色，可选跟随系统 / 浅色）、主题配色、**界面缩放**（**80%–150%**，档位 `80% / 90% / 100% / 110% / 125% / 150%`，桌面端支持快捷键 `Ctrl`/`⌘` + `+` / `-` / `0`）。
- **存储（Storage）**：数据目录位置（更改后下次启动应用生效）、向量缓存上限（MB）。
- **本地模型（Local models）**：运行设备（`auto` / `cpu` / `cuda` / `mps`）、空闲卸载与加载超时、释放显存、管理已下载的模型缓存。
- **组件（Components）**：按需安装的可选组件（见下表）。
- **诊断（Diagnostics）**：日志级别与保留天数、运行环境信息、重启服务、打开日志目录。

### 可选组件

为控制安装包体积，以下依赖不随安装包分发，可在「偏好设置 › 组件」中经内置 `uv` 一键安装到数据目录的 `extras/`（不影响系统 Python，安装完成后重启服务生效）：

| 组件 | 依赖包 | 用途 |
|------|--------|------|
| **本地 Embedding 与 Rerank** | `sentence-transformers` + `torch` | 本地离线向量化与重排（`uv` 会按本机显卡自动选择 CPU 或 CUDA 版 `torch`） |
| **知识图谱（LightRAG）** | `lightrag-hku` | 图谱 workspace 构建与 `graph_mixed` / `graph_only` 检索模式 |
| **R2 备份** | `boto3` | Cloudflare R2 快照上传与恢复 |

若组件安装失败，界面会按原因归类说明（**磁盘空间不足**、**无写入权限**、**网络/代理错误**、**当前系统或 Python 版本无适用安装包**、**依赖版本冲突**、**源码编译失败**或**未知错误**），并附带可展开查看的原始报错摘录。

## 数据位置

| 平台 | 数据目录 | 日志目录 |
|------|----------|----------|
| macOS | `~/Library/Application Support/Knowledge Arch` | `~/Library/Logs/Knowledge Arch` |
| Windows | `%LOCALAPPDATA%\Knowledge Arch` | `%LOCALAPPDATA%\Knowledge Arch\Logs` |
| Linux | `~/.local/share/Knowledge Arch` | `~/.local/state/Knowledge Arch/log` |

在「偏好设置 › 存储」修改数据目录后，日志写入 `<数据目录>/logs`。数据目录包含 SQLite 数据库、原件库、向量与图谱索引、`settings.json`、加密保存的密钥（主密钥优先存入系统钥匙串 keyring，不可用时回退文件）以及已安装的 `extras/` 组件；复制整个目录即可迁移或备份。桌面壳偏好（数据目录覆盖、托盘、开机启动、检查更新）保存在 `<系统配置目录>/Knowledge Arch/shell.json`。

## 卸载与数据

卸载时不会删除数据，除非你主动选择：

- **Windows**：卸载器有「删除应用程序数据」勾选框（默认不勾）。勾选后删除 `%LOCALAPPDATA%\Knowledge Arch`、`%APPDATA%\Knowledge Arch`，以及你自选的数据目录（规则同下）。自动更新永远不会删除数据。
- **macOS / Linux**：删除应用（拖进废纸篓、`apt remove`、删掉 AppImage）会保留数据。需要清除时，卸载前使用「偏好设置 › 存储 › 删除全部数据…」。

「删除全部数据…」会删除数据、日志与缓存目录、桌面壳偏好以及系统钥匙串里的主密钥，然后退出应用。自选的数据目录里只删除 Knowledge Arch 自己的文件，其他文件保留（选定时为空的目录会整个删除）。两种方式都不会删除 HuggingFace 模型缓存（`~/.cache/huggingface`）、已安装到 Codex / Claude Code 的 skill，以及 R2 上的备份。

## 开发

```bash
# 1. 后端环境与测试
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/ruff check . && .venv/bin/mypy

# 2. 本地后端：token 只经环境变量传入；--port 0 = 系统分配
# 必须加 --dev-origin http://localhost:3000 放行浏览器开发页的写请求（POST / PATCH），否则会被 CORS 拒绝为 403
KA_TOKEN=dev .venv/bin/python -m ka_server serve --port 26618 --data-dir .dev_data \
  --dev-origin http://localhost:3000

# 3. 前端开发（next dev 把 /api/* 代理到 26618，token 取 NEXT_PUBLIC_KA_TOKEN）
cd web/frontend && npm ci && NEXT_PUBLIC_KA_TOKEN=dev npm run dev

# 4. 桌面壳（需 Rust stable；见 desktop/README.md）
cd desktop && npm ci && npx tauri dev
```

- 前端与后端 HTTP 说明：[`web/frontend/README.md`](./web/frontend/README.md)、[`web/README.md`](./web/README.md)。
- 桌面端打包与发布流程：[`desktop/README.md`](./desktop/README.md)、[`docs/RELEASE.md`](./docs/RELEASE.md)。
- 版本记录：[GitHub Releases](https://github.com/Gar1ton/Knowledge-Arch/releases)。

## 参与贡献

`main` 只放调试好的发布版本，开发在 [`developer`](https://github.com/Gar1ton/Knowledge-Arch/tree/developer) 分支进行：issue 与 PR 请提到 `developer`。架构说明（[`ARCHITECTURE.md`](https://github.com/Gar1ton/Knowledge-Arch/blob/developer/ARCHITECTURE.md)）、代码规范（[`CONVENTIONS.md`](https://github.com/Gar1ton/Knowledge-Arch/blob/developer/CONVENTIONS.md)）、路线图（[`TODO.md`](https://github.com/Gar1ton/Knowledge-Arch/blob/developer/TODO.md)）与完整变更记录也都在 `developer` 上。

## 许可证

本项目以 [AGPL-3.0](./LICENSE) 授权。若你在网络服务中分发或提供本项目的修改版本，须同样以 AGPL-3.0 公开对应的完整源码。

## 致谢

- [LightRAG](https://github.com/HKUDS/LightRAG)：图谱增强检索能力。
- [Milvus Lite](https://github.com/milvus-io/milvus-lite)：内置本地向量库。
- [Tauri](https://tauri.app/)：桌面壳；[uv](https://github.com/astral-sh/uv) 与 [python-build-standalone](https://github.com/astral-sh/python-build-standalone)：内置 Python 运行时与组件安装器。
- Zotero、Notion、Cloudflare R2：资料管理与同步备份生态。
