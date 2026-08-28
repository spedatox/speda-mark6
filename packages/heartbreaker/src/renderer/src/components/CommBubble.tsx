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
 * agent's signature rim and its own mark.
 *
 * Both surfaces render a ROOM. Every agent speaks for itself, on one timeline,
 * in the order things actually happened — a dispatch and the reply to it are
 * two messages from two agents, never one card with the answer folded inside.
 * Message bodies render markdown (agents write it) inside `.prose`, which also
 * re-enables text selection so messages can be copied.
 */


/**
 * Stand-in geometry for the war room / all-hands channel — the only id left
 * with no wordmark art (the roster converging on a point, the same figure
 * the deck's rail uses).
 *
 * This is a MARK, not a letter. Two-letter placeholder tiles were tried and
 * rejected by the owner: a roster of "SP / CT / AT / NC" reads like a spreadsheet
 * of initials, and the whole point of the agent identities is that you know them
 * by their sigil.
 */
function FallbackGlyph({ size }: { size: number }) {
  const s = Math.round(size * 0.62)
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
      <FallbackGlyph size={size} />
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

/* ════════════════════════════════════════════════════════════════════════════
   THE ROOM — one grammar for every surface where agents talk to each other.

   These pieces started in PartyStream (the war room). They live here now
   because the comms tray needs the same grammar: a dispatch and its reply are
   two agents speaking, not one record with an answer folded inside it. The
   tray used to render the reply nested under the task behind a left rule —
   which reads as a transaction log, and puts the answer somewhere the eye
   does not look for one.
   ════════════════════════════════════════════════════════════════════════════ */

const FAILED = ['error', 'timeout', 'offline', 'refused']

/** A working agent — the same ring the tool chain and the deck use. */
export function Working({ id, label }: { id: string; label: string }) {
  const c = agentColor(id)
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
      <span style={{
        width: 13, height: 13, borderRadius: '50%', flexShrink: 0,
        border: `1.5px solid ${c}4d`, borderTopColor: c,
        animation: 'spin 0.7s linear infinite',
      }} />
      {label}
    </span>
  )
}

export function Divider({ text }: { text: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'center' }}>
      <span className="glass-round" style={{
        padding: '5px 14px',
        background: 'rgba(255,255,255,0.03)', border: 'none',
        fontSize: '0.78rem', letterSpacing: '0.1em', textTransform: 'uppercase',
        color: 'var(--hb-text-faint)',
      }}>
        {text}
      </span>
    </div>
  )
}

/** The owner's own line — right-aligned, neutral, tail on the right. */
export function OwnerSay({ text }: { text: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
      <div className="hb-party-owner" style={{
        maxWidth: '64%', padding: '11px 17px',
        background: 'rgba(198,218,235,0.07)',
        border: '1px solid rgba(255,255,255,0.07)',
        fontSize: '0.94rem', lineHeight: 1.55, color: '#e7f0f5',
        whiteSpace: 'pre-wrap',
      }}>
        {text}
      </div>
    </div>
  )
}

/**
 * One participant's line: mark, name, bubble.
 *
 * `head={false}` continues the same agent's run — the mark and the name drop
 * away and the tail squares off, so a burst from one agent reads as one turn
 * of speech. `compact` is the tray cut of the same grammar, not a different
 * one: smaller mark, tighter bubble, everything else identical.
 */
export function AgentSay({ id, commander, compact = false, head = true, badge, foot, children }: {
  id: string
  /** The commander's own bubble carries the accent; the roster answers neutral. */
  commander?: boolean
  compact?: boolean
  head?: boolean
  badge?: React.ReactNode
  foot?: React.ReactNode
  children: React.ReactNode
}) {
  const c = agentColor(id)
  const tile = compact ? 28 : 36

  return (
    <div style={{ display: 'flex', gap: compact ? 9 : 12, alignItems: 'flex-start' }}>
      {head ? (
        <span className="hb-tile" style={{
          width: tile, height: tile, flexShrink: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: `linear-gradient(160deg, ${c}38, ${c}0d)`,
          border: `1px solid ${c}57`,
        }}>
          <Avatar id={id} size={Math.round(tile * 0.61)} />
        </span>
      ) : (
        <span style={{ width: tile, flexShrink: 0 }} />
      )}

      <div style={{ minWidth: 0, flex: compact ? 1 : undefined }}>
        {head && (
          <div style={{
            fontSize: compact ? '0.78rem' : '0.8125rem', color: c,
            marginBottom: 4, fontWeight: 600, textTransform: 'capitalize',
          }}>
            {id}
          </div>
        )}
        <div
          className={head ? 'hb-party-agent' : 'hb-party-agent-mid'}
          style={{
            maxWidth: compact ? '100%' : 560, padding: compact ? '9px 13px' : '11px 16px',
            background: commander ? 'rgba(var(--hb-accent-rgb),0.09)' : 'rgba(255,255,255,0.03)',
            border: `1px solid ${commander ? 'rgba(var(--hb-accent-rgb),0.22)' : 'rgba(255,255,255,0.06)'}`,
            fontSize: compact ? '0.875rem' : '0.94rem', lineHeight: 1.6,
            color: commander ? 'var(--hb-text)' : 'var(--hb-text-dim)',
          }}
        >
          {badge}
          {children}
          {foot}
        </div>
      </div>
    </div>
  )
}

/* ── The transcript ──────────────────────────────────────────────────────── */

/** One line in the room: an agent said something at a moment in time. */
export interface CommMsg {
  key: string
  agent: string
  text: string
  at: number                // epoch ms — what the timeline sorts on
  seq: number               // a task precedes its own reply on an identical ms
  outbound: boolean         // an order going out (tinted) vs work coming back
  running?: boolean
  since?: string
  failed?: boolean
  status?: string
  durationMs?: number | null
  party?: boolean
  broadcast?: boolean
  copyText: string
}

function epoch(iso: string): number {
  return new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z').getTime()
}

/**
 * Flatten dispatch records into a single chronological transcript.
 *
 * A record carries BOTH halves of an exchange, which is right for a
 * transaction log and wrong for a chat: the reply belongs to the agent that
 * wrote it, at the moment it arrived. So one record becomes up to two
 * messages — the task from `from_agent` at `created_at`, the reply from
 * `to_agent` at `created_at + duration_ms` (the only arrival time the record
 * gives us). A dispatch still running contributes a live placeholder instead,
 * so the room shows someone working rather than a gap.
 */
export function commMessages(entries: AgentCommEntry[]): CommMsg[] {
  const out: CommMsg[] = []
  for (const e of entries) {
    const started = epoch(e.created_at)
    const result = e.result ?? ''
    const failed = FAILED.includes(e.status)

    out.push({
      key: `${e.id}:task`, agent: e.from_agent, text: e.task,
      at: started, seq: 0, outbound: true,
      party: e.protocol === 'house_party', broadcast: e.kind === 'broadcast',
      copyText: result ? `${e.task}\n\n--- ${e.to_agent.toUpperCase()} ---\n${result}` : e.task,
    })

    if (e.status === 'running') {
      out.push({
        key: `${e.id}:working`, agent: e.to_agent, text: '',
        at: started, seq: 1, outbound: false,
        running: true, since: e.created_at, copyText: '',
      })
    } else if (result || failed) {
      out.push({
        key: `${e.id}:reply`, agent: e.to_agent, text: result || e.status,
        at: started + (e.duration_ms ?? 0), seq: 1, outbound: false,
        failed, status: e.status, durationMs: e.duration_ms,
        copyText: result || e.status,
      })
    }
  }
  return out.sort((a, b) => a.at - b.at || a.seq - b.seq || a.key.localeCompare(b.key))
}

/** A quiet stretch earns a separator, the way any chat client marks one. */
const GAP_MS = 5 * 60 * 1000

function clock(at: number): string {
  const d = new Date(at)
  const p = (n: number): string => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
}

function CommLine({ m, head, compact }: { m: CommMsg; head: boolean; compact: boolean }) {
  const [open, setOpen] = useState(false)
  const c = agentColor(m.agent)
  const clip = compact ? 260 : 520
  const clipped = m.text.length > clip
  const body = open || !clipped ? m.text : m.text.slice(0, clip) + '…'

  return (
    <AgentSay
      id={m.agent}
      commander={m.outbound}
      compact={compact}
      head={head}
      badge={head && m.broadcast ? (
        <span style={{
          display: 'block', fontSize: '0.78rem', letterSpacing: '0.1em',
          textTransform: 'uppercase', color: 'var(--hb-amber)', marginBottom: 5,
        }}>
          All hands
        </span>
      ) : undefined}
      foot={m.running ? undefined : (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, marginTop: 5,
          fontSize: '0.72rem', color: 'var(--hb-text-faint)',
        }}>
          {clipped && (
            <button
              onClick={() => setOpen(o => !o)}
              title={open ? 'Collapse' : 'Show the full message'}
              style={{
                border: 'none', background: 'transparent', cursor: 'pointer', padding: 0,
                fontSize: '0.72rem',
                color: open ? 'var(--hb-cyan-bright)' : 'var(--hb-text-faint)',
              }}
            >
              {open ? 'Less' : 'More'}
            </button>
          )}
          <span style={{ flex: 1 }} />
          {m.durationMs != null && <span>{(m.durationMs / 1000).toFixed(1)}s</span>}
          <span>{fmtCommTime(new Date(m.at).toISOString())}</span>
          <CopyBtn tint={c} text={m.copyText} />
        </div>
      )}
    >
      {m.running ? (
        <Working id={m.agent} label="working…" />
      ) : m.failed ? (
        <span style={{ color: '#e88a7c' }}>
          {m.status}{m.text && m.text !== m.status ? `: ${body}` : ''}
        </span>
      ) : (
        <CommMarkdown text={body} size="inherit" />
      )}
      {m.running && m.since && (
        <span style={{ marginLeft: 8, color: 'var(--hb-text-faint)' }}>
          <LiveElapsed since={m.since} />
        </span>
      )}
    </AgentSay>
  )
}

/**
 * The transcript. Groups consecutive messages from one agent and drops a time
 * chip whenever the room went quiet, so a long scrollback reads as a
 * conversation with pauses in it rather than one undifferentiated column.
 */
export function CommFeed({ entries, compact = false }: {
  entries: AgentCommEntry[]
  compact?: boolean
}) {
  const msgs = commMessages(entries)
  let prevAgent = ''
  let prevAt = 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: compact ? 4 : 8 }}>
      {msgs.map(m => {
        const gap = prevAt > 0 && m.at - prevAt > GAP_MS
        const head = gap || m.agent !== prevAgent
        const chip = prevAt === 0 || gap
        prevAgent = m.agent
        prevAt = m.at
        return (
          <div key={m.key} style={{
            display: 'flex', flexDirection: 'column', gap: 8,
            marginTop: head && !chip ? 8 : 0,
            animation: 'hbRise 0.3s ease both',
          }}>
            {chip && <Divider text={clock(m.at)} />}
            <CommLine m={m} head={head} compact={compact} />
          </div>
        )
      })}
    </div>
  )
}
