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
 *  - PROSE IS ITS OWN WINDOW, not a strip along the bottom. It was a subtitle
 *    first, on the theory that the words track the voice and are therefore
 *    glanced at rather than read. They do not track it: the text arrives at
 *    generation speed and the speech trails it, so three cramped lines were
 *    unreadable AND out of step. As a window it can be read at leisure, which is
 *    what it was always going to be used for.
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
  // What the agent is SAYING, as a window rather than a strip along the bottom.
  // Also not staged by the agent — the board assembles it from the narration it
  // wrote between the windows.
  | 'narration'

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
  chart: 'chart', calendar: 'calendar', map: 'map', html: 'widget', htm: 'widget', widget: 'widget', svg: 'widget',
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
  quote: 'QUOTE', activity: 'ACTIVITY', narration: 'NARRATION',
}

/** Base window size per kind, in px, before the fit pass. A plot needs width to
 *  be read; a stat tile is one number and wants to stay small enough that a
 *  board of six of them still reads as a row of figures. */
const SIZE: Record<PanelKind, { w: number; h: number }> = {
  math:     { w: 440, h: 200 },
  chart:    { w: 600, h: 390 },
  map:      { w: 520, h: 380 },
  calendar: { w: 640, h: 480 },
  code:     { w: 500, h: 330 },
  widget:   { w: 620, h: 430 },
  table:    { w: 600, h: 420 },
  stat:     { w: 280, h: 190 },
  image:    { w: 420, h: 320 },
  article:  { w: 400, h: 330 },
  card:     { w: 340, h: 340 },
  timeline: { w: 440, h: 320 },
  quote:    { w: 400, h: 190 },
  // Tall rather than wide: it is a running list, and the thing worth seeing is
  // how many steps have happened, not each one's full width.
  activity: { w: 340, h: 320 },
  // The widest of them, and tall. It is prose meant to be READ — the reason it
  // stopped being a three-line caption is that a subtitle only works when it is
  // in step with the voice, and it never was.
  narration: { w: 480, h: 320 },
}

/** Windows that bring no chrome of their own and therefore need the glass. The
 *  rich blocks (chart, calendar, map, code, widget) already draw their own
 *  panel — wrapping those in a second one would double every border. */
export const FRAMED = new Set<PanelKind>([
  'math', 'stat', 'image', 'article', 'card', 'quote',
  'activity', 'narration',
])

/**
 * Kinds whose renderer copes with a HALF-WRITTEN body, and can therefore be put
 * on the board while the agent is still typing it.
 *
 * This is what makes the board assemble in real time instead of popping into
 * existence. Every window used to wait for its closing fence, so a dossier card
 * the model spent eight seconds writing was eight seconds of nothing followed by
 * a card — which is exactly the "no real-time magic" complaint. The presentation
 * parsers were built forgiving precisely so this would be safe: a card with two
 * of its four fields renders those two and grows.
 *
 * A table is here too. Before its delimiter row arrives remark-gfm sees ordinary
 * text, so it reads as a line that then becomes a table — arriving, rather than
 * broken.
 *
 * Everything NOT here waits for its fence to close, and must: a chart spec, a
 * map, a calendar or an HTML widget parsed halfway is not a partial render, it
 * is a syntax error, and LaTeX cut mid-command throws inside KaTeX.
 */
export const STREAMS_LIVE = new Set<PanelKind>([
  'stat', 'image', 'article', 'card', 'timeline', 'quote', 'table',
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

/** The window kind a fence's info line opens, for callers that need to know
 *  what is being written before it finishes. */
export function fenceKindOf(info: string): PanelKind {
  return FENCE_KIND[parseInfo(info).lang] ?? 'code'
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
      // How a body is handed on depends on who renders it.
      //
      //   - The WIDGET kinds (chart, map, calendar, html, svg, code) are keyed
      //     off the fence language by the markdown pipeline, so they keep it.
      //   - TABLE must NOT keep it. Re-fencing pipe rows as ```table produced a
      //     language no renderer claims, so every staged table fell through to
      //     the code block and arrived as raw pipes with a Copy button. Handed
      //     over bare, remark-gfm makes it the table it always was.
      //   - MATH from a ```math fence is LaTeX, and needs the display
      //     delimiters rather than a fence to reach KaTeX.
      //   - The presentation kinds parse their own bodies and take them raw.
      const source = RAW_BODY.has(kind) ? body.join('\n')
        : kind === 'table' ? body.join('\n')
        : kind === 'math' ? ['$$', ...body, '$$'].join('\n')
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

/* Canvas windows retain reading sizes. Overflow belongs to the board's scroll
 * surface, never to a global scale factor that makes every window illegible. */
export interface Placed extends VoicePanel { x: number; y: number; w: number; h: number }
export const GAP = 16

export function pack(panels: VoicePanel[], W: number, H: number, reserveX: number, reserveY: number): Placed[] {
  // Keep the dock's right strip clear while it overlaps this viewport. Reserving
  // it for every row also keeps scrolled content from moving behind the orb.
  const right = reserveY < H && reserveX >= 600 && reserveX < W ? reserveX : W
  const available = Math.max(1, right - GAP * 2 - 12)
  const out: Placed[] = []
  let row: Placed[] = [], rowWidth = 0, y = GAP
  const flush = () => {
    if (!row.length) return
    const flexible = row.filter(p => p.kind !== 'stat')
    const spare = Math.max(0, available - rowWidth)
    const extra = flexible.length ? spare / flexible.length : 0
    let x = GAP
    for (const p of row) {
      const w = Math.min(available, p.w + (p.kind === 'stat' ? 0 : extra))
      out.push({ ...p, x, y, w })
      x += w + GAP
    }
    y += Math.max(...row.map(p => p.h)) + GAP
    row = []; rowWidth = 0
  }
  for (const panel of panels) {
    const base = SIZE[panel.kind].w
    const w = Math.min(base, available, base >= 500 && available >= 1000 && available < 1280 ? (available - GAP) / 2 : available)
    if (row.length && rowWidth + GAP + w > available) flush()
    row.push({ ...panel, x: 0, y, w, h: SIZE[panel.kind].h })
    rowWidth += (row.length > 1 ? GAP : 0) + w
  }
  flush()
  return out
}
