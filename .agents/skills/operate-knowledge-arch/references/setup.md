# First-run setup

Goal: every core data-flow node is ready, and the optional nodes the user wants are configured, with no learning curve for the user. You ask, explain and propose; the user decides and types any keys into the app. Speak the user's language and keep each message short: one question or one proposed change at a time.

## 0. Look before asking

```
KA connection-status
KA setup-status
KA detect
```

`setup-status` lists the core stages (`models`, `embedding`, `vector_store`, `retrieval`, `ask`, `ingest`), the optional ones (`zotero`, `graph`, `notion`, `r2`), existing connections, model assignments and pending secret requests. `detect` probes only this computer: a Zotero data directory, Ollama on `127.0.0.1:11434`, LM Studio on `127.0.0.1:1234`, and the compute device. Do not probe anything else.

Summarise what is already done and what is missing before asking anything. If the user imported data from the old plugin, much may already be configured: keep it unless they want to change it.

## 1. Ask what the user has

In one short message, ask:

- which model services they use or want to use (OpenAI, Anthropic, DeepSeek, Qwen/DashScope, Kimi, Zhipu GLM, SiliconFlow, OpenRouter, a local Ollama / LM Studio, or another OpenAI-compatible endpoint);
- whether they use Zotero;
- whether they want the optional parts: knowledge graph (LightRAG), Notion sync, Cloudflare R2 backup.

Mention anything `detect` found ("I can see Ollama running with qwen3 and a Zotero library at ~/Zotero").

## 2. Model connections

```
KA providers
KA provider-add --preset deepseek
KA provider-add --preset deepseek --apply
```

Presets fill in the address and type. For a custom endpoint use `--label`, `--base-url` and `--kind openai_compatible|anthropic`. Connections are created without a key. If the preset needs one, follow **Secrets stay with the user** in SKILL.md:

```
KA request-secret --target provider:<id> --reason "chat model"
KA secret-status --request-id <request id> --wait
```

Then check it:

```
KA provider-test --id <id>
KA provider-models --id <id>
```

A failed test is usually a wrong key or address: report the message and offer to request the key again. Local Ollama / LM Studio connections need no key.

## 3. Assign models

Three roles, each a model reference `{"provider_id": "<id>", "model": "<model id>"}` chosen from `provider-models`:

| Key | Role | Advice |
|-----|------|--------|
| `flow.models.chat` | everyday answers | required; a fast, inexpensive model is fine |
| `flow.models.research` | `enhanced` / `deep_thinking` answers | optional; a stronger reasoning model. Empty follows chat |
| `flow.models.graph` | LightRAG extraction | optional; only with the knowledge graph. Empty follows chat |

```
KA settings-set flow.models.chat '{"provider_id":"ds","model":"deepseek-chat"}'
KA settings-set flow.models.chat '{"provider_id":"ds","model":"deepseek-chat"}' --apply
```

In PowerShell, quote JSON with single quotes as shown, or escape the double quotes.

## 4. Embedding and rerank

Embedding turns documents into vectors for retrieval. Without it, retrieval falls back to keyword search.

- **Online** (default): `flow.embedding.source` = `{"provider_id": "<OpenAI-compatible id>", "model": "<embedding model>"}`. Only OpenAI-compatible connections support it (Anthropic does not). Typical models: OpenAI `text-embedding-3-small`, SiliconFlow / DashScope multilingual embedding models, Ollama `nomic-embed-text` or `bge-m3`. Prefer a multilingual model when the library mixes Chinese and English.
- **Local**: `flow.embedding.source` = `"local"`; needs the `local_embedding` component (several hundred MB to a few GB, see step 6). Suggest it only when the user wants to stay offline.

Changing the embedding later re-indexes the library: say so before applying (`consequence: rebuild`). On a new library that costs nothing.

Rerank reorders retrieved passages. Default `flow.retrieval.rerank.provider` = `remote` needs `flow.retrieval.rerank.remote` = a model reference to a `/rerank` endpoint (Cohere, Jina, SiliconFlow, Voyage). Without a rerank model, results are simply not reranked; that is acceptable — say so and move on.

The vector store is the built-in Milvus Lite; nothing to configure.

## 5. Optional nodes

Only those the user asked for.

**Zotero** (`settings-show --home flow.zotero`):
- Local library: `flow.zotero.enabled` = `true`, `flow.zotero.access_mode` = `local`; if `detect` found a non-default data directory, set `flow.zotero.data_dir`.
- Zotero Web API: `access_mode` = `server`, then `request-secret --target setting:flow.zotero.server_api_key`.
- Explain `flow.zotero.storage_mode` (`managed_copy` copies PDFs into Knowledge Arch; `linked` / `zotmoov` read them in place) and keep the default unless the user has a reason.
- After it is ready, offer a first sync; it imports the user's library, so ask first.

**Knowledge graph**: needs the `lightrag` component and a ready embedding. Set `flow.graph.enabled` = `true`. Building graphs happens later per collection (see lightrag.md).

**Notion**: `flow.notion.enabled` = `true`, `request-secret --target setting:flow.notion.token` (an internal integration token from notion.so/my-integrations; the user must share the target page with the integration). `flow.notion.parent_page_id`, `database_id` and `qa_database_id` come from the user's Notion page URLs.

**R2 backup**: needs the `r2` component. Set `flow.r2.enabled`, `flow.r2.account_id`, `flow.r2.bucket`, then request `setting:flow.r2.access_key_id` and `setting:flow.r2.secret_access_key` (an R2 API token with object read/write on that bucket).

## 6. Components

```
KA component-install lightrag
KA component-install lightrag --apply
```

Keys: `local_embedding`, `lightrag`, `r2`. Each downloads packages into the Knowledge Arch data directory; tell the user the rough size and that the service restarts afterwards. Install only after they agree.

## 7. Restart if needed, then verify

Several settings report `consequence: restart`. Collect them and restart once at the end:

```
KA restart
KA restart --apply
```

Wait a few seconds, run `KA setup-status` again and confirm every core stage is `ready`. Finish with a short summary: what is configured, what was skipped and how to change it later (the Data Flow page in the app, or ask you again). Suggest a first step: upload a PDF or sync Zotero, then ask a question in the Ask panel.

## Never

- Never ask for, read, repeat or write an API key or token; always `request-secret`.
- Never apply a change the user has not confirmed; never batch unreviewed changes.
- Never edit Knowledge Arch's files, database or `settings.json` directly.
- Never install components or probe services the user has not agreed to.
