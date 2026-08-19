import { useEffect, useRef, useState } from 'react'
import { fetchAgentComms, getHouseParty, setHouseParty } from '../lib/api'
import type { AgentCommEntry } from '../lib/api'
import type { AppConfig } from '../lib/types'
import { useIsMobile } from '../lib/useIsMobile'
import { CommFeed, AvatarStack } from './CommBubble'
import { SkeletonList } from './Skeleton'
import { useT } from '../lib/i18n'

/**
 * AGENT_COMMS — the inter-agent traffic tray.
 *
 * A floating liquid-glass slab anchored bottom-right showing live dispatch
 * traffic between Speda and the Superior Six (GET /agents/comms, written by
 * app/core/dispatch.py) as a chat scrollback — the same fluid-glass bubbles
 * as the House Party war room, compact cut. EXTEND_ grows it into the full
 * traffic console with the DATA_BANKS motion language; also hosts the House
 * Party Protocol stand-down control.
 */

const UI = "'Rajdhani', sans-serif"
const POLL_MS = 3000

export default function CommsTray({ config, onClose }: { config: AppConfig; onClose: () => void }) {
  const t = useT()
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
      fetchAgentComms(config, 120)
        .then(rows => setEntries(rows.slice().reverse()))
        // Always clear — an unreachable backend must not skeleton this forever.
        .finally(() => setLoaded(true))
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

  // Engagement is owner-voice-only (say it to Speda); the UI can only STAND DOWN.
  const standDown = async () => {
    if (!party) return
    setParty(false)                         // optimistic
    setParty(await setHouseParty(config, false))
  }

  const live = entries.filter(e => e.status === 'running').length
  // Who is actually talking in this channel, newest participants first.
  const inChannel = Array.from(new Set(entries.flatMap(e => [e.from_agent, e.to_agent]))).filter(a => a !== 'all')

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
      {/* Header — the deck's group-window bar: who is in the channel, what it
          is, and how many are live. It used to read "AGENT_COMMS // TRAFFIC",
          which named the endpoint rather than the conversation. */}
      <header style={{
        height: 56, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 12,
        padding: '0 18px', borderBottom: '1px solid rgba(255,255,255,0.07)',
      }}>
        {inChannel.length > 0 && <AvatarStack ids={inChannel} size={28} max={4} />}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontFamily: UI, fontSize: '0.94rem', fontWeight: 600,
            letterSpacing: '0.04em', color: 'var(--hb-text)',
          }}>
            {t.commsTray.agentTraffic}
          </div>
          <div style={{
            fontSize: '0.78rem',
            color: live > 0 ? 'var(--hb-green)' : 'var(--hb-text-faint)',
          }}>
            {live > 0 ? t.commsTray.working(live) : t.commsTray.exchanges(entries.length)}
          </div>
        </div>
        <button
          onClick={() => setWide(w => !w)}
          title={wide ? t.commsTray.retractEsc : t.commsTray.extendConsole}
          style={{
            border: 'none', background: 'transparent', cursor: 'pointer', padding: '0 2px',
            fontSize: '0.8125rem', letterSpacing: '0.06em', flexShrink: 0,
            color: wide ? 'var(--hb-cyan-bright)' : 'var(--hb-cyan)', transition: 'color 0.15s',
          }}
        >
          {wide ? t.commsTray.retract : t.commsTray.expand}
        </button>
        <button
          onClick={onClose}
          title={t.message.closeEsc}
          style={{
            border: 'none', background: 'transparent', cursor: 'pointer', flexShrink: 0,
            color: 'var(--hb-text-faint)', display: 'flex', alignItems: 'center', padding: 0,
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
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
        {!loaded ? (
          <SkeletonList rows={3} markSize={26} />
        ) : entries.length === 0 ? (
          <p style={{
            padding: '10px 2px', margin: 0,
            fontSize: '0.875rem', color: 'var(--hb-text-faint)',
          }}>
            {t.commsTray.noTraffic}
          </p>
        ) : (
          <CommFeed entries={entries} compact={!wide} />
        )}
      </div>

      {/* Footer — protocol state, and how to raise it. Standing down is a
          control; engaging is deliberately NOT, because the passphrase gate
          lives with Speda (see the House Party section of CLAUDE.md). */}
      <div style={{
        flexShrink: 0, padding: '12px 16px 16px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10,
      }}>
        <button
          className="glass-round"
          onClick={standDown}
          disabled={!party}
          title={party
            ? t.commsTray.standDownTitle
            : t.commsTray.engagedOnlyTitle}
          style={{
            display: 'flex', alignItems: 'center', gap: 8, height: 28, padding: '0 12px',
            background: 'rgba(217,156,68,0.08)',
            border: '1px solid rgba(217,156,68,0.28)',
            fontSize: '0.78rem', color: party ? 'var(--hb-amber-bright)' : '#d3a04a',
            cursor: party ? 'pointer' : 'default',
          }}
        >
          <span style={{
            width: 5, height: 5, borderRadius: '50%',
            background: party ? 'var(--hb-amber-bright)' : 'var(--hb-icon-dim)',
            boxShadow: party ? '0 0 6px rgba(242,183,92,0.8)' : 'none',
          }} />
          {party ? t.commsTray.housePartyLive : t.commsTray.housePartyOffline}
        </button>
        {!party && (
          <span style={{ fontSize: '0.78rem', color: 'var(--hb-text-faint)', whiteSpace: 'nowrap' }}>
            {t.commsTray.sayHouseParty}
          </span>
        )}
      </div>
    </section>
  )
}
