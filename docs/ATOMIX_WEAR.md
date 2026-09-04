# Atomix Wear

The watch surface of **Atomix**, the owner's health and training agent. Two jobs
in one Wear OS application: it feeds Igor the owner's live biometrics straight
off the wrist, and it renders the training session Atomix planned, one exercise
at a time, capturing what actually happened.

Built on the [Ultron Wear](https://github.com/spedatox/ultron-wear) baseline —
same transport, same persistence discipline, same design system, different
accent and a different data layer.

Status: **design agreed, not yet implemented.** This document is the plan.

---

## Contents

- [1. Why this exists](#1-why-this-exists)
- [2. What ships where](#2-what-ships-where)
- [3. Half A — direct biometrics](#3-half-a--direct-biometrics)
- [4. Half B — the training client](#4-half-b--the-training-client)
- [5. Exercise catalog](#5-exercise-catalog)
- [6. What is reused from Ultron Wear](#6-what-is-reused-from-ultron-wear)
- [7. Build phases](#7-build-phases)
- [8. Open risks](#8-open-risks)

---

## 1. Why this exists

### 1.1 The delay is in the hops, not the code

Health data currently reaches Igor across five hops:

```
watch sensor
  → Samsung Health (watch)
  → Samsung Health (phone)     ← BLE batch sync, 10–30 min, opaque
  → Health Connect (phone)     ← Samsung flushes on its own schedule
  → Speda GO                   ← 4h trickle, 15-min demand floor
  → POST /health/ingest
```

Hops 2 and 3 are Samsung's and we cannot instrument, hurry or observe them. Hop 4
is ours and is deliberately slow: `SYNC_INTERVAL_HOURS = 4L` and
`DEMAND_INTERVAL_MINUTES = 15L` in `HealthSyncManager.kt`, the latter being
WorkManager's own floor for periodic work.

The compounding effect is documented in the code that suffers from it.
`skills/health_data.py` carries a dated post-mortem: on 2026-08-05 the 08:00
health briefing demanded a sync at 05:00:16Z, gave up at 05:00:41Z, and the phone
delivered 4,210 samples at 05:02:18Z — 97 seconds after the wait expired. The
watch had been worn all day. The briefing still reported a link outage.

A watch application reading its own sensors deletes hops 2, 3 and 4. That is the
whole thesis.

### 1.2 The freshness gate finally gets an answer

`/health/sync-demand` exists for one reason, stated plainly in `routers/health.py`:
*"Speda GO carries no Firebase, so nothing can wake it from the server side."*
The demand is a note left where the app will eventually look.

Ultron Wear does carry Firebase. Atomix Wear inherits that, so a `live=true`
health query can **push** the watch and get an answer in seconds rather than
leaving a note and hoping. The `_LIVE_WAIT_INTERACTIVE_S = 25.0` budget in
`health_data.py` stops being a coin flip.

This is the larger win and it is easy to miss: the fix is not only that data
arrives sooner, it is that data can be *demanded* at all.

### 1.3 What this does not fix

Sleep is computed by Samsung Health, not exposed raw by the watch's own sensors.
Weight and body composition come from a scale, not the wrist. Those metrics keep
travelling the phone pipe, which stays exactly as it is.

Atomix Wear is a **fast path for live metrics**, not a replacement for Speda GO's
health tab. Both feed the same endpoint (§3.2).

---

## 2. What ships where

| Piece | Location | New? |
|---|---|---|
| Wear OS application | `packages/atomix-wear` | new package |
| Training endpoints | `packages/igor/app/routers/training.py` | new |
| Plan + log models | `packages/igor/app/models/training.py` | new |
| Plan service | `packages/igor/app/services/training.py` | new |
| `set_training_plan` skill | `packages/igor/app/skills/atomix_training.py` | new |
| Exercise catalog | `packages/igor/app/data/exercises/` | new, vendored |
| Training Protocol amendment | `prompts/agents/atomix/02_training_protocol.md` | edit |
| Health ingest | `routers/health.py`, `services/health.py` | **unchanged** |

**Package placement.** `packages/atomix-wear`, in-repo alongside `speda-go`,
rather than a standalone repository like Ultron Wear. Ultron Wear is a separate
*product* — academic scheduling, MIT-licensed, useful to someone who has never
heard of Mark VI. Atomix Wear is a Mark VI client with no life outside this
system, exactly like Speda GO. In-repo also means it inherits AGPL-3.0-or-later
and the SPDX header rule, and falls under the cross-client parity rule in
`CLAUDE.md`.

---

## 3. Half A — direct biometrics

### 3.1 Collection

Wear OS **Health Services** `PassiveMonitoringClient` for the metrics the watch
derives itself — heart rate and steps at minimum. Passive monitoring is the
correct API here rather than `ExerciseClient`: it batches through Doze, survives
process death, and does not hold the sensor at active-workout duty cycle, which
would visibly cost battery for a background feed.

> **Verify on hardware before relying on it.** Exact `DataType` availability
> varies by device and Wear OS version, and `RESTING_HEART_RATE` in particular
> may be a Samsung-derived value rather than a Health Services passive type. The
> first implementation task is to enumerate what the Galaxy Watch actually
> exposes and write the answer into this section. Do not assume the catalog.

Anything the watch does not derive natively is read from **Health Connect on the
watch** (Wear OS 4+) as a secondary source, using the same record→DTO mapping
`HealthConnectSource.kt` already implements on the phone.

### 3.2 Transport — no backend change

Samples POST to the existing `/health/ingest`. Nothing about the endpoint, the
schema or the tables changes, because the pipe was designed generic:

- `HealthSample` is keyed unique on `(metric, start_ts, origin)`, and the model's
  own docstring calls out that this exists so *"the same walk recorded by two
  apps stays two rows rather than silently overwriting."* That property was
  written for two phone apps; it is exactly what lets the watch and the phone
  both feed the store without a dedupe strategy.
- `metric` is a free string and anything the flat columns cannot carry rides in
  `detail` as JSON. Adding a watch-side metric is not a migration.

Atomix Wear sends `origin = "atomix-wear"` and `device = <watch model>`. Overlap
with the phone pipe is harmless and, during the transition, deliberate.

The token discipline carries over unchanged and is not negotiable: **advance the
cursor only after Igor accepts the batch.** A failed POST re-sends next cycle and
the unique constraint collapses it.

### 3.3 Cadence

Push-driven first, timer second — the inverse of Speda GO, which has no push.

| Trigger | Behaviour |
|---|---|
| FCM data-only message | Sync immediately, expedited. Serves a `live=true` demand. |
| Periodic | Configurable trickle, default well under the phone's 4h. |
| Session end | A completed training session (§4) flushes biometrics with it. |

Data-only is mandatory for the same reason Ultron Wear documents: a message
carrying a `notification` block is rendered by the system tray while the app is
backgrounded and `onMessageReceived` never runs.

Every interval, budget and batch size in this table is a **setting**, wired into
Igor's config schema and reachable from a settings surface — not a constant.
See `CLAUDE.md`, "No hardcoded values".

---

## 4. Half B — the training client

### 4.1 Flow

Three screen kinds, in order:

```
[ session card ]  goal + safety rules — read before starting
       ↓
[ exercise 1..N ]  GIF · name · 3x12 / 50 KG · [Tamamlandı] [Yapamadım]
       ↓
[ summary ]  what was done, what was not → POST /training/log
```

### 4.2 The exercise screen

Per the agreed mockup, on a round face, true black:

- **Circular GIF** at the top — the dataset's 180×180 animation for this
  exercise, centre-cropped to the circle.
- **Name** below it, in Inter (content type, sustained reading).
- **Prescription** — `3x12 / 50 KG` — in Rajdhani, which is what that face is for.
- **Two buttons.** Green `Tamamlandı` (the Atomix accent `#3fae74`) and a
  secondary `Yapamadım`. Both advance to the next exercise; they differ only in
  what they record.

Completion is recorded **once per exercise**, not per set. One tap closes out all
three sets and moves on.

Turkish throughout, per `prompts/core/15_language.md`. The dataset carries
Turkish `instruction_steps`, so exercise cues need no translation work (§5.1).

### 4.3 Why there are two buttons

The Training Protocol is emphatic that deviations are the most valuable data in
the record:

> Log what actually happened, including the deviations — skipped sets,
> substitutions because a machine was taken, pain, an early exit. The deviations
> are the most useful data in the file; a log that only records the plan is
> fiction.
> — `prompts/agents/atomix/02_training_protocol.md`

A wrist client whose only verb is "done" can only ever report a perfect session.
Every log would confirm the plan, Atomix's anti-repetition and stall-detection
logic would have nothing to react to, and the planner would go quietly blind
while appearing to work. `Yapamadım` is the minimum viable contradiction.

It captures the fact, not the reason. Atomix asks why in chat later — that is a
conversation, not a watch interaction.

### 4.4 Endpoints

New router, `packages/igor/app/routers/training.py`. Thin per Rule 1; logic in
`services/training.py`.

| Endpoint | Purpose |
|---|---|
| `GET /training/today` | The current session: goal, rules, ordered exercises with catalog ids and media URLs. |
| `POST /training/log` | Per-exercise outcome. Idempotent on `(plan_id, exercise_index)`. |
| `GET /training/media/{id}.gif` | One exercise animation, immutable, long cache. |

**Auth.** Every one of these requires `X-API-Key` per Rule 12. Note the trap
documented in `routers/health.py`: `GET /health` is unauthenticated because
`AuthMiddleware` matches it *exactly*, not as a prefix. Nothing under
`/training/*` inherits any exemption, and there is no reason to add one — a
training plan is as personal as a heart rate.

### 4.5 Wire format

Extend the shape that already exists rather than inventing one.
`DailyProgramInput` in `skills/atomix_reports.py` is already a session — goal,
warmup, `exercises[{name, load, note}]`, finisher, rules. The watch needs one
field added:

```json
{
  "plan_id": "2026-09-04",
  "goal": "Üst gövde itiş — göğüs ve triceps",
  "rules": ["Sağ omuzda ağrı olursa dur"],
  "exercises": [
    {
      "index": 1,
      "exercise_id": 1,
      "name": "Dumbbell Bench Press",
      "load": "3x12 / 50 KG",
      "note": "Kontrollü in, 2 tekrar yedek bırak",
      "gif_url": "/training/media/1.gif"
    }
  ]
}
```

`exercise_id` is what makes the GIF possible: it resolves into the catalog (§5).
An exercise Atomix cannot resolve still renders — name, load and note without an
animation — because a session must never fail to display over a missing picture.

Warm-up and finisher are **not** sent to the watch. They stay in the PDF and in
chat, keeping the wrist flow short and gym-focused. The session card carries the
goal and the safety rules only.

### 4.6 Writing plans — the agent side

New skill `set_training_plan`, restricted to `atomix`, sibling of
`generate_daily_training_program`. It publishes the session the watch will
render.

The Training Protocol needs two amendments, in the same work:

1. **Push the plan.** After the read-the-log / check-recovery / find-the-gap
   sequence, the decided session goes to the wrist.
2. **Reconcile the log.** A returned `/training/log` is a *reported session* and
   triggers the existing, non-negotiable rule — it gets written into
   `sessions.md` in the same turn, deviations included. The watch feeds the
   ledger; it does not replace it.

### 4.7 Offline

The watch is offline often and by design. Same discipline as Ultron Wear:

- The plan is cached to `filesDir` and read first, so the screen renders without
  waiting on the network.
- GIFs for the current session prefetch on plan fetch and are cached on disk.
- Taps are written durably **before** they are uploaded, via `goAsync()` in the
  receiver — the same guarantee `AttendanceActionReceiver` gives, for the same
  reason: the gap between a tap and a durable write is where a killed process
  loses data.
- Unsent outcomes retry; ingestion is idempotent, so a re-send collapses.

An empty plan response is rejected rather than applied, per the rule Ultron Wear
already states: an empty payload is far more likely a misconfiguration than a
genuinely empty day.

---

## 5. Exercise catalog

### 5.1 Source

[`hasaneyldrm/exercises-dataset`](https://github.com/hasaneyldrm/exercises-dataset)
— 1,324 exercises as JSON, each with a 180×180 thumbnail and a matching 180×180
animation GIF.

Fields we use: `id`, `name`, `target`, `muscle_group`, `secondary_muscles`,
`equipment`, `instruction_steps` (10 languages, **Turkish included**), `image`,
`gif_url`.

180×180 is close to watch-native; a round Wear OS face is roughly 450px, so the
animation sits comfortably as the centrepiece of the exercise card.

### 5.2 Two licences, two storage rules

The dataset is not licensed as one thing, and the split decides how we store it:

| Part | Licence | What we do |
|---|---|---|
| Data — names, targets, equipment, instructions | **MIT** | Vendored into the repo, attributed |
| Media — images and GIFs | **Proprietary to Gym Visual**, carried by that repo under separate written permission | **Never committed.** Fetched at runtime, cached |

The notice is explicit that downstream users must *"obtain their own separate
license for reuse beyond what Gym Visual's terms permit"*, and that every use
must display `© Gym visual — https://gymvisual.com/`. Each record in
`exercises.json` already carries that string in its `attribution` field, so the
wire format (§4.5) passes it through and the exercise card renders it.

Committing 1,324 proprietary GIFs into a public AGPL repository would be
republishing media we do not hold the rights to, under a licence we cannot apply
to it. So Igor holds the **index**, and media is fetched on demand into a
git-ignored cache; `GET /training/media/{id}.gif` serves from that cache. The
watch still sees one stable URL and still caches locally — the offline story in
§4.7 is unchanged. Only the resolution the dataset provides (180×180) is used,
per the same terms.

Igor owning the index is required regardless: Atomix must resolve "incline
dumbbell press" to a catalog id when it writes a plan, and the APK could never
carry 1,324 animations anyway.

`equipment` is also directly useful to the planner: the Training Protocol forbids
programming equipment the gym has not been confirmed to have, and the catalog
lets that check run against a real field instead of a guess.

### 5.3 Reuse beyond the watch

The same catalog can put animations in the daily program PDF and in chat. That is
a **cross-client parity** obligation under `CLAUDE.md`, not a nice-to-have —
scoped in §7 Phase 4, not filed as a follow-up.

---

## 6. What is reused from Ultron Wear

Most of it. Atomix Wear is a re-skin and a new data layer over a proven shell.

| Ultron Wear | Becomes |
|---|---|
| `data/IgorClient.kt` | Same transport, `HttpURLConnection`, plus training calls |
| `data/AttendanceStore.kt` | `SessionStore` — atomic JSON, temp-file-then-rename, mutex |
| `data/ScheduleRepository.kt` | `PlanRepository` — cache → asset → empty |
| `notification/AttendanceActionReceiver.kt` | The `Tamamlandı` / `Yapamadım` durable write |
| `notification/AttendanceMessagingService.kt` | FCM wake for sync demand and plan updates |
| `sync/` (Sync, Register, Fallback) | Same three workers, same responsibilities |
| `design/` (`ColorMath`, `ThemeEngine`, `UltronType`, `Surfaces`) | Re-accented `#8a93a6` → `#3fae74` |
| `tile/`, `complication/` | Glanceable "next exercise" / "today's session" |

The design decisions carry over with their reasoning intact: no backdrop blur, no
ambient gradient (an OLED pixel at true black is unlit), Rajdhani for chrome and
numerals, Inter for prose, palette resolved once per process via
`staticCompositionLocalOf`.

`ThemeEngine` expands a single accent into the full palette by hue-rotating a
fixed token table, so the entire re-skin is one colour constant.

---

## 7. Build phases

Each phase ships something usable on its own.

**Phase 1 — biometrics.** App shell off the Ultron baseline, Health Services
passive collection, POST to the existing `/health/ingest`, FCM registration and
wake. *Zero backend changes.* Fixes the stated pain first and is the lowest-risk
port. Ends when `health_data(live=true)` is answered by the watch in seconds.

**Phase 2 — training backend.** Catalog vendored and indexed, `/training/*`
endpoints, plan and log models, `set_training_plan` skill, Training Protocol
amendments. Testable end-to-end with `curl`, no watch required.

**Phase 3 — training UI.** The mockup: session card, exercise cards with GIF and
two buttons, summary, offline queue. The riskiest UI work, on top of two layers
already proven.

**Phase 4 — parity and configuration.** Plan surface in Heartbreaker, Striker and
Speda GO; every interval and budget from §3.3 wired into the config schema *and*
its settings UI. Under `CLAUDE.md` this is part of the work, not a follow-up.

---

## 8. Open risks

**8.1 Media licensing — resolved, see §5.2.** The dataset's `NOTICE.md` puts the
data under MIT and the media under Gym Visual's proprietary terms, carried by
that repo under separate written permission and explicitly requiring downstream
users to obtain their own licence for reuse beyond those terms. Resolution: the
JSON is vendored and attributed, the GIFs are never committed — fetched at
runtime into a git-ignored cache and displayed with
`© Gym visual — https://gymvisual.com/`. Keep it that way; a future convenience
commit of the media directory would republish proprietary work from a public
AGPL repository.

**8.2 Health Services data types are unverified.** §3.1 assumes heart rate and
steps are available as passive types on the target Galaxy Watch. This has not
been checked on hardware. Enumerate first, then write the real list into §3.1.

**8.3 Battery.** Ultron Wear's own README concedes its performance claims are
structural rather than measured — it has never run on a physical device. Atomix
Wear adds continuous passive sensor collection to that unvalidated baseline.
Measure battery over a full day before widening the collection set.

**8.4 Dual-feed during transition.** While both pipes run, the same walk may be
recorded by Samsung and by the watch under different `origin` values, producing
two legitimate rows. Aggregation in `services/health.py` sums per
`(day, metric)`. Confirm the rollup does not double-count before Phase 1 ships,
or scope watch-origin metrics to ones the phone does not also send.

**8.5 `docs/ATOMIX_HEALTH_SYNC.md` is missing.** Thirteen source files across
`igor` and `speda-go` cite it by section number and it is not tracked in git.
Unrelated to this work and found while doing it; per `CLAUDE.md` it gets fixed
after the assigned task, not left noticed-but-untouched.
