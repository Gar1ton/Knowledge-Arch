<div align="center">

<img src="./logo.svg" width="96" alt="Knowledge Arch Logo" />

# Knowledge Arch

**本地优先的学术知识库桌面应用（macOS / Windows / Linux）**

*PDF 原件库 · Zotero 同步 · 六档检索问答 · Harvard 引用 · LightRAG 图谱 · Notion / R2*

</div>

---

Knowledge Arch 以 PDF 等原件为中心管理资料：上传与清洗、集合与标签、Zotero 镜像、
多档检索问答（答案自带由代码生成的 Harvard 引用）、LightRAG 知识图谱，以及 Notion / R2 同步备份。
安装一个桌面应用即可使用；资料、索引与密钥都保存在本机，只有你配置的模型供应商、Notion、R2
会收到对应的请求。

## 功能

- **原件优先**：保留 PDF 原件、逐页抽取与清洗后的 clean markdown，脚注单独保留并可回读；
  集合树、标签、批量入库与重新清洗。
- **Ask 页即对话入口**：在应用内提问，答案按证据生成，点击短引跳到来源片段；可设置自定义系统提示词。
- **六档检索模式**：从快速查证、整篇精读到多轮深度推演，按问题复杂度选择成本档位（见下表）。
- **Zotero 同步**：本地 Zotero 数据库与 Zotero Web API 两种模式，collection 树镜像为集合树。
- **LightRAG 图谱**：按集合构建 workspace，支持纯图谱与「证据 + 图谱上下文」混合检索。
- **多模型供应商**：OpenAI、Anthropic、DeepSeek、通义、Kimi、智谱、SiliconFlow、OpenRouter、
  Ollama、LM Studio 预设，以及任意 OpenAI 兼容端点；对话 / 研究 / 图谱三个角色可分别指定模型。
- **同步与备份**：Cloudflare R2 保存可跨设备恢复的完整快照；Notion 镜像文章元数据与问答记录。
- **索引自动维护**：新文档入库、更换 embedding 后，Milvus Lite 向量索引按需自动重建。

### 六档检索模式

| 模式 | 适用场景 | 成本 | 范围要求 |
|------|----------|------|----------|
| `default` | 查存 / 单点事实，最快（保留 6 条证据） | ★ | 可全局 |
| `enhanced` | 分析、对比、机制类问题：一次拆解 + 宽召回 + 自检纠偏 | ★★ | 可全局 |
| `deep_thinking` | 综述、系统梳理级任务：多轮迭代推演 + 校验 | ★★★★ | 需绑定集合 |
| `graph_mixed` | 语义/词法证据 + LightRAG 图谱上下文混合 | ★★★ | 需绑定集合 |
| `graph_only` | 纯图谱检索，只用实体关系上下文 | ★★ | 需绑定集合 |
| `fulltext` | 单篇精读：整篇正文直接送入 LLM，不截断 | 随篇幅 | 需选定一篇文档 |

## 安装

从 [Releases](https://github.com/Gar1ton/Knowledge-Arch/releases) 下载对应平台的安装包：

| 平台 | 安装包 | 说明 |
|------|--------|------|
| macOS 11+（Apple Silicon / Intel） | `.dmg` | 拖入「应用程序」 |
| Windows 10/11（x64） | `.msi` | 缺少 WebView2 时安装程序会自动下载 |
| Linux（x64） | `.deb` / `.AppImage` | `.deb` 依赖 `libwebkit2gtk-4.1-0`、`libayatana-appindicator3-1` |

安装包自带 Python 运行时与全部核心依赖，**不需要另装 Python 或 Node.js**。未签名的构建在 macOS
上首次打开需到「系统设置 › 隐私与安全性」放行，Windows 上 SmartScreen 会提示「仍要运行」。

## 首次启动

1. 应用启动本机后端（只监听 `127.0.0.1`、端口由系统分配、每次启动生成新的访问令牌），就绪后打开欢迎页。
2. 欢迎页二选一：
   - **导入旧版插件数据**：选择旧版程序的安装根目录（其中有 `data/plugin_data/`），应用会**复制**（不移动）数据库、原件库、向量索引、
     图谱 workspace 与密钥，改写文档路径并迁移配置；导入完成后列出需要你复核的项。
     若旧数据的 embedding 与当前配置不一致，会提示重建索引。
   - **从空白开始**。
3. 进入「数据流」页按节点完成配置（见下一节），然后在「工作台」上传 PDF 或同步 Zotero，开始提问。

后端若启动失败或意外退出，界面切到崩溃页：显示最后几行日志，可「重启服务」或「打开日志目录」。

## 配置：数据流页

主窗口顶部有「工作台」与「数据流」两页。数据流页把整条处理链画成节点图，点节点即在侧边面板编辑该环节的设置；
每次修改都会提示后果（立即生效 / 需重启服务 / 需重建索引）。

| 节点 | 作用 |
|------|------|
| Zotero 文献库 | 本地 Zotero 数据目录或 Web API（user id + key），同步范围 |
| 上传 / 分块 | 清洗与分块参数 |
| 向量化 | 远程 embedding 模型或本地 sentence-transformers，可选 rerank |
| 向量库 | Milvus Lite 状态、待重建计数 |
| 检索编排 | 默认检索模式与召回参数 |
| LightRAG 图谱 | 图谱 workspace 构建与检索 |
| 模型供应商 | 添加供应商、填 API Key（只显示后四位）、测试连接、拉取模型列表 |
| 模型分配 | 对话（chat）/ 研究（research）/ 图谱（graph）三个角色各用哪个模型；研究与图谱留空则跟随对话 |
| 问答 Ask | 自定义系统提示词等问答设置；「打开 Ask」进入对话 |
| Notion | 集成 token（加密保存，见 [`docs/NOTION_SYNC.md`](./docs/NOTION_SYNC.md)）与目标数据库，手动 / 自动同步 |
| R2 备份 | 账号、密钥、bucket；上传快照与从快照恢复 |

## 偏好设置

菜单「偏好设置…」（macOS `⌘,`，Windows / Linux `Ctrl+,`）或顶栏按钮打开：

- **通用**：界面语言、关闭窗口时最小化到托盘、开机启动、自动检查更新与手动检查。
- **外观**：浅色 / 深色 / 跟随系统，主题配色。
- **存储**：数据目录（可改到其他磁盘，下次启动生效）、embedding 缓存大小。
- **本地模型**：运行设备、空闲卸载与加载超时，释放显存，管理已下载的模型缓存。
- **组件**：按需安装的可选组件（见下节）。
- **诊断**：日志级别与保留天数、运行信息、重启服务、打开日志目录。

### 可选组件

为控制安装包体积，以下依赖不随包分发，在「偏好设置 › 组件」一键安装到数据目录的 `extras/`
（使用内置的 uv，不影响系统 Python）：

| 组件 | 用途 |
|------|------|
| 本地 embedding（torch + sentence-transformers） | 本地向量化与 rerank；按显卡自动选择 torch 版本 |
| LightRAG（`lightrag-hku`） | 图谱构建与图谱检索模式 |
| R2 备份（`boto3`） | Cloudflare R2 快照上传与恢复 |

## 数据位置

| 平台 | 数据 | 日志 |
|------|------|------|
| macOS | `~/Library/Application Support/Knowledge Arch` | `~/Library/Logs/Knowledge Arch` |
| Windows | `%LOCALAPPDATA%\Knowledge Arch` | `%LOCALAPPDATA%\Knowledge Arch\Logs` |
| Linux | `~/.local/share/Knowledge Arch` | `~/.local/state/Knowledge Arch/log` |

在偏好设置里改了数据目录后，日志写到 `<数据目录>/logs`。数据目录包含 SQLite 数据库、原件库、
向量与图谱索引、`settings.json`、加密的密钥和已安装的组件；整个目录拷走即可迁移或备份。
桌面壳自己的偏好（数据目录覆盖、托盘、开机启动、检查更新）在系统配置目录的 `Knowledge Arch/shell.json`。

## 开发

```bash
# 后端
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/ruff check . && .venv/bin/mypy

# 本地后端：token 只经环境变量传入；--port 0 = 系统分配
KA_TOKEN=dev .venv/bin/python -m ka_server serve --port 26618 --data-dir .dev_data

# 前端（next dev 把 /api/* 代理到 26618，token 取 NEXT_PUBLIC_KA_TOKEN）
cd web/frontend && npm ci && NEXT_PUBLIC_KA_TOKEN=dev npm run dev

# 桌面壳（需 Rust stable；见 desktop/README.md）
cd desktop && npm ci && npx tauri dev
```

- 架构与约定：[`ARCHITECTURE.md`](./ARCHITECTURE.md)、[`CONVENTIONS.md`](./CONVENTIONS.md)、
  [`docs/PROJECT_STRUCTURE.md`](./docs/PROJECT_STRUCTURE.md)。
- 前端开发：[`web/frontend/README.md`](./web/frontend/README.md)；后端 HTTP：[`web/README.md`](./web/README.md)。
- 本地出包与 sidecar 细节：[`desktop/README.md`](./desktop/README.md)；发布与签名：[`docs/RELEASE.md`](./docs/RELEASE.md)。
- 路线图与变更：[`TODO.md`](./TODO.md)、[`CHANGELOG.md`](./CHANGELOG.md)。

## 许可证

本项目以 [AGPL-3.0](./LICENSE) 授权。若你在网络服务中分发或提供本项目的修改版本，须同样以 AGPL-3.0
公开对应的完整源码。

## 致谢

- [LightRAG](https://github.com/HKUDS/LightRAG)：图谱增强检索能力。
- [Milvus Lite](https://github.com/milvus-io/milvus-lite)：本地向量库。
- [Tauri](https://tauri.app/)：桌面壳；[uv](https://github.com/astral-sh/uv) 与
  [python-build-standalone](https://github.com/astral-sh/python-build-standalone)：内置运行时与组件安装。
- Zotero、Notion、Cloudflare R2：资料管理与同步备份生态。
