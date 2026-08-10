import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { AgentCommEntry } from '../lib/api'
import { agentColor, fmtCommTime } from '../lib/agents'
import { hasMark } from '../lib/agentMarks'
import AgentMark from './AgentMark'

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

/**
 * Stand-in geometry for the two ids with no wordmark art: Orion (an orbit —
 * the custodian circling the system) and the war room / all-hands channel (the
 * roster converging on a point, the same figure the deck's rail uses).
 *
 * These are MARKS, not letters. Two-letter placeholder tiles were tried and
 * rejected by the owner: a roster of "SP / CT / AT / NC" reads like a spreadsheet
 * of initials, and the whole point of the agent identities is that you know them
 * by their sigil.
 */
function FallbackGlyph({ id, size }: { id: string; size: number }) {
  const s = Math.round(size * 0.62)
  if (id === 'warroom' || id === 'all') {
    return (
      <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <circle cx="12" cy="12" r="3" />
        <circle cx="12" cy="3.5" r="1.6" /><circle cx="19.5" cy="16.5" r="1.6" /><circle cx="4.5" cy="16.5" r="1.6" />
        <line x1="12" y1="5.1" x2="12" y2="9" />
        <line x1="18.1" y1="15.6" x2="14.6" y2="13.5" />
        <line x1="5.9" y1="15.6" x2="9.4" y2="13.5" />
      </svg>
    )
  }
  // Orion — an orbit around a core.
  return (
    <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <circle cx="12" cy="12" r="2.6" fill="currentColor" stroke="none" />
      <ellipse cx="12" cy="12" rx="9.5" ry="5" transform="rotate(-28 12 12)" />
    </svg>
  )
}

export function Avatar({ id, size = 26 }: { id: string; size?: number }) {
  const c = agentColor(id)
  // The agent's own mark, bare — it fills the box with no ring or plate around
  // it. Below ~28px the glass finish's bloom swallows the geometry, so small
  // chips get the flat cut.
  if (hasMark(id)) {
    return (
      <AgentMark agentId={id} size={size} finish={size >= 28 ? 'glass' : 'flat'}
                 style={{ flexShrink: 0 }} />
    )
  }
  return (
    <span className="hb-tile" style={{
      width: size, height: size, flexShrink: 0,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: `linear-gradient(160deg, ${c}33, ${c}0d)`,
      border: `1px solid ${c}52`,
      color: c,
    }}>
      <FallbackGlyph id={id} size={size} />
    </span>
  )
}

/**
 * Overlapping roster stack — who is in this channel, at a glance. Used by the
 * comms tray header and the House Party bar. Each mark sits in a tile rimmed
 * in the page colour so the overlap reads as depth rather than a smear.
 */
export function AvatarStack({ ids, size = 28, max = 5 }: {
  ids: string[]; size?: number; max?: number
}) {
  const shown = ids.slice(0, max)
  const rest = ids.length - shown.length
  return (
    <div style={{ display: 'flex', alignItems: 'center', flexShrink: 0 }}>
      {shown.map((id, i) => (
        <span
          key={id}
          title={id}
          className="hb-tile"
          style={{
            width: size, height: size, flexShrink: 0,
            marginLeft: i === 0 ? 0 : -Math.round(size * 0.29),
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: `linear-gradient(160deg, ${agentColor(id)}3d, ${agentColor(id)}0f)`,
            border: '1.5px solid var(--hb-void)',
            zIndex: shown.length - i,
          }}
        >
          <Avatar id={id} size={Math.round(size * 0.66)} />
        </span>
      ))}
      {rest > 0 && (
        <span style={{ marginLeft: 8, fontSize: '0.8125rem', color: 'var(--hb-text-faint)' }}>
          +{rest}
        </span>
      )}
    </div>
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
  // Body copy sits at reading size. It was 0.7rem/0.76rem — 11px — for text
  // that is full sentences of agent output, which is a caption pretending to
  // be a message.
  const bodyFont = compact ? '0.875rem' : '0.905rem'

  return (
    <div style={{
      display: 'flex', gap: 12, padding: '5px 0',
      flexDirection: mine ? 'row-reverse' : 'row',
      alignItems: 'flex-start',
      animation: 'hbRise 0.3s ease both',
    }}>
      <Avatar id={e.from_agent} size={compact ? 28 : 34} />
      <div
        className={mine ? 'hb-bubble-agent-mine' : 'hb-bubble-agent'}
        style={{
          maxWidth: compact ? '88%' : 'min(72%, 640px)',
          padding: compact ? '10px 13px' : '11px 15px',
          border: `1px solid ${from}33`,
          background: `${from}14`,
          backdropFilter: 'var(--hb-holo-blur)',
          WebkitBackdropFilter: 'var(--hb-holo-blur)',
          boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.12)',
        }}
      >
        {/* meta line: SPEDA ▸ SENTINEL · 06:13:42 · HP · copy/expand controls */}
        <div style={{
          display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 6,
          fontSize: '0.78rem', letterSpacing: '0.02em',
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
                fontSize: '0.78rem',
                color: open ? 'var(--hb-cyan-bright)' : 'var(--hb-text-faint)',
              }}
            >
              {open ? 'Less' : 'More'}
            </button>
          )}
        </div>

        {/* the dispatch (task) */}
        <CommMarkdown text={showTask} size={bodyFont} />

        {/* the reply, nested — the target agent answering in the thread */}
        {e.status === 'running' ? (
          // A spinning ring in the target agent's colour, not a blinking word.
          // The deck's running state is the same ring the tool chain uses, so
          // "something is working" looks identical everywhere in the app.
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10,
            marginTop: 8, fontSize: bodyFont, color: 'var(--hb-text-dim)',
          }}>
            <span style={{
              width: 14, height: 14, borderRadius: '50%', flexShrink: 0,
              border: `1.5px solid ${to}4d`, borderTopColor: to,
              animation: 'spin 0.7s linear infinite',
            }} />
            {e.to_agent} is working…
            {' '}<LiveElapsed since={e.created_at} />
          </div>
        ) : result && (
          <div style={{
            marginTop: 10, paddingLeft: 10,
            borderLeft: `2px solid ${failed ? 'var(--hb-red)' : to}`,
          }}>
            <div style={{
              display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 4,
              fontSize: '0.78rem',
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
