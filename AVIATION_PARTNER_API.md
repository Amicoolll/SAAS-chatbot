# Aviation Partner API Contract — v1

**Audience:** airline tech teams integrating with the Enterprise Drive Chatbot's
aviation features.

This document describes the HTTP contract our chatbot speaks to **your** backend.
You implement it once (or write a thin adapter mapping your existing APIs to
these shapes) and our chatbot becomes airline-agnostic.

**Direction of calls:** chatbot → airline. The chatbot is the client. You are
the server.

**Status:** v1 draft, pending airline-tech review.

---

## Design principles

| Principle | Detail |
|---|---|
| **Transport** | HTTPS only. JSON request and response bodies (`application/json`). |
| **Versioning** | URL prefix `/v1/`. Breaking changes ship under `/v2/`. v1 stays available until consumers migrate. |
| **Auth** | `Authorization: Bearer <SERVICE_TOKEN>` — long-lived service credential issued by the airline to our chatbot. mTLS optional. |
| **End-user authorization** | Each request includes verifying fields (e.g. `booking_reference + last_name`). The airline backend authorizes against those — the bearer token only proves the chatbot is calling. |
| **Idempotency** | All write endpoints (`POST` that creates/changes state) accept `Idempotency-Key: <uuid>`. Repeating the same key returns the same response without re-executing. |
| **Tracing** | Chatbot sends `X-Request-Id` (UUID per call) and `X-Trace-Id` (per user-conversation). Airline echoes them in response logs. |
| **Errors** | Standard envelope: `{ "error": { "code": "...", "message": "...", "details": {} } }`. HTTP status codes per RFC. |
| **Time format** | ISO 8601 with timezone offset, e.g. `2026-06-01T08:00:00+05:30`. |
| **Money** | Integer minor units? **No** — use decimal `amount` + ISO 4217 `currency`. Spec the `amount` as a number, not a string. |
| **IATA codes** | Airports/cities as 3-letter IATA codes (`DEL`, `BOM`). Flight numbers as carrier code + number (`AI101`). |
| **Pagination** | Where applicable: `?cursor=<opaque>&limit=N`; response includes `next_cursor`. |

### Common request headers

```
Authorization: Bearer <SERVICE_TOKEN>          required
Content-Type: application/json                 required for POST
X-Request-Id: <uuid>                           required
X-Trace-Id: <uuid>                             optional
Idempotency-Key: <uuid>                        required for write endpoints
Accept-Language: en-IN                         optional, for localized strings
```

### Common error envelope

```json
{
  "error": {
    "code": "BOOKING_NOT_FOUND",
    "message": "No booking matches the supplied reference and last name.",
    "details": { "booking_reference": "ABC123" }
  }
}
```

| HTTP | Meaning |
|---|---|
| `200` | Success |
| `400` | Malformed request (missing field, bad type) |
| `401` | Bearer token missing or invalid |
| `403` | Bearer token valid but lacks permission for this resource |
| `404` | Resource doesn't exist (booking / flight / seat map) |
| `409` | State conflict (already checked in, seat just taken, check-in not open yet) |
| `422` | Semantically invalid (departure date in the past, seat doesn't exist on this aircraft) |
| `429` | Rate limited; airline includes `Retry-After` header |
| `5xx` | Airline-side failure |

Common error codes used across endpoints: `BOOKING_NOT_FOUND`, `INVALID_CREDENTIALS`, `BOOKING_VERIFICATION_FAILED`, `OPERATION_NOT_ALLOWED`, `RATE_LIMITED`, `INTERNAL_ERROR`.

---

## Endpoint 1 — Retrieve booking

**`POST /v1/bookings/lookup`**

Look up an existing booking by PNR + verifier. We use POST (not GET with query params) because PNR + last name in URLs leak into logs.

### Request

```json
{
  "booking_reference": "ABC123",
  "last_name": "DOE"
}
```

| Field | Required | Notes |
|---|---|---|
| `booking_reference` | yes | Airline PNR. Usually 6 alphanumeric characters. |
| `last_name` | yes | Used by the airline as a verifier. |

### Successful response (200)

```json
{
  "booking_reference": "ABC123",
  "status": "CONFIRMED",
  "passengers": [
    {
      "passenger_id": "p1",
      "first_name": "JOHN",
      "last_name": "DOE",
      "type": "ADULT"
    }
  ],
  "segments": [
    {
      "segment_id": "s1",
      "flight_number": "AI101",
      "origin": "DEL",
      "destination": "BOM",
      "scheduled_departure": "2026-06-01T08:00:00+05:30",
      "scheduled_arrival": "2026-06-01T10:00:00+05:30",
      "cabin_class": "ECONOMY",
      "fare_basis": "Y",
      "status": "CONFIRMED"
    }
  ],
  "contact": {
    "email": "passenger@example.com",
    "phone": "+919876543210"
  },
  "ancillaries": {
    "checked_baggage_kg": 23,
    "seats": [{ "passenger_id": "p1", "segment_id": "s1", "seat": "12A" }]
  },
  "balance_due": {
    "amount": 0,
    "currency": "INR"
  }
}
```

| Enum | Values |
|---|---|
| `status` (booking) | `CONFIRMED`, `PENDING`, `CANCELLED`, `COMPLETED` |
| `status` (segment) | `CONFIRMED`, `CHECKED_IN`, `CANCELLED`, `FLOWN` |
| `cabin_class` | `ECONOMY`, `PREMIUM_ECONOMY`, `BUSINESS`, `FIRST` |
| `type` (passenger) | `ADULT`, `CHILD`, `INFANT` |

### Errors

- `404 BOOKING_NOT_FOUND` — no PNR match
- `403 BOOKING_VERIFICATION_FAILED` — PNR exists but last name doesn't match (avoid PNR enumeration)

---

## Endpoint 2 — Flight status

**`GET /v1/flights/status?flight_number=AI101&date=2026-06-01`**

Real-time status of a specific flight. Chatbot caches responses for 60 seconds to keep load off your API.

### Request

| Query param | Required | Notes |
|---|---|---|
| `flight_number` | yes | IATA flight designator, e.g. `AI101` |
| `date` | yes | Departure date (origin local), `YYYY-MM-DD` |

### Successful response (200)

```json
{
  "flight_number": "AI101",
  "date": "2026-06-01",
  "origin": "DEL",
  "destination": "BOM",
  "scheduled_departure": "2026-06-01T08:00:00+05:30",
  "scheduled_arrival": "2026-06-01T10:00:00+05:30",
  "estimated_departure": "2026-06-01T08:25:00+05:30",
  "estimated_arrival": "2026-06-01T10:30:00+05:30",
  "actual_departure": null,
  "actual_arrival": null,
  "status": "DELAYED",
  "delay_minutes": 25,
  "gate": "B12",
  "terminal": "T3",
  "aircraft_type": "A320"
}
```

| Enum | Values |
|---|---|
| `status` | `SCHEDULED`, `ON_TIME`, `DELAYED`, `BOARDING`, `DEPARTED`, `ARRIVED`, `CANCELLED`, `DIVERTED` |

### Errors

- `404 FLIGHT_NOT_FOUND` — no flight with that number on that date

---

## Endpoint 3 — Flight search (from / to city)

**`POST /v1/flights/search`**

Search for available flights. Used by both "flights from X" and "flights to Y" use cases.

### Request

```json
{
  "origin": "DEL",
  "destination": "BOM",
  "departure_date": "2026-06-01",
  "return_date": "2026-06-08",
  "passengers": { "adults": 1, "children": 0, "infants": 0 },
  "cabin_class": "ECONOMY",
  "max_stops": 1,
  "currency": "INR"
}
```

| Field | Required | Notes |
|---|---|---|
| `origin`, `destination` | yes | IATA airport or city code |
| `departure_date` | yes | `YYYY-MM-DD` |
| `return_date` | no | Omit for one-way |
| `passengers` | yes | At least 1 adult |
| `cabin_class` | no | Default `ECONOMY` |
| `max_stops` | no | `0` = nonstop only; default no filter |
| `currency` | no | Default airline's home currency |

### Successful response (200)

```json
{
  "currency": "INR",
  "results": [
    {
      "result_id": "abc123",
      "outbound_segments": [
        {
          "flight_number": "AI101",
          "origin": "DEL",
          "destination": "BOM",
          "departure_time": "2026-06-01T08:00:00+05:30",
          "arrival_time": "2026-06-01T10:00:00+05:30",
          "duration_minutes": 120,
          "stops": 0,
          "aircraft_type": "A320",
          "cabin_class": "ECONOMY",
          "fare_basis": "Y"
        }
      ],
      "return_segments": [],
      "fare": {
        "base_amount": 4200,
        "taxes_amount": 800,
        "total_amount": 5000,
        "currency": "INR",
        "fare_type": "SAVER",
        "refundable": false,
        "changes_allowed": true
      },
      "baggage_allowance": {
        "cabin_kg": 7,
        "checked_kg": 15
      },
      "seats_remaining": 4
    }
  ],
  "total_results": 12,
  "next_cursor": "eyJwYWdlIjoyfQ=="
}
```

`result_id` is opaque to the chatbot — used as the handoff token if the user proceeds to book that fare.

### Errors

- `422 INVALID_ROUTE` — airline doesn't operate this O&D
- `422 DATE_IN_PAST` — departure date is before today

---

## Endpoint 4 — Boarding pass

**`GET /v1/bookings/{booking_reference}/boarding-pass?passenger_id=p1&segment_id=s1&format=json`**

Retrieve a boarding pass. Returns 409 if the passenger isn't checked in yet — chatbot then prompts the user to do web check-in first.

### Request

Path: `booking_reference`. Query params: `passenger_id`, `segment_id`, `format`.

| Query param | Required | Notes |
|---|---|---|
| `passenger_id` | yes | From the booking response |
| `segment_id` | yes | Which leg |
| `format` | no | `json` (default), `pdf`, `wallet_apple`, `wallet_google` |

Verifier header (since this isn't a POST body):
```
X-Booking-Verifier-LastName: DOE
```

### Successful response — JSON (200)

```json
{
  "passenger": { "first_name": "JOHN", "last_name": "DOE" },
  "flight_number": "AI101",
  "origin": "DEL",
  "destination": "BOM",
  "scheduled_departure": "2026-06-01T08:00:00+05:30",
  "boarding_time": "2026-06-01T07:30:00+05:30",
  "seat": "12A",
  "boarding_group": "1",
  "sequence_number": 42,
  "gate": "B12",
  "terminal": "T3",
  "barcode_format": "PDF417",
  "barcode_data": "M1DOE/JOHN          EABC123 DELBOMAI 0101 152Y012A0042 100"
}
```

`barcode_data` follows IATA BCBP (Bar-Coded Boarding Pass) format — same string that gets encoded in the visual barcode.

### Successful response — PDF (200)

`Content-Type: application/pdf`. Binary body.

### Successful response — Wallet (200)

`Content-Type: application/vnd.apple.pkpass` or Google Wallet JWT.

### Errors

- `409 NOT_CHECKED_IN` — passenger hasn't completed check-in yet
- `404 PASSENGER_NOT_ON_SEGMENT`

---

## Endpoint 5 — Web check-in

**`POST /v1/checkin`**

Check passengers in for a segment. Idempotent.

### Request

```
Idempotency-Key: 7f4e3c2a-...

{
  "booking_reference": "ABC123",
  "last_name": "DOE",
  "passenger_ids": ["p1", "p2"],
  "segment_ids": ["s1"],
  "accept_terms": true,
  "preferences": {
    "ssr": ["WCHR"],
    "meals": [{ "passenger_id": "p1", "meal_code": "VGML" }]
  }
}
```

| Field | Required | Notes |
|---|---|---|
| `booking_reference` + `last_name` | yes | Verifier pair |
| `passenger_ids` | yes | Subset of passengers; partial check-in OK |
| `segment_ids` | yes | Which legs |
| `accept_terms` | yes | Must be `true`. Airline returns 422 otherwise. |
| `preferences.ssr` | no | Special Service Requests, IATA 4-letter codes (e.g. `WCHR` for wheelchair) |
| `preferences.meals` | no | Per-passenger meal codes |

### Successful response (200)

```json
{
  "checkin_id": "ci_xyz",
  "checked_in": [
    {
      "passenger_id": "p1",
      "segment_id": "s1",
      "seat": "12A",
      "boarding_pass_url": "/v1/bookings/ABC123/boarding-pass?passenger_id=p1&segment_id=s1"
    }
  ],
  "warnings": [
    { "code": "MEAL_UNAVAILABLE", "message": "VGML cannot be confirmed for AI101 — defaulting to standard meal." }
  ]
}
```

### Errors

- `409 CHECKIN_NOT_OPEN` — typically opens 48h before departure; response includes `details.opens_at` ISO timestamp
- `409 CHECKIN_CLOSED` — too close to departure
- `409 ALREADY_CHECKED_IN` — partial-checkin can return `200` with the existing seat instead, airline's choice
- `422 PASSPORT_REQUIRED` — international flight, airline requires APIS data first; details should include the missing fields

---

## Endpoint 6 — Seat map and seat selection

### 6a — Get seat map

**`GET /v1/bookings/{booking_reference}/seat-map?segment_id=s1`**

```
X-Booking-Verifier-LastName: DOE
```

Successful response (200):

```json
{
  "aircraft_type": "A320",
  "cabin_class": "ECONOMY",
  "currency": "INR",
  "rows": [
    {
      "row_number": 1,
      "seats": [
        { "seat": "1A", "type": "WINDOW",  "status": "OCCUPIED",  "fee": null },
        { "seat": "1B", "type": "MIDDLE",  "status": "AVAILABLE", "fee": { "amount": 500, "currency": "INR" } },
        { "seat": "1C", "type": "AISLE",   "status": "AVAILABLE", "fee": { "amount": 600, "currency": "INR" } }
      ]
    }
  ],
  "exits": [{ "after_row": 12, "side": "BOTH" }]
}
```

| Enum | Values |
|---|---|
| `type` | `WINDOW`, `MIDDLE`, `AISLE`, `EXIT_ROW`, `EXTRA_LEGROOM`, `BULKHEAD` |
| `status` | `AVAILABLE`, `OCCUPIED`, `BLOCKED`, `RESTRICTED` |

### 6b — Select seats

**`POST /v1/bookings/{booking_reference}/seats`**

```
Idempotency-Key: <uuid>
X-Booking-Verifier-LastName: DOE

{
  "segment_id": "s1",
  "selections": [
    { "passenger_id": "p1", "seat": "12A" },
    { "passenger_id": "p2", "seat": "12B" }
  ]
}
```

Successful response (200):

```json
{
  "selections": [
    { "passenger_id": "p1", "seat": "12A", "fee_charged": { "amount": 500, "currency": "INR" } },
    { "passenger_id": "p2", "seat": "12B", "fee_charged": { "amount": 500, "currency": "INR" } }
  ],
  "total_charged": { "amount": 1000, "currency": "INR" },
  "payment_required": false
}
```

If `payment_required` is `true`, the airline returns a payment intent (separate flow — covered in v2 once we add booking/payment).

### Errors

- `409 SEAT_UNAVAILABLE` — seat was just taken; chatbot re-fetches the seat map
- `422 RESTRICTED_SEAT` — exit row passenger doesn't meet criteria (minor, mobility, etc.)

---

## What we do NOT need from you in v1

To keep the v1 contract small:

- Booking creation (book flight) — deferred to v2
- Payment processing — deferred to v2
- Refunds / cancellations — deferred to v2
- Loyalty / FFP integration — deferred to v2
- APIS / passport data submission — surfaced as an error code (`PASSPORT_REQUIRED`) but not implemented as its own endpoint in v1
- Trip planning, fare rules deep-dive — these come from documents (RAG), not your API

---

## What we need from your team to start

1. **Sandbox URL** for v1 endpoints, even if half are stubs returning fixed data
2. **Service-account bearer token** for the chatbot's calls
3. **One sample booking** on the sandbox we can lookup, check in, and get a boarding pass for
4. **Rate limits** — calls/sec we should respect, and the back-pressure header you'll send (`Retry-After`)
5. **SLA** — 95th-percentile response time targets per endpoint (the chatbot has a 30s budget per Catapult call)
6. **Confirmation** of the auth model (bearer + per-request verifier vs something custom)
7. **List of error codes** beyond the standard ones in this doc that your backend may return

---

## Versioning policy

| Change | v1.x bump | New major (v2) |
|---|---|---|
| Add a new optional field | yes | no |
| Add a new endpoint | yes | no |
| Tighten a constraint, remove a field, change a field's type | no | yes |
| Change auth model | no | yes |

We'll keep v1 callable until all chatbots have migrated to v2.

---

## Open questions for review

- Per-user OAuth instead of bearer + verifier? (Adds complexity, only needed if airlines want fine-grained user-level audit on their side.)
- Webhook callbacks for asynchronous outcomes (e.g. payment authorization), or polling-only?
- Localized strings via `Accept-Language` — confirm which locales each airline supports.
- Multi-segment / multi-airline interline bookings — out of scope for v1?
