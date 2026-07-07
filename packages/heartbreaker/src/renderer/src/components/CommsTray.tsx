import { useEffect, useRef, useState } from 'react'
import { fetchAgentComms, getHouseParty, setHouseParty } from '../lib/api'
import type { AgentCommEntry } from '../lib/api'
import type { AppConfig } from '../lib/types'
import { useIsMobile } from '../lib/useIsMobile'
import { Bubble } from './CommBubble'

/**
 * AGENT_COMMS — the inter-agent traffic tray.
 *
 * A floating liquid-glass slab anchored bottom-right showing live dispatch
 * traffic between SPEDA and the Superior Six (GET /agents/comms, written by
 * app/core/dispatch.py) as a chat scrollback — the same fluid-glass bubbles
 * as the House Party war room, compact cut. EXTEND_ grows it into the full
 * traffic console with the DATA_BANKS motion language; also hosts the House
 * Party Protocol stand-down control.
 */

const MONO = "var(--font-mono)"
const UI = "'Rajdhani', sans-serif"
const POLL_MS = 3000

export default function CommsTray({ config, onClose }: { config: AppConfig; onClose: () => void }) {
  const isMobile = useIsMobile()
  const [entries, setEntries] = useState<AgentCommEntry[]>([])
  const [wide, setWide] = useState(false)
  const [party, setParty] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)
  const feedRef = useRef<HTMLDivElement>(null)
  const pinnedToEnd = useRef(true)  // follow the newest bubble unless the user scrolled up

  useEffect(() => {
    const load = () => {
      // oldest first — a chat scrollback, newest at the bottom
      fetchAgentComms(config, 120).then(rows => { setEntries(rows.slice().reverse()); setLoaded(true) })
    }
    load()
    getHouseParty(config).then(setParty)
    timer.current = setInterval(load, POLL_MS)
    return () => { if (timer.current) clearInterval(timer.current) }
  }, [config])

  useEffect(() => {
    // Esc retracts the extended tray first; a second Esc closes it.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (wide) setWide(false)
      else onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [wide, onClose])

  // Engagement is owner-voice-only (say it to SPEDA); the UI can only STAND DOWN.
  const standDown = async () => {
    if (!party) return
    setParty(false)                         // optimistic
    setParty(await setHouseParty(config, false))
  }

  const live = entries.filter(e => e.status === 'running').length

  useEffect(() => {
    const el = feedRef.current
    if (el && pinnedToEnd.current) el.scrollTop = el.scrollHeight
  }, [entries, wide])

  return (
    <section
      className="hb-holo"
      style={{
        position: 'fixed', zIndex: 480,
        right: isMobile ? 8 : 14, bottom: isMobile ? 8 : 14,
        width: isMobile
          ? 'calc(100vw - 16px)'
          : wide ? 'min(780px, calc(100vw - 28px))' : 420,
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
        animation: 'hbRise 0.35s ease both',
        transition: 'width 0.45s cubic-bezier(0.22, 0.9, 0.3, 1)',
      }}
    >
      <header className="hb-head-glass" style={{ flexShrink: 0, justifyContent: 'space-between', gap: 10 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          AGENT_COMMS // TRAFFIC
          {live > 0 && (
            <span style={{
              fontFamily: MONO, fontSize: '0.52rem', letterSpacing: '0.1em',
              color: 'var(--hb-amber)', textTransform: 'none',
            }}>
              {live} LIVE
            </span>
          )}
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button
            className={party ? 'hb-btn hb-btn-tint' : 'hb-btn'}
            onClick={standDown}
            title={party
              ? 'STAND DOWN — end the House Party Protocol'
              : "Engaged only by telling SPEDA: 'House Party Protocol'"}
            style={{
              gap: 5, height: 18, padding: '0 6px',
              ...(party ? { color: 'var(--hb-amber-bright)' } : {}),
              cursor: party ? 'pointer' : 'default',
              fontFamily: UI, fontSize: '0.56rem', fontWeight: 700, letterSpacing: '0.14em',
            }}
          >
            <span style={{
              width: 5, height: 5, borderRadius: '50%',
              background: party ? 'var(--hb-amber)' : 'var(--hb-icon-dim)',
              boxShadow: party ? '0 0 6px rgba(242,183,92,0.8)' : 'none',
            }} />
            {party ? 'HPP · STAND DOWN' : 'HPP OFFLINE'}
          </button>
          <button
            onClick={() => setWide(w => !w)}
            title={wide ? 'Retract (Esc)' : 'Extend the traffic console'}
            style={{
              display: 'flex', alignItems: 'center', gap: 5,
              border: 'none', background: 'transparent', cursor: 'pointer', padding: '0 2px',
              fontFamily: MONO, fontSize: '0.54rem', letterSpacing: '0.14em',
              color: wide ? 'var(--hb-amber)' : 'var(--hb-icon)', transition: 'color 0.15s',
            }}
          >
            {wide ? 'RETRACT_' : 'EXTEND_'}
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
              style={{ transform: wide ? 'rotate(180deg)' : 'none', transition: 'transform 0.3s cubic-bezier(0.22, 0.9, 0.3, 1)' }}>
              <polyline points="6 14 12 8 18 14" />
            </svg>
          </button>
          <button
            onClick={onClose}
            title="Close (Esc)"
            style={{
              border: 'none', background: 'transparent', cursor: 'pointer',
              color: 'var(--hb-icon-dim)', display: 'flex', alignItems: 'center', padding: 0,
            }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </span>
      </header>

      <div
        ref={feedRef}
        onScroll={() => {
          const el = feedRef.current
          if (el) pinnedToEnd.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60
        }}
        style={{
          overflowY: 'auto',
          padding: '0.35rem 0.6rem 0.45rem',
          height: wide ? '68vh' : isMobile ? 230 : 268,
          transition: 'height 0.45s cubic-bezier(0.22, 0.9, 0.3, 1)',
        }}
      >
        {entries.length === 0 ? (
          <p style={{
            padding: '0.6rem 0.1rem', margin: 0,
            fontFamily: MONO, fontSize: '0.58rem', letterSpacing: '0.14em',
            color: 'var(--hb-icon-dim)',
          }}>
            {loaded
              ? '// NO TRAFFIC — DISPATCHES BETWEEN AGENTS WILL APPEAR HERE'
              : '// LINKING…'}
          </p>
        ) : entries.map(e => (
          <Bubble key={e.id} e={e} compact={!wide} />
        ))}
      </div>
    </section>
  )
}
