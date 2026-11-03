// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useRef, useCallback, memo } from 'react'
import { useChatContext } from '../store/chat'
import Message from './Message'
import type { ChatMessage } from '../lib/types'
import AgentMark from './AgentMark'
import { roomName } from './RoomConversation'
import { useT } from '../lib/i18n'

interface Props {
  onReply?: (message: ChatMessage) => void
  onDelete: (id: string) => void
  onRegenerate: (assistantId: string) => void
  onEditAndResend: (userId: string, newContent: string) => void
}

/**
 * Memoized message wrapper — only re-renders when the message object itself
 * changes (content, streaming, tools, etc.), NOT when a sibling message updates.
 * This is the single biggest perf win: during streaming, only the active message
 * re-renders instead of the entire list.
 *
 * CRITICAL: the handler props are the STABLE, id-taking callbacks from the parent
 * (never re-created per render). The message.id binding and role gating happen
 * INSIDE this component, so MemoMessage's props are `message` (which the reducer
 * only replaces for the one message that changed) plus three constant function
 * refs. That is what lets React's shallow `memo` compare short-circuit every
 * completed row during streaming. Binding ids in the parent's `.map()` (fresh
 * closures each render) would silently defeat the memo.
 */
const MemoMessage = memo(function MemoMessage({
  message, showAttribution, onDelete, onRegenerate, onEditAndResend, onReply,
}: {
  message: ChatMessage
  showAttribution: boolean
  onDelete: (id: string) => void
  onRegenerate: (id: string) => void
  onEditAndResend: (id: string, newContent: string) => void
  onReply?: (message: ChatMessage) => void
}) {
  const t = useT().room
  const handleDelete = useCallback(() => onDelete(message.id), [onDelete, message.id])
  const handleRegenerate = useCallback(
    () => onRegenerate(message.id),
    [onRegenerate, message.id],
  )
  const handleEdit = useCallback(
    (newContent: string) => onEditAndResend(message.id, newContent),
    [onEditAndResend, message.id],
  )
  if (message.roomNotice) {
    const notice = message.roomNotice
    const name = roomName(notice.agent_id)
    return <div className="hb-room-notice" role="status"><AgentMark agentId={notice.agent_id} size={19} finish="flat" />
      <span>{notice.action === 'invite' ? t.joined(name) : notice.action === 'dismiss' ? t.left(name) : t.newLead(name)}</span>
    </div>
  }
  return (
    <div data-reply-message={message.id}><Message
      message={message}
      showAttribution={showAttribution}
      onDelete={message.isWorkerAnchor ? undefined : handleDelete}
      onRegenerate={!message.isWorkerAnchor && message.role === 'assistant' ? handleRegenerate : undefined}
      onEditAndResend={!message.isWorkerAnchor && message.role === 'user' ? handleEdit : undefined}
    />{showAttribution && onReply && message.speaker && message.roomSequence && !message.isStreaming && !message.isWorkerAnchor &&
      <button className="hb-room-reply" onClick={() => onReply(message)}>{t.reply(roomName(message.speaker))}</button>}
    </div>
  )
})

export default function MessageList({ onDelete, onRegenerate, onEditAndResend, onReply }: Props) {
  const { state } = useChatContext()
  // A room ID alone does not make a private chat a shared conversation.
  // Keep past speakers identifiable even after a summoned participant leaves.
  const speakers = new Set(state.messages
    .filter(message => message.role === 'assistant' && !message.isWorkerAnchor)
    .map(message => message.speaker).filter(Boolean))
  const showAttribution = (state.room?.members.length ?? 0) > 1 || speakers.size > 1
  const scrollRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRaf = useRef<number>(0)

  // Scroll to bottom — throttled to one rAF per frame, instant (not smooth)
  // during streaming to avoid stacking smooth-scroll animations at 60fps.
  const scrollToBottom = useCallback((smooth: boolean) => {
    cancelAnimationFrame(scrollRaf.current)
    scrollRaf.current = requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView(smooth ? { behavior: 'smooth' } : undefined)
    })
  }, [])

  // Smooth scroll when a new message appears.
  useEffect(() => {
    scrollToBottom(!state.isStreaming)
  }, [state.messages.length, scrollToBottom, state.isStreaming])

  // Instant scroll while streaming (content growing).
  useEffect(() => {
    if (state.isStreaming) scrollToBottom(false)
  })

  return (
    <div ref={scrollRef} style={{ flex: 1, overflowY: 'auto', padding: '1.5rem 1rem 0.5rem' }}>
      <div style={{ maxWidth: 760, margin: '0 auto' }}>
        {state.messages.map(msg => (
          <MemoMessage
            key={msg.id}
            message={msg}
            showAttribution={showAttribution}
            onDelete={onDelete}
            onRegenerate={onRegenerate}
            onEditAndResend={onEditAndResend}
            onReply={onReply}
          />
        ))}
        <div ref={bottomRef} style={{ height: '1rem' }} />
      </div>
    </div>
  )
}
