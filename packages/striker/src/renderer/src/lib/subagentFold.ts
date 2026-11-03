// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import type { SubagentRun } from './types'

/** Durable event identity prevents replay from repeating text or tool calls. */
export function foldLegionEvent(run: SubagentRun | null, event: Record<string, unknown>): SubagentRun {
  const e = event as {
    id: string; agent?: string; label?: string; phase?: string
    prompt?: string; text?: string; tool?: string; input?: unknown
    result?: string; error?: string; report?: string; ok?: boolean
    source?: SubagentRun['source']; tool_call_id?: string
    execution_id?: string; owner_agent?: string; role?: string; status?: string
    execution_type?: 'legion' | 'forge'; event_id?: string | number; sequence?: number
  }
  const sequence = Number.isSafeInteger(e.sequence) && (e.sequence ?? 0) > 0 ? e.sequence : undefined
  const eventId = e.event_id == null ? undefined : String(e.event_id)
  if (run && ((sequence != null && sequence <= (run.lastSequence ?? 0))
      || (eventId != null && run.seenEventIds?.includes(eventId)))) return run
  const next: SubagentRun = run
    ? { ...run, steps: [...run.steps] }
    : { id: e.id || e.execution_id || '', agent: e.agent ?? '', label: e.label ?? '', running: true, steps: [], source: e.source }
  if (e.agent) next.agent = e.agent
  if (e.label) next.label = e.label
  if (e.source) next.source = e.source
  if (e.execution_id) next.executionId = e.execution_id
  if (e.owner_agent) next.ownerAgent = e.owner_agent
  if (e.role) next.workerRole = e.role
  if (e.execution_type) next.executionType = e.execution_type
  if (e.status) next.status = e.status
  if (sequence != null) next.lastSequence = sequence
  if (eventId != null) next.seenEventIds = [...(run?.seenEventIds ?? []), eventId]
  if (e.phase === 'started') {
    next.prompt = e.prompt ?? next.prompt
    next.running = true
  } else if (e.phase === 'text' && e.text) {
    const last = next.steps[next.steps.length - 1]
    if (last?.kind === 'text') next.steps[next.steps.length - 1] = { ...last, text: (last.text ?? '') + e.text }
    else next.steps.push({ kind: 'text', text: e.text })
  } else if (e.phase === 'tool' && e.tool_call_id) {
    const index = next.steps.findIndex(step => step.kind === 'tool' && step.toolCallId === e.tool_call_id)
    const step = { kind: 'tool' as const, tool: e.tool, input: e.input, toolCallId: e.tool_call_id }
    if (index >= 0) next.steps[index] = { ...next.steps[index], ...step }
    else next.steps.push(step)
  } else if (e.phase === 'tool_result' && e.tool_call_id) {
    const index = next.steps.findIndex(step => step.kind === 'tool' && step.toolCallId === e.tool_call_id)
    const result = e.error ?? e.result
    if (index >= 0) next.steps[index] = { ...next.steps[index], result }
    else next.steps.push({ kind: 'tool', tool: e.tool, toolCallId: e.tool_call_id, result })
  } else if (e.phase === 'finished') {
    next.running = false
    next.ok = e.ok ?? (!e.status || ['succeeded', 'completed', 'ok'].includes(e.status))
    next.report = e.report ?? e.result ?? next.report
  }
  if (e.status && ['queued', 'running', 'staging', 'waiting_input', 'waiting_dependency'].includes(e.status)) next.running = true
  if (e.phase === 'executor_unavailable') { next.running = false; next.status = 'unknown'; next.ok = undefined }
  if (e.status && ['unknown', 'succeeded', 'completed', 'failed', 'cancelled', 'interrupted', 'expired', 'reconciling', 'blocked'].includes(e.status)) next.running = false
  return next
}

/** The worker's corps/role and actual supervisor, never an invented persona. */
export function workerLabel(run: SubagentRun): string {
  const role = run.workerRole || run.agent
  const normalized = role.replace(/^forge_/, '')
  const name = ['autobot', 'coder'].includes(normalized) ? 'Autobot'
    : ['decepticon', 'pentester'].includes(normalized) ? 'Decepticon'
    : run.source === 'forge' || run.executionType === 'forge' ? `Forge · ${normalized}`
    : run.source === 'legion' || run.executionType === 'legion' ? `Legion · ${normalized}` : role
  const owner = run.ownerAgent === 'nightcrawler' || run.ownerAgent === 'friday' ? 'Friday'
    : run.ownerAgent ? run.ownerAgent[0].toUpperCase() + run.ownerAgent.slice(1) : ''
  return owner ? `${name} · supervised by ${owner}` : name
}
