// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useCallback, useEffect, useState } from 'react'
import {
  listSkyfallProjects, saveSkyfallProject, deleteSkyfallProject, armSkyfall,
} from '../lib/api'
import type { SkyfallProject } from '../lib/api'
import type { AppConfig } from '../lib/types'
import { PillBtn } from './settingsUI'
import { useT } from '../lib/i18n'

/**
 * SKYFALL — the project list and its editor, inside the Protocols pane.
 *
 * Two jobs, and they are deliberately different in weight. Picking a project to
 * ARM is one click on a big tile, styled after the agent switcher because that
 * is the gesture the owner already knows. CONFIGURING one is a form you have to
 * open, because writing the target is the act that gives the countdown its
 * meaning — an agent cannot do it, and it should not feel like an afterthought
 * on the way to the launch button either.
 *
 * Arming from here does not open the countdown itself. It fetches the arming
 * payload and dispatches the SAME `speda:skyfall-arm` event Speda's tool
 * produces, so Layout's one listener opens the one screen. Building a second
 * path — a countdown owned by this pane — is exactly how a protocol ends up
 * with a route that skips its own safety.
 *
 * HEADER VALUES ARE NEVER HERE. The server sends header NAMES mapped to a mask.
 * The form shows the mask, and sending it straight back means "leave this one
 * alone", so changing a description does not silently blank an API token the
 * owner never retyped.
 */
export default function SkyfallProjects({ config }: { config: AppConfig }) {
  const t = useT()
  const [projects, setProjects] = useState<SkyfallProject[] | null>(null)
  const [editing, setEditing] = useState<Partial<SkyfallProject> | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setProjects(await listSkyfallProjects(config))
  }, [config])

  useEffect(() => { refresh() }, [refresh])

  const arm = async (project: SkyfallProject) => {
    const payload = await armSkyfall(config, project.id)
    if (!payload) { setError(t.skyfall.armFailed); return }
    window.dispatchEvent(new CustomEvent('speda:skyfall-arm', { detail: payload }))
  }

  const save = async () => {
    if (!editing) return
    setError(null)
    const res = await saveSkyfallProject(config, editing)
    // The server owns the rules and answers in words; re-wording them here would
    // put a second, drifting copy of the validation in the UI.
    if (!res.ok) { setError(res.error || t.skyfall.saveFailed); return }
    setEditing(null)
    await refresh()
  }

  const remove = async (project: SkyfallProject) => {
    if (!window.confirm(t.skyfall.confirmDelete(project.name))) return
    await deleteSkyfallProject(config, project.id)
    await refresh()
  }

  if (editing) {
    return (
      <ProjectForm
        draft={editing}
        error={error}
        onChange={setEditing}
        onCancel={() => { setEditing(null); setError(null) }}
        onSave={save}
      />
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {projects === null ? (
        <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)' }}>
          {t.skyfall.loading}
        </div>
      ) : projects.length === 0 ? (
        <div style={{
          padding: '18px 16px', textAlign: 'center',
          background: 'rgba(255,255,255,0.03)', border: '1px dashed rgba(255,255,255,0.12)',
          fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.7,
        }}>
          {t.skyfall.emptyState}
        </div>
      ) : (
        projects.map(p => (
          <div
            key={p.id}
            className="hb-tile"
            style={{
              display: 'flex', alignItems: 'center', gap: 14, padding: '14px 16px',
              background: 'linear-gradient(90deg, rgba(216,72,60,0.07) 0%, rgba(255,255,255,0.03) 46%)',
              border: '1px solid rgba(216,72,60,0.22)',
            }}
          >
            <div style={{
              width: 3, alignSelf: 'stretch', background: '#d8483c',
              boxShadow: '0 0 12px rgba(216,72,60,0.7)', flexShrink: 0,
            }} />
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontSize: '0.9375rem', color: 'var(--hb-text)' }}>{p.name}</div>
              {p.description && (
                <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', marginTop: 2 }}>
                  {p.description}
                </div>
              )}
              <div style={{
                marginTop: 5, fontFamily: 'var(--font-mono)', fontSize: '0.65rem',
                color: 'var(--hb-text-dim)', wordBreak: 'break-all',
              }}>
                {p.method} {p.url} · {t.skyfall.seconds(p.countdown_seconds)}
                {Object.keys(p.headers || {}).length > 0 && ` · ${t.skyfall.headerCount(Object.keys(p.headers).length)}`}
                {p.has_body && ` · ${t.skyfall.hasBody}`}
              </div>
              {p.last_fired_at && (
                <div style={{ marginTop: 3, fontFamily: 'var(--font-mono)', fontSize: '0.62rem', color: 'var(--hb-text-faint)' }}>
                  {t.skyfall.lastFired(p.last_result || p.last_fired_at)}
                </div>
              )}
            </div>
            <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
              <PillBtn tone="danger" onClick={() => arm(p)} title={t.skyfall.armTitle}>
                {t.skyfall.arm}
              </PillBtn>
              <PillBtn onClick={() => setEditing(p)}>{t.skyfall.edit}</PillBtn>
              <PillBtn onClick={() => remove(p)}>{t.skyfall.delete}</PillBtn>
            </div>
          </div>
        ))
      )}

      {error && (
        <div style={{
          padding: '10px 14px', background: 'rgba(216,72,60,0.08)',
          border: '1px solid rgba(216,72,60,0.3)', color: '#e5897c',
          fontFamily: 'var(--font-mono)', fontSize: '0.68rem',
        }}>
          {error}
        </div>
      )}

      <div>
        <PillBtn onClick={() => setEditing({ method: 'POST', countdown_seconds: 10 })}>
          {t.skyfall.addProject}
        </PillBtn>
      </div>
    </div>
  )
}

const INPUT: React.CSSProperties = {
  width: '100%', padding: '9px 11px',
  background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)',
  color: 'var(--hb-text)', fontFamily: 'var(--font-read)', fontSize: '0.875rem',
  outline: 'none',
}

const MONO_INPUT: React.CSSProperties = {
  ...INPUT, fontFamily: 'var(--font-mono)', fontSize: '0.75rem',
}

function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode
}) {
  return (
    <div>
      <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-dim)', marginBottom: 6 }}>{label}</div>
      {hint && (
        <div style={{ fontSize: '0.75rem', color: 'var(--hb-text-faint)', marginBottom: 6, lineHeight: 1.5 }}>
          {hint}
        </div>
      )}
      {children}
    </div>
  )
}

/** The form. Headers are edited as raw `Name: value` lines rather than a row
 *  builder — one textarea round-trips a masked secret unchanged, and a row
 *  builder is where a "clear" button ends up wiping a token by accident. */
function ProjectForm({ draft, error, onChange, onCancel, onSave }: {
  draft: Partial<SkyfallProject>
  error: string | null
  onChange: (d: Partial<SkyfallProject>) => void
  onCancel: () => void
  onSave: () => void
}) {
  const t = useT()
  const set = (patch: Partial<SkyfallProject>) => onChange({ ...draft, ...patch })
  const headerText = Object.entries(draft.headers || {})
    .map(([k, v]) => `${k}: ${v}`).join('\n')

  const parseHeaders = (text: string) => {
    const out: Record<string, string> = {}
    for (const line of text.split('\n')) {
      const at = line.indexOf(':')
      if (at <= 0) continue
      const name = line.slice(0, at).trim()
      if (name) out[name] = line.slice(at + 1).trim()
    }
    set({ headers: out })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Field label={t.skyfall.fieldName}>
        <input style={INPUT} value={draft.name || ''} placeholder={t.skyfall.namePlaceholder}
               onChange={e => set({ name: e.target.value })} />
      </Field>

      <Field label={t.skyfall.fieldDescription} hint={t.skyfall.descriptionHint}>
        <input style={INPUT} value={draft.description || ''}
               onChange={e => set({ description: e.target.value })} />
      </Field>

      <div style={{ display: 'flex', gap: 10 }}>
        <div style={{ width: 120, flexShrink: 0 }}>
          <Field label={t.skyfall.fieldMethod}>
            <select style={INPUT} value={draft.method || 'POST'}
                    onChange={e => set({ method: e.target.value })}>
              {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map(m => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </Field>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Field label={t.skyfall.fieldUrl}>
            <input style={MONO_INPUT} value={draft.url || ''} placeholder="https://…"
                   onChange={e => set({ url: e.target.value })} />
          </Field>
        </div>
      </div>

      <Field label={t.skyfall.fieldCountdown} hint={t.skyfall.countdownHint}>
        <input style={{ ...INPUT, width: 120 }} type="number" min={3} max={300}
               value={draft.countdown_seconds ?? 10}
               onChange={e => set({ countdown_seconds: Number(e.target.value) })} />
      </Field>

      <Field label={t.skyfall.fieldBody} hint={t.skyfall.bodyHint}>
        <textarea style={{ ...MONO_INPUT, minHeight: 90, resize: 'vertical' }}
                  value={draft.body || ''} placeholder={'{\n  "key": "value"\n}'}
                  onChange={e => set({ body: e.target.value })} />
      </Field>

      <Field label={t.skyfall.fieldHeaders} hint={t.skyfall.headersHint}>
        <textarea style={{ ...MONO_INPUT, minHeight: 70, resize: 'vertical' }}
                  value={headerText} placeholder={'Authorization: Bearer …'}
                  onChange={e => parseHeaders(e.target.value)} />
      </Field>

      {error && (
        <div style={{
          padding: '10px 14px', background: 'rgba(216,72,60,0.08)',
          border: '1px solid rgba(216,72,60,0.3)', color: '#e5897c',
          fontFamily: 'var(--font-mono)', fontSize: '0.68rem', lineHeight: 1.6,
        }}>
          {error}
        </div>
      )}

      <div style={{ display: 'flex', gap: 10 }}>
        <PillBtn tone="accent" onClick={onSave}>{t.skyfall.save}</PillBtn>
        <PillBtn onClick={onCancel}>{t.skyfall.cancel}</PillBtn>
      </div>
    </div>
  )
}
