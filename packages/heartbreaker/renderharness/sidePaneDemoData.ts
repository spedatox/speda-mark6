import type { SubagentRun } from '../src/renderer/src/lib/types'

export const workspaceTour: SubagentRun = {
  id: 'a'.repeat(32), executionId: 'a'.repeat(32), agent: 'autobot', ownerAgent: 'optimus',
  source: 'forge', executionType: 'forge', label: 'Workspace tour', running: true, status: 'running', lastSequence: 2,
  prompt: 'Review the Mark VI workspace and summarize its major components.',
  steps: [
    { kind: 'text', text: 'I’ll inspect the top-level folders and read the project guides.' },
    { kind: 'tool', tool: 'list_files', toolCallId: 'list', input: { path: '.' }, result: 'app/\nforge/\npackages/heartbreaker/\npackages/speda-go/' },
  ],
}

export const mapReview: SubagentRun = {
  id: 'map-review', agent: 'autobot', ownerAgent: 'optimus', source: 'forge',
  label: 'Review the workspace map', running: false, ok: true,
  steps: [{ kind: 'text', text: 'The map shows the desktop client, Legion and Android connected to Igor.' }],
  report: 'The diagram matches the workspace brief.',
}
