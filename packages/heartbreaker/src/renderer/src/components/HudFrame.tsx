import { useEffect, useRef, useState } from 'react'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { useHealth } from '../lib/useHealth'
import { useIsMobile } from '../lib/useIsMobile'

/**
 * Project Heartbreaker — HUD viewport frame.
 * Fixed, non-interactive overlay. Corner brackets + a top strip showing REAL
 * telemetry: backend host, live link state, round-trip latency, registered tool
 * count, active model, session count, clock. No fake mission-control theatre.
 *
 * Under 768px the strip consolidates: link state, system designation and the
 * active model stay visible; everything else collapses into a DIAG dropdown.
 */

function shortModel(id?: string): string {
  if (!id) return '—'
  const map: Record<string, string> = {
    'claude-opus-4-7': 'OPUS 4.7',
    'claude-sonnet-4-6': 'SONNET 4.6',
    'claude-haiku-4-5-20251001': 'HAIKU 4.5',
  }
  return map[id] ?? id.replace(/^claude-/, '').toUpperCase()
}

function hostOf(apiBase?: string | null): string {
  if (!apiBase) return '—'
  try { return new URL(apiBase).host } catch { return apiBase }
}

/** label: value readout cell */
function Stat({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 4, whiteSpace: 'nowrap' }}>
      <span style={{ color: 'var(--hb-text-faint)', letterSpacing: '0.12em' }}>{label}</span>
      <span style={{ color: color || 'var(--hb-text-dim)' }}>{value}</span>
    </span>
  )
}

function Divider() {
  return <span style={{ width: 1, height: 10, background: 'rgba(95,165,188,0.2)' }} />
}

/** One row of the mobile DIAG dropdown */
function DiagRow({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 14 }}>
      <span style={{ color: 'var(--hb-text-faint)', letterSpacing: '0.12em' }}>{label}</span>
      <span style={{ color: color || 'var(--hb-text-dim)' }}>{value}</span>
    </div>
  )
}

export default function HudFrame() {
  const { state } = useChatContext()
  const { settings } = useSettings()
  const apiBase = state.config?.apiBase
  const apiKey = state.config?.apiKey
  const health = useHealth(apiBase, apiKey)
  const isMobile = useIsMobile()
  const [diagOpen, setDiagOpen] = useState(false)
  const diagRef = useRef<HTMLDivElement>(null)

  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  // Dismiss the DIAG dropdown on outside tap, and whenever we leave mobile
  useEffect(() => {
    if (!diagOpen) return
    const h = (e: MouseEvent) => {
      if (diagRef.current && !diagRef.current.contains(e.target as Node)) setDiagOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [diagOpen])
  useEffect(() => { if (!isMobile) setDiagOpen(false) }, [isMobile])

  const time = now.toLocaleTimeString('en-GB', { hour12: false })
  // Reference chrome clock — "MON. 02 09"
  const wkday = now.toLocaleDateString('en-US', { weekday: 'short' }).toUpperCase()
  const dateTag = `${wkday}. ${String(now.getDate()).padStart(2, '0')} ${String(now.getMonth() + 1).padStart(2, '0')}`

  const online = health.online
  const linkColor = online ? 'var(--hb-green)' : 'var(--hb-red)'

  const stripStyle: React.CSSProperties = {
    position: 'fixed', top: 0, left: 0, right: 0, height: 22,
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: isMobile ? '0 10px' : '0 26px', gap: 10, zIndex: 9998, pointerEvents: 'none',
    fontFamily: "'Share Tech Mono', monospace", fontSize: '0.6rem',
    letterSpacing: '0.06em',
    // Top anchor of the glass HUD — whisper tint, gentle frost under the telemetry.
    // Strictly square-cornered, anchored edge to edge.
    background: 'rgba(10, 16, 26, 0.15)',
    backdropFilter: 'blur(8px)',
    WebkitBackdropFilter: 'blur(8px)',
  }

  if (isMobile) {
    return (
      <div className="hb-seam-b" style={stripStyle}>
        {/* Left — link state only (host lives in DIAG) */}
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: linkColor }}>
          <span style={{
            width: 6, height: 6, background: linkColor, display: 'inline-block',
            animation: online ? 'none' : 'hbBlink 1s step-end infinite',
          }} />
          {online ? 'ONLINE' : 'OFFLINE'}
        </span>

        {/* Center — system designation, compressed to the codename. Flex
            (not absolute) so it shares space with the clusters and ellipsizes
            instead of colliding on narrow screens */}
        <span style={{
          flex: '0 1 auto', minWidth: 0,
          overflow: 'hidden', textOverflow: 'ellipsis',
          fontFamily: "'Rajdhani', sans-serif", fontSize: '0.56rem', fontWeight: 700,
          letterSpacing: '0.2em', textTransform: 'uppercase',
          color: 'rgba(122,150,161,0.55)', whiteSpace: 'nowrap',
        }}>
          HEARTBREAKER
        </span>

        {/* Right — model + collapsed diagnostics */}
        <div ref={diagRef} style={{
          display: 'flex', alignItems: 'center', gap: 8,
          pointerEvents: 'auto', position: 'relative',
        }}>
          <Stat label="MODEL" value={shortModel(settings.model)} color="var(--hb-cyan-bright)" />
          <button
            onClick={() => setDiagOpen(v => !v)}
            style={{
              display: 'flex', alignItems: 'center', gap: 4,
              height: 16, padding: '0 6px',
              border: `1px solid ${diagOpen ? 'var(--hb-edge-bright)' : 'var(--hb-edge)'}`,
              background: diagOpen ? 'rgba(54,171,202,0.16)' : 'var(--hb-holo-fill)',
              color: diagOpen ? 'var(--hb-cyan-bright)' : 'var(--hb-text-dim)',
              fontFamily: "'Share Tech Mono', monospace", fontSize: '0.56rem',
              letterSpacing: '0.1em', cursor: 'pointer',
              transition: 'border-color 0.12s, background 0.12s, color 0.12s',
            }}
          >
            DIAG
            <svg width="7" height="7" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"
              style={{ transform: diagOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>
              <polyline points="6 9 12 15 18 9"/>
            </svg>
          </button>

          {diagOpen && (
            <div className="hb-glass-sm" style={{
              position: 'absolute', top: 'calc(100% + 6px)', right: 0,
              minWidth: 180,
              padding: '0.55rem 0.7rem',
              display: 'flex', flexDirection: 'column', gap: 7,
              background: 'rgba(150, 190, 225, 0.07)',
              backdropFilter: 'var(--hb-holo-blur)',
              WebkitBackdropFilter: 'var(--hb-holo-blur)',
              border: '1px solid var(--hb-edge)',
              boxShadow: 'var(--hb-holo-shadow)',
              fontFamily: "'Share Tech Mono', monospace", fontSize: '0.6rem',
              animation: 'dropDown 0.12s ease',
            }}>
              <DiagRow label="HOST" value={hostOf(apiBase)} />
              <DiagRow label="TOOLS" value={health.tools ?? '--'} />
              <DiagRow label="RTT" value={health.latencyMs != null ? `${health.latencyMs}ms` : '--'}
                       color={health.latencyMs != null && health.latencyMs < 400 ? 'var(--hb-green)' : 'var(--hb-amber)'} />
              <DiagRow label="SESS" value={String(state.sessions.length).padStart(2, '0')} />
              <DiagRow label="DATE" value={dateTag} color="var(--hb-amber)" />
              <DiagRow label="TIME" value={time} color="var(--hb-cyan-bright)" />
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <>
      {/* Top strip — real telemetry */}
      <div className="hb-seam-b" style={stripStyle}>
        {/* Left — connection */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Stat label="HOST" value={hostOf(apiBase)} />
          <Divider />
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: linkColor }}>
            <span style={{
              width: 6, height: 6, background: linkColor, display: 'inline-block',
              animation: online ? 'none' : 'hbBlink 1s step-end infinite',
            }} />
            {online ? 'ONLINE' : 'OFFLINE'}
          </span>
        </div>

        {/* Center — system designation */}
        <span style={{
          position: 'absolute', left: '50%', transform: 'translateX(-50%)',
          fontFamily: "'Rajdhani', sans-serif", fontSize: '0.62rem', fontWeight: 700,
          letterSpacing: '0.42em', textTransform: 'uppercase',
          color: 'rgba(122,150,161,0.55)', whiteSpace: 'nowrap',
        }}>
          SPEDA OS<span style={{ color: 'rgba(54,171,202,0.6)' }}> // </span>HEARTBREAKER
        </span>

        {/* Right — operating parameters */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Stat label="MODEL" value={shortModel(settings.model)} color="var(--hb-cyan-bright)" />
          <Divider />
          <Stat label="TOOLS" value={health.tools ?? '--'} />
          <Divider />
          <Stat label="RTT" value={health.latencyMs != null ? `${health.latencyMs}ms` : '--'}
                color={health.latencyMs != null && health.latencyMs < 400 ? 'var(--hb-green)' : 'var(--hb-amber)'} />
          <Divider />
          <Stat label="SESS" value={String(state.sessions.length).padStart(2, '0')} />
          <Divider />
          {/* Amber date chip + time — "05.06.10 | 04:30:14" */}
          <span className="hb-chip-amber" style={{ height: 13 }}>{dateTag}</span>
          <span style={{ color: 'var(--hb-cyan-bright)' }}>{time}</span>
        </div>
      </div>

    </>
  )
}
