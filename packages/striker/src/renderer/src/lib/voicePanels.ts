// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * The canvas's pure half: how a presentation is cut into windows, and where
 * those windows go. Split out of VoiceCanvas.tsx deliberately — a module that
 * exports both a component and plain functions cannot Fast Refresh, so every
 * edit to the layout maths would blow away the board's drag state mid-session.
 *
 * ── What changed, and why it matters ────────────────────────────────────────
 * This used to be a PARSER of chat output. The agent wrote its usual markdown
 * answer and this file scavenged whatever fenced blocks happened to be in it,
 * then swept every leftover paragraph into one RESPONSE window. That made voice
 * mode a transcript with a bigger font: if the model did not reach for a chart
 * unprompted, there was no chart, because nothing had ever asked it to present.
 *
 * Now the agent DIRECTS the board (see igor core/surface.py _VOICE_BRIEF). It
 * authors each window, titles it, and places it in the reply at the moment its
 * narration reaches it. This file's job is therefore no longer interpretation —
 * it is transcription: read what the agent staged, in the order it staged it.
 *
 * Two consequences follow, and both are deliberate:
 *
 *  - PROSE IS NOT A WINDOW. Spoken narrative goes to the caption strip under the
 *    orb, live, as a subtitle. The board carries evidence only. A window holding
 *    the same sentences the owner is currently hearing is the transcript-with-a-
 *    font-size mode all over again.
 *  - TITLES ARE AUTHORED. `CHART_01` is fine for a solved equation and useless
 *    for `VANKO / ARREST RECORD`. The agent names its own windows; the
 *    generated label is only the fallback for a block that arrived untitled.
 */

export type PanelKind =
  | 'math' | 'chart' | 'map' | 'calendar' | 'code' | 'widget' | 'table'
  | 'stat' | 'image' | 'article' | 'card' | 'timeline' | 'quote'
  // Not something the agent stages — the board's own window, showing what the
  // machine is DOING. It is a PanelKind so the packer, the drag, the resize and
  // EXTEND all treat it as the window it is, rather than it becoming a second
  // kind of thing floating over the board with its own rules.
  | 'activity'

export interface VoicePanel {
  id: string
  kind: PanelKind
  /** Window title, `MAIN_SUB` split like every other panel header in the app.
   *  Authored by the agent where it named one; generated where it did not. */
  title: string
  /** The block's body, without its fences. Parsed by whichever renderer owns
   *  this kind — the markdown kinds still go through the transcript's own
   *  pipeline, so a chart here is the same chart it is in chat. */
  source: string
}

/** Fenced languages that are an artefact in their own right, and which window
 *  kind each becomes. Anything else fenced is source code. */
const FENCE_KIND: Record<string, PanelKind> = {
  chart: 'chart', calendar: 'calendar', map: 'map', html: 'widget', svg: 'widget',
  table: 'table', math: 'math',
  // The presentation vocabulary — kinds that exist so a fact can be SHOWN
  // rather than spoken. Their bodies are small forgiving formats rather than
  // markdown; see components/VoicePanelBody.tsx for each one's shape.
  stat: 'stat', image: 'image', article: 'article', card: 'card',
  timeline: 'timeline', quote: 'quote',
}

/** The presentation vocabulary — the kinds that exist so a fact can be SHOWN
 *  rather than said, as opposed to the renderer kinds the transcript already
 *  shares with chat. Lives here rather than beside the renderers because it is
 *  read by Message.tsx, and Message.tsx and VoicePanelBody.tsx already import
 *  each other: a Set evaluated at module init is exactly the thing in a cycle
 *  that can be read before it exists.
 *
 *  These are the kinds chat has no other renderer for, so it is also the set
 *  that decides whether a fence becomes a window or a code block. */
export const BOARD_KINDS = new Set<PanelKind>([
  'stat', 'image', 'article', 'card', 'timeline', 'quote',
])

/** Fallback label, for a window the agent left untitled. */
const LABEL: Record<PanelKind, string> = {
  math: 'SOLUTION', chart: 'CHART', map: 'MAP', calendar: 'SCHEDULE',
  code: 'SOURCE', widget: 'RENDER', table: 'TABLE', stat: 'FIGURE',
  image: 'IMAGE', article: 'SOURCE', card: 'FILE', timeline: 'TIMELINE',
  quote: 'QUOTE', activity: 'ACTIVITY',
}

/** Base window size per kind, in px, before the fit pass. A plot needs width to
 *  be read; a stat tile is one number and wants to stay small enough that a
 *  board of six of them still reads as a row of figures. */
const SIZE: Record<PanelKind, { w: number; h: number }> = {
  math:     { w: 440, h: 200 },
  chart:    { w: 580, h: 350 },
  map:      { w: 520, h: 380 },
  calendar: { w: 620, h: 370 },
  code:     { w: 500, h: 330 },
  widget:   { w: 580, h: 390 },
  table:    { w: 520, h: 300 },
  stat:     { w: 260, h: 150 },
  image:    { w: 420, h: 320 },
  article:  { w: 400, h: 330 },
  card:     { w: 340, h: 340 },
  timeline: { w: 440, h: 320 },
  quote:    { w: 400, h: 190 },
  // Tall rather than wide: it is a running list, and the thing worth seeing is
  // how many steps have happened, not each one's full width.
  activity: { w: 420, h: 300 },
}

/** Windows that bring no chrome of their own and therefore need the glass. The
 *  rich blocks (chart, calendar, map, code, widget) already draw their own
 *  panel — wrapping those in a second one would double every border. */
export const FRAMED = new Set<PanelKind>([
  'math', 'table', 'stat', 'image', 'article', 'card', 'timeline', 'quote',
  'activity',
])

/* ── Reading the stage direction ───────────────────────────────────────────
 * A window is a fenced block whose info line is `kind | TITLE`. Everything
 * outside a fence is narration and belongs to the caption, not the board. */

/** `chart | REVENUE / MONTHLY` → kind + title. The separator is optional; a
 *  bare ```chart is still a chart, it just gets a generated label. */
function parseInfo(info: string): { lang: string; title: string } {
  const bar = info.indexOf('|')
  if (bar < 0) return { lang: info.trim().toLowerCase(), title: '' }
  return {
    lang: info.slice(0, bar).trim().toLowerCase(),
    title: info.slice(bar + 1).trim(),
  }
}

/** Titles render as `MAIN_SUB`, so an authored one is normalised into that
 *  shape: upper-cased, and the first separator becomes the underscore that
 *  splits the cyan half from the white half. `REVENUE / MONTHLY` reads as
 *  `REVENUE` + `_MONTHLY` on the grip. */
function screenTitle(raw: string, kind: PanelKind, n: number): string {
  const t = raw.trim().toUpperCase().replace(/\s*[/|·—–-]\s*/, '_').replace(/\s+/g, ' ')
  if (!t) return `${LABEL[kind]}_${String(n).padStart(2, '0')}`
  // A title with no separator still needs one, or the grip renders it entirely
  // in the sub colour — `_` at the end is how a one-word title stays legible.
  return t.includes('_') ? t : `${t}_`
}

/* ── Splitting ─────────────────────────────────────────────────────────────
 * Walks the reply once and lifts out the staged windows, in the order the agent
 * staged them. Order is the whole point: it is the cue track, and a window's
 * position in the stream is when it appears on screen.
 *
 * Display math and pipe tables are still recognised outside a fence. Not
 * because the agent is expected to write them that way — the brief asks for
 * `math |` and `table |` blocks — but because a model reaching for `$$…$$` out
 * of habit should still get a window rather than have its formula silently
 * dropped into narration that the speech path then refuses to say. */
export function splitPanels(text: string): VoicePanel[] {
  const lines = text.split('\n')
  const out: VoicePanel[] = []
  const seen: Partial<Record<PanelKind, number>> = {}

  const push = (kind: PanelKind, source: string, title: string) => {
    const n = (seen[kind] = (seen[kind] ?? 0) + 1)
    out.push({ id: `${kind}-${n}`, kind, title: screenTitle(title, kind, n), source })
  }

  let i = 0
  while (i < lines.length) {
    const line = lines[i]

    // ── Staged window ─────────────────────────────────────────────────────
    const fence = /^[ \t]*```[ \t]*(.*)$/.exec(line)
    if (fence) {
      const { lang, title } = parseInfo(fence[1] || '')
      const body: string[] = []
      i++
      while (i < lines.length && !/^[ \t]*```/.test(lines[i])) body.push(lines[i++])
      if (i < lines.length) i++                      // the closing fence
      const kind = FENCE_KIND[lang] ?? 'code'
      // The markdown kinds are re-fenced, because they are rendered by the
      // transcript's own pipeline and it needs the fence to recognise them. The
      // presentation kinds have their own renderers and take the raw body.
      const source = RAW_BODY.has(kind)
        ? body.join('\n')
        : ['```' + (lang || ''), ...body, '```'].join('\n')
      push(kind, source, title)
      continue
    }

    // ── Display math written the old way ──────────────────────────────────
    // `$$…$$` or `\[…\]`, opening at the start of a line. Inline math stays in
    // the narration where it belongs — only a display block is its own object.
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
      push('math', body.join('\n'), '')
      continue
    }

    // ── Table written the old way ─────────────────────────────────────────
    if (/^[ \t]*\|.*\|[ \t]*$/.test(line) && /^[ \t]*\|[-: |]+\|[ \t]*$/.test(lines[i + 1] ?? '')) {
      const body: string[] = []
      while (i < lines.length && /^[ \t]*\|/.test(lines[i])) body.push(lines[i++])
      push('table', body.join('\n'), '')
      continue
    }

    // Narration. It is not a window — it is the caption (see captionOf).
    i++
  }
  return out
}

/** Kinds whose renderer parses the body itself and must not see fences. */
const RAW_BODY = new Set<PanelKind>([
  'stat', 'image', 'article', 'card', 'timeline', 'quote',
])

/**
 * The spoken half of the reply — everything outside a staged window — as one
 * running string for the caption strip.
 *
 * This is the same cut the speech path makes (lib/voice.ts `speakable`), and
 * deliberately so: the subtitle must say what is being SAID. It is a second
 * implementation rather than a shared one because the two run on different
 * material — voice.ts judges a live delta stream line by line and has to track
 * fence state across calls, while this re-reads the whole visible text on every
 * render. Sharing the traversal would mean giving the streaming path a reason to
 * hold state it does not need, or this one a reason to be stateful.
 */
export function captionOf(text: string): string {
  const out: string[] = []
  let inFence = false
  let inMath = false
  for (const line of text.split('\n')) {
    const t = line.trim()
    if (inFence) { if (t.startsWith('```')) inFence = false; continue }
    if (t.startsWith('```')) { inFence = true; continue }
    if (inMath) { if (t.includes('$$') || t.includes('\\]')) inMath = false; continue }
    if (t.startsWith('$$') || t.startsWith('\\[')) {
      inMath = !(t.length > 2 && (t.endsWith('$$') || t.endsWith('\\]')))
      continue
    }
    if (t.startsWith('|') && t.endsWith('|') && t.length > 1) continue
    out.push(
      line
        .replace(/^\s{0,3}#{1,6}\s+/, '')
        .replace(/^\s*[-*+]\s+/, '')
        .replace(/^\s*\d+\.\s+/, '')
        .replace(/^\s*>\s?/, '')
        .replace(/\*\*([^*]+)\*\*/g, '$1')
        .replace(/(^|\W)\*([^*\n]+)\*/g, '$1$2')
        .replace(/`([^`\n]+)`/g, '$1'),
    )
  }
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim()
}

/** Does this reply want the workspace? A spoken answer with nothing staged does
 *  not — a yes, a no, the time. The orb keeps the screen and the words run
 *  along the bottom. One staged window and the board opens. */
export function hasArtifacts(panels: VoicePanel[]): boolean {
  return panels.length > 0
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
