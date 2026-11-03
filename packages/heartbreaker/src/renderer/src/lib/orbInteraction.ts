// SPDX-License-Identifier: AGPL-3.0-or-later
export interface OrbDock { dx: number; dy: number; scale: number }
interface Options {
  dock: OrbDock
  paint: (dock: OrbDock, active: boolean) => void
  commit: (dock: OrbDock) => void
  frame: (callback: () => void) => number
  cancelFrame: (id: number) => void
  delay: (callback: () => void, ms: number) => number
  cancelDelay: (id: number) => void
}

/** Hot pointer/wheel paths touch one compositor transform. Settings and the
 * board only receive the settled result, once per gesture. */
export function createOrbInteraction(options: Options) {
  let current = { ...options.dock }, saved = { ...options.dock }
  let dragging = false, dirty = false, raf: number | null = null, timer: number | null = null
  let origin = { x: 0, y: 0, dx: 0, dy: 0 }
  const cancelTimer = () => { if (timer !== null) options.cancelDelay(timer); timer = null }
  const paint = () => { raf = null; options.paint(current, dragging || dirty) }
  const schedule = () => { if (raf === null) raf = options.frame(paint) }
  const flush = () => {
    cancelTimer()
    if (raf !== null) options.cancelFrame(raf)
    raf = null
    options.paint(current, false)
    if (dirty) { dirty = false; saved = { ...current }; options.commit({ ...current }) }
  }
  return {
    refresh() { options.paint(current, dragging || dirty) },
    sync(dock: OrbDock) {
      if (!dragging && !dirty && (dock.dx !== saved.dx || dock.dy !== saved.dy || dock.scale !== saved.scale)) current = { ...dock }
      saved = { ...dock }
    },
    begin(x: number, y: number) { cancelTimer(); dragging = true; origin = { x, y, dx: current.dx, dy: current.dy } },
    move(x: number, y: number) {
      if (!dragging) return
      current = { ...current, dx: origin.dx + x - origin.x, dy: origin.dy + y - origin.y }
      dirty = true; schedule()
    },
    end() { dragging = false; flush() },
    zoom(delta: number, mode = 0) {
      if (!delta) return
      const pixels = delta * (mode === 1 ? 16 : mode === 2 ? 800 : 1)
      const scale = Math.max(.5, Math.min(2.6, current.scale * Math.exp(-Math.max(-120, Math.min(120, pixels)) * .0015)))
      if (scale === current.scale) return
      current = { ...current, scale }; dirty = true; schedule(); cancelTimer()
      if (!dragging) timer = options.delay(flush, 160)
    },
    dispose() { dragging = false; flush() },
  }
}

/** Bound the backing store without reducing the settled orb's fine lines. */
export function orbBufferSize(width: number, height: number, deviceRatio: number) {
  const w = Math.max(1, Math.round(width)), h = Math.max(1, Math.round(height))
  const ratio = Math.min(Math.max(deviceRatio || 1, 2), 3, 2048 / Math.max(w, h))
  return { width: w, height: h, ratio }
}
