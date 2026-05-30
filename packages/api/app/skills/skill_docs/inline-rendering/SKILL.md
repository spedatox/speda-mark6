---
name: inline-rendering
description: Renders SVG and HTML as live previews directly inside the chat. Use when the user requests charts, graphs, diagrams, flowcharts, data visualisations, UI mockups, or any visual output.
---

# Inline Rendering

Output a fenced code block — the frontend renders it flush inside the message, like a native
UI element. There is NO surrounding card, white background, or border drawn by you. The render
must look like it belongs in a sleek dark chat app.

## Rule 1 — Prefer SVG for diagrams, charts, and graphs

SVG renders inline, scales perfectly, needs no libraries, and looks native. Use it for almost
everything: line/bar/scatter charts, economic diagrams, flowcharts, supply-demand graphs,
network diagrams, illustrations.

Hand-draw the SVG. Compute the coordinates yourself. Example — a clean dark-theme line chart:

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

## Rule 2 — Use HTML + JS ONLY for genuine interactivity

Reach for HTML only when the user needs sliders, live recalculation, hover tooltips, or
animation that SVG can't do. Then follow these rules exactly:

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
| A static chart, graph, or diagram | **SVG** |
| An economic / conceptual diagram | **SVG** |
| A flowchart or illustration | **SVG** |
| Sliders / live recalculation / hover tooltips | HTML + JS |
| A downloadable file | `generate_document` (only if explicitly asked) |

When unsure: choose SVG. It always looks native.
