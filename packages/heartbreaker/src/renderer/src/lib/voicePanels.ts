// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * The canvas's pure half: how an answer is cut into windows, and where those
 * windows go. Split out of VoiceCanvas.tsx deliberately — a module that exports
 * both a component and plain functions cannot Fast Refresh, so every edit to
 * the layout maths would blow away the board's drag state mid-session.
 */

export type PanelKind = 'text' | 'math' | 'chart' | 'map' | 'calendar' | 'code' | 'widget' | 'table'

export interface VoicePanel {
  id: string
  kind: PanelKind
  /** Window title, `MAIN_SUB` split like every other panel header in the app. */
  title: string
  /** The markdown for this window alone — rendered by the transcript's own
   *  pipeline, so a chart here is the same chart it is in chat. */
  source: string
}

/** Fenced languages that are an artefact in their own right, and which window
 *  kind each becomes. Anything else fenced is source code. */
const FENCE_KIND: Record<string, PanelKind> = {
  chart: 'chart', calendar: 'calendar', map: 'map', html: 'widget', svg: 'widget',
}

const LABEL: Record<PanelKind, string> = {
  text: 'RESPONSE', math: 'SOLUTION', chart: 'CHART', map: 'MAP',
  calendar: 'SCHEDULE', code: 'SOURCE', widget: 'RENDER', table: 'TABLE',
}

/** Base window size per kind, in px, before the fit pass. A plot needs width to
 *  be read; a worked equation needs almost none. */
const SIZE: Record<PanelKind, { w: number; h: number }> = {
  text:     { w: 420, h: 300 },
  math:     { w: 440, h: 200 },
  chart:    { w: 580, h: 350 },
  map:      { w: 520, h: 380 },
  calendar: { w: 620, h: 370 },
  code:     { w: 500, h: 330 },
  widget:   { w: 580, h: 390 },
  table:    { w: 520, h: 300 },
}

/** Windows that bring no chrome of their own and therefore need the glass. The
 *  rich blocks (chart, calendar, map, code, widget) already draw their own
 *  panel — wrapping those in a second one would double every border. */
export const FRAMED = new Set<PanelKind>(['text', 'math', 'table'])

/* ── Splitting ─────────────────────────────────────────────────────────────
 * Walks the markdown once and cuts it into windows. Everything that is not an
 * artefact is PROSE, and all prose merges into a single RESPONSE window — the
 * spoken narrative is one thing, and letting it fragment into a window per
 * paragraph would bury the artefacts it exists to introduce. */
export function splitPanels(text: string): VoicePanel[] {
  const lines = text.split('\n')
  const out: VoicePanel[] = []
  const prose: string[] = []
  const seen: Partial<Record<PanelKind, number>> = {}

  const push = (kind: PanelKind, source: string) => {
    const n = (seen[kind] = (seen[kind] ?? 0) + 1)
    out.push({
      id: `${kind}-${n}`,
      kind,
      title: `${LABEL[kind]}_${String(n).padStart(2, '0')}`,
      source,
    })
  }

  let i = 0
  while (i < lines.length) {
    const line = lines[i]

    // ── Fenced block ──────────────────────────────────────────────────────
    const fence = /^[ \t]*```[ \t]*([\w-]*)/.exec(line)
    if (fence) {
      const lang = (fence[1] || '').toLowerCase()
      const body = [line]
      i++
      while (i < lines.length && !/^[ \t]*```/.test(lines[i])) body.push(lines[i++])
      if (i < lines.length) body.push(lines[i++])       // the closing fence
      push(FENCE_KIND[lang] ?? 'code', body.join('\n'))
      continue
    }

    // ── Display math ──────────────────────────────────────────────────────
    // `$$…$$` or `\[…\]`, opening at the start of a line. Inline math stays in
    // the prose where it belongs — only a display block is its own object.
    const open = /^[ \t]*(\$\$|\\\[)/.exec(line)
    if (open) {
      const close = open[1] === '$$' ? '$$' : '\\]'
      const body = [line]
      // A one-line `$$x$$` closes immediately; anything else runs until it does.
      const closesHere = line.trim().length > 2 && line.trimEnd().endsWith(close)
      i++
      if (!closesHere) {
        while (i < lines.length && !lines[i].includes(close)) body.push(lines[i++])
        if (i < lines.length) body.push(lines[i++])
      }
      push('math', body.join('\n'))
      continue
    }

    // ── Table ─────────────────────────────────────────────────────────────
    // A run of pipe rows, header + delimiter at minimum. Data wants its own
    // window on a board — reading it inside the narrative is what a transcript
    // does, and this is not one.
    if (/^[ \t]*\|.*\|[ \t]*$/.test(line) && /^[ \t]*\|[-: |]+\|[ \t]*$/.test(lines[i + 1] ?? '')) {
      const body: string[] = []
      while (i < lines.length && /^[ \t]*\|/.test(lines[i])) body.push(lines[i++])
      push('table', body.join('\n'))
      continue
    }

    prose.push(line)
    i++
  }

  if (prose.join('\n').trim()) {
    out.unshift({ id: 'text-1', kind: 'text', title: `${LABEL.text}_01`, source: prose.join('\n') })
  }
  return out
}

/** Does this reply want the workspace? A pure spoken answer does not — the orb
 *  keeps the screen and the words run along the bottom. One artefact and the
 *  board opens. */
export function hasArtifacts(panels: VoicePanel[]): boolean {
  return panels.some(p => p.kind !== 'text')
}

/* ── Layout ────────────────────────────────────────────────────────────────
 * Shelf packing, left to right, wrapping down. Two rules beyond the obvious:
 * the docked orb owns the bottom-right corner and nothing may be placed under
 * it, and if the board overflows, every window scales down together rather
 * than the last ones falling off the bottom. */

export interface Placed extends VoicePanel { x: number; y: number; w: number; h: number }

export const GAP = 14
/** How far the fit pass may scale the whole board. The floor is a legibility
 *  limit; the ceiling stops a lone chart from becoming a billboard. */
const MIN_SCALE = 0.55
const MAX_SCALE = 1.45

export function pack(
  panels: VoicePanel[], W: number, H: number, reserveX: number, reserveY: number,
): Placed[] {
  // The orb's corner, as a quadrant: everything right of reserveX AND below
  // reserveY is spoken for.
  const hitsOrb = (x: number, y: number, w: number, h: number) =>
    x + w > reserveX && y + h > reserveY

  const run = (scale: number): { out: Placed[]; bottom: number } => {
    const out: Placed[] = []
    let x = GAP, y = GAP, rowH = 0
    for (const p of panels) {
      let w = Math.round(Math.min(SIZE[p.kind].w * scale, Math.max(240, W - GAP * 2)))
      const h = Math.round(SIZE[p.kind].h * scale)

      if (x > GAP && x + w > W - GAP) { x = GAP; y += rowH + GAP; rowH = 0 }
      if (hitsOrb(x, y, w, h)) {
        // Try the next shelf first — that is where the room usually is…
        if (x > GAP) { x = GAP; y += rowH + GAP; rowH = 0 }
        // …and if the shelf itself runs into the orb, narrow to clear it.
        if (hitsOrb(x, y, w, h)) w = Math.max(240, Math.round(reserveX - GAP - x))
      }

      out.push({ ...p, x, y, w, h })
      x += w + GAP
      rowH = Math.max(rowH, h)
    }
    return { out, bottom: y + rowH + GAP }
  }

  // Find the LARGEST scale the board still fits at, rather than shrinking by
  // the overflow ratio in one pass. Two reasons that pass was wrong: shrinking
  // changes where rows wrap, so the corrected layout is shorter than the ratio
  // predicted and leaves the bottom of the board empty; and with few windows
  // there is room to grow, which a shrink-only rule can never use. Bisection is
  // ~9 packs of a handful of rectangles — nothing, and it fills the board.
  const fits = (s: number) => run(s).bottom <= H
  let lo = MIN_SCALE, hi = MAX_SCALE
  if (fits(hi)) return run(hi).out
  for (let i = 0; i < 9; i++) {
    const mid = (lo + hi) / 2
    if (fits(mid)) lo = mid
    else hi = mid
  }
  // `lo` is only known to fit if something did; at the floor nothing does, and a
  // board scaled to nothing is worse than one you have to drag a window on.
  return run(lo).out
}
