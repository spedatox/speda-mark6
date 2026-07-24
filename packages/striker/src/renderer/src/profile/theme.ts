/**
 * Striker (SPEDA Mark VI Core) is single-agent, so there is no runtime hue-morph
 * or House Party colour parade — the palette is static (theme/striker.css). All
 * that survives here is deriving the profile's hover accent from its base accent.
 */

interface Rgb { r: number; g: number; b: number }

function hexToRgb(hex: string): Rgb {
  const h = hex.replace('#', '')
  return { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16) }
}
function clamp(n: number): number { return Math.max(0, Math.min(255, Math.round(n))) }
function toHex(n: number): string { return clamp(n).toString(16).padStart(2, '0') }
function rgbToHex({ r, g, b }: Rgb): string { return `#${toHex(r)}${toHex(g)}${toHex(b)}` }

function mix(hex: string, target: string, t: number): string {
  const a = hexToRgb(hex), b = hexToRgb(target)
  return rgbToHex({ r: a.r + (b.r - a.r) * t, g: a.g + (b.g - a.g) * t, b: a.b + (b.b - a.b) * t })
}

const WHITE = '#ffffff'
const VOID = '#04080a'

/** Derive the bright (active) and dim shades from a single accent. */
export function deriveAccents(accent: string): { accent: string; bright: string; dim: string } {
  return { accent, bright: mix(accent, WHITE, 0.28), dim: mix(accent, VOID, 0.62) }
}
