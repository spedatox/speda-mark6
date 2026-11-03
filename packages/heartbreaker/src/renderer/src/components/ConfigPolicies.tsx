import { useEffect, useState } from 'react'
import { getConfigPolicies, saveConfigPolicy } from '../lib/api'
import type { ConfigPolicy } from '../lib/api'
import type { AppConfig } from '../lib/types'
import { useSettings } from '../store/settings'

export default function ConfigPolicies({ config }: { config: AppConfig }) {
  const { settings } = useSettings()
  const tr = settings.locale === 'tr'
  const [policies, setPolicies] = useState<ConfigPolicy[]>([])
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState('')
  const load = async () => {
    try { setPolicies(await getConfigPolicies(config)); setError('') }
    catch (e) { setError(String(e)) }
  }
  useEffect(() => { void load() }, [config.apiBase, config.apiKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const save = async (policy: ConfigPolicy) => {
    setBusy(policy.id); setError(''); setSaved('')
    try {
      const value = JSON.parse(drafts[policy.id])
      if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error(tr ? 'Bir JSON nesnesi gerekli.' : 'A JSON object is required.')
      setPolicies(await saveConfigPolicy(config, policy, value))
      setDrafts(d => { const next = { ...d }; delete next[policy.id]; return next })
      setSaved(policy.id)
    } catch (e) { setError(String(e)) }
    finally { setBusy(null) }
  }
  return <section style={{ border: '1px solid var(--hb-edge)', padding: 14 }}>
    <strong>{tr ? 'Gelişmiş çalışma politikaları' : 'Advanced runtime policies'}</strong>
    <p style={{ color: 'var(--text-muted)', fontSize: '.8rem' }}>{tr
      ? 'Tüm model, düşünme, bütçe, çalışan ve Iacon alanları. Bu politikalar kaydedildiğinde etkinleşir; arka uç yeniden başlatılmaz. JSON şeması tüm seçenekleri gösterir.'
      : 'Every model, thinking, spending, worker and Iacon policy field. Policy saves apply without restarting the backend. The JSON schema lists all options.'}</p>
    {error && <p role="alert" style={{ color: 'var(--hb-red)' }}>{error}</p>}
    <button className="hb-btn" disabled={busy !== null} onClick={() => void load()}>{tr ? 'Politikaları yeniden yükle (taslak korunur)' : 'Reload policies (keep drafts)'}</button>
    {policies.map(policy => <details key={policy.id} style={{ marginTop: 12 }}>
      <summary style={{ cursor: 'pointer' }}>{policy.label} · v{policy.revision}</summary>
      <textarea aria-label={policy.label} spellCheck={false} disabled={busy !== null}
        value={drafts[policy.id] ?? JSON.stringify(policy.value, null, 2)}
        onChange={e => { setSaved(''); setDrafts(d => ({ ...d, [policy.id]: e.target.value })) }}
        style={{ width: '100%', minHeight: 230, marginTop: 10, padding: 10, fontFamily: 'var(--font-mono)', fontSize: 12, background: 'var(--glass-fill)', color: 'var(--text-primary)', border: '1px solid var(--hb-edge)' }} />
      <button className="hb-btn hb-btn-tint" disabled={busy !== null || !(policy.id in drafts)} onClick={() => void save(policy)}>{tr ? 'Politikayı kaydet' : 'Save policy'}</button>
      {saved === policy.id && <span role="status"> {tr ? 'Kaydedildi' : 'Saved'}</span>}
      <details><summary>{tr ? 'Tüm alanlar ve sınırlar' : 'All fields and constraints'}</summary><pre style={{ overflow: 'auto', maxHeight: 300, fontSize: 11 }}>{JSON.stringify(policy.schema, null, 2)}</pre></details>
    </details>)}
  </section>
}
