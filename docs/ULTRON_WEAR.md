# ULTRON WEAR — academic schedule & attendance on the wrist

Ultron Wear is Ultron Mark III's watch surface: the timetable, and the ledger
that answers the only question that actually matters during a semester —
**kaç ders daha kaçırabilirim?**

Repo: `ultron-core` (package `com.spedatox.ultroncore`, label "Ultron Wear").
Target: Galaxy Watch 6 Classic, Wear OS 4+ (minSdk 33).

---

## 1. The attendance rule

Turkish undergraduate attendance is counted **per ders saati**, not per course
and not per day. A 3-hour Monday course is three independent occurrences a week;
you can attend the 09:00 and miss the 11:00, and the yoklama records exactly one
absence.

```
scheduled  = every teaching hour the term holds        (holidays removed)
effective  = scheduled − hours the instructor cancelled
allowed    = floor(effective × (1 − required_rate))
remaining  = allowed − hours absent
```

Three things about this are load-bearing:

1. **Cancelled ≠ absent.** A cancelled class is the instructor not holding it,
   which removes the hour from the denominator entirely. It is a distinct state
   in the schema, the math, the UI and the wire format.
2. **Cancellations can *cost* you an absence.** 70% of a smaller number is a
   smaller number. Cancel 5 hours out of 42 and the budget drops from 12 to 11.
   Counter-intuitive, correct, and covered by
   `test_enough_cancellations_shrink_the_budget`.
3. **`floor`, never `round`.** 42 hours × 0.30 = 12.6 → **12**. Rounding up
   would hand the owner a spare absence he does not have, on the one question
   where being wrong costs a course.

Defaults are 14 weeks / 70%, both configurable per term.

The math is implemented **twice** — `app/services/academic.py` on the server and
`data/AttendanceCalculator.kt` on the watch — because the watch must produce a
verdict with no network. The two must agree; the server's version is the one
under test (`tests/test_attendance.py`, 14 cases).

---

## 2. The pipeline

```
        ┌──────────┐   */5 * * * 1-5    ┌───────────┐
        │   n8n    │ ─────────────────► │   Igor    │
        └──────────┘  POST /academic/   └─────┬─────┘
                       ask-pending             │ FCM HTTP v1 (data-only, fid)
                                               ▼
   ┌──────────────────────────────────────────────────────┐
   │  Ultron Wear                                          │
   │   · notification, 3 action buttons: Girdim/Girmedim/  │
   │     İptal oldu → written to a local JSON ledger       │
   │   · local fallback fires 15 min after the bell if no  │
   │     push arrived                                      │
   └───────────────────────┬───────────────────────────────┘
                           │ POST /academic/attendance  (push + pull, 1 trip)
                           ▼
                     ┌───────────┐
                     │   Igor    │ ── check_attendance skill ──► SPEDA / Ultron
                     └───────────┘
```

### Why n8n hits a plain endpoint, not `/trigger/ultron`

n8n remains the sole scheduling organ — it owns the cron. But routing the
per-lecture check through `POST /trigger/ultron` would spend a full agentic turn
every five minutes to discover that no class ended: ~100 LLM calls a day to
answer "no". `POST /academic/ask-pending` is a DB query and a conditional push,
with no reasoning in it, and it follows the precedent already set by the
`DELETE /admin/outputs` cleanup n8n calls on a schedule.

### Why the watch also asks locally

The question fires the moment a lecture ends — precisely when the watch is most
likely to be on a campus wifi with no route out, inside a concrete building, or
in Doze. FCM guarantees "eventually, if reachable"; an attendance question that
arrives four hours late is one you answer from memory, badly.

So every upcoming occurrence gets a local WorkManager job armed for **15 minutes
after the bell** (`FallbackAskScheduler`). If the push landed, the FCM handler
cancels it. If not, the watch asks on its own, offline, from its cached
schedule. Set `FallbackAskScheduler.ENABLED = false` to run FCM-only.

The n8n `window_minutes` (20) must stay **above** the fallback grace (15) or the
watch always wins the race and every push is a duplicate.

---

## 3. Firebase setup (the part only you can do)

FCM HTTP v1 needs a **service account**. This is a third kind of Google
credential, distinct from the two Igor already has:

| Credential | What it authorises | Used for |
|---|---|---|
| `GOOGLE_WORKSPACE_CLIENT_ID/SECRET` | a *user* consenting to share their data | Gmail, Calendar |
| `GOOGLE_MAPS_API_KEY` | a product API, billed per call | Routes, Places |
| **`FCM_CREDENTIALS_FILE`** | **the server acting as itself** | **FCM push** |

Workspace OAuth **cannot** send FCM — it is a user-delegated flow with no
messaging scope. The one thing that *is* reusable: a Firebase project **is** a
Google Cloud project, so add Firebase to the GCP project your Workspace OAuth
client already lives in instead of creating a new one.

The legacy server-key endpoint that needed none of this was decommissioned in
June 2024; v1 with an OAuth2 bearer token scoped to
`https://www.googleapis.com/auth/firebase.messaging` is the only path.

**Steps:**

1. [console.firebase.google.com](https://console.firebase.google.com) → add
   Firebase to your existing GCP project.
2. Add an **Android app** with package name `com.spedatox.ultroncore`.
   Download `google-services.json` → `ultron-core/app/google-services.json`.
   The build detects it and applies the google-services plugin automatically;
   without it the app still compiles and runs, just with push disabled.
3. Project settings → **Service accounts** → Generate new private key.
   Mount the JSON into the Igor container and set:
   ```
   FCM_CREDENTIALS_FILE=/app/secrets/firebase-service-account.json
   ```
   `FCM_PROJECT_ID` is optional — it is read from the file when blank.
4. Import `packages/igor/scripts/n8n/ultron_wear_attendance.json` into n8n, set
   its timezone to `Europe/Istanbul`, activate.

### Registration is by FID, not token

firebase-messaging **25.1.0** deprecated `getToken()`, `deleteToken()` and
`onNewToken()`; the SDK source marks the callback *"@deprecated Use
onRegistered(String) instead"*. The Admin SDKs followed — `Message(token=…)`
now raises a `DeprecationWarning` and `Message(fid=…)` is the supported target.

Ultron Wear therefore registers its **Firebase Installation ID** via
`onRegistered` / `FirebaseInstallations.getInstance().id`, stores it in
`devices.fid`, and `services/fcm.py` puts it in the `fid` field. Tokens still
work today; building a new integration on them would have scheduled a migration
for the middle of a semester.

---

## 4. Loading the real schedule

`app/src/main/assets/courses.json` ships **empty** on purpose — an app that
invents "Prof. John Smith" when it has no data is worse than one that says it
has no data. Push the real timetable to Igor:

```bash
curl -X PUT https://your-igor/academic/schedule \
  -H "X-API-Key: $SPEDA_API_KEY" \
  -H "Content-Type: application/json" \
  -d @schedule.json
```

Shape (see `ultron-core/docs/courses.sample.json` for a filled-in example):

```json
{
  "term": {
    "start_date": "2026-09-21",
    "total_weeks": 14,
    "required_rate": 0.70,
    "holidays": ["2026-10-29"]
  },
  "courses": [
    { "id": "phys101_mon_0900", "code": "PHYS101", "name": "Fizik I",
      "instructor": "Dr. R. Wilson", "roomNumber": "C-310",
      "dayOfWeek": "MONDAY", "startTime": "09:00", "endTime": "09:50" }
  ]
}
```

- `start_date` is the **Monday of week 1**.
- **One entry per teaching hour.** Three hours = three entries.
- Entries of the same subject **must share `code`** — that grouping is what the
  attendance budget is computed against.
- `id` must be stable: it is the key the ledger joins on. Renaming an id orphans
  that slot's history.

---

## 5. Endpoints

All require `X-API-Key` (Rule 12). `ask-pending` additionally requires
`X-N8N-Secret`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/academic/schedule` | Timetable + term, for the watch to cache |
| `PUT` | `/academic/schedule` | Replace timetable + term |
| `POST` | `/academic/attendance` | Bidirectional ledger sync |
| `GET` | `/academic/attendance/summary` | Per-subject verdicts |
| `POST` | `/academic/ask-pending` | n8n: push the question if a lecture just ended |
| `POST` | `/devices/register` | Store a device's FID |

### Sync semantics

Identity is `(slot_id, date)`; conflicts resolve **last-write-wins on
`recorded_at`** (epoch millis from the recording device). The watch re-sends
records whose POST failed, so ingest must be idempotent — it is, and
`test_reingesting_the_same_occurrence_does_not_double_count` holds it there. A
stale record arriving late never clobbers a newer answer.

---

## 6. Skills

| Skill | Agent | Notes |
|---|---|---|
| `check_attendance` | all | Read-only (Rule 9). "Kaç hakkım kaldı" — call before advising him to skip anything. |
| `ask_attendance` | all | Manual re-send of a missed question; the routine path is the n8n endpoint. |
| `send_push_notification` | all | Now really implemented. Was a stub returning *"Push notification delivery not yet configured."* |

---

## 7. Design language

Ultron Wear runs the Speda (Heartbreaker) design language, ported from
`packages/speda-go/designsystem`: the same `ThemeEngine`, one accent
in → the whole palette out, re-hued. Ultron's accent is **`#8a93a6`** from
`Brands.kt`, hue ≈ 221°, so the structural tokens land in cool blue-slate.

Two deliberate deviations, both documented in the source:

- **No backdrop blur.** The web/phone glass is `blur(28px) saturate(140%)`. On
  the Exynos W930 that forces an offscreen layer and a per-frame RenderEffect in
  a scrolling list — to blur *pure black*, which has nothing to refract. What
  survives is the occluding fill + milky tint + 1px rim, which is the blur-less
  fallback Heartbreaker's own CSS already sanctions.
- **No ambient blobs, no body gradient.** The phone port already flattens the
  160° gradient to `#000000` because an OLED pixel at true black is switched
  off. On a watch with a tenth the battery that argument only gets stronger.

Type is the real two-family split: **Rajdhani** for HUD chrome (caps labels, day
headers, numerals) and **Inter** for anything the eye has to read (course names,
prompt copy). Sizes are re-derived for a 1.5" display, not scaled by a constant.
