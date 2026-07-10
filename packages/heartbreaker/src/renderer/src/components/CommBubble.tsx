import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { AgentCommEntry } from '../lib/api'
import { agentColor, fmtCommTime } from '../lib/agents'

/** Live-updating elapsed seconds since a dispatch started — makes a running
 *  (background) dispatch visibly alive rather than a frozen "WORKING…". */
function LiveElapsed({ since }: { since: string }) {
  const start = new Date(since.endsWith('Z') || since.includes('+') ? since : since + 'Z').getTime()
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  const s = Math.max(0, Math.round((now - start) / 1000))
  return <span style={{ opacity: 0.75 }}>{s < 60 ? `${s}s` : `${Math.floor(s / 60)}m${s % 60}s`}</span>
}

/**
 * Shared fluid-glass chat pieces for inter-agent traffic — used by both the
 * AGENT_COMMS tray and the House Party war room so the whole comms surface
 * speaks the same Mark VI hologram language: liquid-glass slabs with the
 * agent's signature rim, monogram avatars, replies threaded under the task.
 * Message bodies render markdown (agents write it) inside `.prose`, which also
 * re-enables text selection so messages can be copied.
 */

const MONO = "var(--font-mono)"
const UI = "'Rajdhani', sans-serif"

export function Avatar({ id, size = 26 }: { id: string; size?: number }) {
  const c = agentColor(id)
  // Placeholder identity: a single big first initial in the agent's colour.
  // Clean and modern on purpose — swapped for the real logos once they land.
  const initial = id.charAt(0).toUpperCase()
  return (
    <span style={{
      width: size, height: size, flexShrink: 0, borderRadius: '50%',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: `${c}12`,
      border: `1px solid ${c}59`,
      color: c,
      fontFamily: UI, fontWeight: 800, fontSize: size * 0.52,
      letterSpacing: 0, lineHeight: 1,
    }}>
      {initial}
    </span>
  )
}

/** Markdown body — selectable (.prose flips user-select back on) and scaled
 *  down to bubble size. GFM only; no math/chart plugins in the comms feed. */
export function CommMarkdown({ text, size }: { text: string; size: string }) {
  return (
    <div className="prose hb-comm-md" style={{ fontSize: size, lineHeight: 1.5 }}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  )
}

/** Tiny copy-to-clipboard control for a bubble's meta line. */
export function CopyBtn({ text, tint }: { text: string; tint: string }) {
  const [done, setDone] = useState(false)
  return (
    <button
      onClick={e => {
        e.stopPropagation()
        navigator.clipboard.writeText(text).then(() => {
          setDone(true)
          setTimeout(() => setDone(false), 1600)
        })
      }}
      title="Copy message"
      style={{
        border: 'none', background: 'transparent', cursor: 'pointer',
        padding: 0, display: 'flex', alignItems: 'center',
        color: done ? 'var(--hb-green)' : 'var(--hb-icon-dim)',
        transition: 'color 0.15s',
      }}
    >
      {done ? (
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
          <polyline points="20 6 9 17 4 12" />
        </svg>
      ) : (
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          onMouseEnter={e => { (e.currentTarget.parentElement as HTMLElement).style.color = tint }}
          onMouseLeave={e => { (e.currentTarget.parentElement as HTMLElement).style.color = 'var(--hb-icon-dim)' }}>
          <rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
        </svg>
      )}
    </button>
  )
}

export function Bubble({ e, mine = false, compact = false }: {
  e: AgentCommEntry
  mine?: boolean
  compact?: boolean
}) {
  const [open, setOpen] = useState(false)
  const from = agentColor(e.from_agent)
  const to = agentColor(e.to_agent)
  const failed = ['error', 'timeout', 'offline'].includes(e.status)
  const clip = compact ? 200 : 420
  const clipped = e.task.length > clip || (e.result ?? '').length > clip
  const showTask = open || e.task.length <= clip ? e.task : e.task.slice(0, clip) + '…'
  const result = e.result ?? ''
  const showResult = open || result.length <= clip ? result : result.slice(0, clip) + '…'
  const bodyFont = compact ? '0.7rem' : '0.76rem'

  return (
    <div style={{
      display: 'flex', gap: 8, padding: '0.3rem 0',
      flexDirection: mine ? 'row-reverse' : 'row',
      animation: 'hbRise 0.3s ease both',
    }}>
      <Avatar id={e.from_agent} size={compact ? 22 : 26} />
      <div
        className="hb-glass-sm"
        style={{
          maxWidth: compact ? '88%' : 'min(72%, 640px)',
          padding: compact ? '0.4rem 0.55rem 0.45rem' : '0.45rem 0.6rem 0.5rem',
          border: `1px solid ${from}44`,
          background: `${from}0d`,
          backdropFilter: 'var(--hb-holo-blur)',
          WebkitBackdropFilter: 'var(--hb-holo-blur)',
          boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.12)',
        }}
      >
        {/* meta line: SPEDA ▸ SENTINEL · 06:13:42 · HP · copy/expand controls */}
        <div style={{
          display: 'flex', alignItems: 'baseline', gap: 7, marginBottom: 3,
          fontFamily: MONO, fontSize: '0.52rem', letterSpacing: '0.08em',
        }}>
          <span style={{ color: from, fontWeight: 700 }}>{e.from_agent.toUpperCase()}</span>
          <span style={{ color: 'var(--hb-icon-dim)' }}>▸</span>
          <span style={{ color: to, fontWeight: 700 }}>{e.to_agent.toUpperCase()}</span>
          <span style={{ color: 'var(--hb-icon-dim)' }}>{fmtCommTime(e.created_at)}</span>
          {e.protocol === 'house_party' && <span style={{ color: 'var(--hb-amber)' }}>HP</span>}
          {e.kind === 'broadcast' && <span style={{ color: 'var(--hb-amber)' }}>BROADCAST</span>}
          <span style={{ flex: 1 }} />
          <CopyBtn
            tint={from}
            text={result ? `${e.task}\n\n--- ${e.to_agent.toUpperCase()} ---\n${result}` : e.task}
          />
          {clipped && (
            <button
              onClick={() => setOpen(o => !o)}
              title={open ? 'Collapse' : 'Show the full exchange'}
              style={{
                border: 'none', background: 'transparent', cursor: 'pointer', padding: 0,
                fontFamily: MONO, fontSize: '0.5rem', letterSpacing: '0.12em',
                color: open ? 'var(--hb-amber)' : 'var(--hb-icon)',
              }}
            >
              {open ? 'LESS_' : 'MORE_'}
            </button>
          )}
        </div>

        {/* the dispatch (task) */}
        <CommMarkdown text={showTask} size={bodyFont} />

        {/* the reply, nested — the target agent answering in the thread */}
        {e.status === 'running' ? (
          <p style={{
            margin: '0.4rem 0 0', fontFamily: MONO, fontSize: '0.56rem',
            letterSpacing: '0.12em', color: 'var(--hb-amber)',
          }}>
            {e.to_agent.toUpperCase()} WORKING<span style={{ animation: 'hbBlink 1.1s ease-in-out infinite' }}>…</span>
            {' '}<LiveElapsed since={e.created_at} />
          </p>
        ) : result && (
          <div style={{
            marginTop: '0.45rem', paddingLeft: '0.55rem',
            borderLeft: `2px solid ${failed ? 'var(--hb-red)' : to}`,
          }}>
            <div style={{
              display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 2,
              fontFamily: MONO, fontSize: '0.52rem', letterSpacing: '0.08em',
            }}>
              <span style={{ color: to, fontWeight: 700 }}>{e.to_agent.toUpperCase()}</span>
              {failed && <span style={{ color: 'var(--hb-red)' }}>{e.status.toUpperCase()}</span>}
              {e.duration_ms != null && (
                <span style={{ color: 'var(--hb-icon-dim)' }}>{(e.duration_ms / 1000).toFixed(1)}s</span>
              )}
            </div>
            {failed ? (
              <p style={{
                margin: 0, fontFamily: "'SamsungOne','Inter',sans-serif",
                fontSize: bodyFont, lineHeight: 1.45, color: '#d98a7a',
                whiteSpace: 'pre-wrap', userSelect: 'text',
              }}>{showResult}</p>
            ) : (
              <CommMarkdown text={showResult} size={bodyFont} />
            )}
          </div>
        )}
      </div>
    </div>
  )
}
