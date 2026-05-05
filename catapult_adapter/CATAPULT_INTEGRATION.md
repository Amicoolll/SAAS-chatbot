# Catapult Integration — RAG Chatbot Tool (v1)

## What this is

A **submission-ready Catapult tool** that exposes the Enterprise Drive Chatbot's
retrieval-augmented Q&A as a reusable Catapult capability. Any project on the
Catapult platform that enables this tool can call:

```
catapult.tools.ragChatbot.ask({ question, collection_id, ... })
catapult.tools.ragChatbot.search({ query, collection_id, ... })
catapult.tools.ragChatbot.ingest({ collection_id, documents })
```

…and Catapult handles auth, rate limiting, PII scanning, audit, and approval
workflows around the call.

## Why a separate adapter (and not changes to the main app)

The Enterprise Drive Chatbot is a full product: Drive OAuth, multi-tenant sync
pipelines, web fallback, conversation history, admin endpoints, feature flags,
etc. Catapult expects a much narrower contract — three operations, no Drive,
no OAuth, stateless. Trying to bend `app/api/chat_pg.py` into Catapult's shape
would compromise both.

Instead, `catapult_adapter/` is a **separate FastAPI service** that lives in
the same repo but:

- **Imports from `app.services.*` one-way only.** Nothing under `app/` ever
  imports from `catapult_adapter/`. If the adapter is deleted tomorrow,
  `git diff app/` shows zero changes.
- **Runs as its own process** (`uvicorn catapult_adapter.service.main:app`)
  with its own FastAPI app, lifespan, and middleware. The existing
  `uvicorn app.main:app` is untouched.
- **Has its own Dockerfile.** Catapult deploys this image; the main app's
  deployment is unaffected.
- **Shares only the Postgres database** (the same `chunks` and `documents`
  tables). This is intentional — the adapter is reading the same KB. If full
  DB isolation is required later, we'd add a separate DB and a sync job;
  not in v1.

## Scope of v1

### In scope

| Catapult Operation | What it does | Reuses from `app/` |
|---|---|---|
| `ask` | Answer a question using KB chunks; falls back to web search via Tavily; falls back to plain LLM if neither has an answer | `greetingHandler`, `embed_texts`, `retrieve_hybrid_chunks`, `chat_with_context`, `chat_with_web_context`, `chat_without_context`, `search_web` |
| `search` | Return top-k retrieved chunks (no LLM call) | Same vector + hybrid retrieval |
| `ingest` | Accept raw text documents, chunk + embed, store under `(tenant_id, user_id, collection_id)` | `chunk_text`, `embed_texts`, `Document`, `Chunk` ORM |

### Out of scope (intentionally, for v1)

| Feature | Why excluded | Future plan |
|---|---|---|
| Drive OAuth + sync | Doesn't fit Catapult's model — Catapult expects documents to be pushed in via `ingest`, not pulled by the tool | Stays as a non-Catapult feature in the main app; main app pushes ingested chunks to the same DB |
| Conversation persistence | Catapult invocations are stateless; conversation history can be passed in the `ask` request body | If multi-turn state is needed, callers persist it themselves |
| Admin endpoints, feature flags | Catapult enforces governance; per-tenant flags become per-project tool config | Map relevant flags into `tool_config` schema if required |
| No-info detection / off-topic redirect | Requires careful prompt analysis; v1 keeps adapter pure | Port from `chat_pg.py` once we extract those helpers to a shared module |
| Greetings as a separate path | Already handled inside the `ask` operation | n/a |

### Identity mapping

Catapult sends headers; the adapter resolves them to the same `(tenant_id,
user_id)` tuple the existing tables use:

| Catapult header | Maps to |
|---|---|
| `x-catapult-tenant-id` | `tenant_id` (column on `chunks`, `documents`) |
| `x-catapult-user-id` | `user_id` (column on `chunks`, `documents`) |
| `x-catapult-request-id` | Logged for traceability |
| `x-catapult-trace-id` | Propagated to OpenAI/Tavily calls (via logs) |

The request body's `collection_id` is **also** the `user_id` for v1. That keeps
the existing schema unchanged. If the platform team wants a logical-collection
column, that's a v2 schema change.

## Manifest summary

- **Tool ID:** `rag-chatbot@1.0.0`
- **Risk tier:** `medium` — calls external APIs (OpenAI, Tavily), reads/writes
  vector store. No high-risk write operations.
- **Deployment mode:** `isolated-service` only (Python tool — embedded mode
  is Node.js-only per the submission guide §2.3).
- **Profiles supported:** `small`, `medium`, `large` (default `medium`).
- **Model capabilities:** `chat`, `embeddings`.
- **External providers:** OpenAI (chat + embeddings), Tavily (web search).
- **Infra:** PostgreSQL ≥15 with pgvector extension.
- **Egress allowed_domains:** `api.openai.com`, `api.tavily.com`.
- **Stores:** PostgreSQL chunk text + embeddings, document metadata. **No raw
  user PII in logs.**

## How developers will use it

```ts
import { Catapult } from "@catapult/sdk";
const catapult = new Catapult({ apiKey: process.env.CATAPULT_API_KEY });

// Index some documents
await catapult.tools.ragChatbot.ingest({
  collection_id: "hr-docs",
  documents: [
    { document_id: "policy-001", content: "Refund policy: ...", metadata: { source: "wiki" } },
  ],
});

// Ask a question against them
const { answer, sources, source_type } = await catapult.tools.ragChatbot.ask({
  question: "What is our refund policy?",
  collection_id: "hr-docs",
});
```

## Versioning strategy

This is **v1.0.0**. Future versions will be admitted to the Catapult library as
separate entries (`rag-chatbot@1.1.0`, `rag-chatbot@2.0.0`, etc.) without
disturbing existing consumers.

| Change | Version bump | Notes |
|---|---|---|
| Add a new operation, add an optional field to a request, add a field to a response | minor (1.0.0 → 1.1.0) | Backward compatible; existing callers continue to work |
| Bug fix, prompt tuning, internal refactor | patch (1.0.0 → 1.0.1) | No API change |
| Remove an operation, remove a field, change a field's type, tighten validation | major (1.0.0 → 2.0.0) | Breaking; old version stays available until consumers migrate |

### Planned roadmap (not in v1)

- **v1.1**: port no-info detection + off-topic redirect from `chat_pg.py`
  (after extracting them to a shared module).
- **v1.2**: expose agent-type selection (`general`, `aviation`, `medical`,
  etc.) via `tool_config`.
- **v1.3**: streaming responses (`streaming` capability) for `ask`.
- **v2.0**: dedicated `collection_id` column on `chunks` (decouple from
  `user_id` namespace).

## File layout

```
catapult_adapter/
├── CATAPULT_INTEGRATION.md          (this file)
├── README.md                        (developer quick-start)
├── Dockerfile                       (python:3.11-slim image, runs only the adapter)
├── manifest.yaml                    (Catapult tool manifest)
├── schemas/                         (8 JSON Schema files — req/res for each op + 2 configs)
├── service/
│   ├── main.py                      (FastAPI app + health endpoints)
│   ├── headers.py                   (x-catapult-* → identity)
│   ├── models.py                    (Pydantic request/response models)
│   └── operations/
│       ├── ask.py
│       ├── search.py
│       └── ingest.py
└── tests/                           (adapter-only tests; main app tests untouched)
```

## How to verify isolation

```bash
# 1) Adapter file count is purely additive — no edits to app/
git diff --name-only main..HEAD -- app/    # → empty (after the adapter PR)

# 2) Main app still runs unchanged
uvicorn app.main:app --reload                # main app, port 8000

# 3) Adapter runs as a separate service
uvicorn catapult_adapter.service.main:app --port 8080

# 4) All existing tests still pass
PYTHONPATH=. pytest tests/ --ignore=tests/test_hybrid_retrieval_integration.py

# 5) Adapter tests pass independently
PYTHONPATH=. pytest catapult_adapter/tests/
```

## Open questions for the Catapult team

1. **`x-catapult-user-id` semantics.** The submission guide describes it as
   "end-user identifier." Is this stable per end-user across requests, or
   ephemeral? Our `chunks.user_id` is the persistence boundary, so we need
   stability.
2. **Per-project DB credentials.** v1 uses the same `DATABASE_URL` as the main
   app. Is there a Catapult-recommended pattern for per-project DB isolation?
3. **Provider key delivery.** The guide says "provider keys passed via
   environment variables managed by the platform team." Confirm the variable
   names Catapult sets (we currently read `OPENAI_API_KEY` and
   `TAVILY_API_KEY`).

---

*Document version: 1.0.0 — initial submission*
