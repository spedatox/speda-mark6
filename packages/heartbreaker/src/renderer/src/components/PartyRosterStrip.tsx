import { useEffect, useMemo, useState } from 'react'
import { fetchAgentComms } from '../lib/api'
import type { AgentCommEntry } from '../lib/api'
import type { AppConfig } from '../lib/types'
import { ROSTER } from '../lib/agents'
import { Avatar } from './CommBubble'

const MONO = "'Share Tech Mono', monospace"
const UI = "'Rajdhani', sans-serif"
const POLL_MS = 2500

/**
 * The engaged-protocol command strip — lives under the header while the app
 * IS the war room (the HPP profile takeover). Live WORKING/STANDBY status per
 * roster member from the house_party dispatch traffic, plus the STAND DOWN
 * control. All chrome colour comes from the theme vars, which the party cycle
 * is continuously repainting — the strip parades through the roster's colours
 * with the rest of the console.
 */
export default function PartyRosterStrip({ config, onStandDown }: {
  config: AppConfig
  onStandDown: () => void
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
        animation: 'hbBlink 1.6s ease-in-out infinite',
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
        ALL HANDS · FULL GRADE
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
        {working.size > 0 ? `${working.size} WORKING` : 'CHANNEL OPEN'}
      </span>

      <button
        className="hb-btn hb-btn-tint"
        onClick={onStandDown}
        title="End the protocol — dispatches return to the background tier"
        style={{
          height: 22, padding: '0 0.6rem', color: '#e8a196', flexShrink: 0,
          fontFamily: UI, fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.16em',
        }}
      >
        STAND DOWN
      </button>
    </div>
  )
}
