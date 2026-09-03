// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import VoicePanelBody from './VoicePanelBody'
import { GAP, pack, type Placed, type VoicePanel } from '../lib/voicePanels'

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  THE CANVAS — voice mode's presentation board.
 *
 *  Voice mode does not show a document and it does not show a conversation. The
 *  agent PRESENTS: it narrates, and the board carries the evidence it is
 *  narrating about — a figure as a tile, a source as a cutting with its photo,
 *  a person as a file, each in its own window. What is being said runs as a
 *  subtitle under the orb (VoiceMode's caption strip), because a window holding
 *  the sentences the owner is currently hearing is a transcript with a bigger
 *  font, which is the thing this mode exists not to be.
 *
 *  The agent stages these windows itself and places each one in its reply at
 *  the moment its narration reaches it (igor core/surface.py _VOICE_BRIEF), so
 *  the board assembles in step with the voice without anything having to sync
 *  against audio timestamps. Writing order is the cue track.
 *
 *  Three things own a window's geometry:
 *    - the LAYOUT, which packs windows as they materialise (the agent managing
 *      the board), and re-packs whenever a new one arrives;
 *    - the OWNER, who can drag any window anywhere and resize it from its
 *      bottom-right corner. Either gesture PINS the window — the layout stops
 *      touching it — until REFLOW hands the board back; and
 *    - EXTEND, which blows one window up to fill the board and is the only
 *      state that overrides the other two.
 * ════════════════════════════════════════════════════════════════════════════
 */

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

/** Floor for a hand-resized window. Smaller than this and the grip, the title
 *  and the EXTEND button no longer fit on one row, which loses the only handle
 *  the window can be recovered by. */
const MIN_W = 200
const MIN_H = 90

/** A window's own geometry once the owner has touched it. Position and size are
 *  one record because either gesture pins the window — a window the owner has
 *  resized must not then be slid across the board by the next repack. */
interface Pin { x: number; y: number; w?: number; h?: number }

interface Props {
  panels: VoicePanel[]
  width: number
  height: number
  /** Top-left corner of the docked orb's keep-out quadrant. */
  reserveX: number
  reserveY: number
  /** Bumped by the owner's REFLOW — hands every pinned window back to the layout. */
  reflow: number
  /** Delay between one window's entrance and the next, from Settings → Canvas. */
  stagger: number
}

export default function VoiceCanvas({
  panels, width, height, reserveX, reserveY, reflow, stagger,
}: Props) {
  /** Windows the owner has moved or resized. Absolute, and immune to the layout. */
  const [pinned, setPinned] = useState<Record<string, Pin>>({})
  const [z, setZ] = useState<Record<string, number>>({})
  /** The window currently under a pointer gesture — its transition is killed for
   *  the duration, because easing a window that is following the cursor is lag. */
  const [held, setHeld] = useState<string | null>(null)
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
   * repack, more text landing in a window) must never replay an entrance that
   * has already happened. */
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

  /** One pointer gesture, for both the grip and the resize corner. They differ
   *  only in what the delta is applied to, which is not worth two listeners'
   *  worth of duplicated setup and teardown. */
  const gesture = useCallback((
    e: React.PointerEvent, p: Placed, box: { x: number; y: number; w: number; h: number },
    apply: (dx: number, dy: number, from: typeof box) => Pin,
  ) => {
    if (full) return
    e.preventDefault()
    e.stopPropagation()
    raise(p.id)
    setHeld(p.id)
    const originX = e.clientX, originY = e.clientY
    const from = { ...box }
    const move = (ev: PointerEvent) => {
      setPinned(prev => ({ ...prev, [p.id]: apply(ev.clientX - originX, ev.clientY - originY, from) }))
    }
    const up = () => {
      setHeld(null)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }, [full, raise])

  return (
    <div style={{ position: 'absolute', inset: 0, zIndex: 2, overflow: 'hidden' }}>
      {placed.map(p => {
        const pin = pinned[p.id]
        const isFull = full === p.id
        const box = isFull
          ? { x: GAP, y: GAP, w: width - GAP * 2, h: height - GAP * 2 }
          : { x: pin?.x ?? p.x, y: pin?.y ?? p.y, w: pin?.w ?? p.w, h: pin?.h ?? p.h }
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
              // while the owner is dragging or resizing, where easing is lag.
              transition: held === p.id
                ? 'none'
                : 'left 0.45s cubic-bezier(0.4,0,0.2,1), top 0.45s cubic-bezier(0.4,0,0.2,1), width 0.45s cubic-bezier(0.4,0,0.2,1), height 0.45s cubic-bezier(0.4,0,0.2,1)',
              // `both` matters: the window is held invisible through its delay,
              // so a staggered arrival cannot flash the whole board first.
              animation: 'widgetEntrance 0.42s ease both',
              animationDelay: `${(arrival.current.get(p.id) ?? 0) * stagger}ms`,
            }}
          >
            {/* Grip — the title, and the only place a drag starts. Deliberately
                not the body: a drag that begins on a chart fights its tooltip. */}
            <div
              onPointerDown={e => gesture(e, p, box, (dx, dy, from) => ({
                // Never let a window be dragged fully off the board — the grip
                // has to stay reachable or it is gone for good.
                x: clamp(from.x + dx, 90 - from.w, width - 90),
                y: clamp(from.y + dy, 0, height - 34),
                w: from.w, h: from.h,
              }))}
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

            <VoicePanelBody panel={p} />

            {/* Resize corner. Hidden while a window fills the board, where the
                board's edge is the size and there is nothing to drag against. */}
            {!isFull && (
              <div
                onPointerDown={e => gesture(e, p, box, (dx, dy, from) => ({
                  x: from.x, y: from.y,
                  // Clamped against the board's far edge as well as the floor, so
                  // a window cannot be grown out past where its own corner can be
                  // reached to shrink it again.
                  w: clamp(from.w + dx, MIN_W, Math.max(MIN_W, width - from.x - 4)),
                  h: clamp(from.h + dy, MIN_H, Math.max(MIN_H, height - from.y - 4)),
                }))}
                title="Resize"
                style={{
                  position: 'absolute', right: 0, bottom: 0,
                  width: 16, height: 16, cursor: 'nwse-resize',
                  touchAction: 'none',
                  // The corner mark, drawn rather than iconised: two hairlines
                  // meeting, which reads as a grip at this size where a glyph
                  // would just be a smudge.
                  borderRight: '2px solid var(--hb-icon-dim)',
                  borderBottom: '2px solid var(--hb-icon-dim)',
                  borderBottomRightRadius: 4,
                  opacity: 0.75,
                }}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}
