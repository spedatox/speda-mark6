# ATOMIX HEALTH SYNC — Samsung Health → Igor → Atomix

**Goal:** Atomix (the owner's personal-health agent) sees the owner's real
biometrics — steps, sleep, heart rate, exercise, body composition — collected by
Samsung Health on the owner's phone/watch, synced to Igor, and exposed as a
Tier 1 Skill. Ambient, automatic, opt-in per data type, and invisible after the
one-time setup.

**Status:** **steps 1–2 implemented** (backend: model, service, endpoints,
`health_data` skill — see §4). Steps 3–6 (the Android courier, the n8n digest)
are still design-only. Builds on the Heartbreaker Core client
(`packages/heartbreaker-android`) and the existing skill/registry architecture.
Nothing here changes the orchestrator (CLAUDE.md Rule 5).

---

## 0. The one decision that shapes everything: Health Connect, not the Samsung SDK

There are two ways to read Samsung Health data:

| Path | Verdict |
|---|---|
| **Samsung Health Data SDK** | ❌ Requires a Samsung partner application/approval, ships as a Samsung-only AAR, and couples us to Samsung's release cadence. Built for commercial partners, not personal tools. |
| **Health Connect** (`androidx.health.connect:connect-client`) | ✅ Android's system health-data store. **Samsung Health already syncs its data into Health Connect** (One UI 5.1+; system-integrated since Android 14). Standard Jetpack client, standard system permission sheet, no partnership, works identically for Galaxy Watch data (it lands in Samsung Health → Health Connect). |

So the pipeline never talks to Samsung at all. Samsung Health remains the
*collector* (phone sensors + Galaxy Watch); Health Connect is the *system
mailbox* we read; Heartbreaker Droid is the *courier*; Igor is the *warehouse*;
Atomix is the *analyst*.

This also future-proofs the pipe: if the owner ever switches to Google Fit,
Garmin, Withings, or manual entry apps, they all write to the same Health
Connect records — the entire pipeline below works unchanged.

**Compatibility note:** minSdk 31 (project baseline) is fine — on Android 13
and below Health Connect is a Play-installable APK; on 14+ it's part of the OS.
The owner's device runs One UI with Samsung Health ↔ Health Connect sync
available under Samsung Health ▸ Settings ▸ Health Connect (one-time enable).

---

## 1. The data journey (UX-first)

The guiding principle: **one honest consent moment, then silence.** Health data
is the most sensitive stream in the system — the UX must make consent explicit
and legible, then never nag again. After setup the owner should *forget the
pipeline exists* until Atomix casually says "your sleep debt is stacking up".

### 1.1 Setup journey (one time, ~30 seconds)

```
Settings ▸ Interface? No — Settings gets a new tab: HEALTH  (Atomix-green accents)

┌──────────────────────────────────────────────┐
│ ATOMIX HEALTH LINK                           │
│                                              │
│ Sync Samsung Health to Atomix       [toggle] │
│ Steps · sleep · heart rate · exercise ·      │
│ weight — read from Health Connect and        │
│ synced to your backend. Atomix reads it;     │
│ nothing leaves your server.                  │
│                                              │
│ DATA TYPES                                   │
│  [✓] Steps & distance    [✓] Sleep           │
│  [✓] Heart rate          [✓] Exercise        │
│  [✓] Weight & body comp  [ ] Blood oxygen    │
│                                              │
│ LAST SYNC   today 14:32 · 6 records          │
│ [SYNC NOW]              [DISCONNECT + WIPE]  │
└──────────────────────────────────────────────┘
```

1. Owner flips the toggle → **Health Connect's own system permission sheet**
   appears (not a custom dialog — the OS sheet is the trust anchor; it lists
   exactly which record types Heartbreaker requests, read-only).
2. On grant → immediate **30-day backfill** with a progress line
   ("Backfilling… 23 days"), so Atomix is useful on day one, not day thirty.
3. Done. A `LAST SYNC` line is the only permanent UI footprint.

Per-type checkboxes map 1:1 to Health Connect permissions — unchecking a type
revokes nothing at the OS level but stops reading + syncing it (and `DISCONNECT
+ WIPE` calls the backend delete endpoint, then drops the local sync token).

**First-run nudge (gentle):** when the owner first opens *Atomix* in the app
(not SPEDA — the moment intent is obvious), a one-time dismissible banner:
"Atomix can read your Samsung Health data — set up in Settings ▸ Health." No
launch-time popups; health consent must never be an interruption.

### 1.2 Steady-state journey (invisible)

```
Samsung Health / Galaxy Watch
        │  (Samsung's own background sync)
        ▼
Health Connect (on-device system store)
        │  WorkManager periodic sync, ~every 4h + on app open
        │  Changes API differential token → only NEW/CHANGED records
        ▼
Heartbreaker Droid  ── POST /health/ingest (X-API-Key, batched JSON) ──►  Igor
                                                                           │
                              ┌────────────────────────────────────────────┤
                              ▼                                            ▼
                    health_samples / health_daily              nightly n8n trigger →
                    (SQLite, per-metric rows)                  Atomix digest → memory
                              ▲
                              │  health_data skill (Tier 1, read-only)
                              ▼
                           ATOMIX
```

- **WorkManager** `PeriodicWorkRequest` (~4h, `requiresBatteryNotLow`, backoff
  on failure) + an opportunistic sync on app foreground. No foreground service,
  no persistent notification — this is a trickle, not a stream.
- **Differential sync:** Health Connect's Changes API hands us a token; each
  sync reads only records changed since the last token. First sync = 30-day
  backfill via time-range reads. Token stored in DataStore; token expiry
  (HC invalidates after ~30 days idle) → transparent re-backfill.
- **Offline-tolerant:** failed POST → keep the token un-advanced, retry next
  cycle. Records are idempotent upserts server-side (unique on
  `(metric, start_ts, origin)`), so re-sends are harmless.
- **Battery honesty:** reads are local IPC (cheap); one small HTTPS POST per
  cycle. Target: unmeasurable battery impact.

### 1.3 What Atomix does with it (the payoff)

- **Pull:** the owner asks anything — "how did I sleep this week?", "am I
  moving less than last month?" — Atomix calls `health_data` and answers with
  real numbers, trends, and its evidence-based coaching voice.
- **Ambient:** the nightly digest (n8n-triggered, Rule: n8n owns ALL
  scheduling) writes a compact rolling summary into Atomix's source-of-truth
  memory file — so even without a tool call, Atomix's baseline awareness
  ("owner averages 6.2h sleep, trending down") rides its prompt via the
  existing memory system.
- **Proactive:** the digest turn runs with the existing automations machinery;
  Atomix can flag anomalies through the normal notification path (push /
  Telegram via `output_mode`), e.g. resting HR elevated 3 days running.

---

## 2. Android implementation (`packages/heartbreaker-android`)

New package: `app/src/main/kotlin/com/speda/heartbreaker/health/`

| File | Responsibility |
|---|---|
| `HealthSyncManager.kt` | Orchestrates: permission state, backfill, differential sync, token bookkeeping. The only class that touches the HC client. |
| `HealthConnectSource.kt` | Thin wrapper over `HealthConnectClient` — availability check, permission set, `readRecords`/`getChanges` per enabled type. |
| `HealthSyncWorker.kt` | `CoroutineWorker` — calls `HealthSyncManager.sync()`; scheduled from `AppGraph` when the toggle is on. |
| `HealthDtos.kt` | Wire DTOs mirroring the backend schema (§3.1). |
| `ui/settings/HealthTab.kt` | The Settings ▸ HEALTH tab (§1.1) — toggle, type checkboxes, last-sync line, SYNC NOW, DISCONNECT + WIPE. Atomix-green tint via the existing brand table. |

- **Dependency:** `androidx.health.connect:connect-client` (stable). No Google
  Play Services, no Samsung SDK.
- **Manifest:** `<uses-permission android:name="android.permission.health.READ_STEPS"/>`
  etc. (one per record type), plus the `ACTION_SHOW_PERMISSIONS_RATIONALE`
  activity-alias Health Connect requires (a simple composable stating the §1.1
  privacy sentence).
- **Settings keys** (DataStore, joins the existing `SettingsStore`):
  `health_enabled`, `health_types` (set), `health_changes_token`,
  `health_last_sync`, `health_backfill_done`.
- **Record types, v1:** Steps, Distance, SleepSession (+stages), HeartRate
  (sampled), RestingHeartRate, ExerciseSession, Weight, BodyFat, OxygenSaturation
  (off by default). Everything else is a checkbox away later — the schema is
  metric-generic.

**UX details that matter:**
- HC not installed / Samsung sync off → the toggle explains and deep-links
  (`Intent` to HC onboarding / Play listing) instead of failing silently.
- Permission partially granted → checkboxes reflect reality (OS is truth).
- `SYNC NOW` gives immediate feedback ("14 records → Igor") — the only place
  the pipeline is ever visible on demand.

---

## 3. Backend implementation (`packages/igor`)

### 3.1 Ingestion — `POST /health/ingest`

Router stays logic-free (Rule 1): validate schema → hand to
`services/health.py` → return counts.

```jsonc
// POST /health/ingest   (X-API-Key)
{
  "device": "Galaxy S24 Ultra",
  "samples": [
    { "metric": "steps",       "start": "2026-07-18T00:00:00+03:00", "end": "…T23:59:59+03:00", "value": 8412,  "unit": "count",  "origin": "com.sec.android.app.shealth" },
    { "metric": "sleep_session","start": "…T23:41:00+03:00", "end": "…T06:12:00+03:00", "value": 391, "unit": "min",
      "detail": { "stages": { "deep": 74, "rem": 88, "light": 201, "awake": 28 } } },
    { "metric": "heart_rate",  "start": "…T14:00:00+03:00", "end": "…T14:00:00+03:00", "value": 61, "unit": "bpm" }
  ]
}
// → { "accepted": 3, "duplicates": 0 }
```

- **Model** `app/models/health_sample.py`: `id, metric, start_ts, end_ts, value
  (float), unit, detail (JSON), origin, device, created_at`. Unique index
  `(metric, start_ts, origin)` → idempotent upserts.
  - *As built, one addition:* a `day` column holding the owner's **local**
    calendar date, derived from the offset the phone sends and stored alongside
    the UTC-naive timestamps. UTC alone cannot recover it — a 00:30 +03:00
    bedtime is 21:30 UTC the *previous* day, which would file a late-night walk
    or a sleep session under the wrong date in every rollup.
  - *As built:* metrics roll up by family (`cumulative` sums, `duration` sums +
    longest, `instant` min/max/avg/last). An **unrecognised metric defaults to
    `instant`**, never to a sum — inventing a total for a metric we don't
    understand would read as authoritative and be meaningless.
- **Service** `app/services/health.py`: upsert batch; maintain a `health_daily`
  rollup table (`date, metric, agg json`) updated on ingest — the skill answers
  90% of questions from dailies without scanning raw samples.
- **Endpoints:** `POST /health/ingest`, `GET /health/status` (last sync, counts
  — feeds the Settings tab), `DELETE /health/data` (the WIPE button; full
  truncate, logged).
- Auth: standard `AuthMiddleware` X-API-Key (Rule 12). No new auth surface.

### 3.2 The skill — `app/skills/health_data.py` (Tier 1)

```python
class HealthDataSkill(Skill):
    name = "health_data"
    read_only = True          # retrieval → parallel-safe (Rule 9)
    description = (
        "Query the owner's synced Samsung Health biometrics: steps, distance, "
        "sleep sessions with stages, heart rate, resting heart rate, exercise "
        "sessions, weight and body composition. Use it whenever the owner asks "
        "about their sleep, activity, fitness trends, or when health context "
        "would ground your coaching in real numbers. Do NOT use it for medical "
        "diagnosis, for system/server health (that is Orion's domain), or when "
        "the owner asks about anyone else's health. Returns daily aggregates or "
        "raw samples for a metric over a date range, plus period-over-period "
        "trend deltas, as compact JSON."
    )
    input_schema = {  # metric, range ("today", "7d", "30d", ISO dates), granularity
        ...
    }
```

- Registered at startup with every other Tier 1 skill; **not** restricted to
  Atomix (`restricted_to=None`) — SPEDA may legitimately relay ("ask Atomix
  about my sleep" shouldn't require a dispatch just to read a number), and the
  roster is trusted (single-owner system). Revisit if sensitivity demands
  `restricted_to={"atomix"}`.
- Returns dailies by default; raw samples only when granularity asks — keeps
  tool results small and cache-friendly.

### 3.3 Ambient digest (n8n-owned, per CLAUDE.md)

- n8n nightly cron → existing `POST /trigger/atomix` with
  `{"type": "cron", "job": "health_digest", "output_mode": "silent"}`.
- The digest turn instructs Atomix to call `health_data` (7d/30d), then update
  its **source-of-truth memory file** through the existing memory tools — a
  bounded `## Health baseline` section (rolling averages, trend arrows, last
  update stamp). Memory architecture (docs/MEMORY_ARCHITECTURE.md) already
  carries this into every Atomix prompt.
- Anomaly worth waking the owner → the same turn uses the notification skill
  with `output_mode="push"` semantics via a follow-up n8n `push` trigger. No
  new plumbing.

### 3.4 What we deliberately do NOT do

- **No backend scheduler** — n8n owns cadence (CLAUDE.md, non-negotiable).
- **No health data in the system prompt** — only the digest's compact baseline
  reaches the prompt via memory; raw metrics stay behind the tool.
- **No third-party export** — data flows device → owner's server, nothing else.
- **No write-back to Health Connect** in v1 (Atomix logging workouts/weight for
  the owner is a natural v2; requires write permissions and a confirm-first UX).
- **No iOS/HealthKit** — out of scope with no iOS client planned.

---

## 4. Build order

| Step | Piece | Depends on |
|---|---|---|
| ✅ 1 | Model + service + `/health/ingest`,`/status`,`/data` endpoints | nothing |
| ✅ 2 | `health_data` skill + registry entry | 1 |
| 3 | Android `HealthConnectSource` + `HealthSyncManager` + backfill | 1 |
| 4 | `HealthSyncWorker` + Settings ▸ HEALTH tab | 3 |
| 5 | n8n nightly digest workflow + memory-baseline prompt | 2 |
| 6 | Polish: WIPE flow, token-expiry re-backfill, status surfacing | 3–5 |

Steps 1–2 are backend-only and shippable alone (the skill just returns "no data
synced yet" — which Atomix already handles gracefully by asking the owner to
set up the link). Steps 3–4 light the pipe. Step 5 makes it ambient.

## 5. Verification

1. **Unit:** ingest idempotency (double-POST same batch → `duplicates`), daily
   rollup math, skill range parsing.
2. **Device:** enable sync on the phone → confirm HC permission sheet lists
   exactly the checked types → `SYNC NOW` → `GET /health/status` shows counts.
3. **End-to-end:** ask Atomix "how did I sleep last night?" on the phone →
   watch a `health_data` tool call in the feed → answer cites real stages.
4. **Ambient:** fire the n8n digest manually → Atomix memory file gains the
   baseline section → next-day prompt contains it (verify via prompt debug).
5. **Privacy:** DISCONNECT + WIPE → `/health/status` shows zero; HC permissions
   revoked from the system sheet stop the worker without error spam.
