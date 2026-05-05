# Catapult Adapter — Deployment Brief for DevOps

This is a **second** service that ships in the same repo as the main
Enterprise Drive Chatbot. It does **not** replace the main app — it runs
alongside it, sharing the same database and secrets, and exposes a
Catapult-compatible HTTP contract.

---

## TL;DR

| Setting | Value |
|---|---|
| **Image build** | `docker build -f catapult_adapter/Dockerfile -t rag-chatbot-catapult:1.0.0 .` |
| **Build context** | repository root (NOT `catapult_adapter/`) |
| **Listening port** | `8080` |
| **Process** | `gunicorn catapult_adapter.service.main:app -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:8080` (already in the Dockerfile `CMD`) |
| **Public URL needed** | An HTTPS endpoint, e.g. `catapult.your-domain.com` — value goes into Catapult's `tool_config.service_url` |
| **Resources to start** | 1 vCPU, 1 GiB RAM, 1 replica (min), 2 replicas (max) |

---

## 1. Build

```bash
# Run from the repo root — the build context must include both app/ and catapult_adapter/
docker build -f catapult_adapter/Dockerfile -t rag-chatbot-catapult:1.0.0 .
```

The image is `python:3.11-slim` based, ~250 MB. It copies `app/` and
`catapult_adapter/` only — nothing else from the repo is needed at runtime.

---

## 2. Environment variables

The adapter reads the same `app.core.config.settings` as the main app, so
the env-var surface is the same. Inject these at deploy time (Secrets
Manager, SSM Parameter Store, ECS task secrets, etc. — whichever the
existing chatbot uses).

### Required

| Variable | Notes |
|---|---|
| `DATABASE_URL` | **Same value as the main app.** Both services point at the same Postgres + pgvector instance and share the `chunks` / `documents` tables. |
| `OPENAI_API_KEY` | **Same value as the main app.** |

### Optional (defaults are sane)

| Variable | Default | Notes |
|---|---|---|
| `TAVILY_API_KEY` | unset | When set, web-search fallback runs in `/ask` — same behavior as the main app |
| `ENABLE_HYBRID_RAG` | `false` | Match whatever the main app uses |
| `RAG_DISTANCE_THRESHOLD` | `0.45` | Match whatever the main app uses |
| `EMBED_DIM` | `1536` | Must match the embedding model used to populate the DB |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Same as main app |
| `OPENAI_CHAT_MODEL` | `gpt-4o-mini` | Same as main app |
| `CREATE_PGVECTOR_EXTENSION` | `false` | Leave false — main app already created the extension |
| `LOG_LEVEL` | `INFO` | Standard Python logging level |

The adapter does **not** need `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_REDIRECT_URI`, `FRONTEND_URL`, `ADMIN_TOKEN`, or any of the Drive
sync vars — those are main-app only.

---

## 3. Health probes (for ALB target group / k8s probes / ECS health check)

| Endpoint | Use for | Expected when healthy |
|---|---|---|
| `GET /tools/rag-chatbot/health` | Overall status | `200 {"status":"ok","version":"1.0.0"}` |
| `GET /tools/rag-chatbot/health/ready` | **ALB / k8s readiness** | `200 {"ready":true}` (returns `503` if Postgres is unreachable) |
| `GET /tools/rag-chatbot/health/live` | **k8s liveness** | `200 {"alive":true}` |

All three are unauthenticated, return within 5 seconds, and do not run
expensive operations. The readiness probe issues a `SELECT 1` against the DB.

**Recommended probe config**:
- Initial delay: 20 s
- Interval: 30 s
- Timeout: 5 s
- Healthy threshold: 2
- Unhealthy threshold: 3

---

## 4. Network requirements

### Inbound

- TCP 8080 from the load balancer / Catapult Runtime only.
- HTTPS termination at the LB (the container speaks plain HTTP on 8080).

### Outbound (egress)

The adapter calls these external domains. They must be reachable from the
container's network namespace:

| Domain | Purpose |
|---|---|
| `api.openai.com` | Embeddings + chat completions |
| `api.tavily.com` | Web-search fallback (skipped if `TAVILY_API_KEY` is unset) |

These are the same domains the main app already calls — if the main app's
egress works, the adapter's egress works.

### To Postgres

The adapter needs reachability to whatever Postgres the main app uses:
private VPC IP, RDS endpoint, etc. If using AWS RDS, attach the same
security group / subnet as the main app.

---

## 5. Reverse proxy / ALB rule

### Option A — Subdomain (recommended)

Point `catapult.your-domain.com` at the adapter's target group on port 8080.
Simplest, no path rewriting, works cleanly with TLS.

### Option B — Path prefix

Route `/catapult/*` from the existing ALB to the adapter's target group,
**preserving the `/catapult/` prefix** … no, actually **stripping it** is
cleaner because the adapter expects paths starting with `/tools/rag-chatbot/`.

Example NGINX:

```nginx
location /catapult/ {
    proxy_pass http://catapult-adapter-upstream/;   # trailing slash strips /catapult/
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout                 60s;
}
```

Catapult would then call `https://your-domain.com/catapult/tools/rag-chatbot/ask`.
Make sure the `service_url` you give Catapult includes the prefix.

---

## 6. Smoke test after deploy

```bash
URL=https://catapult.your-domain.com   # or your-domain.com/catapult

# Health
curl -sf $URL/tools/rag-chatbot/health
curl -sf $URL/tools/rag-chatbot/health/ready
curl -sf $URL/tools/rag-chatbot/health/live

# End-to-end: ingest → ask
curl -sf -X POST $URL/tools/rag-chatbot/ingest \
  -H 'Content-Type: application/json' \
  -H 'x-catapult-tenant-id: smoke' \
  -H 'x-catapult-user-id: smoke' \
  -H 'x-catapult-request-id: smoke-001' \
  -d '{"collection_id":"smoke","documents":[{"document_id":"d1","content":"Refunds are processed within 5 business days."}]}'

curl -sf -X POST $URL/tools/rag-chatbot/ask \
  -H 'Content-Type: application/json' \
  -H 'x-catapult-tenant-id: smoke' \
  -H 'x-catapult-user-id: smoke' \
  -H 'x-catapult-request-id: smoke-002' \
  -d '{"question":"How long do refunds take?","collection_id":"smoke"}'
```

If the second call returns `"source_type": "documents"` with a sensible
answer, the adapter is end-to-end functional. Clean up the smoke test rows
afterward:

```sql
DELETE FROM chunks WHERE tenant_id='smoke';
DELETE FROM documents WHERE tenant_id='smoke';
```

---

## 7. Logging and observability

The adapter emits structured single-line logs to stdout. Every line
includes the Catapult request context, so platform-side log search can
correlate failures back to the originating SDK call:

```
INFO catapult_adapter.ask: ask_started collection=hr-docs agent=general top_k=5 history_turns=0 request_id=req-abc-123 trace_id=trace-xyz tenant=acme app=support-bot
INFO catapult_adapter.ask: ask_ok mode=kb_grounded chunks=2 latency_ms=187 request_id=req-abc-123 trace_id=trace-xyz tenant=acme app=support-bot
```

Ship stdout to whatever log aggregator the main app uses (CloudWatch,
Datadog, etc.) — no special configuration needed.

The adapter also propagates `X-Trace-Id` and `X-Request-Id` headers to
downstream OpenAI and Tavily calls (per Catapult submission guide §7.3),
so traces stitch end-to-end if you have OpenAI/Tavily request logging.

---

## 8. Resource sizing

| Replicas | When |
|---|---|
| 1 (warm) | Default. Catapult health probes need a constantly-warm endpoint. |
| 2 | If sustained `/ask` traffic exceeds ~5 RPS — each request hits OpenAI synchronously and can take 1–3 s. |
| Auto-scale on CPU > 60% | Reasonable for unknown traffic patterns. |

CPU and memory are not the bottleneck — the adapter spends most of its
time waiting on OpenAI. Scaling up replicas mostly helps with concurrency,
not throughput.

---

## 9. Rollout plan (suggested)

1. Build + push the image to your registry (ECR / GHCR / etc.).
2. Provision the new ECS service / k8s Deployment / equivalent, using
   **the same DB and same secrets** as the main app.
3. Add the ALB target group + listener rule (subdomain or path prefix).
4. Wait for the readiness probe to go green.
5. Run the smoke test from §6 with `curl`.
6. Hand the public URL to the Catapult platform team — that becomes
   `tool_config.service_url` in their bundle.
7. Catapult does its own integration test from the Runtime, then admits
   the tool to their library.

---

## 10. Rollback

The adapter is a separate service. **Rolling it back has zero impact on
the main chatbot.** Two paths:

- **Stop the adapter** — Catapult's calls 503; the main app keeps serving users.
- **Roll back to a previous adapter image** — point the service at an older
  tag; same DB, same secrets, no migration concerns.

There are **no database migrations** owned by the adapter. It writes to
the same `chunks` / `documents` tables the main app's `/index/run` already
uses, with the same schema. You can run the adapter and the main app
indexing path concurrently against the same DB without conflict.

---

## Open questions for the Catapult platform team

These belong in the conversation with their team, not in DevOps's deploy plan,
but knowing the answers may shape how you configure things:

1. Will Catapult call our adapter from the public internet, or do they need
   it inside their VPC (peering / PrivateLink)?
2. Do they expect us to manage `OPENAI_API_KEY` / `TAVILY_API_KEY`, or do
   they inject their own provider keys (per submission guide §15)?
3. Confirm `x-catapult-user-id` is stable per end-user — we use it as the
   `user_id` partition in the `chunks` / `documents` tables.

See [CATAPULT_INTEGRATION.md](CATAPULT_INTEGRATION.md) for full design context.
