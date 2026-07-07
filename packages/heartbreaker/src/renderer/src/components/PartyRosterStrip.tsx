import { useEffect, useMemo, useState } from 'react'
import { fetchAgentComms } from '../lib/api'
import type { AgentCommEntry } from '../lib/api'
import type { AppConfig } from '../lib/types'
import { ROSTER } from '../lib/agents'
import { Avatar } from './CommBubble'

const MONO = "var(--font-mono)"
const UI = "'Rajdhani', sans-serif"
const POLL_MS = 2500

/**
 * The war-room command strip — lives under the header while the app IS the
 * war room (the takeover), in both STANDBY (protocol offline) and ENGAGED
 * (protocol live). Live WORKING/STANDBY status per roster member from the
 * house_party dispatch traffic, a ROSTER CORES button (per-agent model
 * config), and the exit control — EXIT in standby, STAND DOWN in engaged. All
 * chrome colour comes from the theme vars, which the party cycle is
 * continuously repainting, so the strip parades through the roster's colours
 * with the rest of the console.
 */
export default function PartyRosterStrip({ config, engaged, onExit, onOpenConfig }: {
  config: AppConfig
  engaged: boolean
  onExit: () => void
  onOpenConfig: () => void
}) {
  const [entries, setEntries] = useState<AgentCommEntry[]>([])

  useEffect(() => {
    const load = () => fetchAgentComms(config, 150).then(setEntries).catch(() => {})
    load()
    const t = setInterval(load, POLL_MS)
    return () => clearInterval(t)
  }, [config])

  const partyEntries = useMemo(
    () => entries.filter(e => e.protocol === 'house_party'),
    [entries],
  )

  const working = useMemo(() => {
    const w = new Set<string>()
    for (const e of partyEntries) if (e.status === 'running') w.add(e.to_agent)
    return w
  }, [partyEntries])

  const doneCount = useMemo(() => {
    const c: Record<string, number> = {}
    for (const e of partyEntries) if (e.status === 'ok') c[e.to_agent] = (c[e.to_agent] ?? 0) + 1
    return c
  }, [partyEntries])

  return (
    <div className="hb-holo" style={{
      display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0,
      margin: '0.45rem 0.6rem 0', padding: '0.32rem 0.65rem',
      overflowX: 'auto',
      animation: 'hbRise 0.4s ease both',
    }}>
      <span className="hb-round" style={{
        width: 7, height: 7, flexShrink: 0,
        background: 'var(--accent)',
        boxShadow: '0 0 8px var(--accent)',
        animation: engaged ? 'hbBlink 1.6s ease-in-out infinite' : 'none',
      }} />
      <span style={{
        fontFamily: UI, fontSize: '0.68rem', fontWeight: 700,
        letterSpacing: '0.16em', textTransform: 'uppercase',
        color: 'var(--accent-hover)', whiteSpace: 'nowrap', flexShrink: 0,
      }}>
        House Party Protocol
      </span>
      <span className="hb-hide-sm" style={{
        fontFamily: MONO, fontSize: '0.54rem', letterSpacing: '0.1em',
        color: 'var(--hb-icon)', whiteSpace: 'nowrap',
      }}>
        {engaged ? 'ALL HANDS · FULL GRADE' : 'STANDBY · ROSTER HELD'}
      </span>

      <span style={{ flex: 1, minWidth: 8 }} />

      {ROSTER.map(id => {
        const busy = working.has(id)
        return (
          <span
            key={id}
            title={`${id.toUpperCase()} — ${busy ? 'working' : 'standby'}${doneCount[id] ? ` · ${doneCount[id]} done` : ''}`}
            style={{ position: 'relative', flexShrink: 0, opacity: busy ? 1 : 0.6 }}
          >
            <Avatar id={id} size={22} />
            <span className="hb-round" style={{
              position: 'absolute', right: -1, bottom: -1, width: 7, height: 7,
              background: busy ? 'var(--hb-amber)' : 'var(--hb-icon-dim)',
              boxShadow: busy ? '0 0 6px rgba(242,183,92,0.9)' : 'none',
              border: '1px solid rgba(4,9,12,0.8)',
              animation: busy ? 'hbBlink 1.6s ease-in-out infinite' : 'none',
            }} />
          </span>
        )
      })}

      <span className="hb-hide-sm" style={{
        fontFamily: MONO, fontSize: '0.56rem', letterSpacing: '0.1em',
        color: working.size > 0 ? 'var(--hb-amber)' : 'var(--hb-icon)',
        whiteSpace: 'nowrap', flexShrink: 0,
      }}>
        {working.size > 0 ? `${working.size} WORKING` : engaged ? 'CHANNEL OPEN' : 'STANDBY'}
      </span>

      <button
        className="hb-btn"
        onClick={onOpenConfig}
        title="Roster cores — configure every agent's model"
        style={{
          height: 22, padding: '0 0.55rem', gap: '0.35rem', flexShrink: 0,
          fontFamily: UI, fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.14em',
        }}
      >
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
        </svg>
        CORES
      </button>

      <button
        className="hb-btn hb-btn-tint"
        onClick={onExit}
        title={engaged
          ? 'Stand down — end the protocol, dispatches return to the background tier'
          : 'Exit the war room — return to SPEDA'}
        style={{
          height: 22, padding: '0 0.6rem', color: '#e8a196', flexShrink: 0,
          fontFamily: UI, fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.16em',
        }}
      >
        {engaged ? 'STAND DOWN' : 'EXIT'}
      </button>
    </div>
  )
}
