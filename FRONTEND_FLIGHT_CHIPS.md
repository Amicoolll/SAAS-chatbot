# Frontend Integration — Flight Status & Flight Search Chips

For the frontend team. Covers the **3 chips** powered by today's backend:

- **Flight status** — *"What's the live status of flight X on date Y?"*
- **Flights from city** — *"Show me flights leaving X"*
- **Flights to city** — *"Show me flights arriving at Y"*

The latter two share the same backend tool (`flight_search`); the
frontend disambiguates by which slot it pre-fills.

---

## 1. Common contract — every request

| | |
|---|---|
| **Endpoint** | `POST /chat_pg` |
| **Auth headers** | `X-Tenant-Id: <from your auth>`, `X-User-Id: <from your auth>` |
| **Content-Type** | `application/json` |
| **agent_type** | always `"aviation"` for these chips |
| **conversation_id** | from `POST /conversations` (call once per chat session) |

Request body shape (universal to both chips):

```json
{
  "conversation_id": "<uuid from POST /conversations>",
  "agent_type": "aviation",
  "action": "<tool name — see chip table below>",
  "action_params": { /* what's been collected so far */ },
  "user_input": "<raw text the user just typed>"
}
```

You only set ONE of `question` or `action`. For chips, always use `action`.
`user_input` is **optional but recommended** — when you send the user's raw
text instead of pre-filling `action_params` yourself, the backend uses an LLM
to extract values and supports any-order input like *"BOM 2026-06-01"*
or *"DEL to BOM tomorrow"* (modulo "tomorrow"/"next Monday" — those need
explicit dates today, see [§5.5](#55-known-limitations)).

### Universal response shape

```json
{
  "mode": "<see §1.1>",
  "answer": "string the user sees in a chat bubble",
  "missing_param": "<field name>",        // present when mode = action_collecting
  "param_schema": { ... },                // present when mode = action_collecting
  "action_state": {
    "action": "<tool name>",
    "collected": { ... },                  // params gathered so far — echo back next turn
    "complete": true | false
  },
  "tool_name": "<tool name>",              // present when mode = tool_executed / tool_error
  "tool_result": { ... },                  // present when mode = tool_executed
  "render_as": "flight_status_card" | "flight_results_card" | "text",
  "error_code": "<string>",                // present when mode = tool_error
  "error_status": 404,                     // present when mode = tool_error
  "source_type": "tool" | "none",
  "sources": []                            // empty for chip-action responses
}
```

### 1.1. The 3 `mode` values to handle

| `mode` | When | UI |
|---|---|---|
| `action_collecting` | Backend is asking for the next missing required param | Render `answer` as a chat bubble + show input box. Persist `action_state` in component state. |
| `tool_executed` | All required params collected, tool ran successfully | Render the rich card from `tool_result` according to `render_as`. Plus `answer` as a one-liner above/below the card. Drop `action_state`. |
| `tool_error` | Tool returned an error (e.g. flight not found) | Render `answer` as a chat bubble. Optionally show a "Try again" chip that resets `action_params`. Use `error_code` for telemetry. |

---

## 2. Chip A — Flight Status

| | |
|---|---|
| **Chip label** | "Flight status" |
| **Backend `action`** | `"flight_status"` |
| **Required fields** | `flight_number`, `date` |
| **Render card** | `render_as: "flight_status_card"` |

### 2.1. The conversational flow

```
User clicks "Flight status" chip
  ↓
Frontend POST /chat_pg
  body: { action: "flight_status", action_params: {} }
  ↓
Backend response:
  mode: "action_collecting"
  answer: "Sure — to check a flight's status, I'll need the flight number
           (e.g. AI101) and the departure date (YYYY-MM-DD). You can share
           them both at once or one at a time."
  ↓
Frontend renders answer + input box.
User types "AI101" (or "AI101 2026-06-01" — both work)
  ↓
Frontend POST /chat_pg
  body: {
    action: "flight_status",
    action_params: previousResponse.action_state.collected,  // {} so far
    user_input: "AI101"
  }
  ↓
Backend response:
  mode: "action_collecting"
  answer: "Thanks! Got the flight number (AI101). What's the departure date?
           (YYYY-MM-DD, e.g. 2026-06-01)"
  action_state.collected: { "flight_number": "AI101" }
  ↓
User types "2026-06-01"
  ↓
Frontend POST /chat_pg
  body: {
    action: "flight_status",
    action_params: { "flight_number": "AI101" },
    user_input: "2026-06-01"
  }
  ↓
Backend response:
  mode: "tool_executed"
  render_as: "flight_status_card"
  answer: "Flight AI101 — delayed by 25 min, gate B12."
  tool_result: { ... see §2.2 ... }
```

### 2.2. `tool_result` shape — flight_status_card

```ts
type FlightStatusCard = {
  flight_number: string;            // "AI101"
  date: string;                     // "2026-06-01"
  origin: string;                   // "DEL" (IATA)
  destination: string;              // "BOM" (IATA)
  scheduled_departure: string;      // ISO 8601 with offset
  scheduled_arrival: string;
  estimated_departure: string | null;
  estimated_arrival: string | null;
  actual_departure: string | null;
  actual_arrival: string | null;
  status:
    | "SCHEDULED" | "ON_TIME" | "DELAYED" | "BOARDING"
    | "DEPARTED" | "ARRIVED" | "CANCELLED" | "DIVERTED";
  delay_minutes: number;            // 0 if on time
  gate: string | null;
  terminal: string | null;
  aircraft_type: string | null;
};
```

**Suggested card layout:**

```
┌─────────────────────────────────────┐
│  AI101  •  2026-06-01    [DELAYED]  │
│  ─────────────────────────────────  │
│  DEL  ──→  BOM                      │
│  08:00     10:00     (scheduled)    │
│  08:25     10:30     (estimated)    │
│  ─────────────────────────────────  │
│  Gate B12  ·  Terminal T3  ·  A320  │
│  Delay: +25 min                     │
└─────────────────────────────────────┘
```

### 2.3. Error path — flight not found

Backend returns:
```json
{
  "mode": "tool_error",
  "error_code": "FLIGHT_NOT_FOUND",
  "error_status": 404,
  "answer": "We couldn't find that flight on the date you specified. Please double-check the flight number and date and try again.",
  "tool_name": "flight_status"
}
```

Render `answer` as a chat bubble. Optionally render a "Try again" chip that resets to empty `action_params`.

---

## 3. Chip B — Flights from city / Flights to city (same backend tool)

| | |
|---|---|
| **Chip labels** | "Flights from city" + "Flights to city" |
| **Backend `action`** | `"flight_search"` (same for both chips) |
| **Required fields** | `origin`, `destination`, `departure_date` |
| **Optional fields** | `return_date`, `cabin_class`, `passengers`, `currency`, `max_stops` |
| **Render card** | `render_as: "flight_results_card"` |

### 3.1. The chip → field-pre-fill mapping

The two chips share one tool. Frontend disambiguates by which slot it pre-fills when the user types the FIRST value:

| Chip clicked | First user input goes into… |
|---|---|
| **Flights from city** | `action_params.origin` |
| **Flights to city** | `action_params.destination` |

Example — user clicks **Flights from city** then types "BOM":

```json
POST /chat_pg
{
  "action": "flight_search",
  "action_params": { "origin": "BOM" }
}
```

Backend response:
```json
{
  "mode": "action_collecting",
  "missing_param": "destination",
  "answer": "Where are you flying to? (Airport code or city name like BOM or Mumbai)"
}
```

Same flow if user clicks **Flights to city** + types "BOM" — frontend puts BOM into `destination`, backend asks for origin.

### 3.2. Multi-field input in one message (preferred UX)

If the user types more than one value at once, send the raw text as `user_input` and let the backend extract:

```json
POST /chat_pg
{
  "action": "flight_search",
  "action_params": {},
  "user_input": "DEL to BOM 2026-06-01"
}
```

Backend extracts `origin=DEL`, `destination=BOM`, `departure_date=2026-06-01` in one LLM call → `mode: tool_executed` immediately.

This works for any phrasing the LLM can parse:

| User typed | Backend extracts |
|---|---|
| `"DEL to BOM 2026-06-01"` | origin=DEL, destination=BOM, departure_date=2026-06-01 |
| `"flights from delhi to mumbai on 2026-06-01"` | origin=DEL, destination=BOM, departure_date=2026-06-01 (city names → IATA) |
| `"DEL BOM 2026-06-01 returning 2026-06-08"` | + return_date=2026-06-08 → round-trip |
| `"BOM"` (alone, with chip context) | depends on chip — pre-fill manually |

### 3.3. Round-trip support — optional `return_date`

The bot **never asks** for `return_date` (it's optional), but if the user mentions a return date OR the frontend pre-fills it from a date-picker widget, the search runs as round-trip and `tool_result.results[*].return_segments` is populated.

**Two ways to trigger round-trip:**

```json
// Option 1: user types it
{
  "action": "flight_search",
  "action_params": {},
  "user_input": "DEL BOM 2026-06-01 returning 2026-06-08"
}

// Option 2: form widget pre-fills it
{
  "action": "flight_search",
  "action_params": {
    "origin": "DEL",
    "destination": "BOM",
    "departure_date": "2026-06-01",
    "return_date": "2026-06-08"
  }
}
```

### 3.4. `tool_result` shape — flight_results_card

```ts
type FlightSearchResults = {
  currency: string;                 // "INR"
  total_results: number;            // count of options
  next_cursor: string | null;       // pagination token (always null in v1)
  results: FlightResult[];
};

type FlightResult = {
  result_id: string;                // opaque token; use later for booking
  outbound_segments: FlightSegment[];
  return_segments: FlightSegment[]; // empty for one-way
  fare: {
    base_amount: number;
    taxes_amount: number;
    total_amount: number;           // for round-trip, sum of both legs
    currency: string;
    fare_type: "SAVER" | "FLEXI" | "PREMIUM" | "BUSINESS_SAVER" | "BUSINESS_FLEXI";
    refundable: boolean;
    changes_allowed: boolean;
  };
  baggage_allowance: {
    cabin_kg: number;
    checked_kg: number;
  };
  seats_remaining: number | null;
};

type FlightSegment = {
  flight_number: string;            // "AI101"
  origin: string;                   // "DEL"
  destination: string;              // "BOM"
  departure_time: string;           // ISO 8601 with offset
  arrival_time: string;
  duration_minutes: number;
  stops: number;
  aircraft_type: string | null;
  cabin_class: "ECONOMY" | "PREMIUM_ECONOMY" | "BUSINESS" | "FIRST";
  fare_basis: string;
};
```

**Suggested card layout (one card per result):**

```
┌─────────────────────────────────────────────────┐
│  AI101   DEL → BOM                              │
│  08:00 → 10:15  (2h 15m, nonstop, A320)         │
│  ────────────                                   │
│  AI104   BOM → DEL          ← only for round    │
│  19:00 → 21:15  (2h 15m, nonstop, A320)            trip
│  ─────────────────────────────────────────────  │
│  ₹10,350  •  Saver, non-refundable, changes OK  │
│  Baggage: 7kg cabin / 15kg checked              │
│  4 seats left                                   │
│  [ Select ]                                     │
└─────────────────────────────────────────────────┘
```

For one-way, hide the second segment block.

### 3.5. Error path — no flights found

```json
{
  "mode": "tool_error",
  "error_code": "NO_FLIGHTS_FOUND",
  "error_status": 404,
  "answer": "We couldn't find any flights for that route on that date. Try a different date or check the airport codes.",
  "tool_name": "flight_search"
}
```

---

## 4. Code samples

### 4.1. TypeScript: chip click handler

```ts
type ChipKey = "flight_status" | "flight_search_from" | "flight_search_to";

async function onChipClick(chip: ChipKey, conversationId: string) {
  const action =
    chip === "flight_status" ? "flight_status" : "flight_search";

  const body = {
    conversation_id: conversationId,
    agent_type: "aviation",
    action,
    action_params: {},
  };

  const response = await fetch("/chat_pg", {
    method: "POST",
    headers: {
      "X-Tenant-Id": tenantId,
      "X-User-Id": userId,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  return handleChipResponse(await response.json(), chip);
}
```

### 4.2. TypeScript: subsequent turns (user types text)

```ts
type ActionState = {
  action: string;
  collected: Record<string, unknown>;
  complete: boolean;
};

async function onUserMessage(
  text: string,
  state: ActionState,
  conversationId: string,
  chipKey: ChipKey,
) {
  // For "Flights from city" chip on the very first turn, pre-fill origin
  // with the user's typed value (so they can just type "BOM" and have it
  // mean origin, not destination).
  let actionParams = state.collected;
  if (
    chipKey === "flight_search_from" &&
    Object.keys(state.collected).length === 0
  ) {
    actionParams = { origin: text.toUpperCase() };
  } else if (
    chipKey === "flight_search_to" &&
    Object.keys(state.collected).length === 0
  ) {
    actionParams = { destination: text.toUpperCase() };
  }

  const body = {
    conversation_id: conversationId,
    agent_type: "aviation",
    action: state.action,
    action_params: actionParams,
    user_input: text, // raw text — backend extracts when needed
  };

  const response = await fetch("/chat_pg", {
    method: "POST",
    headers: {
      "X-Tenant-Id": tenantId,
      "X-User-Id": userId,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  return handleChipResponse(await response.json(), chipKey);
}
```

### 4.3. TypeScript: response router

```ts
function handleChipResponse(resp: any, chipKey: ChipKey) {
  switch (resp.mode) {
    case "action_collecting":
      renderBubble(resp.answer);
      showInputBox();
      saveState(resp.action_state);
      return;

    case "tool_executed":
      // Render the rich card based on render_as
      switch (resp.render_as) {
        case "flight_status_card":
          renderFlightStatusCard(resp.tool_result);
          break;
        case "flight_results_card":
          renderFlightResultsCard(resp.tool_result);
          break;
        default:
          renderBubble(resp.answer);
      }
      clearActionState();
      return;

    case "tool_error":
      renderBubble(resp.answer);
      showRetryChip(chipKey);
      logError(resp.error_code);
      return;
  }
}
```

### 4.4. curl — quick test

```bash
URL=http://localhost:8001
H=(-H 'X-Tenant-Id: demo' -H 'X-User-Id: demo' -H 'Content-Type: application/json')

# Create conversation
CONV=$(curl -sf -X POST $URL/conversations "${H[@]}" -d '{}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['conversation_id'])")

# Flight status — single message with both fields
curl -sf -X POST $URL/chat_pg "${H[@]}" -d "{
  \"conversation_id\": \"$CONV\",
  \"agent_type\": \"aviation\",
  \"action\": \"flight_status\",
  \"action_params\": {},
  \"user_input\": \"AI101 on 2026-06-01\"
}" | python -m json.tool

# Flight search — round-trip
curl -sf -X POST $URL/chat_pg "${H[@]}" -d "{
  \"conversation_id\": \"$CONV\",
  \"agent_type\": \"aviation\",
  \"action\": \"flight_search\",
  \"action_params\": {},
  \"user_input\": \"DEL BOM 2026-06-01 returning 2026-06-08\"
}" | python -m json.tool
```

---

## 5. Common patterns

### 5.1. Conversation state ownership

The frontend owns the `action_state` between turns. Backend is stateless — it only sees what's in the current request body.

On every turn after the chip click:
1. Take `action_state` from the previous response
2. Send `action: state.action`
3. Send `action_params: state.collected`
4. Add `user_input: <text the user just typed>`

When `complete: true` arrives, drop the state and return to the chip view.

### 5.2. When to send `user_input` vs pre-fill `action_params`

| Source of value | Use |
|---|---|
| User typed in chat box (free text) | `user_input` |
| User picked a date from a date picker widget | `action_params: { departure_date: "..." }` |
| User picked a city from a dropdown | `action_params: { origin: "..." }` |
| Mix of both | Both — `action_params` wins for fields it specifies, `user_input` fills others |

### 5.3. Smart input examples

These all work today (LLM extraction in the backend):

| User typed | What gets extracted |
|---|---|
| `"BOM"` (with from-city chip context, frontend pre-fills) | origin=BOM |
| `"DEL to BOM"` | origin=DEL, destination=BOM |
| `"flights from delhi to mumbai on 2026-06-01"` | origin=DEL, destination=BOM, departure_date=2026-06-01 |
| `"DEL BOM 2026-06-01 returning 2026-06-08"` | all 4 fields incl. round-trip |
| `"my pnr is ABC123"` (for retrieve_booking chip) | booking_reference=ABC123 |

### 5.4. Validation failure UX

If the user types something that doesn't match any field's schema (e.g. random text), the LLM extractor returns `{}` and the backend re-prompts with a hint:

```json
{
  "mode": "action_collecting",
  "missing_param": "origin",
  "answer": "That doesn't look like a valid origin. Where are you flying from? (Airport code or city name like DEL or Delhi)"
}
```

Render normally. The user sees the hint and tries again.

### 5.5. Known limitations

| Not supported today | Workaround |
|---|---|
| Relative dates ("tomorrow", "next Monday") | User must type ISO format (`2026-06-01`). Backend can be extended to know `today` in a follow-up commit. |
| Only domestic Indian airports seeded | Mock has DEL, BOM, BLR. Real airline integration will return whatever its inventory has. |
| Flight status caching (60s recommended by spec) | Not implemented yet. If users spam the chip, every click hits the airline API. |

---

## 6. Quick reference card

Print this on a sticky note:

| Action you want | What to send |
|---|---|
| Show flight status flow | `action: "flight_status", action_params: {}` |
| Show "flights from X" flow | `action: "flight_search", action_params: { origin: "<X>" }` |
| Show "flights to Y" flow | `action: "flight_search", action_params: { destination: "<Y>" }` |
| Continue collecting (any chip) | `action_params: state.collected, user_input: <text>` |
| Form widget pre-fill | `action_params: { field1: val1, field2: val2 }, no user_input` |

Mode you got back → what to render:

| `mode` | Render |
|---|---|
| `action_collecting` | Chat bubble + input box, save `action_state` |
| `tool_executed` | Rich card per `render_as`, drop `action_state` |
| `tool_error` | Chat bubble + "Try again" chip, log `error_code` |

---

## Need help?

- The full backend swagger is at `http://<your-host>/docs`
- Backend OpenAPI JSON is at `/openapi.json` for codegen
- All Pydantic shapes used here live in
  [`app/domains/aviation/models.py`](app/domains/aviation/models.py) — that's the
  source of truth if anything in this doc looks wrong.
