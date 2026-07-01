import { useEffect, useRef, useCallback, memo } from 'react'
import { useChatContext } from '../store/chat'
import Message from './Message'
import type { ChatMessage } from '../lib/types'

interface Props {
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
  message, onDelete, onRegenerate, onEditAndResend,
}: {
  message: ChatMessage
  onDelete: (id: string) => void
  onRegenerate: (id: string) => void
  onEditAndResend: (id: string, newContent: string) => void
}) {
  const handleDelete = useCallback(() => onDelete(message.id), [onDelete, message.id])
  const handleRegenerate = useCallback(
    () => onRegenerate(message.id),
    [onRegenerate, message.id],
  )
  const handleEdit = useCallback(
    (newContent: string) => onEditAndResend(message.id, newContent),
    [onEditAndResend, message.id],
  )
  return (
    <Message
      message={message}
      onDelete={handleDelete}
      onRegenerate={message.role === 'assistant' ? handleRegenerate : undefined}
      onEditAndResend={message.role === 'user' ? handleEdit : undefined}
    />
  )
})

export default function MessageList({ onDelete, onRegenerate, onEditAndResend }: Props) {
  const { state } = useChatContext()
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
            onDelete={onDelete}
            onRegenerate={onRegenerate}
            onEditAndResend={onEditAndResend}
          />
        ))}
        <div ref={bottomRef} style={{ height: '1rem' }} />
      </div>
    </div>
  )
}
