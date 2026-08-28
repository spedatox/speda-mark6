/**
 * Voices tab — per-agent voice pin + ElevenLabs tuning, from Settings.
 *
 * Mirrors AutomationBuilder's list-then-inline-editor pattern: a row per
 * dispatch-target agent, click Edit to replace the list with that agent's
 * voice picker and the four ElevenLabs sliders plus the speaker-boost
 * toggle — the same parameters ElevenLabs' own Studio settings panel
 * exposes, so "configure all parameters, customizable for each voice" means
 * exactly what it looks like it means.
 *
 * Two things clear independently and must never take each other down: the
 * voice PIN (falls back to the profile's own default voice, app/profiles/
 * *.py) and the TUNING (falls back to that voice's own dashboard settings on
 * ElevenLabs). "Reset" here clears both at once for a clean slate — picking a
 * voice again with no tuning applied is one click either way.
 */

import { useEffect, useState } from 'react'
import { fetchVoiceAgents, fetchVoiceOptions, saveVoiceAgent, clearVoiceAgent } from '../lib/api'
import type { VoiceAgentInfo, VoiceOption, VoiceSettings } from '../lib/api'
import type { AppConfig } from '../lib/types'
import { PillBtn, SettingsField, SettingsSection, SliderField, Switch, fieldStyle } from './settingsUI'
import { SkeletonList } from './Skeleton'
import { useT } from '../lib/i18n'

// ElevenLabs' own documented defaults for a voice nobody has tuned — the
// sliders' starting position when no override exists yet, so opening the
// editor shows what is ACTUALLY in effect rather than an arbitrary midpoint.
const DEFAULTS: Required<VoiceSettings> = {
  stability: 0.5, similarity_boost: 0.75, style: 0, speed: 1.0, use_speaker_boost: true,
}

export default function VoicesTab({ config }: { config: AppConfig }) {
  const t = useT()
  const a = t.settingsVoices
  const [agents, setAgents] = useState<VoiceAgentInfo[]>([])
  const [voiceOptions, setVoiceOptions] = useState<VoiceOption[]>([])
  const [loaded, setLoaded] = useState(false)
  const [editing, setEditing] = useState<string | null>(null) // agent_id, or null = list

  const load = async () => {
    setLoaded(false)
    const [ag, vo] = await Promise.all([fetchVoiceAgents(config), fetchVoiceOptions(config)])
    setAgents(ag)
    setVoiceOptions(vo)
    setLoaded(true)
  }
  useEffect(() => { void load() }, [config]) // eslint-disable-line react-hooks/exhaustive-deps

  const current = agents.find(g => g.agent_id === editing) ?? null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 22, maxWidth: 720 }}>
      <SettingsSection title={a.title} first />
      <p style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.55, margin: 0 }}>
        {a.blurb}
      </p>

      {!editing && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {!loaded ? <SkeletonList rows={4} /> : agents.map(ag => (
            <div key={ag.agent_id} className="hb-tile" style={{
              display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px',
              background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)',
            }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: '0.9375rem', color: 'var(--hb-text)' }}>{ag.name}</div>
                <div style={{
                  fontSize: '0.8125rem', color: 'var(--hb-text-faint)', marginTop: 2,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {voiceOptions.find(v => v.id === ag.effective_voice)?.display ?? ag.effective_voice}
                  {ag.voice_settings && ` · ${a.tuned}`}
                </div>
              </div>
              <PillBtn onClick={() => setEditing(ag.agent_id)}>{t.settingsAutomations.edit}</PillBtn>
            </div>
          ))}
        </div>
      )}

      {editing && current && (
        <VoiceEditor
          agent={current}
          voiceOptions={voiceOptions}
          onCancel={() => setEditing(null)}
          onSave={async patch => {
            const next = await saveVoiceAgent(config, current.agent_id, patch)
            if (next.length) setAgents(next)
            setEditing(null)
          }}
          onClearAll={async () => {
            const next = await clearVoiceAgent(config, current.agent_id)
            if (next.length) setAgents(next)
            setEditing(null)
          }}
        />
      )}
    </div>
  )
}

function VoiceEditor({ agent, voiceOptions, onCancel, onSave, onClearAll }: {
  agent: VoiceAgentInfo
  voiceOptions: VoiceOption[]
  onCancel: () => void
  onSave: (patch: { voice_id?: string | null } & VoiceSettings) => Promise<void>
  onClearAll: () => Promise<void>
}) {
  const t = useT()
  const a = t.settingsVoices
  const [voiceId, setVoiceId] = useState(agent.voice_id ?? '')
  // The bare ElevenLabs voice id for manual entry — a fallback for when the
  // catalog can't be listed (e.g. the API key lacks the voices_read
  // permission) but synthesis itself still works fine on text_to_speech
  // alone. Only meaningful while voiceId is an elevenlabs ref; derived from
  // it on open so editing an already-pinned custom voice shows its id here
  // too, not just in the (possibly voice-less) dropdown above.
  const [manualId, setManualId] = useState(
    agent.voice_id?.startsWith('elevenlabs:') ? agent.voice_id.split(':').slice(2).join(':') : '',
  )
  const [stability, setStability] = useState(agent.voice_settings?.stability ?? DEFAULTS.stability)
  const [similarity, setSimilarity] = useState(agent.voice_settings?.similarity_boost ?? DEFAULTS.similarity_boost)
  const [style, setStyle] = useState(agent.voice_settings?.style ?? DEFAULTS.style)
  const [speed, setSpeed] = useState(agent.voice_settings?.speed ?? DEFAULTS.speed)
  const [speakerBoost, setSpeakerBoost] = useState(agent.voice_settings?.use_speaker_boost ?? DEFAULTS.use_speaker_boost)
  const [busy, setBusy] = useState(false)

  // Grouped by engine, same idea as the automations agent picker grouping —
  // the owner's custom ElevenLabs voices sit alongside Azure/OpenAI's, never
  // interleaved.
  const byProvider: Record<string, VoiceOption[]> = {}
  for (const v of voiceOptions) (byProvider[v.provider] ??= []).push(v)

  function pickFromCatalog(fullRef: string) {
    setVoiceId(fullRef)
    setManualId(fullRef.startsWith('elevenlabs:') ? fullRef.split(':').slice(2).join(':') : '')
  }
  function typeManualId(raw: string) {
    setManualId(raw)
    setVoiceId(raw.trim() ? `elevenlabs:eleven_multilingual_v2:${raw.trim()}` : '')
  }

  async function submit() {
    setBusy(true)
    await onSave({
      voice_id: voiceId || null,
      stability, similarity_boost: similarity, style, speed, use_speaker_boost: speakerBoost,
    })
    setBusy(false)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <SettingsSection title={agent.name} first />

      <SettingsField label={a.voicePick} hint={a.voicePickHint}>
        <select
          value={voiceOptions.some(o => o.id === voiceId) ? voiceId : ''}
          onChange={e => { if (e.target.value) pickFromCatalog(e.target.value) }}
          style={{ ...fieldStyle, marginBottom: 10 }}
        >
          <option value="">{`${a.useDefault} — ${agent.default_voice}`}</option>
          {Object.entries(byProvider).map(([provider, opts]) => (
            <optgroup key={provider} label={provider}>
              {opts.map(o => <option key={o.id} value={o.id}>{o.display}</option>)}
            </optgroup>
          ))}
        </select>
      </SettingsField>

      {/* The catalog needs the ElevenLabs key's voices_read permission —
          text_to_speech (the permission synthesis itself needs) is a
          SEPARATE scope, so a key that can speak may still list nothing
          here. This box works regardless: paste the id straight from the
          ElevenLabs dashboard. */}
      <SettingsField label={a.manualVoiceId} hint={a.manualVoiceIdHint}>
        <input
          value={manualId}
          onChange={e => typeManualId(e.target.value)}
          placeholder={a.manualVoiceIdPlaceholder}
          style={fieldStyle}
        />
      </SettingsField>

      <SettingsSection title={a.tuning} />
      <p style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.5, margin: '-8px 0 0' }}>
        {a.tuningHint}
      </p>

      <SliderField
        label={a.stability} value={stability} min={0} max={1} step={0.01} onChange={setStability}
        low={a.variable} high={a.stable} format={v => v.toFixed(2)}
      />
      <SliderField
        label={a.similarity} value={similarity} min={0} max={1} step={0.01} onChange={setSimilarity}
        low={a.low} high={a.high} format={v => v.toFixed(2)}
      />
      <SliderField
        label={a.style} value={style} min={0} max={1} step={0.01} onChange={setStyle}
        low={a.none} high={a.exaggerated} format={v => v.toFixed(2)}
      />
      <SliderField
        label={a.speed} value={speed} min={0.7} max={1.2} step={0.01} onChange={setSpeed}
        low={a.slower} high={a.faster} format={v => v.toFixed(2)}
      />

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: '0.845rem', color: 'var(--hb-text-dim)' }}>{a.speakerBoost}</span>
        <Switch on={speakerBoost} onChange={setSpeakerBoost} />
      </div>

      <div style={{ display: 'flex', gap: 10 }}>
        <PillBtn tone="accent" onClick={() => { if (!busy) void submit() }}>
          {busy ? t.settingsAutomations.saving : t.settingsAutomations.save}
        </PillBtn>
        <PillBtn onClick={onCancel}>{t.settingsAutomations.cancel}</PillBtn>
        <PillBtn tone="danger" onClick={() => { if (!busy) void onClearAll() }}>{a.resetAll}</PillBtn>
      </div>
    </div>
  )
}
