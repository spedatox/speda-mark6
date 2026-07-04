import type { AgentModelInfo } from '../lib/api'
import type { ModelInfo } from '../lib/types'
import { agentColor } from '../lib/agents'
import GlassSelect from './GlassSelect'

/**
 * Per-agent model pin — a compact fluid-glass select used in the war-room
 * roster and the Systems board AGENT CORES section. "PROFILE" = the agent's
 * own policy (Sonnet interactive / Haiku background); anything else pins that
 * agent to a specific model for interactive AND dispatch runs. Rendering is
 * GlassSelect (liquid-glass popover + built-in background freeze) — never the
 * native OS dropdown.
 */
export default function AgentModelPicker({ info, models, onPin, large = false }: {
  info: AgentModelInfo
  models: ModelInfo[]
  onPin: (model: string | null) => void
  /** Full liquid-glass trigger (ROSTER CORES window) vs the dense board bar. */
  large?: boolean
}) {
  const pinned = !!info.override
  const c = agentColor(info.agent_id)
  const profileLabel = `PROFILE (${info.default_main.split(':').pop()})`
  return (
    <GlassSelect
      value={info.override ?? ''}
      options={[
        { value: '', label: profileLabel },
        ...models.map(m => ({ value: m.id, label: m.name.toUpperCase() })),
      ]}
      onChange={v => onPin(v || null)}
      tint={c}
      active={pinned}
      large={large}
      title={pinned
        ? `${info.agent_id} pinned to ${info.override} — select PROFILE to restore its own policy`
        : `${info.agent_id} on profile policy: ${info.default_main} / ${info.default_background} (bg)`}
    />
  )
}
