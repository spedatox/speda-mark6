import { useEffect } from 'react'
import { ROSTER, agentColor } from '../lib/agents'
import { Avatar } from './CommBubble'

const MONO = "var(--font-mono)"
const UI = "'Rajdhani', sans-serif"

/**
 * HOUSE PARTY PROTOCOL — the activation cinematic (Iron Man 3).
 *
 * A full-screen frosted-void sequence that plays while the app transforms
 * into the war room underneath it. Engage: the directive lands, the title
 * slams in through blur, the whole roster boots online one by one with
 * colour pings, a shockwave fires, and the overlay dissolves onto the
 * already-transformed console. Stand down: the roster winks out in reverse
 * and the veil lifts on the restored agent.
 *
 * Three modes. `engage` — the full protocol boot ("TAKE 'EM TO CHURCH", ALL
 * HANDS ONLINE). `standby` — the same boot cinematic with calmer copy, played
 * when the owner opens the war room from the UI (protocol offline). `standdown`
 * — the reverse wink-out on leaving. `engage` and `standby` are both ENTER
 * cinematics and share timing/animation; only the copy differs.
 *
 * `onIgnite` fires mid-sequence while the screen is fully covered — the app
 * swaps profile + theme under here so the reveal lands already transformed.
 * Timings below are the single source of truth; the keyframe delays in the
 * JSX are derived from them.
 */
const ENTER = { ignite: 2600, done: 3650 }
const STANDDOWN = { ignite: 850, done: 1750 }

const KEYFRAMES = `
@keyframes hbHppIn   { from { opacity: 0; } to { opacity: 1; } }
@keyframes hbHppOut  { to { opacity: 0; filter: blur(6px); transform: scale(1.02); } }
@keyframes hbHppSlam {
  0%   { opacity: 0; transform: scale(1.25); filter: blur(18px); letter-spacing: 0.62em; }
  60%  { opacity: 1; filter: blur(0); }
  100% { opacity: 1; transform: scale(1); letter-spacing: 0.3em; }
}
@keyframes hbHppSub  { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
@keyframes hbHppBoot {
  0%   { opacity: 0; transform: scale(1.8); filter: blur(8px); }
  70%  { opacity: 1; filter: blur(0); transform: scale(0.96); }
  100% { opacity: 1; transform: scale(1); }
}
@keyframes hbHppPing { from { box-shadow: 0 0 0 0 currentColor; } to { box-shadow: 0 0 0 18px transparent; } }
@keyframes hbHppWave { from { opacity: 0.9; transform: scale(0.1); } to { opacity: 0; transform: scale(3.4); } }
@keyframes hbHppWink { to { opacity: 0; transform: scale(0.55); filter: blur(4px); } }
`

export default function PartyActivation({ mode, onIgnite, onDone }: {
  mode: 'engage' | 'standby' | 'standdown'
  onIgnite: () => void
  onDone: () => void
}) {
  const entering = mode !== 'standdown'

  useEffect(() => {
    const t = entering ? ENTER : STANDDOWN
    const a = setTimeout(onIgnite, t.ignite)
    const b = setTimeout(onDone, t.done)
    return () => { clearTimeout(a); clearTimeout(b) }
  // The sequence plays exactly once per mount — re-arming on callback
  // identity would replay the swap.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode])

  // Copy differs by mode; the ENTER animation is identical for both entrances.
  const directive = mode === 'engage'
    ? '// DIRECTIVE CONFIRMED — "TAKE \'EM TO CHURCH"'
    : '// WAR ROOM ONLINE — ROSTER ON STATION'
  const subtitle = mode === 'engage' ? 'Protocol' : 'Standby'
  const closer = mode === 'engage'
    ? 'ALL HANDS ONLINE — CHANNEL OPEN'
    : 'ROSTER ON STATION — STANDBY HELD'

  const engage = entering

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9999,
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      gap: engage ? '2.4rem' : '1.6rem',
      padding: '2rem',
      background: 'rgba(3, 7, 10, 0.9)',
      backdropFilter: 'blur(24px) saturate(130%)',
      WebkitBackdropFilter: 'blur(24px) saturate(130%)',
      overflow: 'hidden',
      animation: engage
        ? 'hbHppIn 0.22s ease both, hbHppOut 0.6s ease 2.95s both'
        : 'hbHppIn 0.18s ease both, hbHppOut 0.5s ease 1.2s both',
    }}>
      <style>{KEYFRAMES}</style>

      {engage ? (
        <>
          {/* Shockwave — fires as the theme ignites underneath */}
          <div className="hb-round" style={{
            position: 'absolute', width: 280, height: 280,
            border: '1px solid var(--hb-amber-bright)',
            boxShadow: '0 0 40px rgba(242, 183, 92, 0.35), inset 0 0 24px rgba(242, 183, 92, 0.15)',
            animation: 'hbHppWave 1s ease-out 2.55s both',
            pointerEvents: 'none',
          }} />

          {/* The directive */}
          <p style={{
            fontFamily: MONO, fontSize: '0.66rem', letterSpacing: '0.3em',
            color: 'var(--hb-amber)', textTransform: 'uppercase',
            animation: 'hbHppSub 0.35s ease 0.3s both',
          }}>
            {directive}
          </p>

          {/* Title slam */}
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.8rem' }}>
            <span className="hb-party-cycle" style={{
              fontFamily: UI, fontWeight: 800, lineHeight: 1,
              fontSize: 'clamp(2.4rem, 8vw, 5rem)',
              textTransform: 'uppercase', whiteSpace: 'nowrap',
              textShadow: '0 0 46px currentColor',
              animation: 'hbPartyCycle 10s linear infinite, hbHppSlam 0.7s cubic-bezier(0.2, 0.9, 0.25, 1) 0.5s both',
            }}>
              House Party
            </span>
            <span style={{
              fontFamily: UI, fontWeight: 700, lineHeight: 1,
              fontSize: 'clamp(1.1rem, 3.2vw, 1.9rem)',
              letterSpacing: '0.58em', textTransform: 'uppercase',
              color: 'var(--hb-text-dim)', paddingLeft: '0.58em',
              animation: 'hbHppSub 0.4s ease 0.85s both',
            }}>
              {subtitle}
            </span>
          </div>

          {/* Caution — the protocol is heavy/expensive/prototype. Engage only. */}
          {mode === 'engage' && (
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: '0.5rem',
              padding: '0.28rem 0.8rem',
              border: '1px solid rgba(242, 183, 92, 0.5)',
              background: 'rgba(242, 183, 92, 0.08)',
              borderRadius: 999,
              fontFamily: MONO, fontSize: '0.56rem', letterSpacing: '0.24em',
              color: 'var(--hb-amber-bright)', textTransform: 'uppercase',
              animation: 'hbHppSub 0.4s ease 0.95s both',
            }}>
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
                <path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
              </svg>
              Heavy · Expensive · Prototype
            </span>
          )}

          {/* The roster boots online, one by one */}
          <div style={{
            display: 'flex', flexWrap: 'wrap', justifyContent: 'center',
            gap: 'clamp(0.9rem, 3vw, 1.8rem)', maxWidth: 720,
          }}>
            {ROSTER.map((id, i) => {
              const d = 1.05 + i * 0.18
              const c = agentColor(id)
              return (
                <div key={id} style={{
                  display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6,
                  animation: `hbHppBoot 0.42s cubic-bezier(0.2, 0.9, 0.3, 1.2) ${d}s both`,
                }}>
                  <span className="hb-round" style={{
                    color: c, display: 'flex',
                    animation: `hbHppPing 0.7s ease-out ${d + 0.12}s both`,
                  }}>
                    <Avatar id={id} size={44} />
                  </span>
                  <span style={{
                    fontFamily: UI, fontSize: '0.62rem', fontWeight: 700,
                    letterSpacing: '0.18em', textTransform: 'uppercase',
                    color: 'var(--hb-text-dim)',
                  }}>
                    {id}
                  </span>
                  <span style={{
                    fontFamily: MONO, fontSize: '0.5rem', letterSpacing: '0.14em', color: c,
                    animation: `hbHppSub 0.3s ease ${d + 0.22}s both`,
                  }}>
                    ONLINE
                  </span>
                </div>
              )
            })}
          </div>

          <p style={{
            fontFamily: MONO, fontSize: '0.6rem', letterSpacing: '0.26em',
            color: 'var(--hb-amber-bright)', textTransform: 'uppercase',
            animation: 'hbHppSub 0.35s ease 2.45s both',
          }}>
            {closer}
          </p>
        </>
      ) : (
        <>
          <span style={{
            fontFamily: UI, fontWeight: 800, lineHeight: 1,
            fontSize: 'clamp(1.3rem, 4vw, 2.1rem)',
            letterSpacing: '0.4em', textTransform: 'uppercase',
            color: 'var(--hb-text-dim)', paddingLeft: '0.4em',
            animation: 'hbHppSub 0.3s ease 0.1s both',
          }}>
            House Party Protocol
          </span>
          <p style={{
            fontFamily: MONO, fontSize: '0.64rem', letterSpacing: '0.26em',
            color: '#e8a196', textTransform: 'uppercase',
            animation: 'hbHppSub 0.3s ease 0.25s both',
          }}>
            {'// STAND DOWN — ALL UNITS RETURNING TO STATION'}
          </p>
          <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: '1.2rem' }}>
            {ROSTER.map((id, i) => (
              <span key={id} className="hb-round" style={{
                display: 'flex',
                animation: `hbHppWink 0.4s ease ${0.35 + (ROSTER.length - 1 - i) * 0.09}s both`,
              }}>
                <Avatar id={id} size={34} />
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
