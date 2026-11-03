import { useEffect, useState } from 'react'
import { getConfigPolicies, saveConfigPolicy } from '../lib/api'
import type { ConfigPolicy } from '../lib/api'
import type { AppConfig } from '../lib/types'
import { useSettings } from '../store/settings'
import { policyLabel, validatePolicy } from '../lib/policyFields'
import type { PolicySchema, PolicyValue } from '../lib/policyFields'
import PolicyField from './PolicyField'
import { PillBtn } from './settingsUI'

type Draft = { value: Record<string, PolicyValue>; revision: number }
export default function ConfigPolicies({ config }: { config: AppConfig }) {
  const { settings } = useSettings()
  const tr = settings.locale === 'tr'
  const [policies, setPolicies] = useState<ConfigPolicy[]>([])
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState('')
  const load = async () => {
    setLoading(true)
    try { setPolicies(await getConfigPolicies(config)); setError('') }
    catch (e) { setError(String(e)) }
    finally { setLoading(false) }
  }
  useEffect(() => { setDrafts({}); setSaved(''); void load() }, [config.apiBase, config.apiKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const discard = (id: string) => setDrafts(d => { const next = { ...d }; delete next[id]; return next })
  const save = async (policy: ConfigPolicy) => {
    const draft = drafts[policy.id]
    if (!draft) return
    const errors = validatePolicy(policy.schema as PolicySchema, policy.schema as PolicySchema, draft.value)
    if (errors.length) { setError(`${tr ? 'Alanları kontrol edin' : 'Check these fields'}: ${errors.join(' ')}`); return }
    setBusy(policy.id); setError(''); setSaved('')
    try {
      setPolicies(await saveConfigPolicy(config, { ...policy, revision: draft.revision }, draft.value))
      discard(policy.id); setSaved(policy.id)
    } catch (e) { setError(String(e)) }
    finally { setBusy(null) }
  }
  const headings = tr
    ? { models: 'Modeller ve düşünme', coordination: 'Çalıştırma ve çalışan sınırları', global: 'Genel harcama sınırları', iacon: 'Iacon bakım ayarları' }
    : { models: 'Models and thinking', coordination: 'Run and worker limits', global: 'Overall spending limits', iacon: 'Iacon maintenance' }
  return <section style={{ border: '1px solid var(--hb-edge)', padding: 14 }}>
    <strong>{tr ? 'Model ve çalışma ayarları' : 'Model and runtime settings'}</strong>
    <p style={{ color: 'var(--text-muted)', fontSize: '.8rem' }}>{tr
      ? 'Bu ayarlar kaydedildiğinde yeni çalıştırmalara uygulanır. Sunucuyu yeniden başlatmanız gerekmez.'
      : 'These settings apply to new runs when saved. No server restart is needed.'}</p>
    {error && <p role="alert" style={{ color: 'var(--hb-red)' }}>{error}</p>}
    <PillBtn disabled={busy !== null || loading} onClick={() => void load()}>{tr ? 'Ayarları yenile (taslak korunur)' : 'Reload settings (keep drafts)'}</PillBtn>
    {loading && <p role="status">{tr ? 'Yükleniyor…' : 'Loading…'}</p>}
    {policies.map(policy => {
      const draft = drafts[policy.id]
      const value = draft?.value ?? policy.value as Record<string, PolicyValue>
      const schema = policy.schema as PolicySchema
      const conflict = draft && draft.revision !== policy.revision
      return <details key={policy.id} style={{ marginTop: 12, border: '1px solid var(--hb-edge)', padding: 14 }}>
        <summary style={{ cursor: 'pointer', color: 'var(--hb-text)', fontWeight: 600 }}>{headings[policy.id as keyof typeof headings] ?? policy.label}{draft ? (tr ? ' · Kaydedilmedi' : ' · Unsaved') : ''}</summary>
        {conflict && <p role="alert">{tr ? 'Bu ayarlar başka bir yerde değişti. Son kayıtlı değerleri kullanmak için değişiklikleri geri alın.' : 'These settings changed elsewhere. Discard your edits to use the latest saved values.'}</p>}
        <fieldset disabled={busy !== null || loading} style={{ border: 0, padding: '16px 0 0', margin: 0, minWidth: 0 }}>
          {Object.entries(schema.properties ?? {}).map(([key, field]) => <PolicyField key={key} schema={field} root={schema} value={value[key]}
            path={`${policy.id}.${key}`} label={policyLabel(key, tr)} tr={tr} disabled={busy !== null || loading}
            onChange={next => { setSaved(''); setError(''); setDrafts(d => ({ ...d, [policy.id]: { revision: d[policy.id]?.revision ?? policy.revision, value: { ...(d[policy.id]?.value ?? policy.value as Record<string, PolicyValue>), [key]: next } } })) }} />)}
        </fieldset>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          <PillBtn tone="accent" disabled={busy !== null || loading || !draft || !!conflict} onClick={() => void save(policy)}>{busy === policy.id ? (tr ? 'Kaydediliyor…' : 'Saving…') : (tr ? 'Ayarları kaydet' : 'Save settings')}</PillBtn>
          {draft && <PillBtn disabled={busy !== null || loading} onClick={() => { discard(policy.id); setError('') }}>{tr ? 'Değişiklikleri geri al' : 'Discard changes'}</PillBtn>}
          {saved === policy.id && <span role="status">{tr ? 'Kaydedildi' : 'Saved'}</span>}
        </div>
      </details>
    })}
  </section>
}
