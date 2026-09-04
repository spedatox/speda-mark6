# Atomix Wear

The watch surface of **Atomix**, the owner's health and training agent. Reads the
wrist's own sensors and posts them straight to Igor, deleting the phone from the
health pipeline; in a later phase it also renders the training session Atomix
planned, one exercise at a time.

The design and its reasoning live in [docs/ATOMIX_WEAR.md](../../docs/ATOMIX_WEAR.md).
This file covers building and running it.

> **Status: Phase 1, not yet compiled.** The module has never been built — there
> is no JDK or Android SDK on the machine it was written on. It is structurally
> complete and CI (`.github/workflows/atomix-wear-ci.yml`) will be the first
> thing to actually compile it. Treat every claim below as intent until that
> workflow is green. See [Known unknowns](#known-unknowns).

---

## Contents

- [What Phase 1 does](#what-phase-1-does)
- [Build setup](#build-setup)
- [Project layout](#project-layout)
- [How data flows](#how-data-flows)
- [Known unknowns](#known-unknowns)

---

## What Phase 1 does

Collects heart rate, steps, distance and resting heart rate from Health Services
passive monitoring, queues them durably, and uploads to Igor's existing
`POST /health/ingest`.

**No backend change was required.** The endpoint is metric-generic and idempotent
on `(metric, start_ts, origin)`, so the watch feeds it under
`origin = "atomix-wear"` alongside whatever Speda GO is still sending. Sleep,
weight and body composition stay on the phone pipe — the watch does not derive
them.

The training client (the GIF-per-exercise session UI) is Phase 3 and is not in
this module yet. The current screen reports whether collection is running, and
nothing else.

---

## Build setup

### Requirements

- JDK 17
- Android SDK 36
- A Wear OS 4+ target (API 33+)

### `local.properties`

```properties
sdk.dir=C:\\Users\\<user>\\AppData\\Local\\Android\\Sdk

# Optional. Omit both for a collect-only build that never uploads.
IGOR_BASE_URL=https://<igor-host>
IGOR_API_KEY=<the X-API-Key value>
```

Injected into `BuildConfig` at build time and git-ignored; no credential is
committed. A build without them still collects and queues — it simply cannot
upload, and says so on screen rather than pretending the link is up.

### Firebase (strongly recommended)

Place `google-services.json` in `app/`. Gradle detects the file and applies the
`google-services` plugin conditionally, because that plugin hard-fails when the
file is absent and would otherwise stop anyone compiling the module.

This is not optional decoration. FCM is what lets Igor **wake the watch** for a
`live=true` health query. Without it the app falls back to a fifteen-minute
demand poll — still better than the phone, but not the point.

### Building

```bash
./gradlew :app:assembleDebug
```

Run it from `packages/atomix-wear`; this is a self-contained Gradle build, not a
module of a root project.

---

## Project layout

```
app/src/main/kotlin/com/spedatox/atomixwear/
├── AtomixWear.kt                    Application, object graph
├── data/
│   ├── HealthDtos.kt                Wire format — mirrors igor/app/schemas/health.py
│   ├── IgorClient.kt                REST transport (HttpURLConnection)
│   └── SampleQueue.kt               Durable pending-upload buffer
├── health/
│   ├── BiometricSource.kt           Health Services passive registration
│   ├── BiometricListenerService.kt  Batched delivery → DTOs → queue
│   └── BootReceiver.kt              Re-arm collection after reboot
├── sync/
│   ├── HealthSyncWorker.kt          Drains the queue to Igor
│   ├── SyncDemandWorker.kt          Polls /health/sync-demand (FCM fallback)
│   ├── SyncMessagingService.kt      FCM wake
│   └── SyncScheduler.kt             Every trigger, in one place
└── presentation/
    └── MainActivity.kt              Permissions + Phase 1 status screen
```

---

## How data flows

```
Health Services (passive, batched)
    → BiometricListenerService      persist FIRST, upload second
    → SampleQueue                   atomic JSON, survives reboot and no-network
    → HealthSyncWorker              drains in batches of 2,000
    → POST /health/ingest
```

Three things upload: an FCM wake (expedited — someone is waiting), an
opportunistic nudge when new data lands, and a 30-minute trickle so a watch that
is never pushed still drains.

**The retry contract:** rows leave the queue only after Igor returns 2xx. With
the server's unique constraint, a failed upload costs a re-send, never a
reading.

---

## Known unknowns

Listed because they are genuinely unverified, not as boilerplate.

- **Never compiled.** See the status note above.
- **Health Services versions are guessed.** `healthServices` and `healthConnect`
  in `gradle/libs.versions.toml` are unverified pins. Resolve them against
  current stable on first build.
- **Passive data types are assumed.** `BiometricSource.wanted` requests
  `HEART_RATE_BPM`, `STEPS`, `DISTANCE` and `RESTING_HEART_RATE`. Which of these
  a Galaxy Watch actually exposes passively must be read from
  `getCapabilitiesAsync()` at runtime — the code already intersects against it,
  but the *expected* set is a guess. `RESTING_HEART_RATE` may be Samsung-derived
  with no passive type behind it.
- **Battery is unmeasured.** Continuous passive collection on a watch that has
  never been profiled. Measure across a full day before widening the set.
- **Double-counting is unconfirmed.** While both pipes run, the same walk can be
  recorded by Samsung and by the watch under different `origin` values, which is
  two legitimate rows. Confirm `services/health.py` aggregation does not
  double-count before relying on daily totals.
- **The launcher icon is a placeholder.** Replace `ic_launcher_foreground.xml`
  with the real mark from `logos/atomix.svg`.
