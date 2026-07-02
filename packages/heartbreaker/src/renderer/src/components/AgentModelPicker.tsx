import type { AgentModelInfo } from '../lib/api'
import type { ModelInfo } from '../lib/types'
import { agentColor } from '../lib/agents'

/**
 * Per-agent model pin — a compact mono select used in the war-room roster and
 * the Systems board AGENT CORES section. "PROFILE" = the agent's own policy
 * (Sonnet interactive / Haiku background); anything else pins that agent to a
 * specific model for interactive AND dispatch runs.
 */
export default function AgentModelPicker({ info, models, onPin }: {
  info: AgentModelInfo
  models: ModelInfo[]
  onPin: (model: string | null) => void
}) {
  const pinned = !!info.override
  const c = agentColor(info.agent_id)
  return (
    <select
      value={info.override ?? ''}
      onChange={e => onPin(e.target.value || null)}
      title={pinned
        ? `${info.agent_id} pinned to ${info.override} — select PROFILE to restore its own policy`
        : `${info.agent_id} on profile policy: ${info.default_main} / ${info.default_background} (bg)`}
      style={{
        width: '100%', height: 18, padding: '0 2px',
        border: `1px solid ${pinned ? `${c}88` : 'var(--hb-line)'}`,
        background: pinned ? `${c}14` : 'rgba(10, 18, 26, 0.55)',
        color: pinned ? c : 'var(--hb-icon)',
        fontFamily: "'Share Tech Mono', monospace", fontSize: '0.5rem',
        letterSpacing: '0.05em', cursor: 'pointer',
        transition: 'border-color 0.12s, background 0.12s',
      }}
    >
      <option value="">PROFILE ({info.default_main.split(':').pop()})</option>
      {models.map(m => (
        <option key={m.id} value={m.id}>{m.name.toUpperCase()}</option>
      ))}
    </select>
  )
}
