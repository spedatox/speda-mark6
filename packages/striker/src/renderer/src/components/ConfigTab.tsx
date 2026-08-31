// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useMemo, useState } from 'react'
import { getConfig, saveConfig, getMemorySources, setMemorySource } from '../lib/api'
import type { AppConfig } from '../lib/types'
import type { ConfigFieldInfo, ConfigGroupInfo, ConfigSaveResult, MemorySources } from '../lib/api'
import GlassSelect from './GlassSelect'
import { Switch } from './settingsUI'
import { Skeleton, SkeletonText, SkeletonList } from './Skeleton'
import { useT } from '../lib/i18n'

/**
 * ConfigTab — the full backend configuration surface: every API key, token,
 * feature flag, and endpoint the owner can set, grouped and editable in-app.
 * Reads GET /config (secrets masked to a hint), tracks only DIRTY fields, and
 * PUTs the delta. Non-secret values arrive pre-filled; secrets show whether one
 * is stored and are overwritten only if the owner types a new one (empty = clear).
 */

const MONO = 'var(--font-mono)'
type EditVal = string | number | boolean

export default function ConfigTab({ config }: { config: AppConfig }) {
  const t = useT()
  const [groups, setGroups] = useState<ConfigGroupInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [edits, setEdits] = useState<Record<string, EditVal>>({})
  const [reveal, setReveal] = useState<Record<string, boolean>>({})
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<ConfigSaveResult | null>(null)
  const [query, setQuery] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      const g = await getConfig(config)
      setGroups(g)
      // Open the first group by default so the panel isn't a wall of collapsed rows.
      setOpen(o => (Object.keys(o).length ? o : g.length ? { [g[0].id]: true } : {}))
    } finally {
      // Always clear — an unreachable backend must not skeleton this pane forever.
      setLoading(false)
    }
  }
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const dirtyKeys = useMemo(() => Object.keys(edits), [edits])
  const setEdit = (key: string, v: EditVal) => setEdits(e => ({ ...e, [key]: v }))
  const clearEdit = (key: string) =>
    setEdits(e => { const n = { ...e }; delete n[key]; return n })

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return groups
    return groups
      .map(g => ({
        ...g,
        fields: g.fields.filter(
          f => f.label.toLowerCase().includes(q) || f.key.toLowerCase().includes(q) || g.label.toLowerCase().includes(q),
        ),
      }))
      .filter(g => g.fields.length > 0)
  }, [groups, query])

  const save = async () => {
    if (!dirtyKeys.length || saving) return
    setSaving(true)
    setResult(null)
    try {
      const r = await saveConfig(config, edits)
      setResult(r)
      setEdits({})
      await load()
    } catch (e) {
      setResult({ applied_live: [], restart_required: [], rejected: [String(e)] })
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', paddingBottom: '4.5rem' }}>
        <SkeletonText lines={2} lastWidth="55%" />
        <Skeleton height={44} />
        <div className="hb-skeleton-group" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {[220, 160, 190].map((w, i) => (
            <div key={i} style={{ border: '1px solid var(--border)', background: 'rgba(255,255,255,0.02)', padding: '0.85rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', ['--hb-skeleton-delay' as string]: `${i * 0.07}s` }}>
                <Skeleton width={11} height={11} radius={3} />
                <Skeleton width={w} height={13} />
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', paddingBottom: '4.5rem' }}>
      <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', lineHeight: 1.55, margin: 0 }}>
        {t.configTab.introPre} <code style={{ fontFamily: MONO }}>.env</code>{t.configTab.introMid}{' '}
        <span style={{ color: 'var(--hb-amber)' }}>{t.configTab.restartRequiredLabel}</span> {t.configTab.introPost}
      </p>

      <SourceOfTruthPanel config={config} />

      {/* Search */}
      <input
        value={query}
        onChange={e => setQuery(e.target.value)}
        placeholder={t.configTab.searchPlaceholder}
        style={{
          width: '100%', background: 'var(--glass-fill)',
          boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.35)',
          border: '1px solid var(--hb-edge)', padding: '0.55rem 0.7rem',
          color: 'var(--text-primary)', fontSize: '0.84rem', fontFamily: 'inherit',
          outline: 'none',
        }}
      />

      {filtered.map(g => {
        const isOpen = !!open[g.id] || !!query
        const groupDirty = g.fields.filter(f => f.key in edits).length
        return (
          <div key={g.id} style={{ border: '1px solid var(--border)', background: 'rgba(255,255,255,0.02)' }}>
            <button
              onClick={() => setOpen(o => ({ ...o, [g.id]: !o[g.id] }))}
              style={{
                width: '100%', display: 'flex', alignItems: 'center', gap: '0.6rem',
                padding: '0.7rem 0.85rem', border: 'none', background: 'transparent',
                cursor: 'pointer', textAlign: 'left',
              }}
            >
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                style={{ color: 'var(--hb-cyan)', flexShrink: 0, transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }}>
                <polyline points="9 18 15 12 9 6" />
              </svg>
              <span style={{ flex: 1 }}>
                <span style={{ fontSize: '0.86rem', fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '0.02em' }}>
                  {g.label}
                </span>
                <span style={{ display: 'block', fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 2, lineHeight: 1.4 }}>
                  {g.blurb}
                </span>
              </span>
              {groupDirty > 0 && (
                <span className="glass-round" style={{
                  flexShrink: 0, fontSize: '0.78rem', color: 'var(--hb-amber-bright)',
                  background: 'rgba(217,156,68,0.1)',
                  border: '1px solid rgba(217,156,68,0.32)', padding: '2px 10px',
                }}>
                  {t.configTab.groupEdited(groupDirty)}
                </span>
              )}
            </button>

            {isOpen && (
              <div style={{ padding: '0.25rem 0.85rem 0.9rem', display: 'flex', flexDirection: 'column', gap: '0.9rem' }}>
                {g.fields.map(f => (
                  <Field
                    key={f.key}
                    f={f}
                    edit={edits[f.key]}
                    dirty={f.key in edits}
                    revealed={!!reveal[f.key]}
                    onReveal={() => setReveal(r => ({ ...r, [f.key]: !r[f.key] }))}
                    onChange={v => setEdit(f.key, v)}
                    onReset={() => clearEdit(f.key)}
                  />
                ))}
              </div>
            )}
          </div>
        )
      })}

      {/* Sticky save bar — sticks to the bottom of the scroll column, bleeding
          to its padding edges. */}
      <div style={{
        position: 'sticky', bottom: '-1.5rem',
        margin: '0 -1.25rem -1.5rem', padding: '0.7rem 1.25rem',
        borderTop: '1px solid var(--hb-line)',
        background: 'linear-gradient(180deg, transparent, rgba(6,14,20,0.92) 45%)',
        display: 'flex', alignItems: 'center', gap: '0.85rem',
        backdropFilter: 'blur(4px)',
      }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          {result ? (
            <span style={{ fontSize: '0.72rem', fontFamily: MONO, color: 'var(--text-secondary)' }}>
              {result.applied_live.length > 0 && <span style={{ color: 'var(--hb-green)' }}>{t.configTab.appliedLive(result.applied_live.length)}</span>}
              {result.restart_required.length > 0 && <span style={{ color: 'var(--hb-amber)' }}>{t.configTab.needsRestart(result.restart_required.length)}</span>}
              {result.rejected.length > 0 && <span style={{ color: 'var(--hb-red)' }}>{t.configTab.rejected(result.rejected.length)}</span>}
            </span>
          ) : (
            <span style={{ fontSize: '0.72rem', fontFamily: MONO, color: 'var(--text-muted)' }}>
              {dirtyKeys.length ? t.configTab.unsavedChanges(dirtyKeys.length) : t.configTab.noChanges}
            </span>
          )}
        </div>
        {dirtyKeys.length > 0 && (
          <button
            onClick={() => { setEdits({}); setResult(null) }}
            className="hb-btn"
            style={{ padding: '0.45rem 0.85rem', fontSize: '0.78rem' }}
          >
            {t.configTab.discard}
          </button>
        )}
        <button
          onClick={save}
          disabled={!dirtyKeys.length || saving}
          className="hb-btn hb-btn-tint"
          style={{
            padding: '0.5rem 1.2rem', color: 'var(--hb-cyan-bright)',
            fontSize: '0.82rem', fontWeight: 700, letterSpacing: '0.04em',
            opacity: dirtyKeys.length && !saving ? 1 : 0.5,
            cursor: dirtyKeys.length && !saving ? 'pointer' : 'not-allowed',
          }}
        >
          {saving ? t.configTab.saving : t.configTab.saveChanges}
        </button>
      </div>
    </div>
  )
}

function Field({ f, edit, dirty, revealed, onReveal, onChange, onReset }: {
  f: ConfigFieldInfo
  edit: EditVal | undefined
  dirty: boolean
  revealed: boolean
  onReveal: () => void
  onChange: (v: EditVal) => void
  onReset: () => void
}) {
  const t = useT()
  const labelRow = (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem', marginBottom: '0.3rem' }}>
      <label style={{ fontSize: '0.9375rem', color: 'var(--hb-text)' }}>{f.label}</label>
      {f.requires_restart && (
        <span
          title={t.configTab.restartEffectTitle}
          className="glass-round"
          style={{
            fontSize: '0.72rem', color: 'var(--hb-amber-bright)',
            background: 'rgba(217,156,68,0.1)', border: '1px solid rgba(217,156,68,0.28)',
            padding: '1px 9px',
          }}
        >
          {t.configTab.restartChip}
        </span>
      )}
      {dirty && <span style={{ fontSize: '0.78rem', color: 'var(--hb-cyan-bright)' }}>{t.configTab.editedDot}</span>}
      <span style={{ flex: 1 }} />
      {dirty && (
        <button onClick={onReset} title={t.configTab.revertFieldTitle}
          style={{ border: 'none', background: 'transparent', color: 'var(--hb-text-faint)', cursor: 'pointer', fontSize: '0.8125rem' }}>
          {t.configTab.revert}
        </button>
      )}
    </div>
  )

  // Same 44px field the rest of the settings pane uses; the corner comes from
  // the `.hb-settings` scope, which outranks the theme's blanket reset.
  const inputStyle: React.CSSProperties = {
    width: '100%', height: 44,
    background: 'rgba(255,255,255,0.03)',
    border: `1px solid ${dirty ? 'rgba(var(--hb-accent-rgb),0.45)' : 'rgba(255,255,255,0.09)'}`,
    padding: '0 16px', color: 'var(--hb-text)',
    fontSize: '0.9375rem', fontFamily: f.secret ? MONO : 'var(--font-read)',
    outline: 'none', transition: 'border-color 0.15s',
  }

  let control: React.ReactNode

  if (f.type === 'bool') {
    const current = dirty ? Boolean(edit) : Boolean(f.value)
    control = <Switch on={current} onChange={onChange}
      title={current ? t.configTab.onClickToTurnOff : t.configTab.offClickToTurnOn} />
  } else if (f.type === 'select') {
    const current = String(dirty ? edit : (f.value ?? f.options[0] ?? ''))
    control = (
      <div style={{ maxWidth: 240 }}>
        <GlassSelect
          value={current}
          options={f.options.map(o => ({ value: o, label: o }))}
          onChange={v => onChange(v)}
          tint="var(--hb-cyan-bright)"
          active={dirty}
          large
        />
      </div>
    )
  } else if (f.secret) {
    control = (
      <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'stretch' }}>
        <input
          type={revealed ? 'text' : 'password'}
          value={dirty ? String(edit) : ''}
          onChange={e => onChange(e.target.value)}
          placeholder={f.is_set ? t.configTab.storedTypeToReplace(f.hint || '••••') : (f.placeholder || t.configTab.notSet)}
          style={{ ...inputStyle, flex: 1 }}
          autoComplete="off"
          spellCheck={false}
        />
        <button onClick={onReveal} className="hb-tile" title={revealed ? t.configTab.hide : t.configTab.showWhatYouTyped}
          style={{
            padding: '0 14px', height: 44, flexShrink: 0, cursor: 'pointer',
            fontFamily: 'var(--font-read)', fontSize: '0.845rem',
            border: '1px solid rgba(255,255,255,0.09)',
            background: 'rgba(255,255,255,0.03)', color: 'var(--hb-text-dim)',
          }}>
          {revealed ? t.configTab.hide : t.common.show}
        </button>
      </div>
    )
  } else {
    const current = String(dirty ? edit : (f.value ?? ''))
    control = (
      <input
        type={f.type === 'int' ? 'number' : 'text'}
        value={current}
        onChange={e => onChange(f.type === 'int' ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value)}
        placeholder={f.placeholder}
        style={inputStyle}
        spellCheck={false}
      />
    )
  }

  return (
    <div>
      {labelRow}
      {control}
      {f.help && (
        <p style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', margin: '6px 0 0', lineHeight: 1.5 }}>
          {f.help}
          {f.secret && f.is_set && !dirty && (
            <button onClick={() => onChange('')} title={t.configTab.clearSecretTitle}
              style={{ marginLeft: 8, border: 'none', background: 'transparent', color: '#e5897c', cursor: 'pointer', fontSize: '0.8125rem' }}>
              {t.configTab.clearStored}
            </button>
          )}
        </p>
      )}
    </div>
  )
}

/**
 * SourceOfTruthPanel — per-agent source-of-truth memory file. Each agent's
 * chosen /memories/*.md is preloaded into its prompt (read) and is where it
 * writes its domain data. The owner picks an existing file per agent here.
 */
function SourceOfTruthPanel({ config }: { config: AppConfig }) {
  const [data, setData] = useState<MemorySources | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)

  const load = async () => {
    try {
      setData(await getMemorySources(config))
    } finally {
      // Always clear — an unreachable backend must not skeleton this forever.
      setLoaded(true)
    }
  }
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const assign = async (agentId: string, path: string) => {
    setBusy(agentId)
    try {
      await setMemorySource(config, agentId, path || null)
      await load()
    } catch { /* surfaced by reload */ }
    finally { setBusy(null) }
  }

  if (!loaded) {
    return (
      <div style={{ border: '1px solid var(--border)', background: 'rgba(255,255,255,0.02)', padding: '0.85rem' }}>
        <SkeletonList rows={2} mark={false} />
      </div>
    )
  }
  if (!data || data.agents.length === 0) return null
  const fileName = (p: string) => p.replace('/memories/', '')

  return (
    <div style={{ border: '1px solid var(--border)', background: 'rgba(255,255,255,0.02)' }}>
      <div style={{ padding: '0.7rem 0.85rem 0.2rem' }}>
        <span style={{ fontSize: '0.86rem', fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '0.02em' }}>
          Agent Source of Truth
        </span>
        <span style={{ display: 'block', fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 2, lineHeight: 1.4 }}>
          The one <code style={{ fontFamily: MONO }}>/memories</code> file each agent reads its domain data from and writes every update back to.
          Sentinel → finance, Atomix → sessions. Pick any existing memory file.
        </span>
      </div>
      <div style={{ padding: '0.4rem 0.85rem 0.85rem', display: 'flex', flexDirection: 'column', gap: '0.55rem' }}>
        {data.agents.map(a => {
          const options = [
            { value: '', label: a.default ? `— default (${fileName(a.default)}) —` : '— none —' },
            ...data.files.map(f => ({ value: f, label: fileName(f) })),
          ]
          // Reflect the active file if it's one we can list; else the default/none option.
          const current = a.source && data.files.includes(a.source) ? a.source : ''
          return (
            <div key={a.agent_id} style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
              <div style={{ width: 130, flexShrink: 0 }}>
                <div style={{ fontSize: '0.9375rem', color: 'var(--hb-text)' }}>{a.name}</div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)' }}>
                  {a.source ? fileName(a.source) : '—'}
                </div>
              </div>
              <div style={{ flex: 1, maxWidth: 260, opacity: busy === a.agent_id ? 0.5 : 1 }}>
                <GlassSelect
                  value={current}
                  options={options}
                  onChange={v => assign(a.agent_id, v)}
                  tint="var(--hb-cyan-bright)"
                  active={!!a.source}
                  large
                />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
