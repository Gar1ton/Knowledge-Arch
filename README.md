<div align="center">

<img src="./desktop/assets/logo.svg" width="96" alt="Knowledge Arch Logo" />

# Knowledge Arch

**Local-first academic knowledge base desktop app (macOS / Windows / Linux)**

*PDF Originals · Zotero Sync · 6-Mode Cited Q&A · Harvard Citations · LightRAG Graph · Notion / R2*

[![version](https://img.shields.io/badge/version-v2.0.1-blueviolet)](https://github.com/Gar1ton/Knowledge-Arch/releases)
[![license](https://img.shields.io/badge/license-AGPL--3.0-green)](LICENSE)
[![zh](https://img.shields.io/badge/中文-README__ZH.md-red)](README_ZH.md)

</div>

---

> **Origin**: Knowledge Arch is the standalone successor of the AstrBot plugin [**AstrBot KA v1.2.0**](https://github.com/Gar1ton/Astrbot_Knowledge_Repository/tree/8e6f1af). The whole implementation was carried over from that release and decoupled from the chat-bot host; plugin data can be imported on first launch.

Knowledge Arch manages research papers and sources around original files (such as PDFs): document ingestion and cleaning, collection trees and tags, Zotero mirroring, multi-mode retrieval Q&A with deterministic code-generated Harvard citations, LightRAG knowledge graphs, and Notion / Cloudflare R2 sync and backup. Everything runs in a standalone desktop app—your documents, indexes, and encrypted API keys stay on your machine, and only the model providers, Notion, and R2 endpoints you configure receive requests.

## Installation

Download the installer for your platform from [Releases](https://github.com/Gar1ton/Knowledge-Arch/releases):

| Platform | Installer | Notes |
|----------|-----------|-------|
| macOS 11+ (Apple Silicon / Intel) | `.dmg` | Drag into **Applications** |
| Windows 10/11 (x64) | NSIS `setup.exe` | Installs to the current user directory (no administrator privileges required; English / Simplified Chinese installer UI); downloads WebView2 automatically if missing |
| Linux (x64) | `.deb` / `.AppImage` | `.deb` depends on `libwebkit2gtk-4.1-0` and `libayatana-appindicator3-1` |

The installer bundles a standalone Python 3.12 runtime and all core dependencies—**no external Python or Node.js installation is required**. On unsigned builds, macOS requires allowing the app in **System Settings › Privacy & Security** on first launch, and Windows SmartScreen prompts **More info › Run anyway**.

## First Launch & Quick Start

1. **Local backend startup**: Launching the app starts the local backend service (listening only on `127.0.0.1` on an OS-assigned port, with a fresh 256-bit access token per launch) and opens the **Welcome** screen on an empty library.
2. **Choose the interface language**: 中文 or English; change it later in **Preferences › General**.
3. **Choose how to initialize your library**:
   - **Start empty**: Begin with a blank library.
   - **Import old plugin data**: Select the root directory of a v1 plugin installation (which contains `data/plugin_data/`). Knowledge Arch **copies** (never modifies or deletes) the SQLite database, original PDFs, vector index, graph workspaces, and secrets, rewrites absolute file paths, and migrates settings:
     - **Keeps local Embedding**: Imported legacy configurations keep **Local model** (`local`) as the Embedding source by default (unless the old config explicitly used an external Embedding API). Install the **Local embedding & rerank** component in **Preferences › Components**, or switch to an online Embedding API (which will rebuild the vector index).
     - **Switches to built-in Milvus Lite**: If the old config was set to SQLite keyword recall only, it is switched to the built-in Milvus Lite and listed under **Needs your attention** after import.
4. **Set up with an AI assistant (recommended)**: the last welcome step shows a prompt to paste into Codex or Claude Code.
   The assistant asks which model services you use and whether to connect Zotero / Notion / R2, then configures the whole data flow; the page shows the connection and each node's progress live.
   - Copying the prompt turns on **Preferences › General › Allow AI assistants to connect**: the app writes its local address and a per-launch token to a connection file only your user can read, and deletes it when access is turned off or the app quits.
   - The assistant can only use configuration, retrieval and Q&A endpoints and **cannot write any API key or token**: when a key is needed, the app shows a field and you type it into the app yourself; the assistant never sees it.
   - You can also install the skill for Codex / Claude Code with one click in Preferences, then simply ask it to "set up Knowledge Arch".
5. **Or configure models manually & start asking** (on the **Data Flow** page; choosing *Skip, I'll set it up myself* on the welcome page starts a step-by-step tutorial, which you can replay from Preferences › General):
   - Open the **Model providers** node and add at least one **OpenAI-compatible** connection (or Anthropic for chat/research/graph).
   - Open **Model assignment** to select your **Chat model** (and optionally separate **Research** and **Graph** models).
   - Open **Embedding** (and optionally **Retrieval orchestration** for Rerank) to select an OpenAI-compatible connection and model—or install the local component in **Preferences › Components**.
   - Switch to **Workspace**, upload PDFs or sync Zotero, and ask questions in the **Ask** panel.

If the backend fails to start or exits unexpectedly, the window switches to the **Service stopped** screen showing the last log lines, with buttons to **Restart service** or **Open log folder**.

## Language & Output Behavior

| Scope | Behavior |
|-------|----------|
| **UI language** | Defaults to **English**. Switchable to **Simplified Chinese** (`中文`) in **Preferences › General**. |
| **Terminal logs & system messages** | All backend terminal/file logs, desktop shell menus/tray/notifications, and backend diagnostic/error messages are in **English**. |
| **Answer language** | Configurable in the **Ask** node on the **Data Flow** page (`auto` / `zh` / `en`; `auto` follows the question language). Chinese answers are fully supported. Fallback messages and the `参考文献` / `References` heading follow the answer language. Because the current prompt system was designed around Chinese, all notices surfaced by deep thinking are in Chinese for now. |

## Configuration: Data Flow Page

The top bar switches between **Workspace** and **Data Flow**. The Data Flow page visualizes the processing pipeline as interactive nodes—click any node to edit its settings in the side panel. Every setting indicates whether changes take effect immediately, require **Restart service**, or require **Index rebuild**.

| Node | Purpose & Key Behavior |
|------|------------------------|
| **Zotero library** | Mirror a local Zotero data directory or Zotero Web API (user ID + key); collection trees mirror into Knowledge Arch collections. |
| **Upload / Chunk** | PDF cleaning (`pymupdf4llm`), optional OCR for scanned PDFs, default collection, and chunking. |
| **Embedding** | **Defaults to Online API**: select an OpenAI-compatible connection added in **Model providers** and an embedding model (e.g., `text-embedding-3-small`). Until a connection is selected, Embedding stays disabled and retrieval falls back to SQLite keyword recall. Local `sentence-transformers` is available after installing the optional component. |
| **Vector store** | Uses **only the built-in Milvus Lite** (bundled with the app; not user-switchable). Before Embedding is configured, the node waits for Embedding and **retrieval automatically falls back to SQLite keyword recall**. Once Embedding is ready, Milvus Lite indexes automatically. |
| **Retrieval orchestration** | Fuses Milvus Lite dense recall and SQLite lexical recall via RRF. **Rerank defaults to Online API (`remote`)**: select an OpenAI-compatible connection and model; compatible with **Cohere, Jina, SiliconFlow, and Voyage `/rerank`** endpoints (`results` and `data` response formats). Until a connection is selected, results are not reranked. If an online rerank call fails, only that query falls back to initial retrieval order. |
| **LightRAG graph** | Builds per-collection LightRAG workspaces and runs graph retrieval (requires the **Knowledge graph** optional component and a ready Embedding configuration). |
| **Model providers** | Presets for OpenAI, Anthropic, DeepSeek, DashScope (Qwen), Moonshot (Kimi), Zhipu GLM, SiliconFlow, OpenRouter, Ollama, LM Studio, plus custom OpenAI-compatible endpoints. API keys are stored encrypted (only the last 4 characters are shown); supports connection testing and fetching model lists. |
| **Model assignment** | Assign models to three roles: **Chat**, **Research** (`enhanced` / `deep_thinking`), and **Graph** (LightRAG). When **Research** or **Graph** is left empty, it follows the **Chat** model. |
| **Ask** | Answer language (`auto` / `zh` / `en`), custom system prompt, and timeouts. Click **Open Ask** to jump to Q&A. |
| **Notion** | One-way push of article metadata and Q&A records via the Notion REST API (encrypted integration token; see [`docs/NOTION_SYNC.md`](./docs/NOTION_SYNC.md)). |
| **R2 backup** | Content-addressed full snapshots of the data directory to Cloudflare R2 for cross-device restore (requires the **R2 backup** optional component). |

> **Note on API key updates**: Changing only the API key of an existing connection does not hot-reload an already-initialized online Embedding or online Rerank client; click **Restart service** for the new key to take effect.

## Features & Retrieval Modes

- **Originals first**: Preserves original PDFs, page-level extraction, and cleaned Markdown; footnotes are stored separately and read back on demand. Supports nested collections, tags, batch ingestion, and re-cleaning.
- **Evidence-grounded Ask**: Answers cite retrieved passages and render deterministic **Harvard in-text citations** and reference lists in code (never hallucinated by the LLM); click any short citation to jump to the source chunk.
- **Automatic index maintenance**: Adding documents or changing the embedding configuration schedules Milvus Lite index builds/rebuilds automatically.

### Six Retrieval Modes

| Mode | Best For | Cost | Scope Requirement |
|------|----------|------|-------------------|
| `default` | Fast fact lookup (keeps top 6 evidence chunks) | ★ | Global or scoped |
| `enhanced` | Analytical, comparative, or mechanism questions: query decomposition + wide recall + corrective self-check | ★★ | Global or scoped |
| `deep_thinking` | Literature review and synthesis: multi-round iterative reasoning + verification | ★★★★ | Requires a bound collection |
| `graph_mixed` | Semantic/lexical evidence combined with LightRAG knowledge graph context | ★★★ | Requires a bound collection |
| `graph_only` | Pure knowledge graph retrieval using entity-relationship context | ★★ | Requires a bound collection |
| `fulltext` | Single-paper close reading: sends the full cleaned document text to the LLM without truncation | Varies by length | Requires selecting 1 document |

## Preferences & Optional Components

Open **Preferences** via the application menu (**Preferences…**, macOS `⌘,`, Windows / Linux `Ctrl+,`) or the top-bar button:

- **General**: UI language (**English** default / **中文**), minimize to tray on close, launch at login, automatic update check.
- **Appearance**: Theme mode (dark by default; system or light also available), color palette, and **UI scale** (**80%–150%** in steps of `80% / 90% / 100% / 110% / 125% / 150%`, with shortcuts `Ctrl`/`⌘` + `+` / `-` / `0` in the desktop app).
- **Storage**: Data directory location (takes effect on next app launch) and embedding cache size limit (`MB`).
- **Local models**: Compute device (`auto` / `cpu` / `cuda` / `mps`), idle unload timeout, load timeout, VRAM release, and downloaded model cache management.
- **Components**: On-demand installation of large optional packages (see below).
- **Diagnostics**: Log level (`info` / `debug`), log retention days, runtime info, **Restart service**, and **Open log folder**.

### Optional Components

To keep the installer compact, large optional dependencies are installed on demand in **Preferences › Components** into `<data_dir>/extras/` using the bundled `uv` (isolated from system Python; requires restarting the service after installation):

| Component | Package(s) | Purpose |
|-----------|------------|---------|
| **Local embedding & rerank** | `sentence-transformers` + `torch` | Offline local embedding and cross-encoder reranking (`uv` automatically selects the CPU or CUDA PyTorch build for your hardware) |
| **Knowledge graph** | `lightrag-hku` | LightRAG workspace construction and `graph_mixed` / `graph_only` retrieval |
| **R2 backup** | `boto3` | Cloudflare R2 snapshot upload and restore |

If an installation fails, Knowledge Arch classifies the error by cause (**insufficient disk space**, **permission denied**, **network/proxy error**, **no compatible wheel for this OS/Python**, **dependency conflict**, **source build failure**, or **unknown**) and displays an actionable explanation along with expandable raw error lines.

## Data & Log Locations

| Platform | Data Directory | Log Directory |
|----------|----------------|---------------|
| macOS | `~/Library/Application Support/Knowledge Arch` | `~/Library/Logs/Knowledge Arch` |
| Windows | `%LOCALAPPDATA%\Knowledge Arch` | `%LOCALAPPDATA%\Knowledge Arch\Logs` |
| Linux | `~/.local/share/Knowledge Arch` | `~/.local/state/Knowledge Arch/log` |

If you change the data directory in **Preferences › Storage**, logs are written to `<data_dir>/logs`. The data directory contains the SQLite database, original PDFs, Milvus Lite and LightRAG indexes, `settings.json`, encrypted secrets (master key stored in the OS keyring when available, with file fallback), and installed `extras/` components—copying the directory backs up or migrates your library. Desktop shell preferences (data directory override, tray, autostart, update check) are stored in `<OS config dir>/Knowledge Arch/shell.json`.

## Uninstalling & Your Data

Uninstalling never deletes your data unless you ask for it:

- **Windows**: the uninstaller has a **Delete the application data** checkbox (off by default). When ticked, it removes `%LOCALAPPDATA%\Knowledge Arch`, `%APPDATA%\Knowledge Arch` and your own data folder if you chose one (same rule as below). Updates never delete data.
- **macOS / Linux**: removing the app (dragging it to the Trash, `apt remove`, deleting the AppImage) keeps your data. To remove it, use **Preferences › Storage › Delete all data…** before uninstalling.

**Delete all data…** removes the data, log and cache folders, the shell preferences and the master key in the OS keyring, then quits. If you chose your own data folder, only Knowledge Arch's files are removed from it; anything else stays (a folder that was empty when you picked it is removed entirely). Neither path touches the HuggingFace model cache (`~/.cache/huggingface`), skills installed into Codex / Claude Code, or backups on R2.

## Development

```bash
# 1. Backend environment & tests
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/ruff check . && .venv/bin/mypy

# 2. Local backend server (token passed via KA_TOKEN env var; --port 0 = OS-assigned)
# --dev-origin http://localhost:3000 is required so browser dev write requests (POST / PATCH)
# from next dev are allowed by CORS instead of being rejected with 403.
KA_TOKEN=dev .venv/bin/python -m ka_server serve --port 26618 --data-dir .dev_data \
  --dev-origin http://localhost:3000

# 3. Frontend dev server (next dev proxies /api/* to port 26618 using NEXT_PUBLIC_KA_TOKEN)
cd web/frontend && npm ci && NEXT_PUBLIC_KA_TOKEN=dev npm run dev

# 4. Desktop shell (requires Rust stable; see desktop/README.md)
cd desktop && npm ci && npx tauri dev
```

- Frontend & HTTP API docs: [`web/frontend/README.md`](./web/frontend/README.md), [`web/README.md`](./web/README.md).
- Desktop packaging & release process: [`desktop/README.md`](./desktop/README.md), [`docs/RELEASE.md`](./docs/RELEASE.md).
- Release notes: [GitHub Releases](https://github.com/Gar1ton/Knowledge-Arch/releases).

## Contributing

`main` only carries tested releases. Development happens on the [`developer`](https://github.com/Gar1ton/Knowledge-Arch/tree/developer) branch: open issues and pull requests against `developer`, where you will also find the architecture guide ([`ARCHITECTURE.md`](https://github.com/Gar1ton/Knowledge-Arch/blob/developer/ARCHITECTURE.md)), code conventions ([`CONVENTIONS.md`](https://github.com/Gar1ton/Knowledge-Arch/blob/developer/CONVENTIONS.md)), the roadmap ([`TODO.md`](https://github.com/Gar1ton/Knowledge-Arch/blob/developer/TODO.md)) and the full changelog.

## License

Licensed under [AGPL-3.0](./LICENSE). If you distribute or provide a modified version of this project over a network service, you must make the complete corresponding source code available under AGPL-3.0.

## Acknowledgments

- [LightRAG](https://github.com/HKUDS/LightRAG): graph-enhanced retrieval.
- [Milvus Lite](https://github.com/milvus-io/milvus-lite): embedded local vector database.
- [Tauri](https://tauri.app/): desktop shell; [uv](https://github.com/astral-sh/uv) and [python-build-standalone](https://github.com/astral-sh/python-build-standalone): bundled Python runtime and on-demand component installer.
- Zotero, Notion, and Cloudflare R2 ecosystems.
