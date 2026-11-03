import { Fragment, type ReactNode } from 'react'
import { findAgentMentions } from '../lib/agentMentions'
import AgentMark from './AgentMark'

/** A mention stays ordinary @text on the wire and is an atomic chip while editing. */
export default function AgentMentionText({ text }: { text: string }) {
  const parts: ReactNode[] = []
  let cursor = 0
  for (const mention of findAgentMentions(text)) {
    parts.push(<Fragment key={`text-${cursor}`}>{text.slice(cursor, mention.start)}</Fragment>)
    parts.push(<span key={mention.start} className="hb-agent-mention" contentEditable={false}
      data-agent-mention={mention.agentId} data-mention-text={mention.text}>
      <AgentMark agentId={mention.agentId} size={18} finish="flat" />
      <strong>{mention.text.slice(1)}</strong>
    </span>)
    cursor = mention.end
  }
  parts.push(<Fragment key={`text-${cursor}`}>{text.slice(cursor)}</Fragment>)
  return <>{parts}</>
}
