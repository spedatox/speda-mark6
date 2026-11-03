package com.speda.heartbreaker.domain

import kotlinx.collections.immutable.toPersistentList

fun mergeWorkerRun(prior: SubagentRun?, incoming: SubagentRun): SubagentRun {
    if (prior == null) return incoming
    if ((prior.lastSequence ?: 0) > (incoming.lastSequence ?: 0)) return prior
    if (prior.lastSequence == incoming.lastSequence && !prior.activityPartial)
        return incoming.copy(steps = prior.steps, activityPartial = false)
    return incoming
}

fun boundedWorkerRun(run: SubagentRun): SubagentRun {
    var next = run.copy(steps = run.steps.takeLast(100).toPersistentList(),
        seenEventIds = run.seenEventIds.takeLast(128).toPersistentList(),
        prompt = run.prompt?.take(4000), report = run.report?.take(16000),
        activityPartial = run.activityPartial || run.steps.size > 100 || (run.prompt?.length ?: 0) > 4000 || (run.report?.length ?: 0) > 16000)
    while (next.steps.sumOf { (it.text?.length ?: 0) + (it.result?.length ?: 0) + (it.input?.toString()?.length ?: 0) } > 32000) {
        next = next.copy(steps = next.steps.drop(1).toPersistentList(), activityPartial = true)
    }
    return next
}

fun mergeWorkerHistory(loaded: List<ChatMessage>, current: List<ChatMessage>): List<ChatMessage> = loaded.map { message ->
    val runs = message.subagents.orEmpty().toMutableList()
    current.filter { it.id == message.id || it.coordinationStepId != null && it.coordinationStepId == message.coordinationStepId }.forEach { prior ->
        prior.subagents.orEmpty().forEach { old ->
            val at = runs.indexOfFirst { (it.executionId ?: it.id) == (old.executionId ?: old.id) }
            if (at < 0) runs.add(old) else runs[at] = mergeWorkerRun(old, runs[at])
        }
    }
    if (runs.isEmpty()) message else message.copy(subagents = runs.toPersistentList())
}

fun consolidateWorkerAnchors(messages: List<ChatMessage>): List<ChatMessage> {
    val steps = messages.filter { !it.isWorkerAnchor }.mapNotNull { it.coordinationStepId }.toSet()
    return mergeWorkerHistory(messages.filter { !it.isWorkerAnchor || it.coordinationStepId !in steps }, messages)
}

fun upsertWorkerMessage(messages: List<ChatMessage>, parentStep: String, parentMessage: String?, sessionId: Int,
    run: SubagentRun, create: Boolean): List<ChatMessage> {
    var rows = messages
    var index = rows.indexOfFirst { it.coordinationStepId == parentStep || it.id == parentMessage }
    if (index < 0) {
        if (!create) return rows
        rows = rows + ChatMessage("worker-parent:$parentStep", Role.Assistant, "Background work", sessionId = sessionId,
            coordinationStepId = parentStep, isWorkerAnchor = true)
        index = rows.lastIndex
    }
    return rows.mapIndexed { at, message ->
        if (at != index) message else {
            val runs = message.subagents.orEmpty().toMutableList()
            val old = runs.indexOfFirst { (it.executionId ?: it.id) == (run.executionId ?: run.id) }
            val next = boundedWorkerRun(mergeWorkerRun(runs.getOrNull(old), run))
            if (old < 0) runs.add(next) else runs[old] = next
            message.copy(subagents = runs.toPersistentList())
        }
    }
}
