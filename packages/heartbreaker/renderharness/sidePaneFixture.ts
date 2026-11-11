import { createReadStream } from 'node:fs'
import { resolve } from 'node:path'
import type { Plugin } from 'vite'
import { workspaceTour } from './sidePaneDemoData'

export function sidePaneFixture(): Plugin {
  const executionId = workspaceTour.executionId
  const parentStepId = 'b'.repeat(32)
  return {
    name: 'heartbreaker-side-pane-fixture',
    configureServer(server) {
      server.middlewares.use('/__side-pane', (request, response) => {
        const url = new URL(request.url || '/', 'http://localhost')
        const path = url.pathname
        if (request.headers['x-api-key'] !== 'side-pane-demo') {
          response.writeHead(401).end()
          return
        }
        if (path === '/files/workspace-brief.pdf') {
          response.setHeader('Content-Type', 'application/octet-stream')
          createReadStream(resolve(__dirname, 'fixtures/workspace-brief.pdf')).pipe(response)
          return
        }
        if (path.endsWith('/attach') && request.method === 'GET') {
          response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
          response.write(': connected\n\n')
          const sessionFeed = path === '/legion/sessions/1/attach'
          if (sessionFeed || path === `/legion/executions/${executionId}/attach`) {
            const after = Number(url.searchParams.get('after'))
            const send = (sequence: number, data: Record<string, unknown>) => {
              if (after < sequence) response.write(`id: ${sequence}\ndata: ${JSON.stringify({
                type: 'subagent', data: { ...data, sequence, execution_id: executionId, parent_step_id: parentStepId },
              })}\n\n`)
            }
            const progress = setTimeout(() => send(3, { phase: 'text',
              text: 'I found the desktop, Android and backend entry points. I’m checking how the worker stream reaches the desktop.' }), sessionFeed ? 5500 : 1600)
            const finish = setTimeout(() => {
              send(4, { phase: 'finished', status: 'succeeded', ok: true,
                report: 'The workspace tour is complete. The brief identifies Heartbreaker, Igor and SPEDA Go, and the map shows where Legion fits.' })
              if (!sessionFeed) response.end()
            }, after >= 4 ? 0 : sessionFeed ? 19000 : 14000)
            const keepAlive = sessionFeed ? setInterval(() => response.write(': alive\n\n'), 10000) : undefined
            response.on('close', () => { clearTimeout(progress); clearTimeout(finish); clearInterval(keepAlive) })
          }
          return
        }
        const execution = { execution_id: executionId, status: 'running', role: 'autobot', label: 'Workspace tour',
          owner_agent: 'optimus', execution_type: 'forge', model: 'Demo worker', can_interrupt: false, can_message: false, last_sequence: 2 }
        const body = path === `/legion/executions/${executionId}` ? execution
          : path === '/legion/executions' ? [execution]
          : path === '/sessions/1/room' ? null
          : path.endsWith('/snapshot') ? { session_id: 1, sequence: 2, omitted: 0, cards: [
              { parent_step_id: parentStepId, parent_message_id: 'response', run: workspaceTour },
            ] }
          : []
        response.setHeader('Content-Type', 'application/json')
        response.end(JSON.stringify(body))
      })
    },
  }
}
