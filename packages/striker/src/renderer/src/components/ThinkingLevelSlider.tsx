// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { THINKING_LEVELS, type ThinkingLevel } from '../lib/types'
import { useT } from '../lib/i18n'

/**
 * How hard one model is asked to think — NONE · LOW · MID · HIGH.
 *
 * A four-stop track rather than a free slider: the backend's vocabulary is
 * four discrete levels (llm_client.THINKING_LEVELS), and a continuous control
 * would imply a precision no provider actually offers. The fill runs from the
 * left so "how far along the track" reads as "how much thinking", and NONE is
 * a real stop at the origin rather than an off-switch hidden somewhere else —
 * it is the whole point of the control for a model that burns a minute on a
 * one-line question.
 *
 * `supported: false` (Ollama, NVIDIA — no reasoning knob this backend can
 * reach) renders the track disabled with the reason on hover, rather than
 * hiding it: a missing control invites the question, a greyed one answers it.
 */
export default function ThinkingLevelSlider({
  value, pinned, supported = true, onChange, compact = false,
}: {
  value: ThinkingLevel
  /** The owner's own choice vs. the inherited global default — the default is
   *  drawn dimmer, so a picker full of models reads as "these two I've set". */
  pinned?: boolean
  supported?: boolean
  onChange: (level: ThinkingLevel) => void
  /** Dense variant for a list row, vs. the roomier settings/board one. */
  compact?: boolean
}) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ right: number; bottom: number } | null>(null)
  const idx = Math.max(0, THINKING_LEVELS.indexOf(value))
  const tint = supported
    ? (pinned ? 'var(--hb-cyan-bright)' : 'var(--hb-icon-bright)')
    : 'var(--hb-icon-dim)'

  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent) => {
      const node = event.target as Node
      if (!buttonRef.current?.contains(node) && !panelRef.current?.contains(node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  if (compact) {
    const toggle = () => {
      if (!supported) return
      const rect = buttonRef.current?.getBoundingClientRect()
      if (rect) setPos({ right: Math.max(8, window.innerWidth - rect.right), bottom: window.innerHeight - rect.top + 6 })
      setOpen(v => !v)
    }
    return <>
      <button ref={buttonRef} type="button" disabled={!supported} onClick={toggle}
        className="hb-glass-xs"
        title={supported ? t.thinkingLevel.hint(t.thinkingLevel.names[value], pinned ? t.thinkingLevel.pinned : t.thinkingLevel.inherited) : t.thinkingLevel.unsupported}
        aria-expanded={open}
        style={{ height: 30, padding: '0 0.55rem', display: 'flex', alignItems: 'center', gap: '0.35rem', color: supported ? tint : 'var(--hb-icon-dim)', cursor: supported ? 'pointer' : 'not-allowed', fontFamily: "'Rajdhani',sans-serif", fontSize: '0.65rem', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>
        <span>{t.thinkingLevel.label}</span>
        <span style={{ color: supported ? 'var(--hb-cyan-bright)' : 'var(--hb-icon-dim)' }}>{supported ? t.thinkingLevel.names[value] : t.thinkingLevel.na}</span>
      </button>
      {open && pos && createPortal(
        <div ref={panelRef} className="hb-glass" style={{ position: 'fixed', right: pos.right, bottom: pos.bottom, zIndex: 9952, padding: '0.65rem 0.75rem', animation: 'dropDown 0.12s ease' }}>
          <ThinkingLevelSlider value={value} pinned={pinned} supported={supported} onChange={onChange} />
        </div>, document.body,
      )}
    </>
  }

  return (
    <div
      style={{ display: 'flex', alignItems: 'center', gap: compact ? '0.4rem' : '0.55rem' }}
      title={supported
        ? t.thinkingLevel.hint(t.thinkingLevel.names[value], pinned ? t.thinkingLevel.pinned : t.thinkingLevel.inherited)
        : t.thinkingLevel.unsupported}
    >
      <div style={{ position: 'relative', width: 150, height: 28, opacity: supported ? 1 : 0.4 }}>
        <input className="thinking-range" type="range" min={0} max={THINKING_LEVELS.length - 1} step={1}
          value={idx} disabled={!supported} aria-label={t.thinkingLevel.label}
          onChange={event => onChange(THINKING_LEVELS[Number(event.target.value)])}
          style={{ background: `linear-gradient(to right, ${tint} 0%, ${tint} ${(idx / (THINKING_LEVELS.length - 1)) * 100}%, rgba(var(--hb-accent-rgb),0.18) ${(idx / (THINKING_LEVELS.length - 1)) * 100}%, rgba(var(--hb-accent-rgb),0.18) 100%)` }} />
        <div className="thinking-range-ticks" aria-hidden="true">
          {THINKING_LEVELS.map(level => <i key={level} />)}
        </div>
      </div>
      <span style={{
        fontFamily: "'Rajdhani',sans-serif", fontSize: compact ? '0.62rem' : '0.68rem',
        fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase',
        color: supported ? tint : 'var(--hb-icon-dim)',
        minWidth: compact ? 30 : 38, userSelect: 'none',
      }}>
        {supported ? t.thinkingLevel.names[value] : t.thinkingLevel.na}
      </span>
    </div>
  )
}
