import { useEffect, useRef } from 'react'
import { useChatContext } from '../store/chat'
import Message from './Message'

interface Props {
  onDelete: (id: string) => void
  onRegenerate: (assistantId: string) => void
  onEditAndResend: (userId: string, newContent: string) => void
}

export default function MessageList({ onDelete, onRegenerate, onEditAndResend }: Props) {
  const { state } = useChatContext()
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [state.messages.length, state.isStreaming])

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '1.5rem 1rem 0.5rem' }}>
      <div style={{ maxWidth: 760, margin: '0 auto' }}>
        {state.messages.map(msg => (
          <Message
            key={msg.id}
            message={msg}
            onDelete={() => onDelete(msg.id)}
            onRegenerate={msg.role === 'assistant' ? () => onRegenerate(msg.id) : undefined}
            onEditAndResend={msg.role === 'user' ? (newContent) => onEditAndResend(msg.id, newContent) : undefined}
          />
        ))}
        <div ref={bottomRef} style={{ height: '1rem' }} />
      </div>
    </div>
  )
}
