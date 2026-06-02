import { useEffect, useState } from 'react'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { useHealth } from '../lib/useHealth'

/**
 * Project Heartbreaker — HUD viewport frame.
 * Fixed, non-interactive overlay. Corner brackets + a top strip showing REAL
 * telemetry: backend host, live link state, round-trip latency, registered tool
 * count, active model, session count, clock. No fake mission-control theatre.
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

export default function HudFrame() {
  const { state } = useChatContext()
  const { settings } = useSettings()
  const apiBase = state.config?.apiBase
  const apiKey = state.config?.apiKey
  const health = useHealth(apiBase, apiKey)

  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  const time = now.toLocaleTimeString('en-GB', { hour12: false })

  const online = health.online
  const linkColor = online ? 'var(--hb-green)' : 'var(--hb-red)'

  return (
    <>
      {/* Top strip — real telemetry */}
      <div style={{
        position: 'fixed', top: 0, left: 0, right: 0, height: 22,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0 26px', gap: 10, zIndex: 9998, pointerEvents: 'none',
        fontFamily: "'Share Tech Mono', monospace", fontSize: '0.6rem',
        letterSpacing: '0.06em',
        background: 'linear-gradient(180deg, rgba(6,14,18,0.94), rgba(6,14,18,0))',
        borderBottom: '1px solid rgba(95,165,188,0.12)',
      }}>
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
          <span style={{ color: 'var(--hb-cyan-bright)' }}>{time}</span>
        </div>
      </div>

      {/* Bottom hairline ruler (decorative) */}
      <div style={{
        position: 'fixed', bottom: 0, left: 26, right: 26, height: 4, zIndex: 9998,
        pointerEvents: 'none',
        backgroundImage: 'repeating-linear-gradient(90deg, rgba(95,165,188,0.25) 0 1px, transparent 1px 10px)',
        opacity: 0.4,
      }} />
    </>
  )
}
