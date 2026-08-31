// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState } from 'react'
import type { SubagentRun } from '../lib/types'

/**
 * What a coding peer delegated during a turn.
 *
 * This exists because the alternative failed badly in both directions. Streamed
 * into the reply, a delegate's report read as Optimus's own answer and its
 * completion closed the turn while the peer was still working. Dropped
 * entirely, a delegation that runs for minutes looked exactly like a hang.
 *
 * So: its own region, collapsed by default, and never part of `content`. The
 * default is closed because the report is already summarised by the parent in
 * its reply — this is the receipt, opened when the summary is not enough.
 *
 * Click a row to open the full subagent detail view — a chat-like thread of
 * everything that agent did, with markdown, code blocks, and full tool output.
 */
export function SubagentPanel({
  runs,
  onSelect,
}: {
  runs: SubagentRun[]
  onSelect?: (run: SubagentRun) => void
}) {
  if (!runs.length) return null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem', margin: '0.5rem 0' }}>
      {runs.map(run => (
        <SubagentRow key={run.id} run={run} onSelect={onSelect} />
      ))}
    </div>
  )
}

function SubagentRow({
  run,
  onSelect,
}: {
  run: SubagentRun
  onSelect?: (run: SubagentRun) => void
}) {
  const [open, setOpen] = useState(false)
  const tools = run.steps.filter(s => s.kind === 'tool')
  const failed = run.ok === false

  const handleClick = () => {
    if (onSelect) {
      onSelect(run)
    } else {
      // Fallback: toggle inline expand if no detail view handler is wired
      setOpen(o => !o)
    }
  }

  return (
    <div style={{
      border: '1px solid var(--border)', borderRadius: '0.5rem',
      background: 'rgba(255,255,255,0.02)', overflow: 'hidden',
      transition: 'border-color 120ms, background 120ms',
    }}>
      <button
        onClick={handleClick}
        aria-expanded={onSelect ? undefined : open}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', gap: '0.5rem',
          padding: '0.4rem 0.625rem', background: 'transparent', border: 0,
          color: 'var(--text-secondary)', font: 'inherit', fontSize: '0.8rem',
          cursor: 'pointer', textAlign: 'left',
          transition: 'background 80ms',
        }}
        onMouseEnter={e => {
          (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.03)'
        }}
        onMouseLeave={e => {
          (e.currentTarget as HTMLElement).style.background = 'transparent'
        }}
      >
        {/* Expand arrow — only when no detail-view handler (inline expand fallback) */}
        {!onSelect && (
          <span aria-hidden style={{
            opacity: 0.6, transition: 'transform 120ms',
            transform: open ? 'rotate(90deg)' : 'none', display: 'inline-block',
          }}>›</span>
        )}

        {/* Click target indicator — shown when detail view is available */}
        {onSelect && (
          <span style={{
            opacity: 0.5, fontSize: '0.7rem', color: 'var(--hb-cyan-dim)',
          }}>◎</span>
        )}

        {/* Status first: whether it is still going is the one thing worth
            reading at a glance in a transcript that is still moving. */}
        <span style={{
          color: run.running ? 'var(--accent)' : failed ? 'var(--danger, #e5484d)' : 'var(--text-muted)',
        }}>
          {run.running ? '◐' : failed ? '✗' : '●'}
        </span>

        <span style={{ color: 'var(--text-primary)' }}>
          {run.label || run.agent}
        </span>
        <span style={{ opacity: 0.6 }}>{run.agent}</span>

        <span style={{ marginLeft: 'auto', opacity: 0.6, fontVariantNumeric: 'tabular-nums' }}>
          {run.running
            ? `${tools.length} step${tools.length === 1 ? '' : 's'}…`
            : `${tools.length} step${tools.length === 1 ? '' : 's'}`}
        </span>

        {/* Open-detail affordance */}
        {onSelect && (
          <span style={{ opacity: 0.4, fontSize: '0.7rem' }}>↗</span>
        )}
      </button>

      {/* Inline expand — only used as fallback when no detail view */}
      {!onSelect && open && (
        <div style={{ padding: '0 0.625rem 0.55rem 1.6rem', display: 'flex',
                      flexDirection: 'column', gap: '0.3rem' }}>
          {run.prompt && (
            <p style={{ fontSize: '0.76rem', color: 'var(--text-muted)', margin: 0,
                        paddingBottom: '0.2rem', whiteSpace: 'pre-wrap' }}>
              {run.prompt}
            </p>
          )}

          {run.steps.map((s, i) => s.kind === 'tool' ? (
            <div key={i} style={{ fontSize: '0.76rem', color: 'var(--text-secondary)',
                                  fontFamily: 'var(--font-mono)' }}>
              <span style={{ opacity: 0.5 }}>└ </span>{s.tool}
              {s.result !== undefined && (
                <span style={{ opacity: 0.55 }}> — {firstLine(s.result)}</span>
              )}
            </div>
          ) : (
            <p key={i} style={{ fontSize: '0.78rem', color: 'var(--text-muted)',
                                margin: 0, whiteSpace: 'pre-wrap' }}>{s.text}</p>
          ))}

          {run.report && (
            <div style={{
              marginTop: '0.3rem', paddingTop: '0.4rem',
              borderTop: '1px solid var(--border)', fontSize: '0.8rem',
              color: failed ? 'var(--danger, #e5484d)' : 'var(--text-primary)',
              whiteSpace: 'pre-wrap',
            }}>{run.report}</div>
          )}
        </div>
      )}
    </div>
  )
}

/** Tool output is often long; the row is a receipt, not a transcript. */
function firstLine(text: string): string {
  const line = text.split('\n', 1)[0].trim()
  return line.length > 80 ? `${line.slice(0, 79)}…` : line
}
