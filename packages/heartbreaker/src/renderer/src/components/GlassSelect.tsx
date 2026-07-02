import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

/**
 * GlassSelect — the fluid-glass replacement for native <select>.
 *
 * Native dropdowns are OS chrome: white panels, blue highlights, no theming.
 * This renders the same compact trigger, but opens a liquid-glass popover in a
 * portal — and freezes the whole background behind it (blur + dim backdrop)
 * as part of the component, so no caller ever has to hand-roll the freeze
 * again. Flips upward when there is no room below, clamps to the viewport,
 * scrolls the selected option into view, closes on Esc / backdrop click.
 */

const MONO = "'Share Tech Mono', monospace"

export interface GlassOption {
  value: string
  label: string
}

interface Pos {
  left: number
  width: number
  maxHeight: number
  /** exactly one of top/bottom is set, depending on flip direction */
  top?: number
  bottom?: number
}

export default function GlassSelect({ value, options, onChange, tint, active = false, title }: {
  value: string
  options: GlassOption[]
  onChange: (value: string) => void
  /** Accent for the active state + selected option. */
  tint: string
  /** Whether the trigger renders in its tinted (pinned/engaged) state. */
  active?: boolean
  title?: string
}) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<Pos | null>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const selectedRef = useRef<HTMLButtonElement>(null)

  const openMenu = () => {
    const r = btnRef.current?.getBoundingClientRect()
    if (!r) return
    const below = window.innerHeight - r.bottom - 12
    const above = r.top - 12
    const up = below < 180 && above > below
    const width = Math.max(r.width, 200)
    setPos({
      left: Math.max(8, Math.min(r.left, window.innerWidth - width - 8)),
      width,
      maxHeight: Math.min(300, up ? above : below),
      ...(up ? { bottom: window.innerHeight - r.top + 4 } : { top: r.bottom + 4 }),
    })
    setOpen(true)
  }

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); setOpen(false) }
    }
    // Capture phase so an overlay's own Esc handler (war room minimize) never
    // fires while the dropdown is the topmost layer.
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [open])

  useLayoutEffect(() => {
    if (open) selectedRef.current?.scrollIntoView({ block: 'nearest' })
  }, [open])

  const current = options.find(o => o.value === value)

  return (
    <>
      <button
        ref={btnRef}
        onClick={openMenu}
        title={title}
        style={{
          width: '100%', height: 18, padding: '0 4px',
          display: 'flex', alignItems: 'center', gap: 4,
          border: `1px solid ${active ? `${tint}88` : 'var(--hb-line)'}`,
          background: active ? `${tint}14` : 'rgba(10, 18, 26, 0.55)',
          color: active ? tint : 'var(--hb-icon)',
          fontFamily: MONO, fontSize: '0.5rem', letterSpacing: '0.05em',
          cursor: 'pointer', transition: 'border-color 0.12s, background 0.12s',
        }}
      >
        <span style={{ flex: 1, minWidth: 0, textAlign: 'left', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {current?.label ?? value}
        </span>
        <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
          style={{ flexShrink: 0, transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}>
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && pos && createPortal(
        <>
          {/* The freeze — one dimmed blur sheet over everything behind the menu. */}
          <div
            onClick={() => setOpen(false)}
            style={{
              position: 'fixed', inset: 0, zIndex: 950,
              background: 'rgba(4, 9, 12, 0.45)',
              backdropFilter: 'var(--hb-holo-blur)', WebkitBackdropFilter: 'var(--hb-holo-blur)',
              animation: 'fadeIn 0.15s ease both',
            }}
          />
          <div
            className="hb-holo"
            style={{
              position: 'fixed', zIndex: 951,
              left: pos.left, top: pos.top, bottom: pos.bottom,
              width: pos.width, maxHeight: pos.maxHeight,
              overflowY: 'auto', padding: '0.25rem 0',
              // Occluding glass — never trust backdrop blur alone for
              // readability (nested backdrop roots cancel it).
              background:
                'linear-gradient(rgba(190, 215, 235, 0.06), rgba(190, 215, 235, 0.06)), rgba(8, 14, 22, 0.9)',
              animation: 'dropDown 0.15s ease both',
            }}
          >
            {options.map(o => {
              const selected = o.value === value
              return (
                <button
                  key={o.value}
                  ref={selected ? selectedRef : undefined}
                  className="hb-glass-opt"
                  onClick={() => { onChange(o.value); setOpen(false) }}
                  style={{
                    display: 'block', width: '100%', textAlign: 'left',
                    padding: '0.32rem 0.6rem', border: 'none', cursor: 'pointer',
                    background: selected ? `${tint}14` : 'transparent',
                    borderLeft: `2px solid ${selected ? tint : 'transparent'}`,
                    color: selected ? tint : 'var(--hb-text-dim)',
                    fontFamily: MONO, fontSize: '0.56rem', letterSpacing: '0.08em',
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  }}
                >
                  {o.label}
                </button>
              )
            })}
          </div>
        </>,
        document.body,
      )}
    </>
  )
}
