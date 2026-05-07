# Aviation Tenant Onboarding Runbook

Use this when bringing up a new aviation customer on the Enterprise Drive
Chatbot. Three steps: enable the strict-domain flag, configure the frontend
to send the aviation agent, and ingest the customer's documents.

---

## Pre-requisites

| Requirement | Verify |
|---|---|
| Server reachable | `curl https://your-host/agents` returns the agent list |
| `ADMIN_TOKEN` env var set on server | Required to flip per-tenant flags |
| Postgres + pgvector reachable from the app | Existing main app already depends on this |
| Customer's Google Drive OAuth completed | Customer goes through the OAuth flow at `/drive/oauth/start` |

Pick a stable `tenant_id` for the customer (e.g. `aviation_corp`) — it's the partition key on every table; **don't change it later.**

---

## Step 1 — Enable `strict_domain` for the tenant

This is the **hard refusal** flag. Off-topic questions get rejected before retrieval or LLM calls run.

```bash
curl -X PUT https://your-host/admin/tenants/aviation_corp/features/strict_domain \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

Expected response:
```json
{"tenant_id":"aviation_corp","flag_name":"strict_domain","enabled":true,"check":true}
```

Verify with `GET /admin/tenants/aviation_corp/features` — the response should include `"strict_domain": true`.

This is a **one-time** action per tenant. The flag persists in the `tenant_feature_flags` table.

---

## Step 2 — Frontend configuration

The frontend must send `"agent_type": "aviation"` in the body of every `POST /chat_pg` call. Two patterns:

### Pattern A: per-tenant default (recommended for aviation-only customers)

The customer's frontend hard-codes `agent_type: "aviation"` for this tenant. End users never see a picker.

```ts
const body = {
  conversation_id: convId,
  question: userInput,
  agent_type: "aviation",   // ← always
};
fetch("/chat_pg", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-Tenant-Id": "aviation_corp",
    "X-User-Id": currentUser.id,
  },
  body: JSON.stringify(body),
});
```

### Pattern B: user-selectable agent

Fetch `GET /agents` once on app load, render a dropdown, and persist the user's choice. Send the selected `key` field as `agent_type`.

`GET /agents` returns:
```json
{"agents":[{"key":"aviation","name":"Aviation Support Assistant"}, ...]}
```

---

## Step 3 — Document ingestion

The customer uploads aviation FCOMs, QRHs, SOPs, MELs, maintenance manuals, etc. to their Google Drive. Then trigger a sync:

```bash
# 1) Drive sync — downloads files to data/user_<id>/raw/ and skips unchanged ones
curl -X POST https://your-host/drive/sync \
  -H "X-Tenant-Id: aviation_corp" \
  -H "X-User-Id: $USER_ID"

# 2) Index — chunks + embeds + writes to chunks table
curl -X POST https://your-host/index/run \
  -H "X-Tenant-Id: aviation_corp" \
  -H "X-User-Id: $USER_ID"

# 3) Poll until done
curl https://your-host/pipeline/status \
  -H "X-Tenant-Id: aviation_corp" \
  -H "X-User-Id: $USER_ID"
```

Both `/drive/sync` and `/index/run` are background jobs — they return immediately and progress is reported via `/pipeline/status`. Re-running them is idempotent (modified-time skip).

**Document hygiene matters.** The retrieval layer searches every chunk in the collection. Don't mix aviation docs with HR / general docs in the same `(tenant_id, user_id)` partition — the strict-domain refusal won't trigger for HR-style questions because they pass the aviation similarity check.

---

## Verification — three smoke tests

After Steps 1-3 are done:

```bash
HOST=https://your-host
TENANT=aviation_corp
USER=$USER_ID
HEADERS="-H X-Tenant-Id:$TENANT -H X-User-Id:$USER -H Content-Type:application/json"

# Test 1 — on-domain question should return mode=kb_grounded with sources
curl -X POST $HOST/chat_pg $HEADERS \
  -d '{"conversation_id":"smoke-1","question":"What is V1 speed for the 737-800?","agent_type":"aviation"}'

# Test 2 — off-domain question should return mode=refused_off_domain (no sources, no LLM call)
curl -X POST $HOST/chat_pg $HEADERS \
  -d '{"conversation_id":"smoke-2","question":"Give me a good lasagna recipe","agent_type":"aviation"}'

# Test 3 — greeting should return mode=conversational
curl -X POST $HOST/chat_pg $HEADERS \
  -d '{"conversation_id":"smoke-3","question":"hi","agent_type":"aviation"}'
```

| Test | Expected `mode` | Expected `source_type` |
|---|---|---|
| 1 (on-domain) | `kb_grounded` | `documents` |
| 2 (off-domain) | `refused_off_domain` | `none` |
| 3 (greeting) | `conversational` | `none` |

If all three look right, the tenant is live.

---

## Rollback / disable

To temporarily soften the bot (let the LLM redirect off-topic questions instead of refusing them at the door):

```bash
curl -X PUT https://your-host/admin/tenants/aviation_corp/features/strict_domain \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -d '{"enabled": false}'
```

The frontend can keep sending `agent_type: "aviation"` — that part still works without the flag. To fully back out, change frontend to send `agent_type: "general"`.

To fully remove the tenant: delete their rows from `chunks`, `documents`, `drive_oauth_tokens`, `tenant_feature_flags`, `pipeline_state` (filtered by `tenant_id = 'aviation_corp'`).

---

## Common issues

| Symptom | Cause | Fix |
|---|---|---|
| Off-topic questions still get answered (just with a redirect) | `strict_domain` flag is OFF for the tenant | Step 1 |
| All answers come back generic / non-aviation tone | Frontend isn't sending `agent_type: "aviation"` | Step 2 |
| `/chat_pg` returns 503 "Embedding service unavailable" | `OPENAI_API_KEY` missing or invalid | Check server env |
| `mode=refused_off_domain` on a real aviation question | Question is below the `DOMAIN_GUARD_THRESHOLD` cosine similarity (default 0.35) | Lower the threshold via env var, or add seed sentences to `app/services/domain_guard.py:_AVIATION_SEEDS` |
| `mode=llm_fallback` on a question that should be in docs | Either docs aren't indexed yet, or the chunk is below `RAG_DISTANCE_THRESHOLD` | Check `/pipeline/status`; lower `RAG_DISTANCE_THRESHOLD` |
| Admin endpoint returns 503 "ADMIN_TOKEN not configured" | `ADMIN_TOKEN` env var is empty on the server | Set it in the server's secrets |
| Admin endpoint returns 403 | `X-Admin-Token` header missing or wrong | Pass the right token |

---

## Reference

- Flag implementation: [app/services/feature_flags.py](app/services/feature_flags.py)
- Admin endpoint: [app/api/admin_features.py](app/api/admin_features.py)
- Domain guard (cosine similarity): [app/services/domain_guard.py](app/services/domain_guard.py)
- Aviation system prompt: [app/agents/prompts.py:361-383](app/agents/prompts.py#L361-L383)
- Chat orchestration (where strict_domain + agent_type both apply): [app/api/chat_pg.py](app/api/chat_pg.py)
- Agent discovery endpoint: [app/api/agents.py](app/api/agents.py)
