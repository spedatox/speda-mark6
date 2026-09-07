# Striker

The single-agent public build — Speda only, no roster, no switcher, no House Party. A calmer, consumer-facing surface over the same backend.

---

## Contents

- [What it is](#what-it-is)
- [Directory structure](#directory-structure)
- [Dev workflow](#dev-workflow)
- [Profile system](#profile-system)
- [Surviving a dropped connection](#surviving-a-dropped-connection)
- [Known dead code](#known-dead-code)
- [Configuration](#configuration)

---

## What it is

Striker was produced by manually copying Heartbreaker's source tree and stripping it down. There is no scripted fork process, and there isn't going to be one — if a third variant is ever needed, it'll be another manual copy, same as this one. Don't go looking for fork tooling; it doesn't exist.

---

## Directory structure

Same `electron-vite` layout as Heartbreaker (`main/`, `preload/`, `renderer/src/{components,lib,profile,store,theme}`), no `teaser/` build. `profile/` carries the same four files as Heartbreaker, but `index.ts` hardcodes a single profile instead of selecting from a roster (see below).

---

## Dev workflow

```bash
# from packages/striker
npm run dev
npm run build
npm run typecheck    # single tsconfig, unlike Heartbreaker's split node/web configs
npm run web:dev
npm run dist          # electron-vite build + electron-builder --win
```

From the repo root: `npm run striker:dev`, `:build`, `:typecheck`, `:web:dev`, `:web:build`, `:dist` — unlike Heartbreaker, `striker:dist` is exposed at the root. There's no `build-app.ps1` equivalent for Striker; baking a server URL and key into a packaged build means setting `MAIN_VITE_SPEDA_API_BASE`/`MAIN_VITE_SPEDA_API_KEY` manually before running `dist`.

---

## Profile system

`profile/index.ts` hardcodes a single `AppProfile` — Speda, with its own accent color and product name — directly in the file, with no environment-variable selection. There is no roster and no switcher by design.

---

## Screen lock

Same as Heartbreaker, from the same source: `LockScreen` + `LockScreensaver` driven by `lib/useScreenLock.ts`, configured under Settings → Interface. `Ctrl+L` locks, the lock can be raised on launch, and an idle stretch raises it by itself; the passcode lives as a SHA-256 in `localStorage`. The screensaver parades agent cards the same way Heartbreaker's does — Core just has one agent, so it is a roster of one. Speda GO is deliberately excluded — this is a desktop-client feature.

---

## Surviving a dropped connection

A turn runs **detached** on the backend (`app/core/turn_runner.py`) — the HTTP
response is only a subscriber, and dropping it never cancels the run. So a
dropped socket is a reconnect, not a failed answer. The client mints the turn's
`request_id` itself and sends it in the `POST /chat/{agent}` body (so the turn is
recoverable from the instant Send is pressed, before any `start` event has
arrived), re-attaches on `GET /chat/attach/{request_id}` when the stream drops,
and checks `GET /chat/active` before ever telling the owner the backend is
unreachable. Attach replays the whole event buffer, so a reconnect rewinds the
bubble and lets the replay rebuild it.

Reconnect count and give-up timeout are owner settings, not constants. Full
explanation: [HEARTBREAKER.md](../heartbreaker/HEARTBREAKER.md#surviving-a-dropped-connection).

---

## Known dead code

Striker was diverged by hand rather than generated, and it shows: several components and modules from Heartbreaker are still present in the source tree but unreachable from any UI path.

- `CommsTray.tsx` and `profile/warroom.ts`'s war-room profile — present, compile, never imported by anything that renders.
- `profile/brands.ts` — still carries the full eight-persona map from Heartbreaker; nothing in Striker reads it.
- `ConnectionSetupModal.tsx` and `lib/connection.ts` — copied over, but Striker's `App.tsx` reads config directly from `window.api.getConfig()` and never calls into either of them. Note also that Striker's main/preload process doesn't expose `setConfig` at all, so even if this path were wired up it wouldn't persist anything.
- `theme/heartbreaker.css`, `theme/speda.css`, `theme/base.css` — unused; only `theme/striker.css` is actually imported.

None of this is load-bearing. It's cleanup, not a bug — safe to delete in a future pass, but harmless as-is since it's never reached.

---

## Configuration

There's no `connection.json` persistence and no in-app way to change the server URL or key after packaging. `App.tsx` resolves the connection once at boot — from `window.api.getConfig()` on Electron, or `VITE_API_BASE`/`VITE_API_KEY` with a local fallback on the web build — and that's final for the session. Same `X-API-Key` auth scheme as every other client.
