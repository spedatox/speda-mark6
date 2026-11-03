import { useState } from 'react'
import AgentMentionInput from '../src/renderer/src/components/AgentMentionInput'
import AgentMentionText from '../src/renderer/src/components/AgentMentionText'

/** Isolated composer fixture: sends stay local and never invoke a provider. */
export default function MentionPreview() {
  const [draft, setDraft] = useState('')
  const [sent, setSent] = useState('')
  const [width, setWidth] = useState(780)
  const [restricted, setRestricted] = useState(false)
  const send = () => { setSent(draft); setDraft('') }
  return <main style={{ color: 'var(--hb-text)', padding: 16 }}>
    <h1>Agent mentions</h1>
    <nav style={{ display: 'flex', gap: 8 }}>
      <button onClick={() => setWidth(320)}>320px</button><button onClick={() => setWidth(780)}>780px</button>
      <button onClick={() => setRestricted(value => !value)}>{restricted ? 'All agents' : 'Current room only'}</button>
      <button onClick={() => setDraft('')}>Clear draft</button>
      <button onClick={() => setDraft('@Friday dictated text')}>Dictation fixture</button>
    </nav>
    <p>Type @, filter, select, and keep typing. Enter sends to this preview only.</p>
    <div style={{ minHeight: 330 }}>
      <p>Sent message</p><output aria-label="Sent message"><AgentMentionText text={sent} /></output>
      <p>Transport text</p><pre aria-label="Transport text">{JSON.stringify(sent)}</pre>
    </div>
    <div className="hb-holo" style={{ width: `min(${width}px, 100%)`, padding: 16 }}>
      <AgentMentionInput value={draft} onChange={setDraft} placeholder="Message"
        members={restricted ? ['speda', 'ultron', 'friday'] : undefined}
        onFocus={() => {}} onBlur={() => {}} onPaste={() => {}}
        onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send() } }} />
      <button onClick={send}>Send preview</button>
    </div>
    <pre aria-label="Draft text">{JSON.stringify(draft)}</pre>
  </main>
}
