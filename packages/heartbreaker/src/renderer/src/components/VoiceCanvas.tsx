// SPDX-License-Identifier: AGPL-3.0-or-later
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import VoicePanelBody from './VoicePanelBody'
import { GAP, pack, type Placed, type VoicePanel } from '../lib/voicePanels'
import { RenderSurface } from '../lib/renderSurface'

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))
interface Pin { x: number; y: number; w?: number; h?: number }
interface Props {
  panels: VoicePanel[]
  width: number
  height: number
  reserveX: number
  reserveY: number
  reflow: number
  stagger: number
  activity?: React.ReactNode
}

/** A scrollable workspace. Adding evidence grows the board, not smaller type. */
export default function VoiceCanvas({ panels, width, height, reserveX, reserveY, reflow, stagger, activity }: Props) {
  const [pinned, setPinned] = useState<Record<string, Pin>>({})
  const [z, setZ] = useState<Record<string, number>>({})
  const [held, setHeld] = useState<string | null>(null)
  const [full, setFull] = useState<string | null>(null)
  const restoreScroll = useRef<number | null>(null)
  const scroll = useRef<HTMLDivElement>(null)
  const topZ = useRef(10)
  const endGesture = useRef<() => void>()
  const orbInViewport = reserveX < width && reserveY < height
  const expandedWidth = orbInViewport && reserveX >= 600 ? reserveX : width
  const roomAboveOrb = reserveY - 40
  const bottomInset = orbInViewport && expandedWidth === width && roomAboveOrb >= 260
    ? height - reserveY + GAP : 0
  const viewportHeight = Math.max(100, height - 40 - bottomInset)
  const placed = useMemo(() => pack(panels, width, viewportHeight, reserveX, reserveY - 40), [panels, width, viewportHeight, reserveX, reserveY])
  const contentHeight = Math.max(viewportHeight, ...placed.map(p => (pinned[p.id]?.y ?? p.y) + (pinned[p.id]?.h ?? p.h) + GAP))
  // On small screens where the orb cannot have its own strip, allow the last
  // panel to be scrolled completely above it as well.
  const bottomRoom = !bottomInset && reserveX < 600 ? Math.max(0, viewportHeight - Math.max(0, reserveY - 40)) : 0

  useEffect(() => { setPinned({}); setFull(null); scroll.current?.scrollTo({ top: 0, behavior: 'instant' }) }, [reflow])
  useEffect(() => {
    if (!panels.length) { setPinned({}); setFull(null); scroll.current?.scrollTo({ top: 0 }) }
    else if (full && !panels.some(p => p.id === full)) setFull(null)
  }, [panels, full])
  useEffect(() => {
    if (!full) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setFull(null) }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [full])
  useEffect(() => () => endGesture.current?.(), [])
  useLayoutEffect(() => {
    const el = scroll.current
    if (!el) return
    if (full) el.scrollTo({ top: 0, behavior: 'instant' })
    else if (restoreScroll.current !== null) {
      el.scrollTo({ top: restoreScroll.current, behavior: 'instant' })
      restoreScroll.current = null
    }
  }, [full])

  const raise = useCallback((id: string) => {
    setZ(prev => prev[id] === topZ.current ? prev : { ...prev, [id]: ++topZ.current })
  }, [])
  const arrival = useRef(new Map<string, number>())
  if (!panels.length) arrival.current.clear()
  let batch = 0
  for (const p of panels) if (!arrival.current.has(p.id)) arrival.current.set(p.id, Math.min(batch++, 4))

  const gesture = useCallback((
    event: React.PointerEvent, p: Placed, box: { x: number; y: number; w: number; h: number },
    apply: (dx: number, dy: number, from: typeof box) => Pin,
  ) => {
    if (full || event.button !== 0) return
    event.preventDefault(); event.stopPropagation(); endGesture.current?.()
    raise(p.id); setHeld(p.id)
    const originX = event.clientX, originY = event.clientY, originScroll = scroll.current?.scrollTop ?? 0
    const move = (e: PointerEvent) => setPinned(prev => ({ ...prev, [p.id]: apply(e.clientX - originX, e.clientY - originY + (scroll.current?.scrollTop ?? 0) - originScroll, box) }))
    const end = () => {
      setHeld(null)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', end)
      window.removeEventListener('pointercancel', end)
      endGesture.current = undefined
    }
    endGesture.current = end
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', end)
    window.addEventListener('pointercancel', end)
  }, [full, raise])

  const toggle = (id: string) => {
    if (full === id) setFull(null)
    else { restoreScroll.current = scroll.current?.scrollTop ?? 0; setFull(id); raise(id) }
  }
  const slide = (direction: number) => scroll.current?.scrollBy({ top: direction * viewportHeight * .8, behavior: 'smooth' })

  return <div className="hb-canvas-workspace" style={{ position: 'absolute', inset: 0, zIndex: 2 }}>
    <div className="hb-canvas-navigation">
      <span>{panels.length} panels <span className="hb-canvas-nav-hint">· {full ? 'Expanded view · Esc to restore' : contentHeight > viewportHeight ? 'Scroll to explore' : 'Drag to arrange'}</span></span>
      <div><button aria-label="Scroll canvas up" disabled={!!full} onClick={() => slide(-1)}>↑</button><button aria-label="Scroll canvas down" disabled={!!full} onClick={() => slide(1)}>↓</button></div>
    </div>
    <div ref={scroll} className="hb-canvas-scroll" role="region" aria-label="Canvas workspace" tabIndex={0}
      onScroll={event => { if (full && event.currentTarget.scrollTop) event.currentTarget.scrollTop = 0 }}
      style={{ position: 'absolute', inset: `40px 0 ${bottomInset}px`, overflow: full ? 'hidden' : 'auto', scrollbarGutter: 'stable', overscrollBehavior: 'contain' }}>
      <div style={{ position: 'relative', minHeight: contentHeight + bottomRoom }}>
        {placed.map(p => {
          const pin = pinned[p.id], isFull = full === p.id
          const w = Math.min(pin?.w ?? p.w, Math.max(1, width - GAP * 2 - 12))
          const box = isFull
            ? { x: GAP, y: GAP, w: Math.max(1, expandedWidth - GAP * 2 - 12), h: viewportHeight - GAP * 2 }
            : { x: clamp(pin?.x ?? p.x, 0, Math.max(0, width - w - GAP)), y: pin?.y ?? p.y, w, h: pin?.h ?? p.h }
          const title = p.title.replace(/_/g, ' ').trim() || p.kind
          return <section key={p.id} aria-label={title} data-panel-id={p.id} data-kind={p.kind} data-expanded={isFull}
            className="hb-canvas-panel" onPointerDown={() => raise(p.id)}
            style={{ position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h,
              zIndex: isFull ? 9000 : z[p.id] ?? 10, display: 'flex', flexDirection: 'column',
              visibility: full && !isFull ? 'hidden' : 'visible',
              transition: held === p.id || full ? 'none' : 'left .2s ease, top .2s ease, width .2s ease, height .2s ease',
              animation: 'widgetEntrance .25s ease both', animationDelay: `${(arrival.current.get(p.id) ?? 0) * Math.min(stagger, 100)}ms`,
            }}>
            <div className="hb-canvas-grip" onPointerDown={e => gesture(e, p, box, (dx, dy, from) => ({
              x: clamp(from.x + dx, 0, Math.max(0, width - from.w - GAP)), y: Math.max(0, from.y + dy), w: from.w, h: from.h,
            }))} style={{ cursor: isFull ? 'default' : 'move' }}>
              <span className="hb-canvas-grip-mark" aria-hidden>⠿</span><span className="hb-canvas-panel-title" title={title}>{title}</span>
              <button onPointerDown={e => e.stopPropagation()} onClick={() => toggle(p.id)} aria-expanded={isFull}
                aria-label={`${isFull ? 'Restore' : 'Expand'} ${title}`}>{isFull ? 'Restore' : 'Expand'}</button>
            </div>
            <RenderSurface.Provider value={{ canvas: true, expanded: isFull }}>
              {p.kind === 'activity' ? <div className="hb-canvas-activity">{activity}</div> : <VoicePanelBody panel={p} />}
            </RenderSurface.Provider>
            {!isFull && <div className="hb-canvas-resize" title="Resize panel" onPointerDown={e => gesture(e, p, box, (dx, dy, from) => ({
              x: from.x, y: from.y, w: clamp(from.w + dx, Math.min(280, width - GAP * 2), Math.max(1, width - from.x - GAP - 12)),
              h: clamp(from.h + dy, p.kind === 'stat' ? 150 : 240, 1600),
            }))} />}
          </section>
        })}
      </div>
    </div>
  </div>
}
