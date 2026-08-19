/**
 * Settings ▸ Reminders — the standing reminders Igor asks and keeps asking
 * until they are answered.
 *
 * These are DEFINITIONS, not runs. Igor's tick reads them every few minutes,
 * sends each one over the owning agent's Telegram bot with answer buttons, and
 * re-asks until you tap or it runs out of attempts. Nothing here costs a model
 * call — the text is sent verbatim, which is also why it is written by hand
 * rather than composed.
 *
 * A reminder an agent opens itself (Atomix's evening checklist, composed fresh
 * each night) has no definition and so does not appear in this list; its
 * outcome still shows in the history below.
 */
import { useEffect, useState } from 'react'
import {
  getReminders, saveReminder, deleteReminder, getReminderHistory,
} from '../lib/api'
import type { ReminderDefinition, ReminderCycleInfo } from '../lib/api'
import type { AppConfig } from '../lib/types'
import { SkeletonList } from './Skeleton'
import { useT } from '../lib/i18n'
import type { Dict } from '../lib/i18n/en'

const AGENTS = ['speda', 'atomix', 'ultron', 'sentinel', 'nightcrawler', 'centurion', 'orion']

const blank = (t: Dict): ReminderDefinition => ({
  id: '', agent: 'atomix', text: '', at: '09:00', days: '*',
  options: [{ label: t.remindersTab.defaultDoneLabel, value: 'done' }],
  every_minutes: 5, max_asks: 10, enabled: true,
})

/* Same field, label and chip the settings pane uses everywhere else — this tab
   is one of its eight panes, not a separate form. Radii come from classes:
   the theme's blanket reset outranks any inline borderRadius. */
const input: React.CSSProperties = {
  background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.09)',
  color: 'var(--hb-text)', padding: '0 14px', height: 44,
  fontSize: '0.9375rem', fontFamily: 'var(--font-read)', width: '100%',
  outline: 'none',
}
const label: React.CSSProperties = {
  fontSize: '0.845rem', color: 'var(--hb-text-dim)',
  marginBottom: '0.5rem', display: 'block',
}
const chip = (active: boolean): React.CSSProperties => ({
  padding: '0 13px', height: 30, display: 'inline-flex', alignItems: 'center',
  cursor: 'pointer', fontSize: '0.845rem',
  border: `1px solid ${active ? 'rgba(var(--hb-accent-rgb),0.32)' : 'rgba(255,255,255,0.08)'}`,
  background: active ? 'rgba(var(--hb-accent-rgb),0.12)' : 'var(--glass-sheen)',
  color: active ? 'var(--hb-cyan-bright)' : 'var(--hb-text-dim)',
})

/** "1,3,5" ⇄ the day chips. "*" means every day. */
function daysToSet(days: string): Set<number> {
  if (!days || days === '*') return new Set([1, 2, 3, 4, 5, 6, 7])
  return new Set(days.split(',').map(d => Number(d.trim())).filter(Boolean))
}
function setToDays(set: Set<number>): string {
  if (set.size === 7) return '*'
  return [...set].sort().join(',')
}

export default function RemindersTab({ config }: { config: AppConfig }): React.ReactElement {
  const t = useT()
  const DAY_LABELS = t.remindersTab.dayLabels
  const [defs, setDefs] = useState<ReminderDefinition[]>([])
  const [history, setHistory] = useState<ReminderCycleInfo[]>([])
  const [loaded, setLoaded] = useState(false)
  const [draft, setDraft] = useState<ReminderDefinition | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = async (): Promise<void> => {
    try {
      setDefs(await getReminders(config))
      setHistory(await getReminderHistory(config, 20))
    } finally {
      // Always clear — an unreachable backend must not skeleton this forever.
      setLoaded(true)
    }
  }
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (): Promise<void> => {
    if (!draft) return
    const id = draft.id.trim()
    if (!id || !draft.text.trim()) { setError(t.remindersTab.idRequired); return }
    setBusy(true); setError(null)
    const res = await saveReminder(config, { ...draft, id })
    setBusy(false)
    if (res.status !== 'ok') { setError(res.detail || res.status); return }
    setDraft(null)
    await load()
  }

  const remove = async (id: string): Promise<void> => {
    if (!confirm(t.remindersTab.confirmDelete(id))) return
    await deleteReminder(config, id)
    await load()
  }

  const toggle = async (d: ReminderDefinition): Promise<void> => {
    await saveReminder(config, { ...d, enabled: !d.enabled })
    await load()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
      <p style={{ fontSize: '0.8rem', color: 'var(--hb-text-dim)', margin: 0, lineHeight: 1.5 }}>
        {t.remindersTab.intro}
      </p>

      {/* ── The list ──────────────────────────────────────────────────────── */}
      {!loaded ? (
        <SkeletonList rows={3} mark={false} />
      ) : defs.length === 0 && !draft && (
        <div style={{ fontSize: '0.8rem', color: 'var(--hb-text-faint)' }}>
          {t.remindersTab.noneConfigured}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {defs.map(d => (
          <div
            key={d.id}
            style={{
              display: 'flex', alignItems: 'center', gap: '0.75rem',
              padding: '0.6rem 0.75rem', borderRadius: 8,
              border: '1px solid var(--hb-edge)',
              background: 'rgba(255,255,255,0.02)',
              opacity: d.enabled ? 1 : 0.5,
            }}
          >
            <div style={{ minWidth: 52, fontSize: '0.9rem', color: 'var(--hb-accent-bright)' }}>
              {d.at || '—'}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: '0.85rem', color: 'var(--hb-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {d.text}
              </div>
              <div style={{ fontSize: '0.7rem', color: 'var(--hb-text-faint)', letterSpacing: '0.05em' }}>
                {d.id} · {d.agent} · {d.days === '*' ? t.remindersTab.everyDay : d.days.split(',').map(n => DAY_LABELS[Number(n) - 1]).join(' ')}
                {' · '}{t.remindersTab.everyMinMax(d.every_minutes, d.max_asks)}
              </div>
            </div>
            <button onClick={() => toggle(d)} style={chip(d.enabled)}>
              {d.enabled ? t.remindersTab.on : t.remindersTab.off}
            </button>
            <button onClick={() => { setDraft({ ...d }); setError(null) }} style={chip(false)}>{t.remindersTab.edit}</button>
            <button onClick={() => remove(d.id)} style={{ ...chip(false), color: 'var(--hb-danger, #c84a3a)' }}>{t.remindersTab.delete}</button>
          </div>
        ))}
      </div>

      {/* ── Editor ────────────────────────────────────────────────────────── */}
      {draft ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', padding: '0.9rem', border: '1px solid var(--hb-accent)', borderRadius: 8 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
            <div>
              <span style={label}>{t.remindersTab.idPermanent}</span>
              <input
                style={input} value={draft.id} placeholder="lustral_evening"
                onChange={e => setDraft({ ...draft, id: e.target.value.replace(/[^a-z0-9_]/gi, '_').toLowerCase() })}
                disabled={defs.some(d => d.id === draft.id)}
              />
            </div>
            <div>
              <span style={label}>{t.remindersTab.askedBy}</span>
              <select style={input} value={draft.agent} onChange={e => setDraft({ ...draft, agent: e.target.value })}>
                {AGENTS.map(a => <option key={a} value={a}>{a}</option>)}
              </select>
            </div>
          </div>

          <div>
            <span style={label}>{t.remindersTab.messageVerbatim}</span>
            <textarea
              style={{ ...input, minHeight: 70, resize: 'vertical' }}
              value={draft.text} placeholder="💊 150 mg Lustral aldın mı?"
              onChange={e => setDraft({ ...draft, text: e.target.value })}
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.75rem' }}>
            <div>
              <span style={label}>{t.remindersTab.time}</span>
              <input style={input} type="time" value={draft.at} onChange={e => setDraft({ ...draft, at: e.target.value })} />
            </div>
            <div>
              <span style={label}>{t.remindersTab.reaskEvery}</span>
              <input style={input} type="number" min={1} max={1440} value={draft.every_minutes}
                onChange={e => setDraft({ ...draft, every_minutes: Number(e.target.value) })} />
            </div>
            <div>
              <span style={label}>{t.remindersTab.giveUpAfter}</span>
              <input style={input} type="number" min={1} max={200} value={draft.max_asks}
                onChange={e => setDraft({ ...draft, max_asks: Number(e.target.value) })} />
            </div>
          </div>

          <div>
            <span style={label}>{t.remindersTab.days}</span>
            <div style={{ display: 'flex', gap: '0.35rem' }}>
              {DAY_LABELS.map((dl, i) => {
                const n = i + 1
                const set = daysToSet(draft.days)
                return (
                  <button
                    key={dl} style={chip(set.has(n))}
                    onClick={() => {
                      const next = new Set(set)
                      next.has(n) ? next.delete(n) : next.add(n)
                      if (next.size > 0) setDraft({ ...draft, days: setToDays(next) })
                    }}
                  >{dl}</button>
                )
              })}
            </div>
          </div>

          <div>
            <span style={label}>{t.remindersTab.answerButtons}</span>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
              {draft.options.map((o, i) => (
                <div key={i} style={{ display: 'flex', gap: '0.4rem' }}>
                  <input
                    style={input} value={o.label} placeholder="✅ Aldım"
                    onChange={e => {
                      const options = [...draft.options]
                      const text = e.target.value
                      // The value is what lands in the history; keep it derived
                      // and stable so a reworded label doesn't fork the record.
                      options[i] = { label: text, value: o.value || text.toLowerCase().replace(/\s+/g, '_').slice(0, 24) }
                      setDraft({ ...draft, options })
                    }}
                  />
                  {draft.options.length > 1 && (
                    <button style={chip(false)} onClick={() => setDraft({ ...draft, options: draft.options.filter((_, j) => j !== i) })}>×</button>
                  )}
                </div>
              ))}
              {draft.options.length < 6 && (
                <button style={{ ...chip(false), alignSelf: 'flex-start' }}
                  onClick={() => setDraft({ ...draft, options: [...draft.options, { label: '', value: '' }] })}>
                  {t.remindersTab.addButton}
                </button>
              )}
            </div>
          </div>

          {error && <div style={{ fontSize: '0.78rem', color: 'var(--hb-danger, #c84a3a)' }}>{error}</div>}

          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button style={{ ...chip(true), padding: '0.5rem 1rem' }} onClick={save} disabled={busy}>
              {busy ? t.remindersTab.saving : t.remindersTab.save}
            </button>
            <button style={{ ...chip(false), padding: '0.5rem 1rem' }} onClick={() => { setDraft(null); setError(null) }}>
              {t.remindersTab.cancel}
            </button>
          </div>
        </div>
      ) : (
        <button style={{ ...chip(true), alignSelf: 'flex-start', padding: '0.5rem 1rem' }}
          onClick={() => { setDraft(blank(t)); setError(null) }}>
          {t.remindersTab.newReminder}
        </button>
      )}

      {/* ── History ───────────────────────────────────────────────────────── */}
      {history.length > 0 && (
        <div>
          <span style={label}>{t.remindersTab.recentHistory}</span>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
            {history.map((h, i) => (
              <div key={i} style={{ display: 'flex', gap: '0.6rem', fontSize: '0.75rem', color: 'var(--hb-text-dim)' }}>
                <span style={{ minWidth: 82, color: 'var(--hb-text-faint)' }}>{h.day}</span>
                <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.reminder_id}</span>
                <span style={{ color: h.status === 'answered' ? 'var(--hb-accent-bright)' : 'var(--hb-amber, #c8a13a)' }}>
                  {h.status === 'answered' ? h.answer : t.remindersTab.noAnswer}
                </span>
                <span style={{ minWidth: 52, textAlign: 'right', color: 'var(--hb-text-faint)' }}>{h.asks}×</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
