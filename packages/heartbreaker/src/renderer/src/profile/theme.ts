/**
 * ════════════════════════════════════════════════════════════════════════════
 *  THE colour system. One accent in → the ENTIRE palette out, re-hued.
 *
 *  The Stark theme is a single cool-petrol hue (~195°) spanning everything:
 *  the near-black backgrounds, the glass rims, the text, the dim icon scale and
 *  the bright accent. This module takes a brand's accent, reads its HUE, and
 *  regenerates the whole token set at that hue — keeping each token's original
 *  saturation + lightness so the *structure* (depth, contrast, the liquid-glass
 *  feel) is identical, only the colour shifts. Centurion → red everywhere,
 *  Atomix → green everywhere, background included.
 *
 *  Semantic colours (amber = selected, green = ok, red = alert) and external
 *  brand colours (Google) are NOT touched — they carry meaning regardless of
 *  which agent is active.
 * ════════════════════════════════════════════════════════════════════════════
 */

interface Rgb { r: number; g: number; b: number }

function hexToRgb(hex: string): Rgb {
  const h = hex.replace('#', '')
  return { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16) }
}
function clamp(n: number): number { return Math.max(0, Math.min(255, Math.round(n))) }
function toHex(n: number): string { return clamp(n).toString(16).padStart(2, '0') }
function rgbToHex({ r, g, b }: Rgb): string { return `#${toHex(r)}${toHex(g)}${toHex(b)}` }

function rgbToHsl({ r, g, b }: Rgb): { h: number; s: number; l: number } {
  r /= 255; g /= 255; b /= 255
  const max = Math.max(r, g, b), min = Math.min(r, g, b)
  let h = 0, s = 0
  const l = (max + min) / 2
  if (max !== min) {
    const d = max - min
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
    if (max === r) h = (g - b) / d + (g < b ? 6 : 0)
    else if (max === g) h = (b - r) / d + 2
    else h = (r - g) / d + 4
    h /= 6
  }
  return { h: h * 360, s, l }
}

function hslToRgb(h: number, s: number, l: number): Rgb {
  h /= 360
  const hue2rgb = (p: number, q: number, t: number): number => {
    if (t < 0) t += 1
    if (t > 1) t -= 1
    if (t < 1 / 6) return p + (q - p) * 6 * t
    if (t < 1 / 2) return q
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6
    return p
  }
  if (s === 0) return { r: l * 255, g: l * 255, b: l * 255 }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s
  const p = 2 * l - q
  return { r: hue2rgb(p, q, h + 1 / 3) * 255, g: hue2rgb(p, q, h) * 255, b: hue2rgb(p, q, h - 1 / 3) * 255 }
}

function mix(hex: string, target: string, t: number): string {
  const a = hexToRgb(hex), b = hexToRgb(target)
  return rgbToHex({ r: a.r + (b.r - a.r) * t, g: a.g + (b.g - a.g) * t, b: a.b + (b.b - a.b) * t })
}

const WHITE = '#ffffff'
const VOID = '#04080a'

/** Re-hue a base colour to `hue`, preserving its saturation & lightness. */
function rehue(baseHex: string, hue: number): Rgb {
  const { s, l } = rgbToHsl(hexToRgb(baseHex))
  return hslToRgb(hue, s, l)
}

// Structural palette — the original cool-petrol values. Each is re-hued to the
// agent's hue at runtime. This is the whole UI: backgrounds, surfaces, text,
// lines, glass rims and the dim icon scale.
const BASE_HEX: Record<string, string> = {
  '--hb-void': '#04080a', '--hb-base': '#060c0f', '--hb-petrol': '#0b1a22', '--hb-steel': '#13303b',
  '--hb-text': '#cadbe2', '--hb-text-dim': '#7a96a1', '--hb-text-faint': '#46626d',
  '--bg-code': '#08151b', '--bg-code-header': '#0a1d25',
  '--hb-icon': '#3a6472', '--hb-icon-dim': '#2e5260', '--hb-icon-bright': '#5d7f8a',
}
// rgba tokens — [base colour for hue/sat/light, alpha].
const BASE_RGBA: Record<string, [string, number]> = {
  '--hb-line': ['#5fa5bc', 0.26], '--hb-line-bright': ['#6ec8e4', 0.55],
  '--hb-edge': ['#96cdf5', 0.22], '--hb-edge-bright': ['#aae1ff', 0.55],
  '--bg-sidebar': ['#081217', 0.72], '--bg-hover': ['#4696af', 0.12], '--bg-input': ['#08141a', 0.66],
  '--bg-user-bubble': ['#183844', 0.46], '--scrollbar-thumb': ['#468ca0', 0.32], '--scrollbar-thumb-hover': ['#5aafc8', 0.55],
}

/** Derive the bright (active) and dim shades from a single accent. */
export function deriveAccents(accent: string): { accent: string; bright: string; dim: string } {
  return { accent, bright: mix(accent, WHITE, 0.28), dim: mix(accent, VOID, 0.62) }
}

/** Build every CSS custom property the UI uses, re-hued to the brand accent. */
export function buildThemeVars(accent: string): Record<string, string> {
  const { h } = rgbToHsl(hexToRgb(accent))
  const { bright, dim } = deriveAccents(accent)
  const a = hexToRgb(accent), br = hexToRgb(bright), dm = hexToRgb(dim)
  const out: Record<string, string> = {}

  for (const k in BASE_HEX) out[k] = rgbToHex(rehue(BASE_HEX[k], h))
  for (const k in BASE_RGBA) {
    const [hex, alpha] = BASE_RGBA[k]
    const c = rehue(hex, h)
    out[k] = `rgba(${clamp(c.r)}, ${clamp(c.g)}, ${clamp(c.b)}, ${alpha})`
  }

  // Accent family — the EXACT brand colour (not re-hued, so it stays true).
  out['--hb-cyan'] = accent
  out['--hb-cyan-bright'] = bright
  out['--hb-cyan-dim'] = dim
  out['--accent'] = accent
  out['--accent-hover'] = bright
  out['--accent-muted'] = `rgba(${a.r}, ${a.g}, ${a.b}, 0.15)`
  out['--bg-active'] = `rgba(${a.r}, ${a.g}, ${a.b}, 0.16)`
  out['--bg-primary'] = out['--hb-base']
  // Raw triplets so components can tint at any alpha.
  out['--hb-accent-rgb'] = `${a.r}, ${a.g}, ${a.b}`
  out['--hb-cyan-bright-rgb'] = `${br.r}, ${br.g}, ${br.b}`
  out['--hb-cyan-dim-rgb'] = `${dm.r}, ${dm.g}, ${dm.b}`
  // Filled header-bar gradient.
  out['--hb-bar-cyan'] = `linear-gradient(180deg, ${bright} 0%, ${accent} 70%, ${dim} 100%)`

  return out
}

/** Apply a brand's palette to the document root. Call once at startup. */
export function applyTheme(accent: string): void {
  const root = document.documentElement
  const vars = buildThemeVars(accent)
  for (const key in vars) root.style.setProperty(key, vars[key])
}

let _morphRaf = 0

/* ══════════════════════════════════════════════════════════════════════════
   HOUSE PARTY PROTOCOL — the whole-app colour parade.

   While the protocol is engaged the ENTIRE palette (backgrounds, glass rims,
   text, icons — everything applyTheme touches) drifts continuously through
   the full roster's signature colours, one agent at a time, in ROSTER order.
   The message: they are ALL here. The cadence matches the hbPartyCycle CSS
   keyframe (~3s per agent); updates are throttled to ~12Hz — the drift is
   slow enough that finer steps are invisible, and a full :root palette
   rebuild every frame would be wasted style recalc.
   ══════════════════════════════════════════════════════════════════════════ */

let _partyRaf = 0
let _partyOn = false

export function isPartyCycling(): boolean { return _partyOn }

export function startPartyCycle(fromAccent: string, msPerStop = 3000): void {
  stopPartyCycle()
  cancelAnimationFrame(_morphRaf)
  _partyOn = true
  const colors = PARTY_COLORS
  const n = colors.length
  const LEAD_MS = 700   // ease out of the current brand into the parade
  const TICK_MS = 80
  const start = performance.now()
  let last = 0
  const ease = (t: number): number => t * t * (3 - 2 * t)  // smoothstep
  const step = (now: number) => {
    _partyRaf = requestAnimationFrame(step)
    if (now - last < TICK_MS) return
    last = now
    const el = now - start
    if (el < LEAD_MS) { applyTheme(mix(fromAccent, colors[0], ease(el / LEAD_MS))); return }
    const t = (el - LEAD_MS) / msPerStop
    const i = Math.floor(t) % n
    applyTheme(mix(colors[i], colors[(i + 1) % n], ease(t - Math.floor(t))))
  }
  _partyRaf = requestAnimationFrame(step)
}

export function stopPartyCycle(): void {
  _partyOn = false
  cancelAnimationFrame(_partyRaf)
}

// ROSTER order, colours mirroring lib/agents AGENT_COLORS — inlined here so
// the theme engine keeps zero imports from component-land.
const PARTY_COLORS = [
  '#36abca', /* speda */ '#d99c44', /* sentinel */ '#9165e6', /* nightcrawler */
  '#8a93a6', /* ultron */ '#d8483c', /* centurion */ '#3fae74', /* atomix */
  '#2eb6ac', /* optimus */ '#8a7fd6', /* orion */
]

/**
 * Smoothly morph the entire UI from one accent to another over `ms` milliseconds.
 * Every frame, the accent is linearly interpolated and the full palette is
 * rebuilt + applied — so backgrounds, rims, text, icons, glass, everything
 * shifts hue together in real time. Returns a Promise that resolves when done.
 */
export function morphTheme(from: string, to: string, ms = 500): Promise<void> {
  cancelAnimationFrame(_morphRaf)
  return new Promise(resolve => {
    const start = performance.now()
    const step = (now: number) => {
      const t = Math.min((now - start) / ms, 1)
      const eased = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2
      applyTheme(mix(from, to, eased))
      if (t < 1) { _morphRaf = requestAnimationFrame(step) }
      else resolve()
    }
    _morphRaf = requestAnimationFrame(step)
  })
}
