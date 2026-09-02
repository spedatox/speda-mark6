// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useMemo, useState } from 'react'
import AgentMark from './AgentMark'

/**
 * The lock screen's screensaver — the roster, one agent at a time.
 *
 * Each beat is the welcome screen's own move, played alone on black: the mark
 * arrives, the name types itself out beside it, it holds, and the whole card
 * dissolves so the next agent can take the screen. Every beat is lit in that
 * agent's signature accent — the mark, the name, the rule and the wash behind
 * it — so the parade reads as the roster and not as one branded slide reused
 * eight times.
 *
 * The card and the clock drift on two long, mutually prime sines. That is not
 * decoration: this runs unattended for hours, and a bright figure that never
 * moves is how you burn a panel.
 *
 * Nothing here is invented chrome — the marks are the real wordmark geometry
 * (lib/agentMarks), the colours are the real brand accents, and the only text
 * is a name, a model number, a tagline and the time.
 */

export interface SaverAgent {
  agentId: string
  name: string
  modelNumber: string
  tagline: string
  accent: string
}

/** Milliseconds per revealed character of the name. Slow enough to watch the
 *  letters land rather than see a name flicker into existence. */
const TYPE_MS = 88
/** How long the mark leads the name by — it settles before the typing starts. */
const MARK_LEAD_MS = 700
/** The dissolve at the end of a beat. Long on purpose: this is the seam between
 *  two agents, and a fast cut is what makes a parade feel like a slideshow. */
const FADE_MS = 900

const KEYFRAMES = `
@keyframes ssMark {
  0%   { opacity: 0; transform: scale(1.28) rotate(-5deg); filter: blur(10px); }
  60%  { opacity: 1; filter: blur(0); }
  100% { opacity: 1; transform: scale(1) rotate(0deg); filter: blur(0); }
}
@keyframes ssRule   { from { transform: scaleX(0); } to { transform: scaleX(1); } }
@keyframes ssLine   { from { opacity: 0; transform: translateY(7px); } to { opacity: 1; transform: translateY(0); } }
@keyframes ssWash   { from { opacity: 0; } to { opacity: 1; } }
@keyframes ssOut    { to { opacity: 0; filter: blur(7px); transform: scale(0.99); } }
@keyframes ssCaret  { 0%,45% { opacity: 1; } 55%,100% { opacity: 0; } }
@keyframes ssDriftX { 0%,100% { transform: translateX(-4.2vw); } 50% { transform: translateX(4.2vw); } }
@keyframes ssDriftY { 0%,100% { transform: translateY(3.4vh); } 50% { transform: translateY(-3.4vh); } }
@keyframes ssClockX { 0%,100% { transform: translateX(3.6vw); } 50% { transform: translateX(-3.6vw); } }
@keyframes ssClockY { 0%,100% { transform: translateY(-2.6vh); } 50% { transform: translateY(2.6vh); } }
`

export default function LockScreensaver({ agents, dwellMs, lockedLabel }: {
  /** The roster to parade. One entry is legal — Striker has exactly one. */
  agents: SaverAgent[]
  /** How long a finished card holds before it dissolves. */
  dwellMs: number
  /** The word under the clock — localised by the caller. */
  lockedLabel: string
}) {
  const [beat, setBeat] = useState(0)
  const [typed, setTyped] = useState(0)
  const [leaving, setLeaving] = useState(false)
  const [clock, setClock] = useState(() => new Date())

  const calm = useMemo(
    () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    [],
  )

  const agent = agents[beat % agents.length]
  const done = typed >= agent.name.length

  useEffect(() => {
    const id = setInterval(() => setClock(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  // One beat, scheduled end to end: type the name out, hold it, dissolve, next.
  useEffect(() => {
    setTyped(0)
    setLeaving(false)
    const timers: number[] = []
    const name = agent.name

    for (let i = 1; i <= name.length; i++) {
      timers.push(window.setTimeout(() => setTyped(i), MARK_LEAD_MS + i * TYPE_MS))
    }
    const settled = MARK_LEAD_MS + name.length * TYPE_MS
    timers.push(window.setTimeout(() => setLeaving(true), settled + dwellMs))
    timers.push(window.setTimeout(() => setBeat(b => b + 1), settled + dwellMs + FADE_MS))

    return () => timers.forEach(clearTimeout)
  }, [beat, agent.name, dwellMs])

  const two = (n: number) => String(n).padStart(2, '0')
  const accent = agent.accent

  return (
    <div style={{
      position: 'absolute', inset: 0, overflow: 'hidden',
      background: '#04070a',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
    }}>
      <style>{KEYFRAMES}</style>

      {/* The agent's colour, washed in behind everything it lights. */}
      <div
        key={`wash-${beat}`}
        aria-hidden
        style={{
          position: 'absolute', inset: 0, pointerEvents: 'none',
          background: `radial-gradient(ellipse 70% 55% at 50% 44%, ${accent}22, transparent 70%)`,
          animation: `ssWash 1.5s ease both${leaving ? `, ssOut ${FADE_MS}ms ease-in-out both` : ''}`,
        }}
      />

      {/* Drift lives on two nested wrappers so the card's own entrance and exit
          animations stay free — one element cannot run both transforms. */}
      <div style={{
        flex: 1, minHeight: 0, display: 'flex', alignItems: 'center',
        animation: calm ? undefined : 'ssDriftX 71s ease-in-out infinite',
      }}>
        <div style={{ animation: calm ? undefined : 'ssDriftY 97s ease-in-out infinite' }}>
          <div
            key={beat}
            style={{
              display: 'flex', alignItems: 'center', gap: 'clamp(1.4rem, 3vw, 2.6rem)',
              animation: leaving ? `ssOut ${FADE_MS}ms ease-in-out both` : undefined,
            }}
          >
            {/* The entrance rides a WRAPPER and fills `backwards`, never
                `both`: an animation left holding a transform+filter on the SVG
                keeps it on a composited layer rasterised at the entrance scale,
                and the mark stays visibly soft for the rest of the beat. Ending
                the animation clean is what makes it razor sharp. Its glow is
                the mark's own (AgentMark's glass finish), not a CSS shadow. */}
            <span style={{
              display: 'flex', flexShrink: 0,
              animation: 'ssMark 1.15s cubic-bezier(0.16,0.84,0.3,1) backwards',
            }}>
              <AgentMark agentId={agent.agentId} size={110} finish="glass" color={accent} />
            </span>

            <div style={{
              display: 'flex', flexDirection: 'column', justifyContent: 'center',
              gap: '0.55rem', minWidth: 0, minHeight: 128,
            }}>
              {/* lang="en" — the marks are English brand names, and Turkish's
                  dotless-i casing rules would render ATOMİX / SENTİNEL. */}
              <span lang="en" style={{
                display: 'flex', alignItems: 'baseline', gap: '0.7rem',
                fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, lineHeight: 1,
                fontSize: 'clamp(2.2rem, 6vw, 4.2rem)',
                letterSpacing: '0.14em', textTransform: 'uppercase',
                color: '#fff', textShadow: `0 0 34px ${accent}aa`,
                whiteSpace: 'nowrap',
              }}>
                {agent.name.slice(0, typed) || ' '}
                {!done && (
                  <span aria-hidden style={{
                    display: 'inline-block', width: '0.42em', height: '0.78em',
                    background: accent, animation: 'ssCaret 1s step-end infinite',
                  }} />
                )}
                {done && (
                  <span style={{
                    fontSize: '0.34em', fontWeight: 600, letterSpacing: '0.3em',
                    color: accent, animation: 'ssLine 0.7s ease both',
                  }}>
                    {agent.modelNumber}
                  </span>
                )}
              </span>

              <div aria-hidden style={{
                height: 1, width: '100%', transformOrigin: 'left',
                background: `linear-gradient(90deg, ${accent}, ${accent}00)`,
                animation: `ssRule 1.05s cubic-bezier(0.4,0,0.2,1) ${MARK_LEAD_MS}ms both`,
              }} />

              {done && (
                <span lang="en" style={{
                  fontFamily: 'var(--font-mono)', fontSize: '0.68rem',
                  letterSpacing: '0.3em', textTransform: 'uppercase',
                  color: 'var(--hb-text-dim)',
                  animation: 'ssLine 0.75s ease 0.12s both',
                }}>
                  {agent.tagline}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* The clock keeps its own drift — slower, and out of phase with the
          card's, so the two never settle into one moving block. */}
      <div style={{
        paddingBottom: '10vh', flexShrink: 0,
        animation: calm ? undefined : 'ssClockX 113s ease-in-out infinite',
      }}>
        <div style={{
          animation: calm ? undefined : 'ssClockY 149s ease-in-out infinite',
          display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.5rem',
        }}>
          <span style={{
            fontFamily: "'Rajdhani', sans-serif", fontWeight: 300, lineHeight: 1,
            fontSize: 'clamp(2.6rem, 7vw, 4.6rem)',
            letterSpacing: '0.16em', paddingLeft: '0.16em',
            color: 'rgba(219, 230, 236, 0.82)',
          }}>
            {two(clock.getHours())}:{two(clock.getMinutes())}
          </span>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: '0.6rem',
            letterSpacing: '0.32em', textTransform: 'uppercase',
            color: 'var(--hb-text-faint)',
          }}>
            {clock.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}
            {' — '}{lockedLabel}
          </span>
        </div>
      </div>
    </div>
  )
}
