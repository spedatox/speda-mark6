/** Bounded SSE decoding shared by worker detail and conversation subscriptions. */
export class WorkerTransportError extends Error {
  constructor(message: string, readonly retryable: boolean, readonly status?: number) { super(message) }
}

export async function readWorkerJson(response: Response, signal: AbortSignal): Promise<unknown> {
  if (!response.ok) throw new WorkerTransportError(`Worker recovery unavailable (${response.status})`,
    response.status >= 500 || [408,429].includes(response.status), response.status)
  if (!response.body) throw new WorkerTransportError('Worker recovery has no response body', false)
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let text = '', size = 0
  const abort = () => { void reader.cancel().catch(() => {}) }
  signal.addEventListener('abort', abort, { once: true })
  try {
    if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
    while (!signal.aborted) {
      const { done, value } = await reader.read()
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
      if (done) { text += decoder.decode(); break }
      size += value.byteLength
      if (size > 1500000) throw new WorkerTransportError('Worker recovery exceeded its size limit', false)
      text += decoder.decode(value, { stream: true })
    }
    try { return JSON.parse(text) } catch { throw new WorkerTransportError('Worker recovery contains invalid JSON', false) }
  } finally {
    signal.removeEventListener('abort', abort)
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

export async function* decodeWorkerFrames(
  response: Response, signal: AbortSignal, onActivity?: () => void,
): AsyncGenerator<Record<string, unknown>> {
  if (!response.ok) throw new WorkerTransportError(`Worker activity unavailable (${response.status})`,
    response.status >= 500 || [408, 429].includes(response.status), response.status)
  if (!response.body || !response.headers.get('content-type')?.toLowerCase().startsWith('text/event-stream')) {
    throw new WorkerTransportError('Worker activity returned an invalid stream', false)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const encoder = new TextEncoder()
  let buffer = '', data: string[] = [], frameBytes = 0
  const maxBytes = 256 * 1024
  const abort = () => { void reader.cancel().catch(() => {}) }
  signal.addEventListener('abort', abort, { once: true })
  const decode = (atEnd = false) => {
    if (!data.length) return null
    try {
      const envelope = JSON.parse(data.join('\n'))
      if (envelope.type !== 'subagent' || !envelope.data || typeof envelope.data !== 'object' || Array.isArray(envelope.data)) {
        throw new WorkerTransportError('Worker activity frame has an invalid envelope', false)
      }
      return envelope.data as Record<string, unknown>
    } catch (error) {
      if (error instanceof WorkerTransportError) throw error
      throw new WorkerTransportError('Worker activity frame could not be decoded', atEnd)
    } finally { data = []; frameBytes = 0 }
  }
  const line = (value: string) => {
    if (value === '') { const result = decode(); frameBytes = 0; return result }
    frameBytes += encoder.encode(value).byteLength
    if (frameBytes > maxBytes) throw new WorkerTransportError('Worker activity frame exceeded its size limit', false)
    if (value.startsWith('data:')) data.push(value.slice(5).replace(/^ /, ''))
    return null
  }
  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read()
      if (signal.aborted) return
      if (value?.byteLength) onActivity?.()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      while (true) {
        const boundary = buffer.search(/[\r\n]/)
        if (boundary < 0 || (!done && boundary === buffer.length - 1 && buffer[boundary] === '\r')) break
        const width = buffer[boundary] === '\r' && buffer[boundary + 1] === '\n' ? 2 : 1
        const result = line(buffer.slice(0, boundary))
        buffer = buffer.slice(boundary + width)
        if (result) yield result
      }
      if (encoder.encode(buffer).byteLength > maxBytes) throw new WorkerTransportError('Worker activity line exceeded its size limit', false)
      if (done) {
        if (buffer) { const result = line(buffer); if (result) yield result }
        const final = decode(true)
        if (final) yield final
        return
      }
    }
  } finally {
    signal.removeEventListener('abort', abort)
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
