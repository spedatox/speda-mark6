---
name: inline-rendering
description: Renders chart blocks, calendar blocks, SVG diagrams, and HTML widgets as live previews directly inside chat and the canvas board. The owner loves visualizations — use proactively in moderation whenever presenting data series, schedules, architectures, workflows, or comparisons. In Canvas Mode, authoring visual windows is mandatory.
---

# Inline Rendering

Output a fenced code block — the frontend renders it flush inside the message, like a native
UI element. There is NO surrounding card, white background, or border drawn by you. The render
must look like it belongs in a sleek dark chat app.

The owner loves visualizations. Do not wait to be asked: proactively accompany your answers with
the best-fitting visual block whenever data, schedules, structures, or flows are involved, in moderation
("abartmadan, dozunda"). In Canvas Mode (Voice Canvas presentation board), staging visual windows
is a strict mandatory hard rule.

## Rule 0 — Data charts (line/area/bar/pie) → use a ```chart``` block, never SVG or HTML

Anything with series and data points — line, area, bar, or pie/donut — has a dedicated fenced
block type: **```chart**. It renders as a native Stark FUI panel (teal axes, hairline grid,
corner-bracketed header) and is the ONLY correct output for this category. Do not hand-draw a
pie chart in SVG and do not reach for Chart.js in an HTML block — both are wrong when this
block covers the case.

```
{
  "type": "line",
  "title": "PANEL_TITLE",
  "xKey": "month",
  "series": [
    { "key": "value", "label": "SERIES NAME", "color": "#36abca" }
  ],
  "data": [
    { "month": "JAN", "value": 120 },
    { "month": "FEB", "value": 180 }
  ],
  "unit": "K",
  "yDomain": [0, 300]
}
```

- `type`: `"line"` | `"area"` | `"bar"` | `"pie"`
- `title`: optional panel header — `"WORD_SUB"` splits into white + cyan
- `xKey`: which data field maps to the X axis (line/area/bar only)
- `series`: one entry per line/bar. Omit `color` to cycle the palette
- `unit`: appended to tooltip values (e.g. `"%"`, `"ms"`, `" KB"`)
- `yDomain`: optional `[min, max]` — use `"auto"` for either end
- `height`: optional chart height in px (default 210)

**Pie / Donut** — flat `data` array, no `series`/`xKey`:
```
{
  "type": "pie",
  "title": "DISTRIBUTION_STATUS",
  "data": [
    { "label": "BACKEND",  "value": 40 },
    { "label": "FRONTEND", "value": 30 },
    { "label": "ML",       "value": 20 },
    { "label": "OPS",      "value": 10 }
  ]
}
```

Multiple series (grouped bar / multi-line):
```
"series": [
  { "key": "income",  "label": "INCOME" },
  { "key": "expense", "label": "EXPENSE", "color": "#d39a3a" }
]
```

## Rule 1 — Prefer SVG for diagrams, flowcharts, and anything that isn't a data chart

SVG renders inline, scales perfectly, needs no libraries, and looks native. Use it for
everything that is NOT a line/bar/area/pie chart: economic diagrams, flowcharts, supply-demand
graphs, network diagrams, timelines, illustrations.

Hand-draw the SVG. Compute the coordinates yourself. Example — a clean dark-theme diagram:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 420" font-family="Inter, sans-serif">
  <!-- axes -->
  <line x1="70" y1="40" x2="70" y2="360" stroke="#3a3a3a" stroke-width="1.5"/>
  <line x1="70" y1="360" x2="680" y2="360" stroke="#3a3a3a" stroke-width="1.5"/>
  <!-- data line -->
  <polyline fill="none" stroke="#8ab4f8" stroke-width="2.5"
            points="70,300 200,260 330,180 460,120 590,90 680,70"/>
  <!-- labels -->
  <text x="375" y="30" fill="#e3e3e3" font-size="16" font-weight="600" text-anchor="middle">Title</text>
  <text x="60" y="365" fill="#9aa0a6" font-size="11" text-anchor="end">0</text>
</svg>
```

### SVG rules (NON-NEGOTIABLE)

- NO background rect. The SVG must be transparent — the dark chat shows through.
- Text colours: `#e3e3e3` (primary), `#9aa0a6` (muted labels).
- Lines/strokes: `#8ab4f8` (blue), `#7ce8d5` (teal), `#ff6b6b` (red), `#c8a4ff` (purple),
  `#3a3a3a` (axes/grid).
- Always set a `viewBox` (e.g. `0 0 720 420`) and DO NOT set width/height — it scales to fit.
- Use `font-family="Inter, sans-serif"`.

## Rule 2 — Use HTML + JS ONLY for interactivity a ```chart``` block can't do

Reach for HTML only when the user needs sliders, live recalculation, or animation — something
neither the ```chart``` block nor SVG can do. A hover tooltip on a chart is NOT a reason to use
this; the ```chart``` block already has tooltips built in. Then follow these rules exactly:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    html, body { margin: 0; padding: 0; background: transparent; }
    body { font-family: Inter, sans-serif; color: #e3e3e3; }
    .wrap { padding: 8px 4px; }
  </style>
</head>
<body>
  <div class="wrap" style="height: 380px;">
    <canvas id="c"></canvas>
  </div>
  <script>
    new Chart(document.getElementById('c'), {
      type: 'line',
      data: { /* ... */ },
      options: {
        responsive: true,
        maintainAspectRatio: false,   // REQUIRED so it fills the fixed-height wrap
        plugins: { legend: { labels: { color: '#e3e3e3' } } },
        scales: {
          x: { ticks: { color: '#9aa0a6' }, grid: { color: 'rgba(255,255,255,0.06)' } },
          y: { ticks: { color: '#9aa0a6' }, grid: { color: 'rgba(255,255,255,0.06)' } }
        }
      }
    });
  </script>
</body>
</html>
```

### HTML rules (NON-NEGOTIABLE)

- `background: transparent` on html AND body. NEVER white. NEVER a coloured card or border.
- The chart container MUST have an explicit pixel height (e.g. `height: 380px`) and the chart
  MUST use `maintainAspectRatio: false`. Otherwise it collapses or gets clipped.
- All text/ticks/legends use `#e3e3e3` or `#9aa0a6`. Grid lines `rgba(255,255,255,0.06)`.
- Load libraries from `cdn.jsdelivr.net`.
- Do NOT add your own rounded card, shadow, padding-box, or border — the app frames it.

## Decision

| Want | Use |
|------|-----|
| Line / area / bar / pie / donut chart | **```chart``` block** |
| An economic / conceptual diagram | **SVG** |
| A flowchart, network graph, or illustration | **SVG** |
| Sliders / live recalculation / animation | HTML + JS |
| A downloadable file | `generate_document` (only if explicitly asked) |

When unsure and it has series or data points: ```chart```. When unsure and it doesn't: SVG.

### Calendar / schedule → use `calendar` blocks

When the user asks to **see their calendar, week, agenda, or schedule** ("what's on my
calendar this week", "show my schedule", "haftalık takvimim", "bugün ne var") — FIRST fetch
the real events with the calendar tool (list_events / the Google Calendar MCP), THEN render
them as a **```calendar** block. It renders as a layered holographic glass panel (frosted
.hb-holo material, concentric HUD ring, today's date shown as a large glowing numeral) in the
active agent's accent colour.

```calendar
{
  "title": "THIS WEEK",
  "range": "30 JUN – 6 JUL 2026",
  "days": [
    {
      "date": "2026-06-30",
      "events": [
        { "time": "09:00", "end": "10:00", "title": "Standup", "location": "Zoom" },
        { "time": "14:00", "title": "Dentist" }
      ]
    },
    { "date": "2026-07-01", "events": [] },
    { "date": "2026-07-02", "events": [
        { "time": "11:00", "title": "1:1 with Sentinel", "color": "#d99c44" }
    ] }
  ]
}
```

- `days`: one entry per day, each with an ISO `"date"` (`yyyy-mm-dd`) and an `events` array.
  Include every day in the asked range, even empty ones (`"events": []`) — gaps read as free time.
- Each event: `title` (required); optional `time` (`"HH:MM"`), `end`, `location`, and `color`
  (hex — use it to colour-code by category or source agent; otherwise the accent is used).
- The widget auto-detects "today" and renders its date larger and glowing. Weekday labels are
  derived from the date — you don't supply them.
- `title` and `range` are optional captions. Keep the day list in chronological order.
- For a single day ("what's on today"), just pass that one day in `days`.
- Do NOT also dump the events as a markdown list — the block IS the answer. A one-line summary
  above it ("3 etkinliğin var bu hafta:") is good; a redundant text agenda is not.

### Maps, locations, routes → use `map` blocks

When the user asks **where they are, where something is, how to get somewhere, which
route is fastest, or what's nearby** — NEVER answer with raw coordinates or a plain text
list. A pure "show me where I am / where X is" needs no tool — just render a `map` block
with a marker. For anything involving a ROUTE or "best options near me", FIRST call the
tool (`get_route` for directions+traffic, `find_places` for POIs), THEN render the result
as a **```map** block. It renders as a Stark FUI map panel (dark basemap in the agent's
accent, glowing markers, route lines with a live-traffic readout, and a one-tap NAVIGATE
that opens Google Maps).

**A route** — every option `get_route` returned, each by its `routeId`:

```map
{
  "title": "ROUTE_HOME",
  "markers": [
    { "lat": 41.043, "lng": 29.009, "label": "YOU",  "kind": "origin" },
    { "lat": 41.111, "lng": 29.021, "label": "HOME", "kind": "destination" }
  ],
  "routes": [
    { "routeId": "r_1a2b3c4d", "label": "VIA D-100", "durationMin": 34,
      "noTrafficMin": 22, "distanceKm": 18.4, "mode": "drive", "primary": true },
    { "routeId": "r_5e6f7a8b", "label": "VIA COAST", "durationMin": 41,
      "noTrafficMin": 35, "distanceKm": 21.0, "mode": "drive" }
  ],
  "navigate": { "lat": 41.111, "lng": 29.021, "mode": "drive", "label": "HOME" },
  "autoNavigate": false
}
```

**Places** — the whole `find_places` result set by its `placesId`:

```map
{
  "title": "BARBERS_NEARBY",
  "places": "pl_1a2b3c4d",
  "markers": [ { "lat": 40.212, "lng": 28.995, "label": "YOU", "kind": "origin" } ]
}
```

- `center` / `zoom` optional — the client auto-fits the markers + routes when omitted.
- **`routes[].routeId`** comes straight from `get_route` — copy it character for character.
  The client fetches the real geometry, the live-traffic colouring and the turn-by-turn
  from that id. There is no polyline for you to write; never invent one. (Fences written
  before routeIds existed carry an inline `polyline` instead — still supported, still never
  hand-written.)
- **List EVERY route the tool returned, not just the best one.** The card turns them into a
  route switcher: the owner taps between them and the line, the ETA, the traffic and the
  NAVIGATE target all follow the selection. One route means nothing to compare. Mark exactly
  ONE `primary: true` — your recommendation, and what the card opens on.
  Copy `durationMin`, `noTrafficMin`, `distanceKm`, `label` and `mode` per route; the client
  renders `noTrafficMin` vs `durationMin` as the traffic delta.
- **`places`** is the `placesId` from `find_places` — one string, the entire result set. The
  client draws each place as a tappable marker and resolves its own record (address, phone,
  website, opening hours, rating, per-place NAVIGATE). When you pass `places`, do NOT also
  write those POIs into `markers` and do NOT retype their details in prose — say which one
  you'd pick and why, and let the card carry the rest.
- `markers[].kind`: `origin | destination | poi | pin` (chooses the glyph + colour). Use
  `markers` for the owner's own position, an origin/destination, or a single named point —
  a plain "where is X" needs one marker and no tool at all. Put a hand-written POI's rating
  / open state in `subtitle`.
- `navigate` present ⇒ the NAVIGATE button shows and opens Google Maps to that point/mode.
- `autoNavigate: true` **only** when the owner explicitly commanded navigation this turn
  ("take me there", "navigate", "yol tarifi başlat") — it makes the client open Google
  Maps automatically after a short visible countdown. Otherwise `false`.
- Same anti-redundancy rule as the calendar: the block IS the answer. One summary line
  above it ("Evine en hızlı yol D-100 üzerinden, 34 dk — trafik 12 dk ekliyor:") is good;
  a second text list of the same routes/places/coordinates is not.

### Live aircraft tracking → use `aircraft` blocks

When the owner asks to **track, locate, or check the status of a plane by its tail
number/registration** ("track this plane N12345", "where's TC-JJA right now", "bu uçağı
takip et") — FIRST call `track_aircraft`, THEN render the result as an **```aircraft**
block. It renders as a Stark FUI panel with a live-updating position on the same dark
basemap as the map block, plus a status readout (altitude, speed, heading, squawk). The
client polls the live position itself from the tail number — you do not refresh it.

```aircraft
{
  "tail": "N12345",
  "icao24": "a1b2c3",
  "callsign": "UAL123",
  "aircraftType": "B738",
  "lat": 37.358322,
  "lng": -93.374147,
  "altitudeFt": 38000,
  "onGround": false,
  "groundSpeedKt": 338.9,
  "headingDeg": 276.1,
  "squawk": "3301"
}
```

- Every field except `tail`, `lat`, `lng` is optional — copy whatever `track_aircraft`
  returned, character for character; never invent a value it didn't give you.
- `onGround: true` ⇒ the client shows a grounded state and omits speed/heading/altitude
  chrome (altitude on the ground is meaningless and `track_aircraft` won't have given
  you one).
- If `track_aircraft` flagged an emergency squawk (7500/7600/7700), say so explicitly in
  your one-line summary above the fence — do not let it pass silently as just another field.
- This is live ADS-B telemetry only — position, altitude, speed, heading, squawk. It is
  NOT a flight-schedule lookup: never claim a gate, delay, ETA, or origin/destination the
  tool didn't return.
- Same anti-redundancy rule as the map/calendar blocks: the block IS the answer. A
  one-line summary above it is good; do not also retype the fields as a text list below it.

### Ankara bus arrivals → use `bus` blocks

When the owner asks when the next bus is coming, or which lines pass a stop — call
`bus_arrivals` with the stop number, THEN render the result as a **```bus** block. It
renders as a static glass list card: one row per line, closest arrival first. Unlike
`aircraft`, nothing here is live-polled after the fence renders — a bus board is stale
within seconds regardless, so the client draws the snapshot `bus_arrivals` returned and
stops there.

```bus
{
  "stopNumber": "12219",
  "entries": [
    {
      "line": "454-5", "route": "(ÖTA) ÖRNEK-ULUS-SIHHİYE-KIZILAY-ÖVEÇLER",
      "live": true, "eta": "Geldi", "speedKmh": 0, "plate": "06 HO 1061",
      "stopIndex": 50, "totalStops": 50, "tags": []
    },
    {
      "line": "143", "route": "SIHHİYE-SÖĞÜTÖZÜ-BALGAT ADLİYE",
      "live": false, "nextDeparture": "11:00", "inWords": "14 dk"
    }
  ]
}
```

- Copy the JSON `bus_arrivals` gave you EXACTLY, character for character — never invent
  an entry, a plate, or an ETA it didn't return.
- `live: true` entries need `eta`; `speedKmh`, `plate`, `stopIndex`/`totalStops`, and
  `tags` are optional (some urban lines omit one or more). `live: false` entries need
  `nextDeparture` instead — that line has no bus currently inbound, only a schedule.
- Same anti-redundancy rule as every other block: the card IS the answer. One short
  sentence above it (e.g. which line is closest) is good; do not also retype the
  entries as a bulleted list below it.

### Data charts → use `chart` blocks

When the user wants a **data chart** (line, bar, area, pie — anything with series and data
points), use a **```chart** block with this JSON format. It renders as a Stark FUI panel
with teal axes, hairline grid, and a corner-bracketed panel header.

**Line / Area / Bar:**
```
{
  "type": "line",
  "title": "PANEL_TITLE",
  "xKey": "month",
  "series": [
    { "key": "value", "label": "SERIES NAME", "color": "#36abca" }
  ],
  "data": [
    { "month": "JAN", "value": 120 },
    { "month": "FEB", "value": 180 }
  ],
  "unit": "K",
  "yDomain": [0, 300]
}
```
- `type`: `"line"` | `"area"` | `"bar"` | `"pie"`
- `title`: optional panel header — `"WORD_SUB"` splits into white + cyan
- `xKey`: which data field maps to the X axis
- `series`: one entry per line/bar. Omit `color` to cycle the palette
- `unit`: appended to tooltip values (e.g. `"%"`, `"ms"`, `" KB"`)
- `yDomain`: optional `[min, max]` — use `"auto"` for either end
- `height`: optional chart height in px (default 210)

**Pie / Donut:**
```
{
  "type": "pie",
  "title": "DISTRIBUTION_STATUS",
  "data": [
    { "label": "BACKEND",  "value": 40 },
    { "label": "FRONTEND", "value": 30 },
    { "label": "ML",       "value": 20 },
    { "label": "OPS",      "value": 10 }
  ]
}
```

Multiple series (grouped bar / multi-line):
```
"series": [
  { "key": "income",  "label": "INCOME" },
  { "key": "expense", "label": "EXPENSE", "color": "#d39a3a" }
]
```

### Diagrams, flowcharts → use SVG

For diagrams, flowcharts, timelines, network graphs, and anything
that isn't rectangular data series, hand-write an **SVG**.

SVG must be transparent (NO background rect), use a `viewBox` with no width/height,
text in `#e3e3e3`/`#9aa0a6`, strokes in `#8ab4f8`/`#7ce8d5`/`#ff6b6b`/`#c8a4ff`.

**Layout & spacing — labels must NEVER overlap (critical):**
- Add generous padding inside the viewBox — keep all content ≥24px from every edge.
- Position every text label so it does not touch or overlap geometry, points, or
  another label. If two labels would land near the same spot, push them apart and
  use a short leader line instead of stacking them.
- Anchor labels deliberately: `text-anchor="start|middle|end"` and offset with
  `dx`/`dy` so the text clears the thing it annotates (e.g. a point label sits
  6–10px away from its dot, not on top of it).
- Give the viewBox enough room — when in doubt, make it larger and spread elements
  out. A clean, readable diagram beats a dense one. Mentally check every label's
  bounding box against its neighbours before finalising.

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 420" font-family="Inter, sans-serif">
  ...
</svg>
```

### Never produce a "web page" look

NO white backgrounds. NO decorative cards, borders, shadows, or padding boxes around your
visual — the app frames it for you. The visual must look like it belongs in a dark, sleek
chat app, not like a styled HTML document.

### Fence formatting

The fence MUST be on its own line with a blank line before it. Write:

Here is the diagram.

```svg
<svg ...>
```

NOT: "Here is the diagram.```svg"

(Full rules: call `read_skill` with `inline-rendering`.)

## File output — downloadable files vs inline visuals

Decide first: does the user want a **file they can download/save/run**, or an **inline
visual** the app renders in the chat?

**They want a FILE → call a tool, never paste the file as a code block:**
- `.html` page / landing page, `.py` / `.js` / `.sh` script, `.css`, `.json` / `.yaml` /
  `.csv` / `.xml` / `.env` / `.toml`, `.md`, `.txt` → **`save_file`** (filename + full content).
- A formatted report, slide deck, or printable → **`generate_document`** (pdf / docx / pptx).

Triggers for a file: "as an HTML file", "give me the .py", "create a file", "single-file",
"so I can download / save / open / run it", "export as …". When in doubt and the user named
an extension or said "file", use `save_file`.

**They want an inline visual → write a fenced code block, NO tool:**
- Data charts → ```chart``` · diagrams/flowcharts → ```svg```.

Hard rule: a file the user will download is produced with `save_file` / `generate_document`
and delivered as a download card — do **not** dump a large `.html`/`.py` body into the chat
as a code block and call it a file. Pasting the code is not delivering a file.
