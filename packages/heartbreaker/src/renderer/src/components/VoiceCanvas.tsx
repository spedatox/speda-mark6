// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { TextSegment } from './Message'
import { FRAMED, GAP, pack, type Placed, type VoicePanel } from '../lib/voicePanels'

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  THE CANVAS — voice mode's heads-up workspace.
 *
 *  Voice mode does not show a document. It shows a WORKSPACE: the answer is
 *  taken apart and each piece is given its own window, floating over the void
 *  with the orb docked in the corner. Ask for the solution to an equation and
 *  three windows materialise — what Speda is saying, the worked solution, the
 *  plot — instead of one column you have to read top to bottom.
 *
 *  That is the whole point of the mode being a different mode. A transcript
 *  rendered a bit larger is still a transcript; this is an instrument panel.
 *
 *  Two things own a window's position:
 *    - the LAYOUT, which packs windows as they materialise (Speda managing the
 *      board), and re-packs whenever a new one arrives; and
 *    - the OWNER, who can drag any window anywhere. A dragged window is pinned
 *      — the layout stops moving it — until REFLOW hands the board back.
 * ════════════════════════════════════════════════════════════════════════════
 */

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

/** A window's contents, rendered by the transcript's own markdown pipeline. The
 *  narrative window rides its own tail while the answer is still being spoken —
 *  what is being said NOW is the part worth having on screen. */
function PanelBody({ panel }: { panel: VoicePanel }) {
  const framed = FRAMED.has(panel.kind)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (panel.kind !== 'text' || !ref.current) return
    ref.current.scrollTo({ top: ref.current.scrollHeight, behavior: 'smooth' })
  }, [panel.kind, panel.source])

  return (
    <div
      ref={ref}
      className={framed ? 'hb-holo' : undefined}
      style={{
        flex: 1, minHeight: 0, overflow: 'auto',
        padding: framed ? '0.7rem 0.85rem' : 0,
      }}
    >
      <div className="prose" style={{ overflowWrap: 'anywhere', minWidth: 0 }}>
        <TextSegment text={panel.source} />
      </div>
    </div>
  )
}

interface Props {
  panels: VoicePanel[]
  width: number
  height: number
  /** Top-left corner of the docked orb's keep-out quadrant. */
  reserveX: number
  reserveY: number
  /** Bumped by the owner's REFLOW — hands every pinned window back to the layout. */
  reflow: number
}

export default function VoiceCanvas({ panels, width, height, reserveX, reserveY, reflow }: Props) {
  /** Windows the owner has dragged. Absolute, and immune to the layout. */
  const [pinned, setPinned] = useState<Record<string, { x: number; y: number }>>({})
  const [z, setZ] = useState<Record<string, number>>({})
  const [dragging, setDragging] = useState<string | null>(null)
  /** One window blown up to fill the board — a chart on a laptop needs it. */
  const [full, setFull] = useState<string | null>(null)
  const topZ = useRef(10)

  const placed = useMemo(
    () => pack(panels, width, height, reserveX, reserveY),
    [panels, width, height, reserveX, reserveY],
  )

  useEffect(() => { setPinned({}); setFull(null) }, [reflow])
  // A new conversation is a new board.
  useEffect(() => { if (!panels.length) { setPinned({}); setFull(null) } }, [panels.length])

  const raise = useCallback((id: string) => {
    setZ(prev => (prev[id] === topZ.current ? prev : { ...prev, [id]: ++topZ.current }))
  }, [])

  /* ── Arrival ──────────────────────────────────────────────────────────────
   * Windows come in ONE AT A TIME, even when the answer lands in a single burst.
   * A fast model finishes a whole reply between two frames, so without this the
   * board simply appears — five windows blinking into existence at once, which
   * reads as a page load rather than as an assembly.
   *
   * Each window's entrance is delayed by its position among the ones that
   * arrived together, and the delay is remembered per id: a re-render (a drag, a
   * repack, more text landing in the narrative) must never replay an entrance
   * that has already happened. */
  const arrival = useRef<Map<string, number>>(new Map())
  const seenCount = useRef(0)
  {
    // Assign in render — the delay has to exist on the very first paint of a
    // window, and an effect runs a frame too late to matter.
    let batch = 0
    for (const p of panels) {
      if (arrival.current.has(p.id)) continue
      arrival.current.set(p.id, batch++)
    }
    if (panels.length === 0 && seenCount.current !== 0) arrival.current.clear()
    seenCount.current = panels.length
  }

  const startDrag = useCallback((e: React.PointerEvent, p: Placed) => {
    if (full) return
    e.preventDefault()
    raise(p.id)
    setDragging(p.id)
    const originX = e.clientX, originY = e.clientY
    const from = { x: p.x, y: p.y }
    const move = (ev: PointerEvent) => {
      setPinned(prev => ({
        ...prev,
        [p.id]: {
          // Never let a window be dragged fully off the board — the grip has to
          // stay reachable or it is gone for good.
          x: clamp(from.x + ev.clientX - originX, 90 - p.w, width - 90),
          y: clamp(from.y + ev.clientY - originY, 0, height - 34),
        },
      }))
    }
    const up = () => {
      setDragging(null)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }, [full, height, width, raise])

  return (
    <div style={{ position: 'absolute', inset: 0, zIndex: 2, overflow: 'hidden' }}>
      {placed.map(p => {
        const pin = pinned[p.id]
        const isFull = full === p.id
        const box = isFull
          ? { x: GAP, y: GAP, w: width - GAP * 2, h: height - GAP * 2 }
          : { x: pin?.x ?? p.x, y: pin?.y ?? p.y, w: p.w, h: p.h }
        const [main, sub] = [p.title.slice(0, p.title.indexOf('_')), p.title.slice(p.title.indexOf('_'))]

        return (
          <div
            key={p.id}
            onPointerDown={() => raise(p.id)}
            style={{
              position: 'absolute',
              left: box.x, top: box.y, width: box.w, height: box.h,
              zIndex: (isFull ? 9000 : z[p.id]) ?? 10,
              display: 'flex', flexDirection: 'column',
              // Windows GLIDE when the layout moves them — that motion is what
              // reads as the board being managed rather than redrawn. Killed
              // while dragging, where any easing is just lag.
              transition: dragging === p.id
                ? 'none'
                : 'left 0.45s cubic-bezier(0.4,0,0.2,1), top 0.45s cubic-bezier(0.4,0,0.2,1), width 0.45s cubic-bezier(0.4,0,0.2,1), height 0.45s cubic-bezier(0.4,0,0.2,1)',
              // `both` matters: the window is held invisible through its delay,
              // so a staggered arrival cannot flash the whole board first.
              animation: 'widgetEntrance 0.42s ease both',
              animationDelay: `${(arrival.current.get(p.id) ?? 0) * 160}ms`,
            }}
          >
            {/* Grip — the title, and the only place a drag starts. Deliberately
                not the body: a drag that begins on a chart fights its tooltip. */}
            <div
              onPointerDown={e => startDrag(e, p)}
              style={{
                height: 22, flexShrink: 0,
                display: 'flex', alignItems: 'center', gap: '0.5rem',
                padding: '0 0.1rem 0 0.15rem',
                cursor: isFull ? 'default' : 'move',
                fontFamily: "'Rajdhani', sans-serif", fontSize: '0.62rem',
                fontWeight: 700, letterSpacing: '0.2em',
                userSelect: 'none', touchAction: 'none',
              }}
            >
              <span style={{ color: 'var(--hb-icon)', letterSpacing: 0, fontSize: '0.7rem' }}>⣿</span>
              <span style={{ color: pin ? 'var(--hb-amber)' : '#ffffff' }}>{main}</span>
              <span style={{ color: 'var(--hb-cyan)' }}>{sub}</span>
              <span style={{ flex: 1 }} />
              <button
                className="hb-btn"
                onPointerDown={e => e.stopPropagation()}
                onClick={() => { setFull(f => (f === p.id ? null : p.id)); raise(p.id) }}
                title={isFull ? 'Restore' : 'Fill the board'}
                style={{ height: 18, padding: '0 0.4rem', fontSize: '0.55rem', letterSpacing: '0.12em' }}
              >
                {isFull ? 'CLOSE' : 'EXTEND_'}
              </button>
            </div>

            <PanelBody panel={p} />
          </div>
        )
      })}
    </div>
  )
}
