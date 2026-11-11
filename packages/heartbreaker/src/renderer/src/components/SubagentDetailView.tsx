// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useRef, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import type { SubagentRun, SubagentStep } from '../lib/types'
import { foldLegionEvent, workerLabel } from '../lib/subagentFold'
import { attachLegionStream, attachWorkerExecution, inspectWorkerExecution, interruptWorker, messageWorker, newRequestId } from '../lib/api'
import { useChatContext } from '../store/chat'
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

/* ── Main component ──────────────────────────────────────────────────────── */

interface Props {
  name: string
  run: SubagentRun
  legacyTicket?: number
  active: boolean
}

/** Activity and controls for one delegation, hosted in a side pane tab. */
export default function SubagentDetailView({ name, run: initialRun, legacyTicket, active }: Props) {
  const { state } = useChatContext()
  const [run, setRun] = useState(initialRun)
  const [notice, setNotice] = useState('')
  const [controlBusy, setControlBusy] = useState(false)
  const [draft, setDraftState] = useState('')
  const draftRevision = useRef(0)
  const setDraft = (value: string) => { draftRevision.current++; setDraftState(value) }
  const [reconnect, setReconnect] = useState(0)
  const lastSequence = useRef(initialRun.lastSequence ?? 0)
  const selectedExecution = useRef(initialRun.executionId ?? initialRun.id)
  const pendingMessage = useRef<{ text: string; id: string } | null>(null)
  const config = state.config
  const executionId = run.executionId
  const currentSelection = useRef(initialRun.executionId ?? initialRun.id)
  currentSelection.current = initialRun.executionId ?? initialRun.id
  const mounted = useRef(true)
  const replaying = useRef(false)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  useEffect(() => {
    if (selectedExecution.current !== (initialRun.executionId ?? initialRun.id)) {
      selectedExecution.current = initialRun.executionId ?? initialRun.id
      lastSequence.current = initialRun.lastSequence ?? 0
      pendingMessage.current = null
      replaying.current = false
      setControlBusy(false)
      setNotice('')
      setDraft('')
    } else if (replaying.current) return
    else lastSequence.current = Math.max(lastSequence.current, initialRun.lastSequence ?? 0)
    setRun(previous => previous.id === initialRun.id && (previous.lastSequence ?? 0) > (initialRun.lastSequence ?? 0)
      ? previous : initialRun)
  }, [initialRun])
  useEffect(() => {
    if (!config || !executionId) return
    const controller = new AbortController()
    const fullReplay = replaying.current
    void inspectWorkerExecution(config, executionId).then(info => {
      if (controller.signal.aborted || selectedExecution.current !== executionId) return
      setRun(previous => {
        if ((previous.lastSequence ?? 0) > (info.last_sequence ?? 0)) return { ...previous, model: previous.model ?? info.model }
        return { ...previous, ownerAgent: info.owner_agent || info.agent_id || previous.ownerAgent,
          workerRole: info.role || info.worker || previous.workerRole, executionType: info.execution_type || info.backend,
          status: info.status, model: info.model, canInterrupt: info.can_interrupt, canMessage: info.can_message,
          running: ['admitted', 'queued', 'running', 'staging', 'waiting_input', 'waiting_dependency', 'cancel_requested'].includes(info.status ?? 'unknown'),
          report: typeof info.result === 'string' ? info.result : info.result?.report ?? info.result?.reason ?? previous.report }
      })
    }).catch(error => { if (!controller.signal.aborted) setNotice(String(error.message || error)) })
    ;(async () => {
      for await (const event of attachWorkerExecution(config, executionId, controller.signal, lastSequence.current)) {
        if (controller.signal.aborted || selectedExecution.current !== executionId) break
        setRun(previous => {
          const next = foldLegionEvent(previous, event)
          lastSequence.current = Math.max(lastSequence.current, next.lastSequence ?? 0)
          return next
        })
      }
      if (fullReplay && !controller.signal.aborted && currentSelection.current === executionId) {
        replaying.current = false
        setNotice('Recorded activity loaded.')
      }
    })().catch(error => {
      if (!controller.signal.aborted) setNotice(`Connection lost. Reconnect to recover task progress. ${error.message || ''}`)
    })
    return () => controller.abort()
  }, [config, executionId, reconnect])

  useEffect(() => {
    if (!config || legacyTicket === undefined || executionId) return
    const controller = new AbortController()
    void (async () => {
      for await (const event of attachLegionStream(config, legacyTicket, controller.signal)) {
        if (controller.signal.aborted) break
        setRun(previous => foldLegionEvent(previous, event))
      }
    })().catch(() => {
      if (!controller.signal.aborted) setNotice('Connection lost while loading task activity.')
    })
    return () => controller.abort()
  }, [config, executionId, legacyTicket])

  const controlStyle = { padding: '6px 12px', borderRadius: 999, border: '1px solid rgba(255,255,255,0.15)',
    background: 'rgba(255,255,255,0.04)', color: 'var(--hb-text)', cursor: 'pointer', font: 'inherit', fontSize: '0.78rem' }
  const stopTask = async () => {
    if (!config || !executionId || controlBusy || currentSelection.current !== executionId) return
    setControlBusy(true)
    try {
      await interruptWorker(config, executionId)
      if (mounted.current && currentSelection.current === executionId) setNotice('Stop requested for this task. Waiting for its confirmed state.')
    } catch (error) { if (mounted.current && currentSelection.current === executionId) setNotice(error instanceof Error ? error.message : 'Task stop was not confirmed') }
    finally { if (mounted.current && currentSelection.current === executionId) setControlBusy(false) }
  }
  const sendInput = async () => {
    if (!config || !executionId || !draft.trim() || controlBusy || currentSelection.current !== executionId) return
    const text = draft.trim()
    if (pendingMessage.current?.text !== text) pendingMessage.current = { text, id: newRequestId().replace(/-/g, '') }
    const pending = pendingMessage.current
    const revision = draftRevision.current
    setControlBusy(true)
    try {
      const receipt = await messageWorker(config, executionId, text, pending.id)
      if (!mounted.current || currentSelection.current !== executionId) return
      setNotice(receipt.status === 'delivered' ? 'Input delivered at a worker boundary; its response remains pending.'
        : 'Input accepted for this task. Delivery is recorded at a worker boundary.')
      if (pendingMessage.current === pending) pendingMessage.current = null
      if (draftRevision.current === revision) setDraft('')
    } catch (error) { if (mounted.current && currentSelection.current === executionId) setNotice(error instanceof Error ? error.message : 'Task input was not confirmed') }
    finally { if (mounted.current && currentSelection.current === executionId) setControlBusy(false) }
  }
  const scrollRef = useRef<HTMLDivElement>(null)

  const followActivity = useRef(true)
  useEffect(() => {
    if (active && followActivity.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [active, run.steps.length, run.running])

  const failed = run.ok === false
  const running = run.running

  return (
    <div style={{
      flex: 1, minHeight: 0, minWidth: 0,
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: '0.6rem',
        padding: '0.7rem 1rem',
        borderBottom: '1px solid rgba(255,255,255,0.06)',
        background: 'rgba(0,0,0,0.4)',
        flexShrink: 0,
      }}>
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

        {/* Worker name and task title */}
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem', minWidth: 0 }}>
          <span title={workerLabel(run)} style={{
            fontFamily: "'Rajdhani', sans-serif",
            fontSize: '1rem', fontWeight: 600, letterSpacing: '0.06em',
            color: 'var(--hb-text)', flexShrink: 0,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {name}
          </span>
          {run.label && run.label !== name && <span title={run.label} style={{
            fontSize: '0.78rem', color: 'var(--hb-text-dim)',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {run.label}
          </span>}
        </div>

        {/* Step count */}
        <span style={{
          marginLeft: 'auto', fontSize: '0.72rem', color: 'var(--hb-text-dim)', whiteSpace: 'nowrap',
          fontVariantNumeric: 'tabular-nums', letterSpacing: '0.04em',
        }}>
          {run.steps.filter(s => s.kind === 'tool').length} tool{run.steps.filter(s => s.kind === 'tool').length === 1 ? '' : 's'}
          {running ? '…' : ''}
        </span>

      </div>

      {executionId && config && (
        <section className="hb-worker-controls" aria-label="Worker controls" style={{ padding: '0.6rem 1rem', borderBottom: '1px solid rgba(255,255,255,0.08)', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: '0.78rem', color: 'var(--hb-text-dim)' }}>
            <span>{run.status || (running ? 'running' : 'finished')}{run.model ? ` · ${run.model}` : ''}</span>
            <button style={controlStyle} type="button" disabled={controlBusy || !running || run.canInterrupt === false} onClick={stopTask}>Stop task</button>
            <button style={controlStyle} type="button" disabled={controlBusy} onClick={() => { setNotice(''); setReconnect(value => value + 1) }}>Reconnect</button>
            {run.activityPartial && <button style={controlStyle} type="button" disabled={controlBusy} onClick={() => {
              replaying.current = true; lastSequence.current = 0
              setRun(previous => ({ ...previous, steps: [], lastSequence: 0, seenEventIds: [], activityPartial: false }))
              setNotice('Loading recorded activity…'); setReconnect(value => value + 1)
            }}>Load recorded activity</button>}
          </div>
          {running && run.canMessage !== false && (
            <form onSubmit={event => { event.preventDefault(); void sendInput() }} style={{ display: 'flex', gap: 8 }}>
              <textarea aria-label="Message this task" value={draft} onChange={event => setDraft(event.target.value)} maxLength={6000} rows={2}
                placeholder="Steer this task at its next safe boundary"
                style={{ flex: 1, minWidth: 0, resize: 'vertical', color: 'var(--hb-text)', background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)', borderRadius: 6, padding: 8 }} />
              <button style={controlStyle} type="submit" disabled={controlBusy || !draft.trim()}>Send to task</button>
            </form>
          )}
          {notice && <span role="status" style={{ color: 'var(--hb-text-dim)', fontSize: '0.78rem' }}>{notice}</span>}
        </section>
      )}

      {/* ── Body — the chat-like thread ─────────────────────────────────── */}
      <div
        ref={scrollRef}
        onScroll={event => {
          const element = event.currentTarget
          if (active) followActivity.current = element.scrollHeight - element.scrollTop - element.clientHeight < 48
        }}
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

          {legacyTicket !== undefined && notice && <p role="status">{notice}</p>}
        </div>
      </div>
    </div>
  )
}
