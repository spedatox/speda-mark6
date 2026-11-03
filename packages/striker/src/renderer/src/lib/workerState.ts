import type { ChatMessage, SubagentRun } from './types'

export function mergeWorkerRun(prior: SubagentRun | undefined, incoming: SubagentRun): SubagentRun {
  if (!prior) return incoming
  if ((prior.lastSequence ?? 0) > (incoming.lastSequence ?? 0)) return prior
  if ((prior.lastSequence ?? 0) === (incoming.lastSequence ?? 0) && !prior.activityPartial) {
    return { ...prior, ...incoming, steps: prior.steps, activityPartial: false }
  }
  return incoming
}

/** Merge matching identities while preserving events received during history loading. */
export function mergeWorkerHistory(loaded: ChatMessage[], current: ChatMessage[]): ChatMessage[] {
  return loaded.map(message => {
    const runs = [...(message.subagents ?? [])]
    for (const prior of current.filter(old => old.id === message.id || (old.coordinationStepId && old.coordinationStepId === message.coordinationStepId))) {
      for (const old of prior.subagents ?? []) {
        const index = runs.findIndex(run => (run.executionId || run.id) === (old.executionId || old.id))
        if (index < 0) runs.push(old)
        else runs[index] = mergeWorkerRun(old, runs[index])
      }
    }
    return runs.length ? { ...message, subagents: runs } : message
  })
}

/** A bounded preview; the durable event journal remains available for full replay. */
export function boundedWorkerRun(run: SubagentRun): SubagentRun {
  const next = { ...run, steps: run.steps.slice(-100), seenEventIds: run.seenEventIds?.slice(-128) }
  if (next.prompt && next.prompt.length > 4000) { next.prompt = next.prompt.slice(0,4000); next.activityPartial = true }
  if (next.report && next.report.length > 16000) { next.report = next.report.slice(0,16000); next.activityPartial = true }
  if (next.steps.length !== run.steps.length) next.activityPartial = true
  while (JSON.stringify(next).length > 64000 && next.steps.length) {
    next.steps = next.steps.slice(1)
    next.activityPartial = true
  }
  return next
}

export function consolidateWorkerAnchors(messages: ChatMessage[]): ChatMessage[] {
  const realSteps = new Set(messages.filter(message => !message.isWorkerAnchor).map(message => message.coordinationStepId).filter(Boolean))
  return mergeWorkerHistory(messages.filter(message => !message.isWorkerAnchor || !realSteps.has(message.coordinationStepId)), messages)
}

export function upsertWorkerMessage(messages: ChatMessage[], id: string, sessionId: number, run: SubagentRun,
  parentStepId?: string, create = false): ChatMessage[] {
  let parent = messages.find(message => message.id === id || (parentStepId && message.coordinationStepId === parentStepId))
  if (!parent) {
    if (!create || !parentStepId) return messages
    parent = { id, role: 'assistant', content: 'Background work', tools: [], isStreaming: false, isError: false,
      sessionId, coordinationStepId: parentStepId, isWorkerAnchor: true, subagents: [] }
    messages = [...messages, parent]
  }
  const parentId = parent.id
  return messages.map(message => {
    if (message.id !== parentId) return message
    const runs = [...(message.subagents ?? [])]
    const index = runs.findIndex(old => (old.executionId || old.id) === (run.executionId || run.id))
    const next = mergeWorkerRun(runs[index], run)
    if (index < 0) runs.push(next)
    else runs[index] = next
    return { ...message, subagents: runs }
  })
}
