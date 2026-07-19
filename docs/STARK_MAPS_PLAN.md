# Stark Maps & Navigation — Implementation Plan

> **Status (2026-07-19): Phase 1 IMPLEMENTED (backend + Android).** Shipped:
> `app/skills/navigation.py` (get_route/find_places, API-key auth, registered in
> main.py, 7 tests green); `google_maps_api_key` + `owner_home_*` config;
> client-location plumbed onto `context.extra`; the `map` fence prompt contract in
> `06_visual_output.md`; Android `MapSpec`/`Polyline` (decoder verified against
> Google's reference vector), `MapBlock` (MapLibre + `map_style_stark.json`,
> gesture-locked glance card, NAVIGATE/OPEN-IN-MAPS handoff, autoNavigate countdown
> gated to live messages), and fence dispatch in `Prose.kt`. **Also completed the
> half-done inline rendering: Android now renders `svg` fences** (`SvgBlock`, AndroidSVG)
> at parity with desktop. **Pending:** desktop `MapBlock.tsx` (Phase 2 — the fence
> degrades to a code block there until then), and the owner must create the Google
> Maps API key. **The key is set from Heartbreaker → Settings → Configuration →
> "Maps & Navigation"** (a `config_schema.py` group; the Android + desktop config
> tabs render it automatically), persisted server-side to the managed `.env` and
> live-applied (the skill reads it lazily — no restart). Server-side only; the phone
> never holds it.


**Goal:** Heartbreaker (Android first) renders inline, Stark-styled maps in chat instead of
SPEDA reciting coordinates. SPEDA can compute routes A→B with live traffic, compare travel
options, show them on the map, and hand off to Google Maps turn-by-turn navigation with one
tap.

**Verdict up front: yes, this fits the system with zero core changes.** The two rails it
rides on already exist:

1. **Location in:** `PlatformContextProvider.snapshot()` already ships `ClientContext.location`
   (lat/lng/accuracy/place) with every turn, and `core/surface.py:40-45` already injects it
   into the system prompt. SPEDA knows where the owner is.
2. **Widgets out:** the client already has a fence-widget pipeline — ```` ```chart ```` →
   `ChartBlock`, ```` ```calendar ```` → `CalendarBlock` — dispatched in `Prose.kt` (`Fence()`).
   A map is the third fence type: ```` ```map ````.

The only genuinely new machinery is (a) one Tier-1 backend skill that talks to a routing API
(traffic-aware), and (b) a `MapBlock` composable that renders MapLibre tiles under the Stark
FUI chrome. Everything else is contract plumbing.

---

## 0. Architecture Decisions (locked before code)

### D-M1 — Map renderer: MapLibre GL Native (Android SDK)

| Option | Verdict |
|--------|---------|
| **MapLibre GL Native** (`org.maplibre.gl:android-sdk`) | ✅ **Chosen.** Open-source Mapbox fork. Vector tiles + a *style JSON we fully own* — the entire map (land, water, roads, labels) restyles to the Stark dark/teal look. No Google Play Services. Compose-interop via `AndroidView`. |
| Google Maps Compose | ❌ Needs Play Services + billing-enabled key on-device; dark styling is limited JSON theming; contradicts the deliberate no-Play-Services stance in `PlatformContext.kt`. |
| osmdroid | ❌ Raster tiles only — cannot be restyled to Stark; would look like OpenStreetMap pasted into the HUD. |
| Static map images (backend renders PNG) | ❌ No pan/zoom, no smooth route drawing, heavier per-message payloads. Keep as a distant fallback only. |

**Tiles:** vector tiles from **OpenFreeMap** (`https://tiles.openfreemap.org/planet` — free, no
API key, production-tolerated) as the default source, with an optional **MapTiler** key
(`MAPTILER_API_KEY` in client settings) as the paid/reliable upgrade path. The style JSON is
ours either way; only the `sources` block differs.

### D-M2 — Routing + traffic engine: Google Routes API v2 (backend-side)

| Option | Traffic | Verdict |
|--------|---------|---------|
| **Google Routes API v2** (`routes.googleapis.com/directions/v2:computeRoutes`) | ✅ live (`TRAFFIC_AWARE_OPTIMAL`) | ✅ **Chosen.** Traffic-aware durations, up to 3 alternative routes, encoded polylines, per-mode (drive/walk/transit/two-wheeler), toll info. Free tier: 10k calls/mo. One REST call, API key auth — no SDK needed. |
| OSRM / OpenRouteService | ❌ none | Good free fallback, but "traffic" was an explicit requirement. |
| TomTom Routing | ✅ | Viable plan B; smaller free tier, second vendor to manage. |
| Mapbox Directions | ✅ | Ties us to Mapbox billing while we deliberately use MapLibre. |

The key lives **backend-side only** (`GOOGLE_MAPS_API_KEY` in `.env` / `config.py`). The
Android app never holds a routing key — it receives finished route geometry inside the fence.
This matches the multi-tenant rule set: capability logic in a skill, client is a dumb renderer.

Same key also unlocks **Places API (New) `searchText`/`searchNearby`** for the "where *can* I
go" half of the request (find nearby cafés/pharmacies/etc. and drop them as markers).

### D-M3 — Delivery contract: a ```` ```map ```` fence, not a new SSE event type

Charts and calendars prove the pattern: the model emits a fenced JSON spec, the client
intercepts the fence and renders a widget, and history replay works for free (the fence is
just message text in the DB). No changes to `SSEEvent`, the orchestrator, or `turn_runner`.
Route geometry uses **Google encoded polylines** inside the spec to keep the fence compact
(a 20 km route is ~1–2 KB encoded vs ~40 KB as a lat/lng array).

### D-M4 — Google Maps handoff: explicit tap, never auto-fire

The map card carries a **`▸ NAVIGATE`** action that fires
`google.navigation:q=<lat>,<lng>&mode=<d|w|b|two-wheeler>` (falling back to the
`https://www.google.com/maps/dir/?api=1&…` universal URL if no handler). "Redirect me
automatically once set" is implemented as: when the user's message is an explicit navigation
command ("take me there", "start navigation"), SPEDA sets `"autoNavigate": true` in the spec
and the client fires the intent **once** on first render of that message — but only for live
(streaming) messages, never on history replay, and only when the app is foregrounded. A
model-triggered app switch is an outward-facing action; the explicit-command gating keeps the
user in control.

### D-M5 — Skill placement & agent scoping

One new Tier-1 skill file `app/skills/navigation.py` (Rule 5: drop a file in `skills/`,
register at startup, orchestrator untouched). Tools are `read_only = True` and
`requires_network = True`. Available to SPEDA (and any profile whose allowlist permits);
no `restricted_to` — nothing privileged about directions.

---

## 1. Backend — `packages/api`

### 1.1 Config (`app/config.py`)

```python
# ── Navigation / Maps ─────────────────────────────
google_maps_api_key: str = ""   # Routes API v2 + Places API (New). Empty = skill degrades to "not configured".
```

Add to `.env.example` with a comment pointing at the Google Cloud console products to enable
(Routes API, Places API (New)).

### 1.2 New skill — `app/skills/navigation.py`

Two tools, both `read_only=True`, `requires_network=True`, descriptions 3–4+ sentences
(Rule 11):

**`get_route`**
```json
{
  "origin":       {"lat": 0.0, "lng": 0.0} | "free-text address (geocoded)",
  "destination":  "… (required)",
  "mode":         "drive | walk | transit | two_wheeler  (default drive)",
  "alternatives": true,
  "departure_time": "ISO-8601 optional — defaults to now (live traffic)"
}
```
Implementation:
- `origin` omitted → resolve from `context` client location (surface already carries it; see
  §1.4 for making it machine-readable).
- Free-text origin/destination → one Geocoding API call each (same key).
- Call `computeRoutes` with `routingPreference=TRAFFIC_AWARE_OPTIMAL`,
  `computeAlternativeRoutes=true`, field mask requesting
  `routes.polyline.encodedPolyline,routes.duration,routes.staticDuration,routes.distanceMeters,routes.legs,routes.routeLabels,routes.travelAdvisory`.
- Return to Claude a **compact JSON string**: per route → distance, live duration,
  no-traffic duration (their delta *is* the traffic story), summary road names, encoded
  polyline, and a pre-built Google Maps deep-link URL. Include origin/destination
  resolved lat/lngs so the model can build the fence without re-asking.
- Timeout ~10 s via the shared `httpx.AsyncClient`; on error return a plain diagnostic
  string (never raise through the loop).

**`find_places`**
```json
{
  "query": "specialty coffee | pharmacy | 24h gas station …",
  "near": {"lat":0.0,"lng":0.0} | null,
  "open_now": true,
  "max_results": 8
}
```
Places API (New) `searchText` with a location bias circle around `near` (default: client
location). Returns name, lat/lng, rating, open-now, address, price level — enough for the
model to drop markers and reason about "best options".

### 1.3 Registration (`app/main.py`)

```python
from app.skills.navigation import GetRouteSkill, FindPlacesSkill
await registry.register_skill(GetRouteSkill())
await registry.register_skill(FindPlacesSkill())
```
Registered in the Tier-1 block alongside the other skills (startup order rule intact).

### 1.4 Client location, machine-readable

`surface.py` currently renders location into prose for the system prompt — good for
awareness, awkward for tool defaults. Add: at context construction, stash the raw
`ClientLocation` on `AgentContext.trigger_payload["client_location"]` (or a dedicated
optional field on `AgentContext` if the dataclass is being touched anyway) so
`get_route`/`find_places` can read exact coordinates without parsing prompt text. No schema
change on the wire — `ChatRequest.client_context` already carries it.

### 1.5 Prompt contract — `app/prompts/core/06_visual_output.md`

New section after Calendar, same voice as the existing contracts:

> ### Maps, locations, routes → use `map` blocks
> When the user asks **where they are, where something is, or how to get somewhere** —
> NEVER answer with raw coordinates. FIRST call `get_route` / `find_places` when routing or
> POI data is needed (a pure "show me where I am" needs no tool), THEN render a **```map**
> block. …

```map
{
  "title": "ROUTE_HOME",
  "center": {"lat": 41.043, "lng": 29.0094},
  "zoom": 12.5,
  "markers": [
    {"lat": 41.043, "lng": 29.0094, "label": "YOU", "kind": "origin"},
    {"lat": 41.1112, "lng": 29.0210, "label": "HOME", "kind": "destination"},
    {"lat": 41.05, "lng": 29.01, "label": "KRONOTROP", "kind": "poi", "subtitle": "4.6★ · open"}
  ],
  "routes": [
    {"polyline": "<encoded>", "label": "VIA D-100", "durationMin": 34, "noTrafficMin": 22,
     "distanceKm": 18.4, "mode": "drive", "primary": true},
    {"polyline": "<encoded>", "label": "VIA COAST", "durationMin": 41, "noTrafficMin": 35,
     "distanceKm": 21.0, "mode": "drive"}
  ],
  "navigate": {"lat": 41.1112, "lng": 29.0210, "mode": "drive", "label": "HOME"},
  "autoNavigate": false
}
```

Contract rules to state in the prompt:
- `center`/`zoom` optional — client auto-fits bounds of markers+routes when absent.
- `marker.kind`: `origin | destination | poi | pin` (drives glyph + colour).
- `durationMin` vs `noTrafficMin` delta is rendered as the traffic readout — always include
  both when the routing tool provided them.
- Mark exactly one route `primary`; alternatives render dimmed.
- `navigate` present ⇒ the NAVIGATE action shows. `autoNavigate: true` **only** when the
  user explicitly commanded navigation this turn.
- Same anti-redundancy rule as calendar: the block IS the answer; one summary sentence above
  it, no coordinate dumps, no markdown list of the same routes.

Also update `skills/skill_docs/inline-rendering` with the full `map` spec, and add one line
to the decision policy: coordinates are never spoken, always mapped.

### 1.6 Backend tests

`tests/skills/test_navigation.py` — mocked `httpx` responses: route happy path, origin
defaulting from client location, geocode failure → diagnostic string, missing key →
"not configured" string, polyline passthrough integrity.

---

## 2. Android — `packages/heartbreaker-android`

### 2.1 Dependencies (`gradle/libs.versions.toml` + `app/build.gradle.kts`)

```toml
maplibre = "11.8.0"          # org.maplibre.gl:android-sdk
```
MapLibre pulls no Play Services. APK impact ≈ 6–7 MB (native lib per ABI — keep the
existing ABI splits if configured).

### 2.2 Domain — `domain/MapSpec.kt`

Mirror of `ChartSpec.kt`'s philosophy: hand-parsed, lenient, half-streamed JSON → `null`
(fence shows as a shimmering "MAP LINK ESTABLISHING…" placeholder until parseable, exactly
how charts behave mid-stream).

```kotlin
data class MapMarker(lat, lng, label?, kind, subtitle?)
data class MapRoute(polyline, label?, durationMin?, noTrafficMin?, distanceKm?, mode, primary)
data class MapNavigate(lat, lng, mode, label?)
data class MapSpec(title?, center?, zoom?, markers, routes, navigate?, autoNavigate)
fun parseMapSpec(raw: String): MapSpec?
```

Plus `domain/Polyline.kt` — a ~30-line Google encoded-polyline decoder
(`decodePolyline(s): List<LatLng>`), unit-tested against known vectors.

### 2.3 UI — `ui/prose/MapBlock.kt`

The Stark map card, matching `ChartBlock`'s framing (corner brackets, header plate,
accent-tinted hairlines):

- **Header**: `TITLE_SUB` split (white + accent), right-aligned distance/ETA of the primary
  route in the HUD mono type.
- **Map surface**: `AndroidView { MapLibre MapView }` clipped to the panel radius, fixed
  height ~240 dp (spec `height` override like charts). Lifecycle bridged via
  `DisposableEffect` + `LocalLifecycleOwner` (MapView needs onStart/onResume/onDestroy —
  wrap once in a small `rememberMapViewWithLifecycle()` helper).
- **Stark style**: `assets/map_style_stark.json` — our own MapLibre style: near-black
  `#0a0e12` land, `#0d1b22` water, roads in desaturated steel with motorways in dim accent,
  text halos off, glyphs from the tile provider's font stack, POI noise stripped. The
  accent colour (per-agent palette) is injected at runtime: route lines, markers, and the
  location dot are **runtime layers**, not baked into the style, so Sentinel's amber map
  differs from SPEDA's teal automatically via `LocalHbPalette`.
- **Routes**: primary route = accent line (5 dp, glow underlay at 30% alpha, animated
  dash-phase "energy flow" if cheap); alternatives = 2 dp dimmed. Traffic readout chip per
  route: `34 MIN · +12 TRAFFIC` (delta of `durationMin − noTrafficMin`, amber when delta
  > 25%).
- **Markers**: origin = pulsing hollow ring, destination = accent diamond, POI = small dot
  + label chip. Tap a POI marker → bottom chip with subtitle + its own mini NAVIGATE.
- **Action bar** (bottom of card): `▸ NAVIGATE` (fires the intent, §2.4), `⧉ OPEN IN MAPS`
  (universal URL), route selector chips when alternatives exist (tapping re-highlights).
- **Gesture containment**: map pan/zoom fights the chat `LazyColumn` scroll — request
  `parent.requestDisallowInterceptTouchEvent(true)` while a touch is inside the map, or
  start the map gestures-disabled with a "tap to interact" scrim (recommended default:
  scrim; it also prevents accidental tile-fetch scroll jank).
- **Offline/tile failure**: MapLibre renders what it has; add a fallback flat grid
  background (drawn `drawBehind`) so a dead tile server still looks like a deliberate
  Stark wireframe rather than a grey hole. Markers/routes render regardless (they're
  runtime layers, not tiles).

### 2.4 Fence dispatch + navigation intent

`Prose.kt` `Fence()`:
```kotlin
"map" -> MapBlock(code)
```

`MapBlock` gets the intent hook via a `LocalUriHandler`-style local or a lambda plumbed the
same way FileCard handles downloads:
```kotlin
fun launchNavigation(ctx: Context, nav: MapNavigate) {
    val gmm = Intent(ACTION_VIEW, Uri.parse("google.navigation:q=${nav.lat},${nav.lng}&mode=${nav.mode.gm}"))
        .setPackage("com.google.android.apps.maps")
    if (gmm.resolveActivity(pm) != null) ctx.startActivity(gmm)
    else ctx.startActivity(Intent(ACTION_VIEW, Uri.parse(
        "https://www.google.com/maps/dir/?api=1&destination=${nav.lat},${nav.lng}&travelmode=${nav.mode.web}")))
}
```

**autoNavigate**: honoured in `MessageItem`/`ChatViewModel`, not in `MapBlock` — fire once
when the owning message transitions to complete (stream finished), guarded by
(a) message is live, not history-loaded, (b) a per-message "consumed" flag in state,
(c) app in foreground. Show a 3-second inline countdown chip (`NAVIGATING IN 3… CANCEL`) —
Stark-appropriate and an abort affordance in one.

### 2.5 Location freshness (quality-of-life, same PR)

`PlatformContextProvider` uses `getLastKnownLocation`, which can be stale by hours — bad
input for routing. Add `getCurrentLocation()` (API 30+, `LocationManager`, still no Play
Services) with a 2 s timeout falling back to last-known, used when the outgoing message
looks location-relevant is overkill — simplest correct rule: always try current with the
short timeout when location sharing is on. Piggybacks the existing permission gate.

### 2.6 Android tests

- `MapSpecTest` — parser leniency (half JSON → null, unknown keys ignored, marker kinds
  defaulted).
- `PolylineTest` — decoder against Google's published test vectors.
- Reducer untouched (fence is plain text to the stream layer) — no changes needed.

---

## 3. Desktop parity — `packages/heartbreaker` (phase 2, optional)

`Message.tsx`'s `code()` branch gains `map` → `MapBlock.tsx` using **maplibre-gl** (npm) with
the *same* `map_style_stark.json` (shared contract, shared style file — consider hoisting the
style JSON to a shared location or duplicating with a sync note). NAVIGATE opens the
universal URL via `shell.openExternal`. Everything else (spec, prompt) is already done by
Phase 1 — the fence degrades gracefully to a code block on desktop until this lands, which
is acceptable and is exactly how chart fences behaved before their port.

---

## 4. Build / rollout order

| Step | What | Depends on |
|------|------|-----------|
| 1 | `config.py` key + `.env.example`; enable Routes API + Places API (New) on the Google Cloud project, create key, restrict to those two APIs + server IP | — |
| 2 | `skills/navigation.py` + registration + tests | 1 |
| 3 | Prompt contract (`06_visual_output.md`, `inline-rendering` skill doc) | 2 (tool names final) |
| 4 | Android: MapLibre dep + `MapSpec` + polyline decoder + tests | — (parallel with 1–3) |
| 5 | Android: `MapBlock` + Stark style JSON + fence dispatch | 4 |
| 6 | Android: NAVIGATE intent + `autoNavigate` countdown + fresh-location fix | 5 |
| 7 | End-to-end: "how do I get home?" on device → tool call → fence → styled map → tap NAVIGATE → Google Maps opens with the route | 3+6 |
| 8 | Desktop `MapBlock.tsx` | 3 |

### Done signal
1. "Where am I?" renders a Stark map with a pulsing origin marker — zero coordinates in the prose.
2. "How do I get to <place>?" shows ≥1 route with live ETA and a traffic-delta chip; alternatives selectable.
3. "Best coffee near me?" drops POI markers with rating/open chips via `find_places`.
4. NAVIGATE tap opens Google Maps turn-by-turn to the right destination and mode.
5. "Take me there" auto-opens Google Maps after the visible countdown; replaying the same session later does **not** re-fire it.
6. Backend with no `GOOGLE_MAPS_API_KEY` degrades to a spoken answer + tool "not configured" string — no crash, no broken fence.

---

## 5. Risks & mitigations

- **OpenFreeMap availability** — free public infra; mitigation: tile source is one config
  swap to MapTiler, and the wireframe-grid fallback keeps the card presentable offline.
- **Routes API quota** (10k/mo free) — single-user system; realistically hundreds/month.
  Log every call (`request_id` structured log) so Sentinel/budget mode can watch it.
- **Fence size** — encoded polylines keep specs ~1–3 KB; instruct the model (prompt) to cap
  alternatives at 3 and never inline raw coordinate arrays.
- **Streaming half-JSON** — identical to charts: `parseMapSpec` returns null → placeholder;
  no partial map flashes.
- **Chat scroll vs map gestures** — tap-to-interact scrim (chosen default) eliminates it.
- **Transit mode** — Routes v2 transit returns legs/steps rather than one clean polyline;
  phase 1 renders transit as segmented polylines with mode chips, defer step-by-step
  transit UI.
