// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useChatContext } from '../store/chat'
import { fetchAgentComms } from '../lib/api'
import type { AgentCommEntry } from '../lib/api'
import type { AppConfig, ChatMessage } from '../lib/types'
import { AgentSay, Divider, OwnerSay, Working } from './CommBubble'

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

export default function PartyStream({ config, commanderId = 'speda' }: {
  config: AppConfig
  /** Which agent is chairing — Speda under the war-room alias. */
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
