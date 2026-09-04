// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useMemo, useRef, useState } from 'react'
import { resultSummary, stepState, toolSummary } from './Message'
import { useT } from '../lib/i18n'
import type { ToolBadge } from '../lib/types'

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  THE ACTIVITY WINDOW — what the machine is doing, while it is doing it.
 *
 *  Voice mode used to show a glowing "thinking" line and nothing else. A turn
 *  that browsed six pages and a turn that had hung looked identical for two
 *  minutes, so the mode read as slow even when it was working hard. The
 *  information existed the whole time — the backend streams every tool call,
 *  with its arguments, BEFORE it executes (orchestrator.py step 1) — voice mode
 *  simply threw it away.
 *
 *  ── Why this is louder than the transcript's version ────────────────────────
 *  In chat the step list is a collapsed receipt: the answer is on screen, and
 *  the steps are there for when something looks wrong. Here there IS no answer
 *  on screen yet, and the owner is listening rather than reading — so the same
 *  data has the opposite job. It is the only evidence that anything is
 *  happening, which is why this window:
 *
 *    - opens the INSTANT a turn starts, before a single token has arrived;
 *    - never collapses, and never hides a step behind a click;
 *    - shows each call's ARGUMENTS inline rather than one hover away; and
 *    - TIMES every step, live, which the transcript does not do at all.
 *
 *  The timer is the part that actually answers "is this thing broken?". A
 *  counter ticking past twelve seconds on `browse_page` is a slow website; the
 *  same screen with no counter is indistinguishable from a crash.
 * ════════════════════════════════════════════════════════════════════════════
 */

/** When each step started and finished, by tool id.
 *
 *  Measured HERE rather than read off the event, because the events carry no
 *  timestamps: a tool arrives when it starts and is mutated in place when its
 *  result lands. Client-side wall time is therefore the only clock available —
 *  and it is the right one anyway, since what the owner wants to know is how
 *  long THEY have been waiting, not how long the tool spent executing. */
interface Span { start: number; end?: number }

/** Elapsed, in the shortest form that is still precise enough to watch tick.
 *  Sub-minute is tenths — a counter that only moves once a second reads as
 *  frozen — and past a minute it becomes m:ss, where tenths are just noise. */
function elapsed(ms: number): string {
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const s = Math.floor(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** The call's arguments, as one line worth reading.
 *
 *  Deliberately NOT JSON.stringify of the whole input: half of these carry a
 *  document, a file's contents, or a base64 blob, and a window that renders
 *  those is a window nobody can read. Longest-first ordering puts the argument
 *  that identifies the call ahead of its flags. */
function inputLine(tool: ToolBadge): string | null {
  if (!tool.input || typeof tool.input !== 'object') return null
  const entries = Object.entries(tool.input as Record<string, unknown>)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    // Content-bearing keys are the ones that blow the line up, and never the
    // ones that say what the call WAS.
    .filter(([k]) => !/^(content|data|body|text|source|file|image)$/i.test(k))
    .map(([k, v]) => {
      const s = typeof v === 'string' ? v : JSON.stringify(v)
      if (typeof s !== 'string') return null
      const one = s.replace(/\s+/g, ' ').trim()
      return one ? `${k}: ${one.length > 90 ? one.slice(0, 90) + '…' : one}` : null
    })
    .filter((x): x is string => x !== null)
  if (!entries.length) return null
  const line = entries.join('  ·  ')
  return line.length > 180 ? line.slice(0, 180) + '…' : line
}

interface Props {
  tools: ToolBadge[]
  /** True while the turn is still being written — the last step is only "running"
   *  while that holds, or a finished turn shows a spinner for ever. */
  streaming: boolean
  /** Whether any prose has arrived yet. Before it has, this window is the entire
   *  answer to "is anything happening", and says so in as many words. */
  hasText: boolean
}

export default function VoiceActivity({ tools, streaming, hasText }: Props) {
  const t = useT()
  const spans = useRef<Map<string, Span>>(new Map())
  const turnStart = useRef<number>(Date.now())

  // Assigned during render, not in an effect: a step's clock has to start at the
  // moment it first appears, and an effect runs a frame later — which is exactly
  // the frame where the owner is looking for a sign of life.
  const now = Date.now()
  for (const tool of tools) {
    const span = spans.current.get(tool.id)
    if (!span) {
      spans.current.set(tool.id, { start: now })
    } else if (tool.result !== undefined && span.end === undefined) {
      span.end = now
    }
  }
  // A new turn is a new clock. Tools arriving after an empty list means the
  // board has been handed a different message.
  if (tools.length === 0 && spans.current.size > 0) {
    spans.current.clear()
    turnStart.current = now
  }

  const liveIdx = streaming ? tools.length - 1 : -1
  const running = streaming && (tools.length === 0 || tools[tools.length - 1].result === undefined)

  /* ── The tick ─────────────────────────────────────────────────────────────
   * Re-render while something is in flight so the counters move. Ten times a
   * second, and ONLY while running: a permanent interval behind a finished
   * board would repaint the canvas for ever to animate nothing. */
  const [, bump] = useState(0)
  useEffect(() => {
    if (!running) return
    const id = window.setInterval(() => bump(n => n + 1), 100)
    return () => window.clearInterval(id)
  }, [running])

  // Ride the tail: the newest step is the one being waited on.
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [tools.length])

  const failed = useMemo(
    () => tools.filter(x => stepState(x, false) === 'failed').length,
    [tools],
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      {/* The turn's own clock. Separate from the per-step ones because the
          question it answers is different: not "is this step slow" but "how
          long have I been waiting". */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: '0.5rem', flexShrink: 0,
        fontFamily: "'Rajdhani', sans-serif", fontSize: '0.62rem',
        fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase',
        color: 'var(--hb-text-faint)', paddingBottom: '0.4rem',
      }}>
        <span>{tools.length} {tools.length === 1 ? t.voiceActivity.step : t.voiceActivity.steps}</span>
        {failed > 0 && <span style={{ color: 'var(--hb-red)' }}>{failed} {t.voiceActivity.failed}</span>}
        <span style={{ flex: 1 }} />
        {running && (
          <span style={{ color: 'var(--hb-cyan)', fontVariantNumeric: 'tabular-nums' }}>
            {elapsed(now - turnStart.current)}
          </span>
        )}
      </div>

      <div ref={listRef} style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
        {/* Before the first tool and before the first word, this line is the
            whole signal that the machine is alive. It is the thing whose
            absence made the mode feel broken. */}
        {tools.length === 0 && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: '0.6rem',
            fontSize: '0.82rem', color: 'var(--hb-text-dim)', padding: '0.3rem 0',
          }}>
            <Spinner />
            <span>{hasText ? t.voiceActivity.composing : t.voiceActivity.working}</span>
          </div>
        )}

        {tools.map((tool, i) => {
          const state = stepState(tool, i === liveIdx)
          const { verb, target } = toolSummary(tool, t, state === 'running')
          const span = spans.current.get(tool.id)
          const took = span ? (span.end ?? now) - span.start : 0
          const args = inputLine(tool)
          const result = resultSummary(tool)

          return (
            <div
              key={tool.id}
              style={{
                padding: '0.42rem 0',
                borderTop: i === 0 ? 'none' : '1px solid var(--hb-line)',
                // The step being waited on is the one worth looking at.
                opacity: state === 'running' || i === tools.length - 1 ? 1 : 0.72,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.55rem' }}>
                <span style={{ flexShrink: 0, width: 14, alignSelf: 'center' }}>
                  {state === 'running' ? <Spinner />
                    : state === 'failed' ? <Glyph color="var(--hb-red)" d="M18 6L6 18M6 6l12 12" />
                    : <Glyph color="var(--hb-green)" d="M20 6L9 17l-5-5" />}
                </span>
                <span
                  title={tool.name}
                  style={{ color: 'var(--hb-text)', fontSize: '0.84rem', flexShrink: 0 }}
                >
                  {verb}
                </span>
                {target && (
                  <span style={{
                    color: 'var(--hb-text-dim)', fontSize: '0.8rem', minWidth: 0,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {target}
                  </span>
                )}
                <span style={{ flex: 1, minWidth: '0.4rem' }} />
                <span style={{
                  flexShrink: 0, fontSize: '0.72rem', fontVariantNumeric: 'tabular-nums',
                  color: state === 'running' ? 'var(--hb-cyan)' : 'var(--hb-text-faint)',
                }}>
                  {elapsed(took)}
                </span>
              </div>

              {/* The arguments, inline. In chat these sit behind a click because
                  the answer is already on screen; here there is nothing else to
                  look at, and "which page is it reading" is the question. */}
              {args && (
                <div style={{
                  marginLeft: '1.4rem', marginTop: '0.15rem',
                  fontFamily: 'var(--font-mono)', fontSize: '0.68rem', lineHeight: 1.5,
                  color: 'var(--hb-text-faint)', overflowWrap: 'anywhere',
                }}>
                  {args}
                </div>
              )}

              {result && state !== 'running' && (
                <div style={{
                  marginLeft: '1.4rem', marginTop: '0.15rem',
                  fontSize: '0.72rem', lineHeight: 1.5,
                  color: state === 'failed' ? 'var(--hb-red)' : 'var(--hb-text-dim)',
                  overflowWrap: 'anywhere',
                }}>
                  → {result}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** The running marker. Same ring the transcript uses, so a step reads the same
 *  in both places. */
function Spinner() {
  return (
    <span style={{
      display: 'block', width: 13, height: 13, borderRadius: '50%',
      border: '1.5px solid rgba(var(--hb-accent-rgb),0.25)',
      borderTopColor: 'var(--hb-cyan)',
      animation: 'spin 0.7s linear infinite',
    }} />
  )
}

function Glyph({ color, d }: { color: string; d: string }) {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke={color}
      strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  )
}
