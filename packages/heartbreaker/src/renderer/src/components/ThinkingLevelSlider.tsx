// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState } from 'react'
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
  const [hover, setHover] = useState<ThinkingLevel | null>(null)
  const idx = Math.max(0, THINKING_LEVELS.indexOf(value))
  const shown = hover ?? value
  const shownIdx = Math.max(0, THINKING_LEVELS.indexOf(shown))

  const h = compact ? 3 : 4
  const dot = compact ? 7 : 9
  const tint = supported
    ? (pinned ? 'var(--hb-cyan-bright)' : 'var(--hb-icon-bright)')
    : 'var(--hb-icon-dim)'

  return (
    <div
      style={{ display: 'flex', alignItems: 'center', gap: compact ? '0.4rem' : '0.55rem' }}
      title={supported
        ? t.thinkingLevel.hint(t.thinkingLevel.names[value], pinned ? t.thinkingLevel.pinned : t.thinkingLevel.inherited)
        : t.thinkingLevel.unsupported}
    >
      <div
        style={{
          position: 'relative', display: 'flex', alignItems: 'center',
          width: compact ? 62 : 86, height: dot + 6,
          opacity: supported ? 1 : 0.4,
          cursor: supported ? 'pointer' : 'not-allowed',
        }}
        onMouseLeave={() => setHover(null)}
      >
        {/* Track — the unfilled remainder */}
        <div style={{
          position: 'absolute', left: 0, right: 0, height: h, borderRadius: h,
          background: 'rgba(var(--hb-accent-rgb),0.16)',
        }} />
        {/* Fill — origin to the current (or hovered) stop. Zero-width at NONE
            is correct: no thinking, no bar. */}
        <div style={{
          position: 'absolute', left: 0, height: h, borderRadius: h,
          width: `${(shownIdx / (THINKING_LEVELS.length - 1)) * 100}%`,
          background: tint,
          transition: 'width 0.14s ease, background 0.14s',
        }} />
        {THINKING_LEVELS.map((lvl, i) => {
          const on = i <= shownIdx
          return (
            <button
              key={lvl}
              type="button"
              disabled={!supported}
              onClick={() => supported && onChange(lvl)}
              onMouseEnter={() => supported && setHover(lvl)}
              aria-label={t.thinkingLevel.names[lvl]}
              style={{
                position: 'absolute',
                left: `calc(${(i / (THINKING_LEVELS.length - 1)) * 100}% - ${dot / 2}px)`,
                width: dot, height: dot, borderRadius: '50%', padding: 0,
                border: `1.5px solid ${on ? tint : 'rgba(var(--hb-accent-rgb),0.3)'}`,
                background: on ? tint : 'var(--hb-bg)',
                // The current stop keeps a ring even while another is hovered,
                // so the control never loses track of what is actually set.
                boxShadow: i === idx ? `0 0 0 3px rgba(var(--hb-accent-rgb),0.18)` : 'none',
                cursor: supported ? 'pointer' : 'not-allowed',
                transition: 'background 0.14s, border-color 0.14s, box-shadow 0.14s',
              }}
            />
          )
        })}
      </div>
      <span style={{
        fontFamily: "'Rajdhani',sans-serif", fontSize: compact ? '0.62rem' : '0.68rem',
        fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase',
        color: supported ? tint : 'var(--hb-icon-dim)',
        minWidth: compact ? 30 : 38, userSelect: 'none',
      }}>
        {supported ? t.thinkingLevel.names[shown] : t.thinkingLevel.na}
      </span>
    </div>
  )
}
