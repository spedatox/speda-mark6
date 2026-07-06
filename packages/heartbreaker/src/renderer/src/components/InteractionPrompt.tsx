import { useState } from 'react'
import type { QuestionRequest, PermissionRequestData, InteractionQuestion } from '../lib/types'

/**
 * Mid-turn owner interactions from the Optimus peer — the TUI's
 * PermissionModal and AskUserQuestionModal, in HUD form. Rendered as an
 * overlay above the input bar while the stream waits.
 *
 * question   → walk through the questions, options + free-text "Other",
 *              resolve {answers:{question: answer}} or {declined:true}.
 * permission → Yes / Yes-remember / No, resolve {behavior, remember}.
 */

const mono = "'JetBrains Mono', monospace"

// The app's liquid-glass slab (heartbreaker.css --hb-holo-*): frosted fill,
// deep blur, specular rim — over a dark scrim so text stays readable while
// the void shows through.
const frame: React.CSSProperties = {
  position: 'absolute', left: '50%', bottom: '5.5rem', transform: 'translateX(-50%)',
  width: 'min(620px, calc(100% - 2rem))',
  background: 'linear-gradient(rgba(4,10,16,0.55), rgba(4,10,16,0.55)), var(--hb-holo-fill)',
  backdropFilter: 'var(--hb-holo-blur)',
  WebkitBackdropFilter: 'var(--hb-holo-blur)',
  border: '1px solid var(--hb-edge-bright)',
  borderLeft: '3px solid rgba(var(--hb-accent-rgb),0.8)',
  boxShadow: 'var(--hb-holo-shadow), 0 0 32px rgba(var(--hb-accent-rgb),0.18)',
  padding: '0.9rem 1.1rem',
  zIndex: 40,
  animation: 'fadeSlideIn 0.15s ease',
  display: 'flex', flexDirection: 'column', gap: '0.6rem',
}

const headerStyle: React.CSSProperties = {
  fontFamily: mono, fontSize: '0.62rem', letterSpacing: '0.22em',
  textTransform: 'uppercase', color: 'var(--hb-cyan-bright)',
}

const btn = (variant: 'primary' | 'ghost' | 'danger'): React.CSSProperties => ({
  fontFamily: mono, fontSize: '0.66rem', letterSpacing: '0.08em',
  padding: '0.4rem 0.85rem', cursor: 'pointer',
  background: variant === 'primary' ? 'rgba(var(--hb-accent-rgb),0.18)' : 'transparent',
  color: variant === 'danger' ? '#ff6b80' : variant === 'primary' ? 'var(--hb-cyan-bright)' : 'var(--hb-text-dim)',
  border: `1px solid ${variant === 'danger' ? 'rgba(255,107,128,0.4)' : 'rgba(var(--hb-accent-rgb),0.35)'}`,
})

/* ── Permission ──────────────────────────────────────────────────────────── */

export function PermissionPrompt({ data, onResolve }: {
  data: PermissionRequestData
  onResolve: (response: Record<string, unknown>) => void
}) {
  const command = typeof data.input?.command === 'string' ? data.input.command as string : null
  return (
    <div className="hb-glass-sm" style={frame}>
      <div style={headerStyle}>◈ Permission — Optimus wants to run</div>
      <div style={{ fontFamily: mono, fontSize: '0.74rem', color: '#ecf6f9', wordBreak: 'break-word' }}>
        ● {data.summary}
      </div>
      {command && command.split('\n').length > 1 && (
        <pre style={{
          margin: 0, fontFamily: mono, fontSize: '0.62rem', color: 'var(--hb-icon-bright)',
          whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 120, overflow: 'auto',
          background: 'rgba(255,255,255,0.03)', padding: '0.4rem 0.55rem',
        }}>{command}</pre>
      )}
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        <button style={btn('primary')} onClick={() => onResolve({ behavior: 'allow', remember: false })}>
          Yes
        </button>
        {data.rule_label && (
          <button style={btn('ghost')} onClick={() => onResolve({ behavior: 'allow', remember: true })}>
            Yes, {data.rule_label}
          </button>
        )}
        <button style={btn('danger')} onClick={() => onResolve({ behavior: 'deny', remember: false })}>
          No
        </button>
      </div>
    </div>
  )
}

/* ── Questions ───────────────────────────────────────────────────────────── */

export function QuestionPrompt({ data, onResolve }: {
  data: QuestionRequest
  onResolve: (response: Record<string, unknown>) => void
}) {
  const questions = data.questions ?? []
  const [index, setIndex] = useState(0)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [other, setOther] = useState('')

  const q: InteractionQuestion | undefined = questions[index]
  if (!q) return null
  const options = q.options ?? []

  const commit = (answer: string) => {
    const next = { ...answers, [q.question]: answer }
    if (index + 1 < questions.length) {
      setAnswers(next)
      setSelected(new Set())
      setOther('')
      setIndex(index + 1)
    } else {
      onResolve({ answers: next })
    }
  }

  const submitMulti = () => {
    const labels = [...selected].sort().map(i => options[i]?.label ?? '')
    const parts = [...labels, ...(other.trim() ? [other.trim()] : [])].filter(Boolean)
    if (parts.length) commit(parts.join(','))
  }

  return (
    <div className="hb-glass-sm" style={frame}>
      <div style={headerStyle}>
        ◆ {q.header || 'Optimus asks'}{questions.length > 1 ? `  ·  ${index + 1}/${questions.length}` : ''}
      </div>
      <div style={{ fontFamily: "'Inter', sans-serif", fontSize: '0.92rem', color: '#ecf6f9' }}>
        {q.question}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
        {options.map((opt, i) => {
          const active = selected.has(i)
          return (
            <button
              key={i}
              style={{
                ...btn(active ? 'primary' : 'ghost'),
                textAlign: 'left', width: '100%',
                whiteSpace: 'normal', wordBreak: 'break-word', lineHeight: 1.5,
              }}
              onClick={() => {
                if (q.multiSelect) {
                  const next = new Set(selected)
                  if (next.has(i)) next.delete(i)
                  else next.add(i)
                  setSelected(next)
                } else {
                  commit(opt.label)
                }
              }}
            >
              {i + 1}. {opt.label}
              {opt.description ? <span style={{ color: 'var(--hb-text-dim)' }}> — {opt.description}</span> : null}
            </button>
          )
        })}
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <input
          value={other}
          onChange={e => setOther(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && other.trim()) {
              if (q.multiSelect) submitMulti()
              else commit(other.trim())
            }
          }}
          placeholder="Other — type your own answer"
          style={{
            flex: 1, fontFamily: mono, fontSize: '0.68rem',
            background: 'rgba(255,255,255,0.04)', color: '#ecf6f9',
            border: '1px solid rgba(var(--hb-accent-rgb),0.25)', padding: '0.4rem 0.6rem',
            outline: 'none',
          }}
        />
        {q.multiSelect && (
          <button style={btn('primary')} onClick={submitMulti}>Submit</button>
        )}
        <button style={btn('danger')} onClick={() => onResolve({ declined: true })}>Decline</button>
      </div>
    </div>
  )
}
