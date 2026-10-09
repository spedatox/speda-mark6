// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from 'react'
import { fetchAgentPersonalities, saveAgentPersonality } from '../lib/api'
import type { AgentPersonalityInfo, PersonalitySettings } from '../lib/api'
import type { AppConfig } from '../lib/types'
import { useT } from '../lib/i18n'
import { PillBtn, SettingsField, SettingsSection, fieldStyle } from './settingsUI'
import { SkeletonList } from './Skeleton'

const defaults: PersonalitySettings = {
  instructions: '', tone: 'default', humor: 'default', directness: 'default', response_length: 'default',
}

export default function PersonalityTab({ config }: { config: AppConfig }) {
  const t = useT()
  const a = t.settingsPersonality
  const [agents, setAgents] = useState<AgentPersonalityInfo[]>([])
  const [drafts, setDrafts] = useState<Record<string, PersonalitySettings>>({})
  const [selected, setSelected] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    fetchAgentPersonalities(config).then(items => {
      if (cancelled) return
      setAgents(items)
      setDrafts(Object.fromEntries(items.map(item => [item.agent_id, { ...item.settings }])))
      setSelected(items[0]?.agent_id ?? '')
    }).catch(() => { if (!cancelled) setError(a.loadFailed) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [config.apiBase, config.apiKey, reload, a.loadFailed])

  const current = agents.find(item => item.agent_id === selected)
  const draft = drafts[selected]
  const dirty = !!current && !!draft && JSON.stringify(current.settings) !== JSON.stringify(draft)
  function change(patch: Partial<PersonalitySettings>) {
    setDrafts(previous => ({ ...previous, [selected]: { ...previous[selected], ...patch } }))
    setSaved(false)
    setError('')
  }
  async function save() {
    if (!draft || !current) return
    setBusy(true)
    setError('')
    setSaved(false)
    try {
      const next = await saveAgentPersonality(config, current.agent_id, draft)
      const persisted = next.find(item => item.agent_id === current.agent_id)
      if (!persisted) throw new Error('Missing saved agent')
      setAgents(next)
      setDrafts(previous => ({ ...previous, [current.agent_id]: { ...persisted.settings } }))
      setSaved(true)
    } catch { setError(a.saveFailed) }
    finally { setBusy(false) }
  }
  const controls = [
    { key: 'tone' as const, label: a.tone, options: [['default', a.profileDefault], ['familiar', a.familiar], ['professional', a.professional]] },
    { key: 'humor' as const, label: a.humor, options: [['default', a.profileDefault], ['off', a.off], ['dry', a.dry], ['playful', a.playful]] },
    { key: 'directness' as const, label: a.directness, options: [['default', a.profileDefault], ['gentle', a.gentle], ['direct', a.direct]] },
    { key: 'response_length' as const, label: a.responseLength, options: [['default', a.profileDefault], ['brief', a.brief], ['detailed', a.detailed]] },
  ]

  return <div style={{ maxWidth: 720, display: 'flex', flexDirection: 'column', gap: 18 }}>
    <SettingsSection title={a.title} first />
    <p style={{ margin: 0, color: 'var(--hb-text-dim)', fontSize: '0.875rem', lineHeight: 1.6 }}>{a.blurb}</p>
    {loading ? <SkeletonList rows={4} /> : <>
      {error && <div role="alert" style={{ color: 'var(--hb-danger, #f28484)' }}>
        {error} {!current && <PillBtn onClick={() => setReload(value => value + 1)}>{a.retry}</PillBtn>}
      </div>}
      {current && draft && <>
        <SettingsField label={a.agent}>
          <select aria-label={a.agent} value={selected} disabled={busy} style={fieldStyle}
            onChange={event => { setSelected(event.target.value); setSaved(false); setError('') }}>
            {agents.map(item => <option key={item.agent_id} value={item.agent_id}>{item.name}</option>)}
          </select>
        </SettingsField>
        <div style={{ color: 'var(--hb-text-dim)', fontSize: '0.8125rem' }}>{current.domain}</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(260px, 100%), 1fr))', gap: 16 }}>
          {controls.map(control => <SettingsField key={control.key} label={control.label}>
            <select aria-label={control.label} value={draft[control.key]} disabled={busy} style={fieldStyle}
              onChange={event => change({ [control.key]: event.target.value } as Partial<PersonalitySettings>)}>
              {control.options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </SettingsField>)}
        </div>
        <SettingsField label={a.instructions}>
          <textarea aria-label={a.instructions} value={draft.instructions} disabled={busy} maxLength={6000}
            placeholder={a.placeholder} style={{ ...fieldStyle, minHeight: 190, resize: 'vertical', lineHeight: 1.6 }}
            onChange={event => change({ instructions: event.target.value })} />
        </SettingsField>
        <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-dim)' }}>{a.instructionsHint} · {draft.instructions.length}/6000</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
          <PillBtn onClick={() => void save()} disabled={!dirty || busy}>{busy ? t.common.loading : t.common.save}</PillBtn>
          <PillBtn onClick={() => change({ ...defaults })} disabled={busy}>{a.reset}</PillBtn>
          <span role="status" aria-live="polite" style={{ color: 'var(--hb-text-dim)', fontSize: '0.8125rem' }}>
            {dirty ? a.unsaved : saved ? a.saved : a.current}
          </span>
        </div>
      </>}
    </>}
  </div>
}
