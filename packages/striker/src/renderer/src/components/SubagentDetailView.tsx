import { useEffect, useRef, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import type { SubagentRun, SubagentStep } from '../lib/types'
import CodeBlock from './CodeBlock'
import ErrorBoundary from './ErrorBoundary'

/* ── Markdown pipeline (mirrors Message.tsx) ─────────────────────────────── */

const REMARK_PLUGINS = [remarkGfm, remarkMath]
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const REHYPE_PLUGINS: any = [[rehypeKatex, { throwOnError: false, errorColor: '#f87171' }]]

/* ── Sanitize / prepare (mirrors Message.tsx) ────────────────────────────── */

function normalizeCodeFences(text: string): string {
  return text.replace(/([^\n])```/g, '$1\n```')
}

function sanitizePartialMarkdown(text: string): string {
  const fences = (text.match(/```/g) ?? []).length
  if (fences % 2 !== 0) text += '\n```'
  const stripped = text.replace(/```[\s\S]*?```/g, '')
  const ticks = (stripped.match(/(?<!`)`(?!`)/g) ?? []).length
  if (ticks % 2 !== 0) text += '`'
  return text
}

/* ── Markdown segment renderer ──────────────────────────────────────────── */

function MarkdownSegment({ text }: { text: string }) {
  const visible = useMemo(
    () => normalizeCodeFences(sanitizePartialMarkdown(text)),
    [text],
  )
  return (
    <ReactMarkdown
      remarkPlugins={REMARK_PLUGINS}
      rehypePlugins={REHYPE_PLUGINS}
      components={{
        code({ className, children, ...props }: any) {
          const match = /language-(\w+)/.exec(className || '')
          const lang = match ? match[1] : ''
          const code = String(children).replace(/\n$/, '')
          const inline = !match && !code.includes('\n')
          if (!inline && (lang || code.includes('\n'))) {
            return <CodeBlock language={lang}>{code}</CodeBlock>
          }
          return (
            <code style={{
              background: 'rgba(var(--hb-accent-rgb),0.1)',
              border: '1px solid rgba(var(--hb-accent-rgb),0.18)',
              padding: '0.05em 0.4em', fontSize: '0.83em',
              color: 'var(--hb-cyan-bright)',
            }} {...props}>
              {children}
            </code>
          )
        },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        p({ children }: any) {
          return <p style={{ margin: '0 0 0.6rem', lineHeight: 1.7, color: 'var(--hb-text)' }}>{children}</p>
        },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ul({ children }: any) {
          return <ul style={{ margin: '0 0 0.6rem', paddingLeft: '1.4rem', lineHeight: 1.7, color: 'var(--hb-text)' }}>{children}</ul>
        },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ol({ children }: any) {
          return <ol style={{ margin: '0 0 0.6rem', paddingLeft: '1.4rem', lineHeight: 1.7, color: 'var(--hb-text)' }}>{children}</ol>
        },
      }}
    >
      {visible}
    </ReactMarkdown>
  )
}

/* ── Tool step renderer ──────────────────────────────────────────────────── */

function ToolStep({ step }: { step: SubagentStep }) {
  const name = (step.tool ?? '').replace(/_/g, ' ').replace(/-/g, ' ')

  // Format input as key: value pairs
  const inputEntries: [string, string][] = []
  if (step.input && typeof step.input === 'object') {
    for (const [k, v] of Object.entries(step.input as Record<string, unknown>)) {
      if (v == null || v === '') continue
      const val = typeof v === 'string' ? v : JSON.stringify(v, null, 2)
      inputEntries.push([k, val])
    }
  }

  return (
    <div style={{
      margin: '0.5rem 0', border: '1px solid rgba(var(--hb-accent-rgb), 0.12)',
      borderRadius: '0.5rem', background: 'rgba(var(--hb-accent-rgb), 0.02)',
      overflow: 'hidden',
    }}>
      {/* Tool header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: '0.4rem',
        padding: '0.4rem 0.7rem',
        fontFamily: 'var(--font-mono)', fontSize: '0.72rem',
        color: 'var(--hb-cyan-dim)', letterSpacing: '0.04em',
        borderBottom: inputEntries.length > 0 || step.result
          ? '1px solid rgba(var(--hb-accent-rgb), 0.08)' : 'none',
      }}>
        <span style={{ color: 'var(--hb-cyan)' }}>▸</span>
        <span style={{ color: 'var(--hb-text-secondary)' }}>{name}</span>
      </div>

      {/* Tool input */}
      {inputEntries.length > 0 && (
        <div style={{ padding: '0.4rem 0.7rem 0.3rem' }}>
          {inputEntries.map(([k, v]) => (
            <div key={k} style={{
              fontFamily: 'var(--font-mono)', fontSize: '0.68rem',
              lineHeight: 1.5, marginBottom: '0.2rem',
            }}>
              <span style={{ color: 'var(--hb-icon-dim)', userSelect: 'none' }}>{k}: </span>
              <span style={{
                color: 'var(--hb-text-secondary)', whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}>
                {v.length > 500 ? v.slice(0, 500) + '\n…[truncated]' : v}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Tool result */}
      {step.result && (
        <div style={{
          padding: '0.4rem 0.7rem 0.5rem',
          borderTop: inputEntries.length > 0 ? '1px solid rgba(var(--hb-accent-rgb), 0.08)' : 'none',
        }}>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: '0.62rem',
            color: 'var(--hb-icon-dim)', letterSpacing: '0.08em',
            textTransform: 'uppercase', marginBottom: '0.2rem',
          }}>
            result
          </div>
          <pre style={{
            fontFamily: 'var(--font-mono)', fontSize: '0.7rem',
            color: 'var(--hb-text)', lineHeight: 1.5,
            whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            maxHeight: 360, overflow: 'auto',
            margin: 0, padding: '0.4rem 0.6rem',
            background: 'rgba(0,0,0,0.2)', borderRadius: '0.3rem',
          }}>
            {step.result}
          </pre>
        </div>
      )}
    </div>
  )
}

/* ── Single step render ──────────────────────────────────────────────────── */

function StepRow({ step }: { step: SubagentStep }) {
  if (step.kind === 'tool') {
    return (
      <ErrorBoundary label="TOOL">
        <ToolStep step={step} />
      </ErrorBoundary>
    )
  }
  return (
    <div style={{ margin: '0.4rem 0' }}>
      <MarkdownSegment text={step.text ?? ''} />
    </div>
  )
}

/* ── Header icons ────────────────────────────────────────────────────────── */

function IconClose() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  )
}

function IconChevronLeft() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <polyline points="15 18 9 12 15 6" />
    </svg>
  )
}

/* ── Main component ──────────────────────────────────────────────────────── */

interface Props {
  run: SubagentRun
  onClose: () => void
}

/**
 * Full-screen detail view for a single subagent delegation.
 *
 * Opens when the owner clicks a subagent row in the transcript. Shows every step
 * the delegated agent took — text, tool calls, results — rendered with the same
 * markdown and code-block pipeline as the main chat, scoped to one delegation.
 * The close button and Escape key dismiss it.
 */
export default function SubagentDetailView({ run, onClose }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom when steps change (streaming)
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'auto' })
  }, [run.steps.length])

  // Close on Escape
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const failed = run.ok === false
  const running = run.running

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 200,
      background: 'rgba(0, 0, 0, 0.82)',
      display: 'flex', flexDirection: 'column',
      animation: 'fadeIn 0.15s ease',
    }}>
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: '0.6rem',
        padding: '0.7rem 1rem',
        borderBottom: '1px solid rgba(255,255,255,0.06)',
        background: 'rgba(0,0,0,0.4)',
        flexShrink: 0,
      }}>
        {/* Back button */}
        <button
          onClick={onClose}
          aria-label="Close subagent detail"
          style={{
            display: 'flex', alignItems: 'center', gap: '0.3rem',
            background: 'transparent', border: 0,
            color: 'var(--hb-text-dim)', cursor: 'pointer',
            font: 'inherit', fontSize: '0.82rem', padding: '0.25rem 0.4rem',
            borderRadius: '0.3rem',
          }}
        >
          <IconChevronLeft />
          <span style={{ letterSpacing: '0.04em' }}>Back</span>
        </button>

        {/* Status dot */}
        <span style={{
          width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
          background: running ? 'var(--hb-cyan)'
            : failed ? 'var(--danger, #e5484d)'
            : 'var(--hb-green)',
          boxShadow: running
            ? '0 0 8px var(--hb-cyan)'
            : 'none',
          animation: running ? 'pulse 1.2s ease infinite' : 'none',
        }} />

        {/* Label + agent */}
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem', minWidth: 0 }}>
          <span style={{
            fontFamily: "'Rajdhani', sans-serif",
            fontSize: '1rem', fontWeight: 600, letterSpacing: '0.06em',
            color: 'var(--hb-text)', textTransform: 'uppercase',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {run.label || run.agent}
          </span>
          <span style={{
            fontSize: '0.72rem', color: 'var(--hb-text-dim)',
            letterSpacing: '0.04em',
          }}>
            {run.agent}
          </span>
        </div>

        {/* Step count */}
        <span style={{
          marginLeft: 'auto', fontSize: '0.72rem', color: 'var(--hb-text-dim)',
          fontVariantNumeric: 'tabular-nums', letterSpacing: '0.04em',
        }}>
          {run.steps.filter(s => s.kind === 'tool').length} tool{run.steps.filter(s => s.kind === 'tool').length === 1 ? '' : 's'}
          {running ? '…' : ''}
        </span>

        {/* Close button */}
        <button
          onClick={onClose}
          aria-label="Close"
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 32, height: 32, borderRadius: '0.3rem',
            background: 'transparent', border: 0,
            color: 'var(--hb-text-dim)', cursor: 'pointer',
          }}
        >
          <IconClose />
        </button>
      </div>

      {/* ── Body — the chat-like thread ─────────────────────────────────── */}
      <div
        ref={scrollRef}
        style={{
          flex: 1, overflowY: 'auto',
          padding: '1.25rem 1.5rem',
        }}
      >
        <div style={{ maxWidth: 760, margin: '0 auto' }}>
          {/* Prompt — what the subagent was asked */}
          {run.prompt && (
            <div style={{
              marginBottom: '1rem', padding: '0.6rem 0.8rem',
              borderLeft: '2px solid var(--hb-cyan-dim)',
              background: 'rgba(var(--hb-accent-rgb), 0.04)',
              borderRadius: '0 0.4rem 0.4rem 0',
            }}>
              <div style={{
                fontSize: '0.62rem', letterSpacing: '0.1em',
                color: 'var(--hb-icon-dim)', textTransform: 'uppercase',
                marginBottom: '0.3rem',
              }}>
                Task
              </div>
              <div style={{
                fontSize: '0.82rem', color: 'var(--hb-text-secondary)',
                whiteSpace: 'pre-wrap', lineHeight: 1.6,
              }}>
                {run.prompt}
              </div>
            </div>
          )}

          {/* Steps */}
          {run.steps.length === 0 && running && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: '0.5rem',
              padding: '2rem 0', color: 'var(--hb-text-dim)',
              fontFamily: 'var(--font-mono)', fontSize: '0.78rem',
            }}>
              <span style={{
                width: 10, height: 10, borderRadius: '50%',
                background: 'var(--hb-cyan)',
                animation: 'pulse 1.2s ease infinite',
                display: 'inline-block',
              }} />
              Waiting for the subagent to start…
            </div>
          )}

          {run.steps.map((step, i) => (
            <StepRow key={i} step={step} />
          ))}

          {/* Running indicator */}
          {running && run.steps.length > 0 && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: '0.3rem',
              padding: '0.3rem 0', color: 'var(--hb-cyan-dim)',
              fontSize: '0.7rem',
            }}>
              <span style={{
                width: 6, height: 6, borderRadius: '50%',
                background: 'var(--hb-cyan)',
                animation: 'pulse 1.2s ease infinite',
                display: 'inline-block',
              }} />
              Working…
            </div>
          )}

          {/* Terminal report — shown when finished */}
          {!running && run.report && (
            <div style={{
              marginTop: '1rem', padding: '0.7rem 0.9rem',
              borderTop: '1px solid rgba(255,255,255,0.06)',
              borderLeft: `2px solid ${failed ? 'var(--danger, #e5484d)' : 'var(--hb-green)'}`,
              background: 'rgba(var(--hb-accent-rgb), 0.03)',
              borderRadius: '0 0.4rem 0.4rem 0',
            }}>
              <div style={{
                fontSize: '0.62rem', letterSpacing: '0.1em',
                color: failed ? 'var(--danger, #e5484d)' : 'var(--hb-green)',
                textTransform: 'uppercase', marginBottom: '0.3rem',
                fontFamily: 'var(--font-mono)',
              }}>
                {failed ? '✗ Failed' : '✓ Complete'}
              </div>
              <MarkdownSegment text={run.report} />
            </div>
          )}

          <div ref={bottomRef} style={{ height: '1rem' }} />
        </div>
      </div>
    </div>
  )
}
