import { useEffect, useState } from 'react'

/**
 * Project Heartbreaker — HUD viewport frame.
 * A fixed, non-interactive overlay that frames the whole app like a Stark
 * instrument: corner brackets, a dense technical top strip, a live readout,
 * and a slow scanline. pointer-events: none so it never blocks the UI.
 */

function Bracket({ corner }: { corner: 'tl' | 'tr' | 'bl' | 'br' }) {
  const size = 18
  const v = corner[0] === 't' ? { top: 8 } : { bottom: 8 }
  const h = corner[1] === 'l' ? { left: 8 } : { right: 8 }
  const border: React.CSSProperties = {
    borderColor: 'var(--hb-cyan)',
    borderStyle: 'solid',
    borderWidth: `${corner[0] === 't' ? 1 : 0}px ${corner[1] === 'r' ? 1 : 0}px ${corner[0] === 'b' ? 1 : 0}px ${corner[1] === 'l' ? 1 : 0}px`,
  }
  return (
    <div style={{
      position: 'fixed', width: size, height: size, ...v, ...h, ...border,
      opacity: 0.8, zIndex: 9999, pointerEvents: 'none',
    }} />
  )
}

function Seg({ children, accent }: { children: React.ReactNode; accent?: boolean }) {
  return (
    <span style={{
      padding: '1px 6px',
      border: '1px solid var(--hb-line)',
      borderTop: accent ? '1px solid var(--hb-cyan)' : '1px solid var(--hb-line)',
      color: accent ? 'var(--hb-cyan-bright)' : 'var(--hb-text-faint)',
      background: accent ? 'rgba(54,171,202,0.08)' : 'transparent',
      whiteSpace: 'nowrap',
    }}>
      {children}
    </span>
  )
}

export default function HudFrame() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  const time = now.toLocaleTimeString('en-GB', { hour12: false })
  const date = now.toLocaleDateString('en-GB').replace(/\//g, '.')

  return (
    <>
      {/* Corner brackets */}
      <Bracket corner="tl" /><Bracket corner="tr" />
      <Bracket corner="bl" /><Bracket corner="br" />

      {/* Top status strip */}
      <div style={{
        position: 'fixed', top: 0, left: 0, right: 0, height: 22,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0 26px', gap: 8, zIndex: 9998, pointerEvents: 'none',
        fontFamily: "'Share Tech Mono', monospace", fontSize: '0.6rem',
        letterSpacing: '0.1em',
        background: 'linear-gradient(180deg, rgba(6,14,18,0.92), rgba(6,14,18,0))',
        borderBottom: '1px solid rgba(95,165,188,0.12)',
      }}>
        {/* Left cluster — system tags */}
        <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
          <Seg accent>HEARTBREAKER</Seg>
          <Seg>MK·VI</Seg>
          <Seg>SYS·01</Seg>
          <Seg>OBJ·04.A</Seg>
          <span style={{ color: 'var(--hb-text-faint)' }}>MODE / 3Dx.78A</span>
        </div>

        {/* Right cluster — live readout */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ color: 'var(--hb-text-faint)' }}>LON 41.0082 · LAT 28.9784</span>
          <Seg>{date}</Seg>
          <span style={{ color: 'var(--hb-cyan-bright)' }}>{time}</span>
          <span style={{
            width: 6, height: 6, background: 'var(--hb-green)',
            display: 'inline-block', animation: 'hbBlink 2s step-end infinite',
          }} />
        </div>
      </div>

      {/* Bottom hairline + tick ruler */}
      <div style={{
        position: 'fixed', bottom: 0, left: 26, right: 26, height: 4, zIndex: 9998,
        pointerEvents: 'none',
        backgroundImage: 'repeating-linear-gradient(90deg, rgba(95,165,188,0.25) 0 1px, transparent 1px 10px)',
        opacity: 0.4,
      }} />

      {/* Slow scanline sweep */}
      <div style={{
        position: 'fixed', top: 0, left: 0, right: 0, height: 60, zIndex: 9997,
        pointerEvents: 'none',
        background: 'linear-gradient(180deg, transparent, rgba(95,204,230,0.05) 50%, transparent)',
        animation: 'hbScan 9s linear infinite',
      }} />

      {/* Edge vignette */}
      <div style={{
        position: 'fixed', inset: 0, zIndex: 9996, pointerEvents: 'none',
        boxShadow: 'inset 0 0 200px 40px rgba(2,6,8,0.85)',
      }} />
    </>
  )
}
