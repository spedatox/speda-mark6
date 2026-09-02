// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useState } from 'react'
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

/** Milliseconds between one letter of the name starting and the next. */
const TYPE_MS = 62
/** How long ONE letter takes to arrive. Deliberately several times TYPE_MS, so
 *  letters overlap and the name flows on rather than ticking over — a reveal
 *  where each letter is fully in before the next begins reads as a slideshow of
 *  frames no matter how fine the interval. */
const CHAR_MS = 430
/** How long the mark leads the name by — it settles before the typing starts. */
const MARK_LEAD_MS = 700
/** After the last letter: the rule draws, then the model number, then the
 *  tagline. Strictly in that order, and never two at once. */
const RULE_GAP_MS = 60
const MODEL_GAP_MS = 260
const TAGLINE_GAP_MS = 500
/** How long the trailing lines take to arrive. */
const LINE_MS = 520
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
@keyframes ssChar   {
  0%   { opacity: 0; transform: translateY(0.2em); filter: blur(7px); }
  100% { opacity: 1; transform: translateY(0); filter: blur(0); }
}
@keyframes ssLine   { from { opacity: 0; transform: translateY(7px); filter: blur(4px); } to { opacity: 1; transform: translateY(0); filter: blur(0); } }
@keyframes ssWash   { from { opacity: 0; } to { opacity: 1; } }
@keyframes ssOut    { to { opacity: 0; filter: blur(7px); transform: scale(0.99); } }
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
  const [leaving, setLeaving] = useState(false)
  const [clock, setClock] = useState(() => new Date())


  const agent = agents[beat % agents.length]
  /** When the last letter has finished arriving — everything after the name is
   *  timed off this, so the order never depends on render timing. */
  const typeEnd = MARK_LEAD_MS + (agent.name.length - 1) * TYPE_MS + CHAR_MS

  useEffect(() => {
    const id = setInterval(() => setClock(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  // One beat, and only two timers in it. The reveal itself is CSS with staggered
  // delays — driving a letter-by-letter reveal from React state re-renders the
  // whole card once per letter, and what that looks like on screen is a reveal
  // running at the interval's frame rate instead of the display's.
  useEffect(() => {
    setLeaving(false)
    const settled = typeEnd + TAGLINE_GAP_MS + LINE_MS
    const a = window.setTimeout(() => setLeaving(true), settled + dwellMs)
    const b = window.setTimeout(() => setBeat(n => n + 1), settled + dwellMs + FADE_MS)
    return () => { clearTimeout(a); clearTimeout(b) }
  }, [beat, typeEnd, dwellMs])

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

      {/* The card sits centred and STAYS there. An earlier pass drifted it on
          two slow sines against panel burn-in; watching it, a wandering
          wordmark reads as drift in the design rather than care about the
          hardware, and the beat already repaints the whole area every few
          seconds. Centred wins. */}
      <div style={{
        flex: 1, minHeight: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <div>
          <div
            key={beat}
            style={{
              display: 'flex', alignItems: 'center', gap: 'clamp(1.6rem, 3.4vw, 3.2rem)',
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
              // Sized off the viewport rather than a fixed box — this plays on
              // whatever panel the deck is open on, and 110px was a thumbnail.
              width: 'clamp(120px, 14vw, 240px)',
              animation: 'ssMark 1.15s cubic-bezier(0.16,0.84,0.3,1) backwards',
            }}>
              <AgentMark
                agentId={agent.agentId}
                size={240}
                finish="glass"
                color={accent}
                style={{ width: '100%', height: 'auto', display: 'block' }}
              />
            </span>

            <div style={{
              display: 'flex', flexDirection: 'column', justifyContent: 'center',
              gap: '0.7rem', minWidth: 0, minHeight: 'clamp(120px, 14vw, 240px)',
            }}>
              {/* lang="en" — the marks are English brand names, and Turkish's
                  dotless-i casing rules would render ATOMİX / SENTİNEL. */}
              <span lang="en" style={{
                display: 'flex', alignItems: 'baseline', gap: '0.7rem',
                fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, lineHeight: 1,
                fontSize: 'clamp(2.6rem, 7.6vw, 6.2rem)',
                letterSpacing: '0.14em', textTransform: 'uppercase',
                color: '#fff', textShadow: `0 0 34px ${accent}aa`,
                whiteSpace: 'nowrap',
              }}>
                {/* Every letter is in the DOM from the first frame and arrives
                    on its own staggered CSS delay. The name is on the wrapper
                    for a screen reader; the letters themselves are hidden from
                    it so it reads a word, not a column of characters. */}
                <span aria-label={agent.name}>
                  {agent.name.split('').map((ch, i) => (
                    <span
                      key={i}
                      aria-hidden
                      style={{
                        display: 'inline-block',
                        animation: `ssChar ${CHAR_MS}ms cubic-bezier(0.2,0.8,0.2,1) ${MARK_LEAD_MS + i * TYPE_MS}ms backwards`,
                      }}
                    >
                      {ch}
                    </span>
                  ))}
                </span>
                <span style={{
                  fontSize: '0.34em', fontWeight: 600, letterSpacing: '0.3em',
                  color: accent,
                  animation: `ssLine ${LINE_MS}ms ease ${typeEnd + MODEL_GAP_MS}ms backwards`,
                }}>
                  {agent.modelNumber}
                </span>
              </span>

              <div aria-hidden style={{
                height: 1, width: '100%', transformOrigin: 'left',
                background: `linear-gradient(90deg, ${accent}, ${accent}00)`,
                animation: `ssRule 0.85s cubic-bezier(0.4,0,0.2,1) ${typeEnd + RULE_GAP_MS}ms backwards`,
              }} />

              <span lang="en" style={{
                fontFamily: 'var(--font-mono)', fontSize: 'clamp(0.68rem, 1.05vw, 0.95rem)',
                letterSpacing: '0.3em', textTransform: 'uppercase',
                color: 'var(--hb-text-dim)',
                animation: `ssLine ${LINE_MS}ms ease ${typeEnd + TAGLINE_GAP_MS}ms backwards`,
              }}>
                {agent.tagline}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* The clock, parked in its own band clear of the card. */}
      <div style={{ paddingBottom: '10vh', flexShrink: 0 }}>
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.5rem',
        }}>
          <span style={{
            fontFamily: "'Rajdhani', sans-serif", fontWeight: 300, lineHeight: 1,
            fontSize: 'clamp(2.8rem, 7.4vw, 5.4rem)',
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
