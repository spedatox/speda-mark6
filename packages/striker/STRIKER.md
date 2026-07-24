# Striker — SPEDA Mark VI Core

**Codename: Striker. Product name: "SPEDA Mark VI Core".** This is the
single-agent, public/"lite" build of SPEDA — the client you hand to people who
want to try SPEDA without exposing the owner's private roster (the Superior Six).

It is the sibling of [`packages/heartbreaker`](../heartbreaker/HEARTBREAKER.md):
**same app, same backend (Igor), same features on the chat surface** — but a
**single agent (SPEDA)** and a **calmer, simplistic theme** instead of the full
Stark fluid-glass command deck.

> **Lineage.** Striker began as `packages/desktop` (the old neutral template).
> It was brought up to Heartbreaker's feature generation and re-themed. It is no
> longer a "never-themed base" — it is a real product with its own look.

---

## What's the same as Heartbreaker

The whole chat surface is a near-identical port: streaming answers, tool-call
disclosure, rich blocks (code, charts, calendars, maps, files), image/document
attachments, the JARVIS welcome remark, offline transcript cache, session
survivability (re-attach/cancel), the Systems Board telemetry, Settings
(configuration, connections, automations, model routing), and the mobile drawer
layout. It talks to Igor over the same HTTP+SSE surface and owns **zero** business
logic.

Fonts and the **frosted-glass material** are kept. So is the ambient
`NeuralBackground` (retoned neutral).

## What's different (single-agent + simplistic)

- **One agent: SPEDA.** No agent switcher, no roster, no war room / House Party
  Protocol, no inter-agent comms tray, no Forge link. `config.agentId` is fixed
  to `"speda"`, so every call targets `/chat/speda`.
- **Simplistic theme** (`src/renderer/src/theme/striker.css`): the Stark
  *theatrics* are stripped — HUD frame, corner brackets / ruler ticks / scanlines
  / etched seams, and the neon bloom. Corners are **rounded**. The palette is
  **neutral dark with cyan `#36abca` as an accent only** (no petrol-cyan wash
  through the backgrounds). The frosted glass and fonts remain.

---

## Running

```bash
npm run striker:dev       # Electron app
npm run striker:web:dev   # browser-only dev build
```

Config (API base + key) comes from the Electron main process (`get-config` IPC)
with a `http://localhost:8000` / `dev-key` default, so local dev needs no setup.
`MAIN_VITE_SPEDA_API_BASE` / `MAIN_VITE_SPEDA_API_KEY` bake a server URL + key
into a packaged build (`npm run dist`).

`speda.ps1` and `build-app.ps1` at the repo root still drive **Heartbreaker** —
that remains the owner's primary client. Striker is built/run on demand.

---

## Architecture at a glance

```
src/
├── main/        Electron main — window, IPC (get-config, window controls, open-external)
├── preload/     the contextBridge api surface
└── renderer/    the React app — components, lib (api client, hooks), store
                 (chat + settings reducers), profile (single SPEDA profile), theme (striker.css)
```

The renderer never reaches Igor from components — everything goes through
`src/renderer/src/lib/api.ts`. State is two reducers in
`src/renderer/src/store` (chat, settings), with a per-session transcript mirror
in `store/messageCache.ts`.
