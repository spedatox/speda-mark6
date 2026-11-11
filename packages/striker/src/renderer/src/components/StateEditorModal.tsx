import React, { useRef, useState } from 'react'
import { memoryFileLabel, saveMemoryState } from '../lib/api'
import type { IaconStateFields, MemoryFileInfo } from '../lib/api'
import type { AppConfig } from '../lib/types'

const statuses: Record<IaconStateFields['status'], string> = {
  active: 'Active', waiting: 'Waiting', planned: 'Planned',
  completed: 'Completed', cancelled: 'Cancelled', superseded: 'Superseded',
}

function localDate(days = 0): string {
  const date = new Date()
  date.setDate(date.getDate() + days)
  return new Date(date.valueOf() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
}

const fieldStyle: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', padding: '8px 10px', borderRadius: 8,
  border: '1px solid var(--hb-border, #384858)', background: 'var(--hb-surface, #101b25)',
  color: 'var(--hb-text, #edf4fa)', font: 'inherit',
}
const labelStyle: React.CSSProperties = { display: 'grid', gap: 5 }

export default function StateEditorModal({ config, file, onClose, onSaved, onHistory }: {
  config: AppConfig
  file?: MemoryFileInfo
  onClose: () => void
  onSaved: (file: MemoryFileInfo) => void
  onHistory?: () => void
}) {
  const [title, setTitle] = useState(file ? memoryFileLabel(file) : '')
  const [state, setState] = useState<IaconStateFields>(() => file ? JSON.parse(file.content) : {
    summary: '', status: 'active', salience: 'normal', verified_on: localDate(),
    review_on: localDate(7), starts_on: null, ends_on: null, closed_on: null, question: '',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const request = useRef<{ body: string; id: string } | null>(null)
  const terminal = ['completed', 'cancelled', 'superseded'].includes(state.status)

  function change<K extends keyof IaconStateFields>(key: K, value: IaconStateFields[K]) {
    setState(previous => ({ ...previous, [key]: value }))
  }

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (saving) return
    const payload = { ...state, summary: state.summary.trim(), question: state.question.trim(),
      closed_on: terminal ? state.closed_on || localDate() : null }
    if (!title.trim() || !payload.summary || !payload.question) {
      setError('Enter a title, summary, and follow-up question.')
      return
    }
    const body = JSON.stringify([title.trim(), payload])
    if (request.current?.body !== body) request.current = { body, id: crypto.randomUUID() }
    setSaving(true)
    setError('')
    try {
      onSaved(await saveMemoryState(config, title.trim(), payload, request.current.id, file))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the state.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 10000, display: 'grid', placeItems: 'center',
      background: 'rgba(2, 4, 8, 0.76)', padding: 20 }}>
      <form role="dialog" aria-modal="true" aria-label={file ? 'Edit state' : 'New state'} onSubmit={save}
        onKeyDown={event => {
          if (event.key === 'Escape' && !saving) { event.stopPropagation(); onClose() }
        }}
        style={{ width: 'min(100%, 600px)', maxHeight: '90vh', overflowY: 'auto', padding: 22,
          background: 'var(--hb-surface, #101b25)', color: 'var(--hb-text, #edf4fa)',
          border: '1px solid var(--hb-border, #384858)', borderRadius: 14, boxSizing: 'border-box' }}>
        <h2 style={{ margin: '0 0 16px' }}>{file ? 'Edit state' : 'New state'}</h2>
        <fieldset disabled={saving} style={{ display: 'grid', gap: 12, minWidth: 0, border: 0, margin: 0, padding: 0 }}>
          <label style={labelStyle}>Title
            <input autoFocus required maxLength={256} style={fieldStyle} value={title}
              onChange={event => setTitle(event.target.value)} />
          </label>
          <label style={labelStyle}>Summary
            <textarea required maxLength={4000} rows={3} style={fieldStyle} value={state.summary}
              onChange={event => change('summary', event.target.value)} />
          </label>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
            <label style={labelStyle}>Status
              <select style={fieldStyle} value={state.status}
                onChange={event => change('status', event.target.value as IaconStateFields['status'])}>
                {Object.entries(statuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label style={labelStyle}>Priority
              <select style={fieldStyle} value={state.salience}
                onChange={event => change('salience', event.target.value as IaconStateFields['salience'])}>
                <option value="high">High</option><option value="normal">Normal</option><option value="low">Low</option>
              </select>
            </label>
            <label style={labelStyle}>Last confirmed
              <input type="date" required style={fieldStyle} value={state.verified_on}
                onChange={event => change('verified_on', event.target.value)} />
            </label>
            <label style={labelStyle}>Review date
              <input type="date" required min={state.verified_on} style={fieldStyle} value={state.review_on}
                onChange={event => change('review_on', event.target.value)} />
            </label>
            <label style={labelStyle}>Start date
              <input type="date" style={fieldStyle} value={state.starts_on ?? ''}
                onChange={event => change('starts_on', event.target.value || null)} />
            </label>
            <label style={labelStyle}>Expected end
              <input type="date" min={state.starts_on ?? undefined} style={fieldStyle} value={state.ends_on ?? ''}
                onChange={event => change('ends_on', event.target.value || null)} />
            </label>
            {terminal && <label style={labelStyle}>Closed on
              <input type="date" required style={fieldStyle} value={state.closed_on || localDate()}
                onChange={event => change('closed_on', event.target.value || null)} />
            </label>}
          </div>
          <label style={labelStyle}>Orion’s follow-up question
            <textarea required maxLength={500} rows={2} style={fieldStyle} value={state.question}
              onChange={event => change('question', event.target.value)} />
          </label>
          {error && <div role="alert" style={{ color: 'var(--hb-red, #ff8a8a)' }}>{error}</div>}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            {onHistory && <button type="button" className="hb-btn" onClick={onHistory}>File & history</button>}
            <button type="button" className="hb-btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="hb-btn hb-btn-tint">{saving ? 'Saving…' : 'Save state'}</button>
          </div>
        </fieldset>
      </form>
    </div>
  )
}
