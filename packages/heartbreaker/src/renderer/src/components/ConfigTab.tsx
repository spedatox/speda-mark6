import { useEffect, useMemo, useState } from 'react'
import { getConfig, saveConfig } from '../lib/api'
import type { AppConfig } from '../lib/types'
import type { ConfigFieldInfo, ConfigGroupInfo, ConfigSaveResult } from '../lib/api'
import GlassSelect from './GlassSelect'

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
    const g = await getConfig(config)
    setGroups(g)
    // Open the first group by default so the panel isn't a wall of collapsed rows.
    setOpen(o => (Object.keys(o).length ? o : g.length ? { [g[0].id]: true } : {}))
    setLoading(false)
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
    return <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', fontFamily: MONO }}>Loading configuration…</p>
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', paddingBottom: '4.5rem' }}>
      <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', lineHeight: 1.55, margin: 0 }}>
        Everything the backend can be configured with — API keys, bot tokens, endpoints and flags.
        Values are stored in a managed override file that wins over the checked-in <code style={{ fontFamily: MONO }}>.env</code>.
        A <span style={{ color: 'var(--hb-amber)' }}>restart-required</span> field is saved now and takes effect on the next backend restart.
      </p>

      {/* Search */}
      <input
        value={query}
        onChange={e => setQuery(e.target.value)}
        placeholder="Search settings (e.g. telegram, openai, n8n)…"
        style={{
          width: '100%', background: 'rgba(10, 22, 30, 0.55)',
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
                <span style={{
                  flexShrink: 0, fontSize: '0.6rem', fontFamily: MONO, color: 'var(--hb-amber)',
                  border: '1px solid rgba(242,183,92,0.5)', padding: '1px 5px',
                }}>
                  {groupDirty} edited
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
              {result.applied_live.length > 0 && <span style={{ color: 'var(--hb-green)' }}>✓ {result.applied_live.length} applied live. </span>}
              {result.restart_required.length > 0 && <span style={{ color: 'var(--hb-amber)' }}>↻ {result.restart_required.length} need a restart. </span>}
              {result.rejected.length > 0 && <span style={{ color: 'var(--hb-red)' }}>✕ {result.rejected.length} rejected.</span>}
            </span>
          ) : (
            <span style={{ fontSize: '0.72rem', fontFamily: MONO, color: 'var(--text-muted)' }}>
              {dirtyKeys.length ? `${dirtyKeys.length} unsaved change${dirtyKeys.length > 1 ? 's' : ''}` : 'No changes'}
            </span>
          )}
        </div>
        {dirtyKeys.length > 0 && (
          <button
            onClick={() => { setEdits({}); setResult(null) }}
            className="hb-btn"
            style={{ padding: '0.45rem 0.85rem', fontSize: '0.78rem' }}
          >
            Discard
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
          {saving ? 'Saving…' : 'Save changes'}
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
  const labelRow = (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem', marginBottom: '0.3rem' }}>
      <label style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-primary)' }}>{f.label}</label>
      {f.requires_restart && (
        <span title="Takes effect after a backend restart" style={{ fontSize: '0.58rem', fontFamily: MONO, color: 'var(--hb-amber)', letterSpacing: '0.06em' }}>
          RESTART
        </span>
      )}
      {dirty && <span style={{ fontSize: '0.58rem', fontFamily: MONO, color: 'var(--hb-cyan-bright)' }}>● edited</span>}
      <span style={{ flex: 1 }} />
      {dirty && (
        <button onClick={onReset} title="Revert this field"
          style={{ border: 'none', background: 'transparent', color: 'var(--text-muted)', cursor: 'pointer', fontSize: '0.68rem', fontFamily: MONO }}>
          revert
        </button>
      )}
    </div>
  )

  const inputStyle: React.CSSProperties = {
    width: '100%', background: 'rgba(10, 22, 30, 0.55)',
    boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.35)',
    border: `1px solid ${dirty ? 'rgba(var(--hb-cyan-bright-rgb),0.5)' : 'var(--hb-edge)'}`,
    padding: '0.5rem 0.65rem', color: 'var(--text-primary)',
    fontSize: '0.84rem', fontFamily: f.secret ? MONO : 'inherit',
    outline: 'none', transition: 'border-color 0.15s',
  }

  let control: React.ReactNode

  if (f.type === 'bool') {
    const current = dirty ? Boolean(edit) : Boolean(f.value)
    control = (
      <button
        onClick={() => onChange(!current)}
        title={current ? 'On — click to turn off' : 'Off — click to turn on'}
        style={{
          width: 42, height: 24, borderRadius: 999, border: 'none', position: 'relative', cursor: 'pointer',
          background: current ? 'rgba(var(--hb-accent-rgb),0.55)' : 'rgba(var(--hb-accent-rgb),0.2)',
          boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.3)',
        }}
      >
        <span style={{
          position: 'absolute', top: 3, left: current ? 21 : 3, width: 18, height: 18, borderRadius: '50%',
          background: 'rgba(255,255,255,0.85)', boxShadow: '0 1px 3px rgba(0,0,0,0.45)', transition: 'left 0.15s',
        }} />
      </button>
    )
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
          placeholder={f.is_set ? `stored ${f.hint || '••••'} — type to replace` : (f.placeholder || 'not set')}
          style={{ ...inputStyle, flex: 1 }}
          autoComplete="off"
          spellCheck={false}
        />
        <button onClick={onReveal} className="hb-btn" title={revealed ? 'Hide' : 'Show what you typed'}
          style={{ padding: '0 0.6rem', fontSize: '0.72rem', flexShrink: 0 }}>
          {revealed ? 'Hide' : 'Show'}
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
        <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', margin: '0.3rem 0 0', lineHeight: 1.45 }}>
          {f.help}
          {f.secret && f.is_set && !dirty && (
            <button onClick={() => onChange('')} title="Clear this stored secret"
              style={{ marginLeft: '0.5rem', border: 'none', background: 'transparent', color: 'var(--hb-red)', cursor: 'pointer', fontSize: '0.7rem', fontFamily: MONO }}>
              clear stored
            </button>
          )}
        </p>
      )}
    </div>
  )
}
