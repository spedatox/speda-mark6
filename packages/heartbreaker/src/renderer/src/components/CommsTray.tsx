import { useEffect, useRef, useState } from 'react'
import { fetchAgentComms, getHouseParty, setHouseParty, fetchActiveLegionRuns, attachLegionStream } from '../lib/api'
import type { AgentCommEntry, ActiveLegionRun } from '../lib/api'
import type { AppConfig, SubagentRun } from '../lib/types'
import { foldLegionEvent } from '../lib/subagentFold'
import { useIsMobile } from '../lib/useIsMobile'
import { CommFeed, AvatarStack } from './CommBubble'
import SubagentDetailView from './SubagentDetailView'
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
  const [legionRuns, setLegionRuns] = useState<ActiveLegionRun[]>([])
  const [openRun, setOpenRun] = useState<SubagentRun | null>(null)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)
  const feedRef = useRef<HTMLDivElement>(null)
  const pinnedToEnd = useRef(true)  // follow the newest bubble unless the user scrolled up
  const attachAbort = useRef<AbortController | null>(null)

  useEffect(() => {
    const load = () => {
      // oldest first — a chat scrollback, newest at the bottom
      fetchAgentComms(config, 120)
        .then(rows => setEntries(rows.slice().reverse()))
        // Always clear — an unreachable backend must not skeleton this forever.
        .finally(() => setLoaded(true))
      fetchActiveLegionRuns(config).then(setLegionRuns)
    }
    load()
    getHouseParty(config).then(setParty)
    timer.current = setInterval(load, POLL_MS)
    return () => {
      if (timer.current) clearInterval(timer.current)
      attachAbort.current?.abort()
    }
  }, [config])

  // A background legionnaire has no message of its own to hang its live
  // transcript on (it outlives the turn that deployed it) — so clicking its
  // row here opens the same SubagentDetailView fed by its own independent
  // SSE connection instead of message.subagents.
  const openLegionRun = (run: ActiveLegionRun) => {
    attachAbort.current?.abort()
    const ctrl = new AbortController()
    attachAbort.current = ctrl
    setOpenRun({ id: `legion-bg-${run.ticket}`, agent: run.agent, label: run.label, running: true, steps: [], source: 'legion' })
    ;(async () => {
      for await (const event of attachLegionStream(config, run.ticket, ctrl.signal)) {
        setOpenRun(prev => foldLegionEvent(prev, event))
      }
    })().catch(() => { /* aborted or dropped connection — the tray row still reflects /legion/active */ })
  }

  const closeLegionRun = () => {
    attachAbort.current?.abort()
    attachAbort.current = null
    setOpenRun(null)
  }

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

      {/* Background legionnaires currently deployed — none of these own a
          message of their own (they outlive the turn that dispatched them),
          so this row is their only entry point into the live detail view. */}
      {legionRuns.length > 0 && (
        <div style={{
          display: 'flex', flexDirection: 'column', gap: 4,
          padding: '8px 16px', borderBottom: '1px solid rgba(255,255,255,0.06)',
        }}>
          <span style={{
            fontSize: '0.68rem', letterSpacing: '0.08em', textTransform: 'uppercase',
            color: 'var(--hb-cyan)',
          }}>
            {t.commsTray.legionRunning(legionRuns.length)}
          </span>
          {legionRuns.map(run => (
            <button
              key={run.ticket}
              onClick={() => openLegionRun(run)}
              style={{
                display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                background: 'transparent', border: 'none', padding: '2px 0',
                cursor: 'pointer', textAlign: 'left', fontSize: '0.8125rem',
                color: 'var(--hb-text-dim)',
              }}
            >
              <span style={{
                width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                background: 'var(--hb-cyan)', boxShadow: '0 0 6px var(--hb-cyan)',
                animation: 'pulse 1.2s ease infinite',
              }} />
              <span style={{ color: 'var(--hb-text)', flexShrink: 0 }}>{run.agent}</span>
              <span style={{
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0,
              }}>{run.label}</span>
              <span style={{ marginLeft: 'auto', color: 'var(--hb-text-faint)', flexShrink: 0 }}>
                #{run.ticket}
              </span>
            </button>
          ))}
        </div>
      )}

      {openRun && <SubagentDetailView run={openRun} onClose={closeLegionRun} />}

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
