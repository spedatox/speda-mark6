import { useEffect, useMemo, useState } from 'react'
import { fetchAgentComms } from '../lib/api'
import type { AgentCommEntry } from '../lib/api'
import type { AppConfig } from '../lib/types'
import { PARTY_ROSTER } from '../lib/agents'
import { AvatarStack } from './CommBubble'

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

  // Who is actually in the room: whoever the protocol has traffic with, plus
  // the commander. Falls back to the full roster before anything has happened.
  const present = useMemo(() => {
    const s = new Set<string>()
    for (const e of partyEntries) { s.add(e.from_agent); s.add(e.to_agent) }
    s.delete('all')
    return s.size > 1 ? PARTY_ROSTER.filter(id => s.has(id)) : PARTY_ROSTER
  }, [partyEntries])

  return (
    // The war room's bar, per the deck: a 70px header that states the grade,
    // shows the room, and holds the only two controls that matter here.
    <div style={{
      display: 'flex', alignItems: 'center', gap: 16, flexShrink: 0,
      height: 70, padding: '0 30px',
      borderBottom: '1px solid rgba(255,255,255,0.06)',
      overflowX: 'auto',
      animation: 'hbRise 0.4s ease both',
    }}>
      <span className="glass-round" style={{
        display: 'flex', alignItems: 'center', gap: 9, flexShrink: 0,
        height: 34, padding: '0 14px 0 10px',
        background: 'rgba(217,156,68,0.12)',
        border: '1px solid rgba(242,183,92,0.4)',
        fontSize: '0.845rem', fontWeight: 600, letterSpacing: '0.06em',
        color: 'var(--hb-amber-bright)',
      }}>
        <span style={{
          width: 7, height: 7, borderRadius: '50%',
          background: 'var(--hb-amber-bright)', boxShadow: '0 0 8px var(--hb-amber-bright)',
          animation: engaged ? 'hbPulse 1.4s ease-in-out infinite' : 'none',
        }} />
        {engaged ? 'House Party — Full Grade' : 'War Room — Standby'}
      </span>

      {/* max 7 — SPEDA and the Superior Six, the whole party. Clipping at 6
          left a "+1" hanging off the end of a roster that always fits. */}
      <AvatarStack ids={present} size={38} max={7} />

      <span className="hb-hide-sm" style={{
        fontSize: '0.875rem', color: 'var(--hb-text-dim)', whiteSpace: 'nowrap', flexShrink: 0,
      }}>
        {working.size > 0
          ? `${working.size} working`
          : `${present.length} agent${present.length === 1 ? '' : 's'}${engaged ? ' · all online' : ' · held'}`}
      </span>

      <span style={{ flex: 1, minWidth: 8 }} />

      <button
        className="hb-btn glass-round"
        onClick={onOpenConfig}
        title="Roster cores — configure every agent's model"
        style={{
          height: 36, padding: '0 18px', gap: 8, flexShrink: 0,
          fontSize: '0.875rem', color: 'var(--hb-text-dim)',
        }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
        </svg>
        Roster
      </button>

      <button
        className="glass-round"
        onClick={onExit}
        title={engaged
          ? 'Stand down — end the protocol, dispatches return to the background tier'
          : 'Exit the war room — return to SPEDA'}
        style={{
          height: 36, padding: '0 18px', flexShrink: 0, cursor: 'pointer',
          background: 'rgba(216,72,60,0.12)',
          border: '1px solid rgba(216,72,60,0.35)',
          fontSize: '0.875rem', fontWeight: 600, color: '#e5897c',
        }}
      >
        {engaged ? 'Stand Down' : 'Exit'}
      </button>
    </div>
  )
}
