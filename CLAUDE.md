# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Rules (non-negotiable)

1. **No hallucination.** Before referencing any file, function, or line number — read it first. Never assume a function exists, a file path is correct, or a return type matches. If unsure, search or read before writing code.
2. **Reuse existing code.** Before creating new helpers, services, or patterns — check if one already exists. Extend existing constants, extension points, and module patterns rather than creating parallel systems. When adding greetings, extend `_NON_RETRIEVAL_EXACT_NORMALIZED` / `_SINGLE_HEAD_PATTERNS` — don't create a new greeting module. When adding LLM calls, mirror the existing `chat_with_context` shape in `openai_client.py`.
3. **Plan before implementing.** For any multi-file change, present a concrete plan with real file paths and line references. Wait for approval before writing code.
4. **All tests must pass before moving on.** Run `pytest tests/ --ignore=tests/test_hybrid_retrieval_integration.py` after every implementation step. Do not start the next feature with any failures.
5. **Hierarchy of changes.** Read the target files → present plan → get approval → implement → syntax check → run tests → report results. Never skip steps.

## What this is

Multi-tenant Google Drive RAG chatbot backend. Users connect Google Drive via OAuth, files are synced and indexed into pgvector, and questions are answered via hybrid retrieval (dense vectors + Postgres FTS) with OpenAI. Falls back to web search (Tavily) or plain LLM when documents don't have the answer.

## Environment (set up first)

Required in `.env`:
- `DATABASE_URL` — PostgreSQL connection string (must have pgvector extension)
- `OPENAI_API_KEY` — OpenAI API key for embeddings + chat
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` — Google OAuth for Drive

Optional:
- `TAVILY_API_KEY` — enables web search fallback (sign up at https://app.tavily.com/)
- `ADMIN_TOKEN` — protects `/admin/` feature flag endpoints
- `FRONTEND_URL` — OAuth callback redirect base URL
- `ENABLE_HYBRID_RAG` — enables hybrid vector + FTS retrieval (default: false)
- `WEB_SEARCH_GLOBAL_ENABLED` — master kill switch for web search (default: true)

Critical: `EMBED_DIM` (default 1536) must match `OPENAI_EMBEDDING_MODEL`. Changing the model without updating the dim causes a dimension mismatch error at index time.

## Commands

```bash
# Start dev server (dev only — --reload kills background jobs on any file save)
uvicorn app.main:app --reload

# Start for long-running syncs / indexing (no code reloading)
uvicorn app.main:app --workers 1

# Start with Docker (Postgres + backend)
docker-compose up

# Run unit tests (no DB or network needed — MUST pass before any PR)
pytest tests/ --ignore=tests/test_hybrid_retrieval_integration.py

# Run a single test file
pytest tests/test_greeting_handler.py -v

# Run integration tests (needs live Postgres with pgvector)
pytest tests/ -m integration

# Syntax check a file quickly
python -c "import ast, pathlib; ast.parse(pathlib.Path('app/api/chat_pg.py').read_text())"
```

## Architecture (priority order)

### 1. Chat flow (`POST /chat_pg`) — the core endpoint

```
Question → greetingHandler (skip RAG for greetings/acks/closers)
    → embed question (OpenAI text-embedding-3-small)
    → query understanding (rule-based: domain, intent, complexity — no LLM)
    → routing decision (vector-only vs hybrid)
    → retrieval:
        vector: pgvector cosine distance, top-k
        hybrid: vector + Postgres FTS + RRF fusion
    → if chunks found within threshold → chat_with_context (kb_grounded)
    → if no match → _fallback_answer:
        feature flag ON → Tavily web search → chat_with_web_context (web_grounded)
        feature flag OFF or no results → chat_without_context (llm_fallback)
```

Response modes: `kb_grounded` | `web_grounded` | `llm_fallback` | `conversational`

### 2. Drive sync + indexing pipeline — how documents get into the system

```
POST /drive/sync (background task):
    list Drive files → filter supported types → download to data/user_{id}/raw/
    skip unchanged files via .drive_manifest.json (compares modifiedTime)
    checkpoint manifest to disk every 50 files — a crash mid-sync resumes
    from the last checkpoint on the next /drive/sync call (no new endpoint)
    token refresh at start via refresh_and_persist_tokens()

POST /index/run (background task):
    read raw files (txt/csv/xlsx/pdf) → chunk_text → embed_texts (OpenAI batched)
    skip unchanged via Document.modified_time vs manifest
    commit per-file (Document + all its chunks in one transaction) —
    a crash mid-index leaves only completed files in DB; rerun finishes the rest
    store Document + Chunk rows in Postgres

Poll GET /pipeline/status for progress (drive_sync_progress_json, index_progress_json)
```

### 3. Multi-tenancy — affects every query and table

Every table has `tenant_id` + `user_id` columns. Identity comes from request headers `X-Tenant-Id` / `X-User-Id` (resolved in `app/core/deps.py`). Defaults to `demo_tenant` / `demo_user` when headers are absent. All data queries must filter by both.

### 4. Feature flags — per-tenant feature toggling

Per-tenant toggles stored in `tenant_feature_flags` table. Managed via `app/services/feature_flags.py` with 60s in-process TTL cache. Admin API at `/admin/tenants/{id}/features/{flag}` (requires `X-Admin-Token` header). Currently used for `web_search_fallback`.

Enable web search for a tenant:
```bash
curl -X PUT http://localhost:8000/admin/tenants/demo_tenant/features/web_search_fallback \
  -H "X-Admin-Token: your-token" -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

### 5. Agent system — domain-specific prompts

`app/agents/prompts.py` defines 17 agent types (general, medical, logistics, hr, legal, etc.). Each has a `system_prompt` + `output_format`. Selected via `agent_type` field on chat requests. `get_agent(type)` falls back to "general".

## Key modules (by importance)

| Priority | Module | Purpose |
|---|---|---|
| 1 | `app/api/chat_pg.py` | Main chat endpoint — orchestrates greeting detection, retrieval, LLM calls, web fallback |
| 2 | `app/services/drive/routes.py` | Drive sync with manifest-based skip-unchanged |
| 3 | `app/api/index.py` | Raw file → chunk → embed → pgvector indexing |
| 4 | `app/services/drive/client.py` | Google Drive service builder + token refresh (`DriveReconnectRequired`) |
| 5 | `app/services/rag/hybrid_retrieval.py` | Dense + FTS + RRF fusion retrieval |
| 6 | `app/services/rag/query_routing.py` | Decides hybrid vs vector-only per query |
| 7 | `app/services/greetingHandler.py` | Greeting/closer/ack detection and prefix stripping |
| 8 | `app/services/web_search.py` | Tavily API wrapper (stdlib urllib, no external dependency) |
| 9 | `app/services/feature_flags.py` | Per-tenant feature flag reads with TTL cache |
| 10 | `app/services/query_understanding/` | Rule-based domain/intent/complexity classifiers |
| 11 | `app/services/openai_client.py` | OpenAI embedding + chat helpers (chat_with_context, chat_with_web_context, etc.) |
| 12 | `app/services/pipeline_state.py` | Background job progress tracking via DB |

## Database

PostgreSQL + pgvector. No alembic — tables auto-created via `Base.metadata.create_all()` on startup. The `CREATE EXTENSION vector` runs on startup if `CREATE_PGVECTOR_EXTENSION=true` (skip on managed DBs where only admin can create extensions).

Key tables by priority:
1. `chunks` — text + `Vector(1536)` embedding (the core of RAG retrieval)
2. `documents` — file metadata (drive_file_id, modified_time, mime_type)
3. `conversations` + `messages` — chat history per user
4. `pipeline_state` — drive sync / index progress tracking
5. `drive_oauth_tokens` — Google OAuth tokens per (tenant, user)
6. `tenant_feature_flags` — per-tenant on/off toggles

## Testing conventions

- **All unit tests must pass before moving to next feature** — run full suite after every change.
- All unit tests avoid network and DB — use monkeypatch, MagicMock, fake DB classes.
- Integration tests (needing live Postgres) are marked `@pytest.mark.integration` and live in files named `*_integration.py`.
- Skip integration tests in CI: `--ignore=tests/test_hybrid_retrieval_integration.py` or `-m "not integration"`.
- Greeting tests use `@pytest.mark.parametrize` heavily — extend the lists when adding new patterns.
- Drive sync/index tests use `tmp_path` + `monkeypatch.chdir` for file isolation.
- See **Rules** section at top for plan-before-code, no-hallucination, and reusability requirements.
