import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useChatContext } from '../store/chat'
import { fetchAgentComms } from '../lib/api'
import type { AgentCommEntry } from '../lib/api'
import type { AppConfig, ChatMessage } from '../lib/types'
import { agentColor } from '../lib/agents'
import { Avatar } from './CommBubble'

/**
 * THE HOUSE PARTY TRANSCRIPT — the war room as an actual group chat.
 *
 * This is the piece the roster strip and the comms tray never were. While the
 * protocol is engaged the owner is not talking to one agent with a sidebar of
 * machine traffic next to it; they are in a ROOM. So the transcript reads like
 * one: the owner on the right, and every agent that speaks gets its own named
 * bubble on the left under its own mark and colour.
 *
 * Two sources are woven into one stream:
 *   · the chat store — the owner's messages and the commander's replies, which
 *     is the normal turn the war-room session is running
 *   · GET /agents/comms filtered to protocol="house_party" — every dispatch and
 *     every reply, each rendered as the agent that actually said it
 *
 * ORDERING, honestly: ChatMessage carries no timestamp, so the two sources
 * cannot be merged by time. The traffic is therefore appended after the turn it
 * belongs to rather than interleaved into it. In a live party that is also what
 * you see happen — you speak, the commander assigns, the roster reports in —
 * so the reading order is right even though it is not strictly timestamped.
 */

const POLL_MS = 2500
const MD_PLUGINS = [remarkGfm]

/** One participant's line: mark, name, bubble. */
function AgentSay({ id, commander, children }: {
  id: string
  /** The commander's own bubble carries the accent; the roster answers neutral. */
  commander?: boolean
  children: React.ReactNode
}) {
  const c = agentColor(id)
  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
      <span className="hb-tile" style={{
        width: 36, height: 36, flexShrink: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: `linear-gradient(160deg, ${c}38, ${c}0d)`,
        border: `1px solid ${c}57`,
      }}>
        <Avatar id={id} size={22} />
      </span>
      <div style={{ minWidth: 0 }}>
        <div style={{
          fontSize: '0.8125rem', color: c, marginBottom: 4, fontWeight: 600,
          textTransform: 'capitalize',
        }}>
          {id}
        </div>
        <div
          className="hb-party-agent"
          style={{
            maxWidth: 560, padding: '11px 16px',
            background: commander ? 'rgba(var(--hb-accent-rgb),0.09)' : 'rgba(255,255,255,0.03)',
            border: `1px solid ${commander ? 'rgba(var(--hb-accent-rgb),0.22)' : 'rgba(255,255,255,0.06)'}`,
            fontSize: '0.94rem', lineHeight: 1.6,
            color: commander ? 'var(--hb-text)' : 'var(--hb-text-dim)',
          }}
        >
          {children}
        </div>
      </div>
    </div>
  )
}

/** A working agent — the same ring the tool chain and comms tray use. */
function Working({ id, label }: { id: string; label: string }) {
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

function Divider({ text }: { text: string }) {
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
function OwnerSay({ text }: { text: string }) {
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

export default function PartyStream({ config, commanderId = 'speda' }: {
  config: AppConfig
  /** Which agent is chairing — SPEDA under the war-room alias. */
  commanderId?: string
}) {
  const { state } = useChatContext()
  const [comms, setComms] = useState<AgentCommEntry[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const pinned = useRef(true)

  useEffect(() => {
    let alive = true
    const load = () => {
      fetchAgentComms(config, 120)
        .then(rows => {
          if (!alive) return
          // Only the protocol's own traffic belongs in the room. Direct 1:1
          // dispatches keep going to the comms tray where they always were.
          setComms(rows.filter(r => r.protocol === 'house_party').reverse())
        })
        .catch(() => { /* offline — keep what is on screen */ })
    }
    load()
    const id = setInterval(load, POLL_MS)
    return () => { alive = false; clearInterval(id) }
  }, [config])

  useEffect(() => {
    if (pinned.current) bottomRef.current?.scrollIntoView()
  }, [state.messages.length, comms.length, state.isStreaming])

  const say = (m: ChatMessage) =>
    m.role === 'user'
      ? <OwnerSay key={m.id} text={m.content} />
      : (
        <AgentSay key={m.id} id={commanderId} commander>
          <div className="prose hb-comm-md" style={{ fontSize: 'inherit', lineHeight: 'inherit' }}>
            <ReactMarkdown remarkPlugins={MD_PLUGINS}>{m.content}</ReactMarkdown>
          </div>
        </AgentSay>
      )

  return (
    <div
      ref={scrollRef}
      onScroll={() => {
        const el = scrollRef.current
        if (el) pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
      }}
      style={{ flex: 1, overflowY: 'auto', padding: '26px 8px 0' }}
    >
      <div style={{
        maxWidth: 900, margin: '0 auto',
        display: 'flex', flexDirection: 'column', gap: 18,
      }}>
        <Divider text="House Party engaged" />

        {state.messages.map(say)}

        {comms.map(e => (
          <div key={e.id} style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            {/* the assignment, in the voice of whoever gave it */}
            <AgentSay id={e.from_agent} commander={e.from_agent === commanderId}>
              {e.kind === 'broadcast' && (
                <span style={{
                  display: 'block', fontSize: '0.78rem', letterSpacing: '0.1em',
                  textTransform: 'uppercase', color: 'var(--hb-amber)', marginBottom: 5,
                }}>
                  All hands
                </span>
              )}
              {e.task}
            </AgentSay>

            {/* and the answer, in the voice of whoever gave it */}
            {e.status === 'running' ? (
              <AgentSay id={e.to_agent}>
                <Working id={e.to_agent} label="working…" />
              </AgentSay>
            ) : e.result ? (
              <AgentSay id={e.to_agent}>
                {['error', 'timeout', 'offline', 'refused'].includes(e.status) ? (
                  <span style={{ color: '#e88a7c' }}>{e.status}: {e.result}</span>
                ) : (
                  <div className="prose hb-comm-md" style={{ fontSize: 'inherit', lineHeight: 'inherit' }}>
                    <ReactMarkdown remarkPlugins={MD_PLUGINS}>{e.result}</ReactMarkdown>
                  </div>
                )}
              </AgentSay>
            ) : null}
          </div>
        ))}

        <div ref={bottomRef} style={{ height: 16 }} />
      </div>
    </div>
  )
}
