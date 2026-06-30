/** Tiny animation helpers shared across teaser scenes. */

export const clamp01 = (x: number): number => Math.max(0, Math.min(1, x))
export const easeOut = (x: number): number => 1 - Math.pow(1 - clamp01(x), 3)
export const easeInOut = (x: number): number =>
  x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2

/** Opacity envelope: fade in over `fin`s, hold, fade out over `fout`s. */
export function envelope(local: number, total: number, fin = 0.6, fout = 0.6): number {
  const a = clamp01(local / fin)
  const b = clamp01((total - local) / fout)
  return Math.min(a, b)
}

/** Translate a 0..1 progress into a "slide up + fade" transform set. */
export function rise(p: number, px = 16): { opacity: number; transform: string } {
  const e = easeOut(p)
  return { opacity: e, transform: `translateY(${(1 - e) * px}px)` }
}
