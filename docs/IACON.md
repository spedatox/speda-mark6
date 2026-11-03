# Iacon — implementation and operation

Iacon is the renovated Mark VI memory architecture. **Iacon Hall of Records stays
in Hisar**; the local library index holds disposable passages and original revision
IDs, and checks Hisar access before returning evidence.

## What runs

- **Read:** provider-free automatic context, then at most one shared personal,
  native-document or Hisar lookup. Automatic context gets 250 ms (500 ms cold),
  lookup 2.5 seconds; cumulative memory time is capped at five seconds. Model
  answering time is separate. Context limits are 6,000/4,000 UTF-8 bytes, with a
  10,000-byte turn ceiling; these are conservative bounds, not measured tokens.
- **Write:** original owner messages are stored first. Orion interprets one
  6,000-character slice, saves the response, then independently reviews each
  proposal. Code verifies evidence, ownership, links and expected versions.
  Records, immutable revisions, projections and state follow-ups commit together.
  Native documents use the explicit owner writer; models cannot rewrite them.
  The full write schema is exposed only to Orion, keeping it out of ordinary
  answering-model requests. Background prompts specify each payload's required fields.
- **Structure:** entities, claims/preferences, episodes, states and native documents
  have stable IDs and typed payloads. Recorded, effective, occurrence and review
  dates remain distinct. Unknown dates stay unknown. Links join records; competing
  claims can share a conflict group. Corrections refresh affected projections.
- **Living states:** due questions check newer evidence, quiet hours and a 24-hour
  notification cooldown. Telegram buttons support completion, cancellation, still
  open, snooze and mute. Free text becomes evidence for review, not automatic
  completion. Late replies cannot overwrite a newer state. Silence leaves it open.
- **Cost:** SQLite/PostgreSQL reservations serialize shared daily call, token and
  microdollar ceilings before dispatch. Rates are conservative microdollars per
  token for the effective model, covering the most expensive input/output rate.
  Provider fallbacks are disabled. Successful responses survive restarts; unknown
  outcomes are held. Invalid contracts are quarantined, without automatic repairs.
  Reservations are never refunded automatically. Pause survives process restarts.

## Controls

All endpoints require `X-API-Key`. Scheduler endpoints also require `X-N8N-Secret`.
This renovation retains the existing single-owner API (owner ID 1).

| Endpoint | Purpose |
|---|---|
| `POST /iacon/sources` | Immutable explicit owner intake: `key`, `text` |
| `GET/POST /iacon/records` | List or perform an explicit typed owner write |
| `GET /iacon/records/{id}?history=true` | Read a record and latest 20 revisions |
| `GET/PUT /iacon/maintenance` | Inspect work, follow-ups and reservations; versioned policy updates |
| `POST /iacon/drain` | Bounded Orion maintenance and one eligible Telegram follow-up |
| `POST /iacon/work/{id}/resolve` | Discard or resume saved/local work; unknown paid calls cannot redispatch |
| `POST /iacon/followups/{id}/resolve` | Mute, snooze or explicitly reconcile delivery |
| `POST /iacon/telegram/update` | Receive an owner/chat-bound Telegram update forwarded by n8n |
| `POST /iacon/hall-of-records/sync` | Incremental Hisar event synchronization; `?full=true` bootstraps the catalogue |
| `GET /iacon/hall-of-records/sources/{id}` | Live-authorized citation evidence |

Schemas are in `app/schemas/iacon.py`; FastAPI exposes them when DEBUG is enabled.
Policy PUT requires `expected_version` and the complete policy. Spending defaults
to **zero** until finite ceilings and model-specific rates are configured.
`MEMORY_CAPTURE_ENABLED=false` prevents automatic Orion drains; persisted pause
also blocks drains, synchronization and notifications.

## n8n and connections

Use the existing n8n scheduler to POST `/iacon/drain`, for example every 15 minutes.
POST `/iacon/hall-of-records/sync` on the library synchronization schedule. Both
requests carry the API and scheduler headers. The old `/trigger/orion` endpoint
now enters this governed worker, not an unrestricted agent audit.

Configure the three `IACON_TELEGRAM_*` values in `.env`, and enable
`telegram_enabled` in the persisted policy. The bot's Telegram trigger in n8n
forwards the original update JSON to `/iacon/telegram/update`; callback-query
acknowledgement can use n8n's Telegram Answer Query action. This endpoint is **not**
an unauthenticated direct Telegram webhook. Delivery-unknown/sending items need
reconciliation; never replay them automatically. Reply-pending and needs-review
items remain visible in maintenance status.

Configure `HISAR_BASE_URL`, `HISAR_LIBRARY_SYNC_TOKEN` and
`HISAR_LIBRARY_READER_TOKEN`. Original downloads are size/hash verified. PDF,
DOCX, XLSX, CSV and text extraction use the existing Hisar library implementation.
Scanned PDFs require OCR/review; paid embeddings and OCR providers are not enabled.
Keyword retrieval is operational; semantic paraphrase recall is not claimed.

## Migration and validation

`scripts/import_memory_snapshot.py` imports into a **new** database only. It retains
all original sources, native document contents and attribution, catalogs existing
entities, and validates legacy state envelopes before importing their lifecycle.
It does not re-extract old conversations with models or activate Telegram. Invalid
legacy records are reported for review, not silently repaired. Keep the original
snapshot and import report. Live cutover requires reconciling later production
writes; this script is not a live replacement procedure.

The supplied offline snapshot produced 424 native documents, seven entities,
21 states and 18 open-state follow-ups, with no migration review exceptions.
There are 14,292 original/derived source rows. The read-only
`scripts/benchmark_iacon.py` replay measured preparation p95 31–62 ms and lookup
p95 16–47 ms across four queries, 25 turns each, without retrieval failures.
These numbers establish local retrieval speed, not answering-model accuracy.

The client knowledge editor now writes through Catalogue revisions. It preserves
CAS conflicts, reversible retraction, forward restoration and original evidence.
Imported state/ledger documents stay read-only; typed state corrections use the
governed lifecycle. Historical/date/exclusion qualifiers survive projection and
direct record excerpts. Corrected documents retire their obsolete sources from
automatic context without deleting the originals; explicit historical lookup
still identifies those sources as historical. Older prepared views are also
checked against their underlying evidence before selection.

Tests cover owner/evidence boundaries, atomic correction, revocation, idempotency,
concurrent spending admission, pause/restart, saved-response recovery, invalid
contracts, Telegram duplicate/late/unknown outcomes, original Hisar identities,
edition changes, access withdrawal, shared lookup budgets and native migration.
The integration contract additionally exercises native memory/editor behavior on
disposable local PostgreSQL. Live providers, real Telegram delivery, live Hisar
connectivity and production cutover remain release gates. The latency figures
above precede the integration additions and have not been re-certified here.
