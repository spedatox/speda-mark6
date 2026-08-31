// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import type { SubagentRun } from './types'

/**
 * Fold one SUBAGENT-phase event (`started|text|tool|tool_result|finished`,
 * see app/schemas/sse.py's SUBAGENT doc comment) onto a run, creating it on
 * the first `started` frame. Pure — shared between store/chat.ts's SUBAGENT
 * reducer case (runs that belong to a message) and the Legion tray's
 * standalone background-run tracking (runs that don't, since no message owns
 * a legionnaire that outlives the turn that deployed it).
 */
export function foldLegionEvent(run: SubagentRun | null, event: Record<string, unknown>): SubagentRun {
  const e = event as {
    id: string; agent?: string; label?: string; phase?: string
    prompt?: string; text?: string; tool?: string; input?: unknown
    result?: string; report?: string; ok?: boolean; source?: 'legion' | 'peer'
  }
  const next: SubagentRun = run
    ? { ...run, steps: [...run.steps] }
    : { id: e.id, agent: e.agent ?? '', label: e.label ?? '', running: true, steps: [], source: e.source }

  if (e.source) next.source = e.source

  if (e.phase === 'started') {
    next.prompt = e.prompt
    next.running = true
  } else if (e.phase === 'text' && e.text) {
    const last = next.steps[next.steps.length - 1]
    if (last?.kind === 'text') last.text = (last.text ?? '') + e.text
    else next.steps.push({ kind: 'text', text: e.text })
  } else if (e.phase === 'tool') {
    next.steps.push({ kind: 'tool', tool: e.tool, input: e.input })
  } else if (e.phase === 'tool_result') {
    for (let k = next.steps.length - 1; k >= 0; k--) {
      if (next.steps[k].kind === 'tool' && next.steps[k].result === undefined) {
        next.steps[k] = { ...next.steps[k], result: e.result }
        break
      }
    }
  } else if (e.phase === 'finished') {
    next.running = false
    next.ok = e.ok !== false
    next.report = e.report
  }

  return next
}
