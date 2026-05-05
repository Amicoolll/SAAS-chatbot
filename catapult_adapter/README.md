# Catapult Adapter — RAG Chatbot Tool

This directory packages the Enterprise Drive Chatbot's RAG capabilities as a
**Catapult tool** (`rag-chatbot@1.0.0`). It is a separate service from the
main app — they share the same Postgres KB but run as independent processes.

For the full design rationale, scope, and roadmap, see
[CATAPULT_INTEGRATION.md](CATAPULT_INTEGRATION.md).

## Quick start (local)

Run the adapter alongside the main app:

```bash
# Main app (unchanged — port 8000)
PYTHONPATH=. uvicorn app.main:app --reload

# Adapter (port 8080)
PYTHONPATH=. uvicorn catapult_adapter.service.main:app --port 8080 --reload
```

Smoke-test the health endpoints:

```bash
curl http://localhost:8080/tools/rag-chatbot/health
curl http://localhost:8080/tools/rag-chatbot/health/ready
curl http://localhost:8080/tools/rag-chatbot/health/live
```

Call an operation directly (Catapult would normally route via SDK):

```bash
curl -X POST http://localhost:8080/tools/rag-chatbot/ask \
  -H "Content-Type: application/json" \
  -H "x-catapult-tenant-id: demo_tenant" \
  -H "x-catapult-user-id: demo_user" \
  -H "x-catapult-request-id: $(uuidgen)" \
  -d '{"question":"What is our refund policy?","collection_id":"demo_user"}'
```

## Tests

```bash
PYTHONPATH=. pytest catapult_adapter/tests/
```

The adapter tests stub OpenAI, Tavily, and the database — no network or DB
needed. They live under `catapult_adapter/tests/` and are intentionally
separate from the main app's `tests/` directory.

## Docker

```bash
# Build from the repo root (build context = repo root)
docker build -f catapult_adapter/Dockerfile -t rag-chatbot-catapult:1.0.0 .

# Run (provide the same env vars the main app uses)
docker run --rm -p 8080:8080 \
  -e DATABASE_URL=postgresql://... \
  -e OPENAI_API_KEY=sk-... \
  -e TAVILY_API_KEY=tvly-... \
  rag-chatbot-catapult:1.0.0
```

## File map

| File | Purpose |
|---|---|
| [manifest.yaml](manifest.yaml) | Catapult tool manifest (`catapult.tools/v1`) |
| [schemas/](schemas/) | 8 JSON Schemas (3 ops × req+res, plus 2 configs) |
| [service/main.py](service/main.py) | FastAPI app, routes, health endpoints, error handler |
| [service/headers.py](service/headers.py) | `x-catapult-*` → `(tenant_id, user_id)` resolver |
| [service/models.py](service/models.py) | Pydantic mirrors of the JSON schemas |
| [service/operations/ask.py](service/operations/ask.py) | Greeting → retrieve → LLM → web/LLM fallback |
| [service/operations/search.py](service/operations/search.py) | Vector or hybrid retrieval, no LLM |
| [service/operations/ingest.py](service/operations/ingest.py) | Chunk + embed + persist raw text |
| [tests/](tests/) | Adapter-only test suite (network-free) |
| [Dockerfile](Dockerfile) | python:3.11-slim image, gunicorn on 8080 |

## Isolation guarantees

The adapter directory is purely additive. Verify any time:

```bash
git diff main..HEAD -- app/   # → empty
```

Nothing under `app/` imports from `catapult_adapter/`. Deleting this directory
restores the main app to identical behaviour.
