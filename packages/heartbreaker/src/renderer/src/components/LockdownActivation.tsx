import { useEffect } from 'react'

const MONO = 'var(--font-mono)'
const UI = "'Rajdhani', sans-serif"

/**
 * LOCKDOWN PROTOCOL — the containment cinematic.
 *
 * Sibling to PartyActivation, deliberately inverted. House Party's sequence
 * opens outward: the roster boots online one by one, a shockwave fires. This one
 * closes inward — shutters slam from both edges, the sealed ports strike through
 * one after another, and the frame locks. Same idea (a full-screen beat while
 * the app changes state underneath), opposite metaphor.
 *
 * `seal` — engaging. `release` — standing down, the shutters withdrawing.
 * `onIgnite` fires mid-sequence while the screen is fully covered, so the state
 * swap underneath is never seen happening.
 */
const SEAL = { ignite: 2000, done: 2950 }
const RELEASE = { ignite: 800, done: 1650 }

/** Struck through one by one as the seal lands. Mirrors what the backend
 *  actually closes (services/lockdown.py) — the copy is not decorative. */
const PORTS = [
  { label: 'HOST SSH', port: '22' },
  { label: 'APP RAW', port: '8000' },
]

const KEYFRAMES = `
@keyframes lkaIn    { from { opacity: 0; } to { opacity: 1; } }
@keyframes lkaOut   { to { opacity: 0; filter: blur(6px); transform: scale(0.99); } }
@keyframes lkaShutterL { from { transform: translateX(-100%); } to { transform: translateX(0); } }
@keyframes lkaShutterR { from { transform: translateX(100%); } to { transform: translateX(0); } }
@keyframes lkaShutterLOut { from { transform: translateX(0); } to { transform: translateX(-100%); } }
@keyframes lkaShutterROut { from { transform: translateX(0); } to { transform: translateX(100%); } }
@keyframes lkaTitle {
  0%   { opacity: 0; transform: scale(1.14); filter: blur(14px); letter-spacing: 0.55em; }
  60%  { opacity: 1; filter: blur(0); }
  100% { opacity: 1; transform: scale(1); letter-spacing: 0.26em; }
}
@keyframes lkaSub   { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
@keyframes lkaStrike { from { width: 0; } to { width: 100%; } }
@keyframes lkaLock  {
  0%   { opacity: 0; transform: scale(1.6); }
  70%  { opacity: 1; transform: scale(0.94); }
  100% { opacity: 1; transform: scale(1); }
}
@keyframes lkaAlarm { 0%,100%{ opacity: 0.25; } 50%{ opacity: 0.6; } }
`

export default function LockdownActivation({ mode, onIgnite, onDone }: {
  mode: 'seal' | 'release'
  onIgnite: () => void
  onDone: () => void
}) {
  const sealing = mode === 'seal'

  useEffect(() => {
    const t = sealing ? SEAL : RELEASE
    const a = setTimeout(onIgnite, t.ignite)
    const b = setTimeout(onDone, t.done)
    return () => { clearTimeout(a); clearTimeout(b) }
  // Plays exactly once per mount — re-arming on callback identity would replay it.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode])

  const RED = 'var(--hb-red, #e8564a)'

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9999,
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      gap: '1.9rem', padding: '2rem', overflow: 'hidden',
      background: 'rgba(6, 2, 3, 0.92)',
      backdropFilter: 'blur(24px) saturate(120%)',
      WebkitBackdropFilter: 'blur(24px) saturate(120%)',
      animation: sealing
        ? 'lkaIn 0.2s ease both, lkaOut 0.55s ease 2.35s both'
        : 'lkaIn 0.16s ease both, lkaOut 0.5s ease 1.1s both',
    }}>
      <style>{KEYFRAMES}</style>

      {/* Shutters — the containment itself, closing in from both edges */}
      {[
        { side: 'left' as const, anim: sealing ? 'lkaShutterL 0.75s cubic-bezier(0.7,0,0.3,1) both' : 'lkaShutterLOut 0.6s cubic-bezier(0.7,0,0.3,1) 0.15s both' },
        { side: 'right' as const, anim: sealing ? 'lkaShutterR 0.75s cubic-bezier(0.7,0,0.3,1) both' : 'lkaShutterROut 0.6s cubic-bezier(0.7,0,0.3,1) 0.15s both' },
      ].map(s => (
        <div key={s.side} aria-hidden style={{
          position: 'absolute', top: 0, bottom: 0, [s.side]: 0, width: '50%',
          background: `linear-gradient(${s.side === 'left' ? '90deg' : '270deg'}, rgba(232,86,74,0.09), transparent)`,
          borderLeft: s.side === 'right' ? `1px solid ${RED}55` : 'none',
          borderRight: s.side === 'left' ? `1px solid ${RED}55` : 'none',
          animation: s.anim, pointerEvents: 'none',
        }} />
      ))}

      {/* Alarm wash along the top and bottom edges */}
      {sealing && ['top', 'bottom'].map(edge => (
        <div key={edge} aria-hidden style={{
          position: 'absolute', left: 0, right: 0, [edge]: 0, height: 90,
          background: `linear-gradient(${edge === 'top' ? '180deg' : '0deg'}, rgba(232,86,74,0.35), transparent)`,
          animation: 'lkaAlarm 1.2s ease-in-out infinite', pointerEvents: 'none',
        }} />
      ))}

      {sealing ? (
        <>
          <p style={{
            fontFamily: MONO, fontSize: '0.66rem', letterSpacing: '0.3em',
            color: RED, textTransform: 'uppercase', position: 'relative',
            animation: 'lkaSub 0.35s ease 0.25s both',
          }}>
            {'// CONTAINMENT AUTHORIZED — SEALING PERIMETER'}
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.7rem', position: 'relative' }}>
            <span style={{
              fontFamily: UI, fontWeight: 800, lineHeight: 1, color: '#fff',
              fontSize: 'clamp(2.2rem, 7.5vw, 4.6rem)',
              textTransform: 'uppercase', whiteSpace: 'nowrap', letterSpacing: '0.26em',
              textShadow: `0 0 46px ${RED}`,
              animation: 'lkaTitle 0.7s cubic-bezier(0.2,0.9,0.25,1) 0.45s both',
            }}>
              Lockdown
            </span>
            <span style={{
              fontFamily: UI, fontWeight: 700, lineHeight: 1,
              fontSize: 'clamp(1rem, 3vw, 1.7rem)',
              letterSpacing: '0.58em', textTransform: 'uppercase',
              color: 'var(--hb-text-dim)', paddingLeft: '0.58em',
              animation: 'lkaSub 0.4s ease 0.8s both',
            }}>
              Protocol
            </span>
          </div>

          {/* The ports going dark, one by one */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', position: 'relative', minWidth: 'min(320px, 80vw)' }}>
            {PORTS.map((p, i) => {
              const d = 1.15 + i * 0.32
              return (
                <div key={p.port} style={{
                  display: 'flex', alignItems: 'center', gap: '0.8rem',
                  padding: '0.4rem 0.8rem',
                  border: `1px solid ${RED}44`, background: 'rgba(232,86,74,0.06)',
                  animation: `lkaSub 0.3s ease ${d}s both`,
                }}>
                  <span style={{ position: 'relative', flex: 1, fontFamily: MONO, fontSize: '0.66rem', letterSpacing: '0.18em', color: 'var(--hb-text-dim)' }}>
                    {p.label} :{p.port}
                    <span aria-hidden style={{
                      position: 'absolute', left: 0, top: '50%', height: 1,
                      background: RED, animation: `lkaStrike 0.4s ease ${d + 0.18}s both`,
                    }} />
                  </span>
                  <span style={{
                    fontFamily: MONO, fontSize: '0.56rem', letterSpacing: '0.18em', color: RED,
                    animation: `lkaSub 0.25s ease ${d + 0.42}s both`,
                  }}>
                    SEALED
                  </span>
                </div>
              )
            })}
          </div>

          <span style={{ position: 'relative', color: RED, animation: 'lkaLock 0.5s cubic-bezier(0.2,0.9,0.3,1.2) 1.95s both' }}>
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
            </svg>
          </span>

          <p style={{
            fontFamily: MONO, fontSize: '0.6rem', letterSpacing: '0.24em',
            color: 'var(--hb-text-dim)', textTransform: 'uppercase', textAlign: 'center',
            position: 'relative', animation: 'lkaSub 0.35s ease 2.1s both',
          }}>
            Perimeter sealed — uplink and outbound holding
          </p>
        </>
      ) : (
        <>
          <span style={{
            fontFamily: UI, fontWeight: 800, lineHeight: 1, position: 'relative',
            fontSize: 'clamp(1.3rem, 4vw, 2.1rem)',
            letterSpacing: '0.4em', textTransform: 'uppercase',
            color: 'var(--hb-text-dim)', paddingLeft: '0.4em',
            animation: 'lkaSub 0.3s ease 0.1s both',
          }}>
            Lockdown Protocol
          </span>
          <p style={{
            fontFamily: MONO, fontSize: '0.64rem', letterSpacing: '0.26em',
            color: 'var(--hb-cyan-bright)', textTransform: 'uppercase',
            position: 'relative', animation: 'lkaSub 0.3s ease 0.25s both',
          }}>
            {'// STAND DOWN — PERIMETER RESTORED'}
          </p>
        </>
      )}
    </div>
  )
}
