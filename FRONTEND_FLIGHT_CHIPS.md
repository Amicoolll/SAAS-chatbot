# Frontend Integration — Aviation Chips

For the frontend team. Covers the **5 chips** powered by today's backend:

- **Flight status** — *"What's the live status of flight X on date Y?"*
- **Flights from city** — *"Show me flights leaving X"*
- **Flights to city** — *"Show me flights arriving at Y"*
- **Web check-in** — *"Check me in for my flight"* (write op + barcode)
- **Boarding pass** — *"Show me my boarding pass"* (read after check-in)

The Flight-from / Flight-to chips share the same backend tool
(`flight_search`); the frontend disambiguates by which slot it
pre-fills. Web check-in and Boarding pass both use the **two-step
pattern** — retrieve the booking first, then dispatch with the user's
selections from a widget.

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

## 3A. Chip C — Web Check-in (write workflow)

| | |
|---|---|
| **Chip label** | "Web check-in" |
| **Backend `action`** | `"web_checkin"` |
| **Pattern** | **Two-step: retrieve_booking → web_checkin** |
| **Render card** | `render_as: "checkin_card"` |
| **Type** | **Write operation — requires Idempotency-Key** |

This chip is structurally different from chips A and B. The user can't pick passengers/segments without seeing them first, so the frontend chains two backend calls and renders a check-in widget in between.

### 3A.1. The two-step flow (UI sequence)

```
1. User clicks "Web check-in" chip
       ↓
2. Frontend triggers retrieve_booking via the existing chip-A pattern:
   asks the user for PNR + last name conversationally
       ↓
3. Backend returns mode: "tool_executed", render_as: "booking_card"
   with the full booking JSON (passengers, segments)
       ↓
4. Frontend renders booking_card PLUS a check-in widget:
     ☑ John Doe (p1)
     ☑ Jane Doe (p2)
     ▾ Flight: AI101 DEL→BOM (s1)
     ☑ I accept the airline's terms of service
     [ Check in ]
       ↓
5. User picks + clicks Check in
       ↓
6. Frontend generates idempotency_key (UUID v4) and POSTs:
     action: "web_checkin",
     action_params: {
       booking_reference, last_name,                    ← from step 2
       passenger_ids, segment_ids,                       ← from widget
       accept_terms,                                     ← widget checkbox
       idempotency_key                                   ← frontend-generated UUID
     }
       ↓
7. Backend dispatches POST /v1/checkin with Idempotency-Key header
       ↓
8. Frontend renders checkin_card with seats + boarding-pass barcodes
```

### 3A.2. Request body (the dispatch step)

```json
POST /chat_pg
{
  "conversation_id": "<uuid>",
  "agent_type": "aviation",
  "action": "web_checkin",
  "action_params": {
    "booking_reference": "ABC123",
    "last_name": "DOE",
    "passenger_ids": ["p1", "p2"],
    "segment_ids": ["s1"],
    "accept_terms": true,
    "idempotency_key": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

**All six fields are REQUIRED.** Backend won't ask for any conversationally
in production — they're all pre-filled by the frontend widget. If the
frontend forgets a field, the backend will helpfully prompt for it (the
`idempotency_key` and `passenger_ids` prompts contain the literal text
*"[Frontend bug...]"* so a developer notices on the first manual test).

### 3A.3. Idempotency-key generation (critical)

The frontend MUST generate a fresh UUID v4 per check-in attempt:

```ts
import { v4 as uuidv4 } from "uuid";

function startCheckin(booking) {
  const idempotencyKey = uuidv4();          // ← per attempt, store in component state
  // ... show widget, collect selections ...
}

async function submitCheckin(booking, selections, idempotencyKey) {
  const body = {
    conversation_id: convId,
    agent_type: "aviation",
    action: "web_checkin",
    action_params: {
      booking_reference: booking.booking_reference,
      last_name: lastNameFromCheckinFlow,    // remember from retrieve_booking
      passenger_ids: selections.passengers,
      segment_ids: selections.segments,
      accept_terms: selections.accepted,
      idempotency_key: idempotencyKey,       // SAME key on retry
    },
  };
  return fetch("/chat_pg", { method: "POST", body: JSON.stringify(body), headers });
}
```

**Why this matters:** if the user's network drops mid-request, retrying with the SAME key returns the cached response (no double check-in). Generating a fresh key on retry would create two check-ins.

Rule of thumb: **one UUID per Submit click**. Cache it in component state until the response arrives or the user explicitly cancels.

### 3A.4. `tool_result` shape — checkin_card

```ts
type CheckinCard = {
  checkin_id: string;                       // "ci_abc123_2"
  segment_status: "CHECKED_IN" | "PARTIALLY_CHECKED_IN";
  checked_in: CheckedInPassenger[];
  warnings: { code: string; message: string }[];  // e.g. meal not available
};

type CheckedInPassenger = {
  passenger_id: string;                     // "p1" — match against booking.passengers
  segment_id: string;                       // "s1"
  seat: string;                             // "12A"
  boarding_pass_url: string;                // hand to user OR fetch via slice 7 endpoint
  boarding_pass: BoardingPassInfo | null;
};

type BoardingPassInfo = {
  barcode: string;                          // IATA BCBP M1 format string
  seat: string;
  boarding_group: string;                   // "1"
  boarding_time: string;                    // ISO 8601 with offset
  gate: string | null;
};
```

### 3A.5. Suggested card layout

```
┌──────────────────────────────────────────────────┐
│  ✓ Checked in — 2 passengers on AI101            │
│  ──────────────────────────────────────────────  │
│  ┌────────────────────────────────────────────┐  │
│  │  JOHN DOE                       Seat 12A   │  │
│  │  AI101  DEL → BOM  ·  Group 1  ·  Gate B12 │  │
│  │  ┌──────────────────┐                      │  │
│  │  │ ▮▮▮▮ ▮ ▮ ▮▮ ▮▮▮ │  Boarding 07:30      │  │
│  │  └──────────────────┘                      │  │
│  └────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────┐  │
│  │  JANE DOE                       Seat 12B   │  │
│  │  ...                                       │  │
│  └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

### 3A.6. Rendering the IATA barcode

`boarding_pass.barcode` is a raw [IATA BCBP M1](https://www.iata.org/contentassets/1dccc9ed041b4f3bbdcf8ee8682e75c4/2021_03_02-bcbp-implementation-guide-version-7-.pdf) string. To render as an actual scannable barcode, encode it as **PDF417**:

```bash
npm install bwip-js                              # any PDF417 lib will do
```

```ts
import bwipjs from "bwip-js";

function renderBoardingBarcode(canvas: HTMLCanvasElement, barcodeData: string) {
  bwipjs.toCanvas(canvas, {
    bcid: "pdf417",
    text: barcodeData,
    scale: 3,
    height: 10,
    includetext: false,
  });
}
```

If you don't need a scannable barcode for the demo, just show the raw string in a monospace block — gate agents can read M1 strings.

### 3A.7. Error paths — friendly copy + structured codes

The backend always returns a friendly `answer` string AND a stable `error_code`. Use the answer for display, the code for telemetry / branching.

| `error_code` | `error_status` | When | Suggested UX |
|---|---|---|---|
| `BOOKING_NOT_FOUND` | 404 | Wrong PNR | Reset to retrieve_booking step |
| `BOOKING_VERIFICATION_FAILED` | 403 | Wrong last name | Reset to retrieve_booking step |
| `CHECKIN_NOT_OPEN` | 409 | Too far before departure (open at -48h) | Show `details.opens_at` countdown if present |
| `CHECKIN_CLOSED` | 409 | Too close to departure | Tell user to go to airport |
| `ALREADY_CHECKED_IN` | 409 | Selected passengers already checked in | Show a "View boarding pass" button instead of retry |
| `ACCEPT_TERMS_REQUIRED` | 422 | accept_terms was false (frontend bug — checkbox wasn't enforced) | Re-show widget with checkbox forced |
| `IDEMPOTENCY_KEY_REQUIRED` | 422 | Frontend forgot to send the key (frontend bug) | Send a fresh UUID and retry |
| `PASSPORT_REQUIRED` | 422 | International flight, no APIS data on file | Surface APIS-collection flow (slice TBD) |
| `DEPENDENCY_UNAVAILABLE` | 503 | Airline backend unreachable | Show "try again shortly" toast |

Sample 409 response:

```json
{
  "mode": "tool_error",
  "error_code": "CHECKIN_NOT_OPEN",
  "error_status": 409,
  "answer": "Check-in isn't available right now — see details below. It usually opens 48 hours before departure.",
  "tool_name": "web_checkin"
}
```

### 3A.8. The check-in widget — frontend skeleton

Plain React-ish pseudocode for the widget that sits between
`retrieve_booking` and `web_checkin`:

```tsx
function CheckinWidget({ booking, onSubmit }: {
  booking: BookingCard;
  onSubmit: (sel: CheckinSelections) => void;
}) {
  const [passengers, setPassengers] = useState(
    new Set(booking.passengers.map(p => p.passenger_id)),  // default: all selected
  );
  const [segmentId, setSegmentId] = useState(booking.segments[0].segment_id);
  const [accepted, setAccepted] = useState(false);
  const [idempotencyKey] = useState(() => uuidv4());        // frozen for this widget instance

  return (
    <div className="checkin-widget">
      <h4>Who's checking in?</h4>
      {booking.passengers.map(p => (
        <label key={p.passenger_id}>
          <input
            type="checkbox"
            checked={passengers.has(p.passenger_id)}
            onChange={() => togglePassenger(p.passenger_id)}
          />
          {p.first_name} {p.last_name}
        </label>
      ))}

      {booking.segments.length > 1 && (
        <>
          <h4>Which flight?</h4>
          <select value={segmentId} onChange={e => setSegmentId(e.target.value)}>
            {booking.segments.map(s => (
              <option key={s.segment_id} value={s.segment_id}>
                {s.flight_number} {s.origin}→{s.destination}
              </option>
            ))}
          </select>
        </>
      )}

      <label>
        <input
          type="checkbox"
          checked={accepted}
          onChange={e => setAccepted(e.target.checked)}
        />
        I accept the airline's terms of service
      </label>

      <button
        disabled={!accepted || passengers.size === 0}
        onClick={() =>
          onSubmit({
            booking_reference: booking.booking_reference,
            last_name: lastNameFromContext,
            passenger_ids: Array.from(passengers),
            segment_ids: [segmentId],
            accept_terms: accepted,
            idempotency_key: idempotencyKey,
          })
        }
      >
        Check in
      </button>
    </div>
  );
}
```

`onSubmit` calls `/chat_pg` with `action: "web_checkin"` and the
collected `action_params`.

### 3A.9. Things to remember

| | |
|---|---|
| **`last_name` carries forward** | Frontend remembers it from the retrieve_booking step. Don't ask the user twice. |
| **`idempotency_key` is per-attempt** | Frozen for the lifetime of one widget instance. New widget = new UUID. |
| **`passenger_ids` are opaque IDs** | Don't construct them client-side. Always use the IDs from `booking.passengers[*].passenger_id`. |
| **Multi-segment is per-checkin-call** | If the booking has 2 segments, the user check-ins twice (once per segment). v1 doesn't support both-in-one. |
| **Boarding pass URL points at the boarding-pass tool** | `boarding_pass_url` is the airline's URL for that pass. It's served by chip D (boarding pass) below. Cleanest pattern: show a "View boarding pass" button that dispatches `action: "boarding_pass"` with the same identifiers. |

---

## 3B. Chip D — Boarding pass

| | |
|---|---|
| **Chip label** | "Boarding pass" |
| **Backend `action`** | `"boarding_pass"` |
| **Required fields** | `booking_reference`, `last_name`, `passenger_id`, `segment_id` |
| **Optional fields** | `format` (defaults to `"json"`; `pdf`/`wallet_apple`/`wallet_google` deferred — return 501) |
| **Render card** | `render_as: "boarding_pass_card"` |
| **Type** | Read operation; **pre-condition**: passenger must be checked in |

### 3B.1. When this chip fires vs the inline pass from check-in

There are TWO ways the user gets a boarding pass:

| Path | UX |
|---|---|
| Just finished web_checkin | The check-in response already contains `tool_result.checked_in[*].boarding_pass` — render it inline on the checkin_card. **No extra fetch needed.** |
| Returning later (closed chat, came back) | Use this chip — fetches a fresh copy from the airline backend |

So `boarding_pass` is for the *"I want my boarding pass again"* use case, not the immediately-after-check-in case.

### 3B.2. The two-step flow

Mirrors the web_checkin pattern:

```
1. User clicks "Boarding pass" chip
       ↓
2. Frontend triggers retrieve_booking (asks for PNR + last name)
       ↓
3. tool_executed returns booking_card with passengers + segments
       ↓
4. Frontend renders booking_card PLUS a passenger/segment picker:
     ▾ Passenger: [ John Doe ] [ Jane Doe ]
     ▾ Flight: AI101 DEL→BOM (s1)
     [ Get boarding pass ]
       ↓
5. User picks + clicks Get boarding pass
       ↓
6. Frontend POSTs:
     action: "boarding_pass",
     action_params: {
       booking_reference, last_name,           ← from step 2
       passenger_id, segment_id                ← from picker
     }
       ↓
7. Backend dispatches GET /v1/bookings/{ref}/boarding-pass
       ↓
8. Frontend renders boarding_pass_card with full barcode
```

Skip the picker if there's only one passenger AND one segment — just dispatch with those auto-selected.

### 3B.3. Request body

```json
POST /chat_pg
{
  "conversation_id": "<uuid>",
  "agent_type": "aviation",
  "action": "boarding_pass",
  "action_params": {
    "booking_reference": "ABC123",
    "last_name": "DOE",
    "passenger_id": "p1",
    "segment_id": "s1"
  }
}
```

**No idempotency key** (this is a read, idempotent by nature).

### 3B.4. `tool_result` shape — boarding_pass_card

Same shape as the inline `boarding_pass` field on the checkin_card (§3A.4),
but with extra fields the airline knows post-issuance:

```ts
type BoardingPassCard = {
  passenger: { first_name: string; last_name: string };
  flight_number: string;            // "AI101"
  origin: string;                   // "DEL"
  destination: string;              // "BOM"
  scheduled_departure: string;      // ISO 8601 with offset
  boarding_time: string;            // ISO 8601 with offset
  seat: string;                     // "12A"
  boarding_group: string;           // "1"
  sequence_number: number;          // check-in sequence (e.g. 42)
  gate: string | null;              // may not be assigned yet
  terminal: string | null;
  barcode_format: "PDF417" | "QR" | "AZTEC";
  barcode_data: string;             // IATA BCBP M1 string
};
```

### 3B.5. Suggested card layout

```
┌──────────────────────────────────────────────────┐
│  BOARDING PASS                                   │
│  ──────────────────────────────────────────────  │
│  JOHN DOE                              Seq #42   │
│  AI101  ·  DEL → BOM                             │
│  Departure 08:00  ·  Boarding 07:30              │
│  ──────────────────────────────────────────────  │
│  Seat 12A   Group 1   Gate B12   Terminal T3     │
│  ──────────────────────────────────────────────  │
│  ┌──────────────────────────┐                    │
│  │  ▮▮▮ ▮ ▮▮▮ ▮ ▮▮ ▮▮▮ ▮▮  │  PDF417            │
│  │  ▮ ▮▮ ▮▮▮ ▮ ▮▮ ▮ ▮▮▮ ▮  │                    │
│  └──────────────────────────┘                    │
│  [ Save to Wallet ]   [ Download PDF ]           │
└──────────────────────────────────────────────────┘
```

Render `barcode_data` as PDF417 client-side using `bwip-js` (see §3A.6).
"Save to Wallet" / "Download PDF" buttons are **disabled in v1** —
they'd hit the boarding_pass tool with `format: "wallet_apple"` /
`format: "pdf"`, which currently return 501. Surface a "Coming soon"
tooltip.

### 3B.6. Error path — passenger not checked in

The most common error path. Backend returns:

```json
{
  "mode": "tool_error",
  "error_code": "NOT_CHECKED_IN",
  "error_status": 409,
  "answer": "That passenger isn't checked in yet for that flight. Complete web check-in first, then try again.",
  "tool_name": "boarding_pass"
}
```

**Suggested UX:** show a "Complete check-in" call-to-action that
flips back to the web_checkin chip with the same booking + last_name
pre-filled. Avoids dead-ending the user.

### 3B.7. Other error codes

| `error_code` | `error_status` | When | Suggested UX |
|---|---|---|---|
| `NOT_CHECKED_IN` | 409 | Passenger isn't checked in for that segment | Offer "Complete check-in" button |
| `BOOKING_NOT_FOUND` | 404 | Wrong PNR | Reset to retrieve_booking |
| `BOOKING_VERIFICATION_FAILED` | 403 | Wrong last name | Reset to retrieve_booking |
| `FORMAT_NOT_IMPLEMENTED` | 501 | `format` ≠ `json` (pdf / wallet_* deferred in v1) | Disable the corresponding button |
| `DEPENDENCY_UNAVAILABLE` | 503 | Airline backend down | "Try again shortly" toast |

### 3B.8. Things to remember

| | |
|---|---|
| **Pre-condition is "checked in"** | If the user clicks Boarding pass before web check-in, you'll see a 409. UX should detect this and route them through check-in first. |
| **Inline barcode on check-in is canonical for "just-checked-in" UX** | Don't always re-fetch — the check-in response already has the data. Only call `boarding_pass` when the user explicitly asks for it again. |
| **Format=json only in v1** | Binary formats (PDF, Apple Wallet, Google Wallet) all return 501. Hide / disable those buttons until backend implements them. |
| **Sequence number** | The airline's check-in queue position. Useful for boarding-group prioritisation. Can be 0 for some carriers. |
| **`gate` may be null** | Gates are assigned 30-90 min before boarding. Show "Gate TBA" in the UI when null. |

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
| Web check-in: APIS data collection (international flights) | Backend will return `PASSPORT_REQUIRED` (422). Frontend should surface a passport-collection sub-flow — currently TBD. |
| Web check-in: multi-segment in one call | One segment per check-in. Round-trip travelers check in twice. |
| Boarding pass: PDF / Apple Wallet / Google Wallet formats | All three return 501 from the backend in v1. Frontend should render the PDF417 barcode client-side from `barcode_data` and disable those buttons. |
| Web check-in: special meals / wheelchair (SSR) | `preferences.ssr` and `preferences.meals` exist in the API but no widget yet. Frontend can pass them through `action_params.preferences` once added. |

---

## 6. Quick reference card

Print this on a sticky note:

| Action you want | What to send |
|---|---|
| Show flight status flow | `action: "flight_status", action_params: {}` |
| Show "flights from X" flow | `action: "flight_search", action_params: { origin: "<X>" }` |
| Show "flights to Y" flow | `action: "flight_search", action_params: { destination: "<Y>" }` |
| Submit web check-in (after retrieve_booking) | `action: "web_checkin", action_params: { ...all 6 fields incl idempotency_key }` |
| Fetch boarding pass (after retrieve_booking) | `action: "boarding_pass", action_params: { booking_reference, last_name, passenger_id, segment_id }` |
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

