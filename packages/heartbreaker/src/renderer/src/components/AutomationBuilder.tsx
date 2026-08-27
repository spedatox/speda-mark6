/**
 * The automation builder — the owner's side of the automation module.
 *
 * Three things this screen is built to guarantee:
 *
 *  1. NO CRON, EVER. The owner picks a frequency, a time, and days; the backend
 *     compiles the cron and this side never sees or shows one. A schedule
 *     arrives here as STRUCTURE (`AutomationSchedule`) precisely so it can be
 *     rendered in whichever language the deck is set to — see
 *     `packages/igor/app/automations/schedule.py`.
 *  2. THE FORM CANNOT BUILD A BROKEN AUTOMATION. Every shape the backend
 *     refuses is unreachable here: a one-off cannot be given a weekly
 *     frequency, a proactive reminder cannot be saved with no answer buttons, a
 *     past date cannot be chosen. The backend still validates — this only makes
 *     the refusal rare.
 *  3. HIS WORDS ARE NEVER LOST. The instruction box holds what he typed. A
 *     background job rewrites it into an executable instruction; both halves
 *     stay visible and editable afterwards, and re-editing sends it back
 *     through the polisher.
 *
 * Rendered INLINE inside the Settings automations tab rather than as a nested
 * modal: a second overlay above the settings overlay fights it for the
 * backdrop and the escape key, and this pane already owns the whole width.
 */

import { useMemo, useState } from 'react'
import { useT } from '../lib/i18n'
import type { Dict } from '../lib/i18n/en'
import { PillBtn, SettingsField, SettingsSection, fieldStyle } from './settingsUI'
import type {
  AutomationAgent, AutomationDayFlag, AutomationDraft, AutomationFrequency,
  AutomationInfo, AutomationSchedule, AutomationTemplate,
} from '../lib/api'

/** ISO weekday numbers, the vocabulary the backend speaks. 1=Mon … 7=Sun. */
const WEEK: number[] = [1, 2, 3, 4, 5, 6, 7]

const TEMPLATES: AutomationTemplate[] = ['briefing', 'reminder', 'proactive_ask']

/**
 * Which frequencies each template may use. 'reminder' is schedule-agnostic —
 * a plain nudge is exactly as sensible fired once as it is every Monday, so it
 * gets every option. Briefing and proactive-ask stay recurring-only: a
 * one-off "briefing" isn't a briefing, and a one-off ask has nothing to nag
 * about a second time — see packages/igor/app/automations/templates.py.
 */
function allowedFrequencies(template: AutomationTemplate): AutomationFrequency[] {
  return template === 'reminder'
    ? ['once', 'daily', 'weekly', 'monthly']
    : ['daily', 'weekly', 'monthly']
}

/**
 * Render a schedule in the deck's language. The single formatter — the list and
 * the builder both call it, so a row and its editor can never describe the same
 * automation differently.
 */
export function describeSchedule(s: AutomationSchedule | null, t: Dict): string {
  if (!s) return ''
  const a = t.settingsAutomations
  const freq = a.freq[s.frequency] ?? s.frequency
  if (s.frequency === 'weekly' && s.days?.length) {
    const days = s.days.map(d => a.dayShort[d - 1]).join(', ')
    return `${freq} · ${days} · ${s.at}`
  }
  if (s.frequency === 'monthly') return `${freq} · ${a.dayOfMonth} ${s.dom} · ${s.at}`
  if (s.frequency === 'once') return `${freq} · ${s.date} · ${s.at}`
  return `${freq} · ${s.at}`
}

/** The weekday chip row. One component so the schedule's days and a day flag's
 *  days can never drift into looking or numbering differently. */
function WeekPicker({ value, onChange, labels }: {
  value: number[]; onChange: (v: number[]) => void; labels: string[]
}) {
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      {WEEK.map(d => {
        const on = value.includes(d)
        return (
          <button
            key={d}
            onClick={() => onChange(on ? value.filter(x => x !== d) : [...value, d].sort((p, q) => p - q))}
            className="glass-round"
            style={{
              height: 34, padding: '0 14px', cursor: 'pointer',
              fontFamily: 'var(--font-read)', fontSize: '0.845rem',
              background: on ? 'rgba(var(--hb-accent-rgb),0.14)' : 'var(--glass-sheen)',
              border: `1px solid ${on ? 'rgba(var(--hb-accent-rgb),0.34)' : 'rgba(255,255,255,0.1)'}`,
              color: on ? 'var(--hb-cyan-bright)' : 'var(--hb-text-dim)',
            }}
          >
            {labels[d - 1]}
          </button>
        )
      })}
    </div>
  )
}

/** Today as YYYY-MM-DD, for the date input's floor — a past date cannot fire. */
function todayISO(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function AutomationBuilder({ existing, agents, onCancel, onSave }: {
  /** Present when editing; absent when creating. */
  existing?: AutomationInfo
  agents: AutomationAgent[]
  onCancel: () => void
  onSave: (draft: AutomationDraft) => Promise<string | null>
}) {
  const t = useT()
  const a = t.settingsAutomations
  const editing = Boolean(existing)

  const [template, setTemplate] = useState<AutomationTemplate | null>(
    (existing?.template as AutomationTemplate) ?? null,
  )
  const [agentId, setAgentId] = useState(existing?.agent_id ?? 'speda')
  const [name, setName] = useState(existing?.name ?? '')
  const [frequency, setFrequency] = useState<AutomationFrequency>(
    existing?.schedule?.frequency ?? 'daily',
  )
  const [at, setAt] = useState(existing?.schedule?.at ?? '09:00')
  const [days, setDays] = useState<number[]>(existing?.schedule?.days ?? [1])
  const [dom, setDom] = useState<number>(existing?.schedule?.dom ?? 1)
  const [date, setDate] = useState(existing?.schedule?.date ?? todayISO())
  // The owner's own wording is what he edits — never the polished instruction,
  // which is the machine's rendering of it and would grow on every round-trip.
  const [instruction, setInstruction] = useState(
    existing?.instruction_raw ?? existing?.instruction ?? '',
  )
  const [options, setOptions] = useState<string[]>(existing?.options ?? ['', ''])
  const [everyMinutes, setEveryMinutes] = useState(existing?.every_minutes ?? 5)
  const [maxAsks, setMaxAsks] = useState(existing?.max_asks ?? 10)
  const [dayFlags, setDayFlags] = useState<AutomationDayFlag[]>(existing?.day_flags ?? [])

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const freqs = useMemo(
    () => (template ? allowedFrequencies(template) : []),
    [template],
  )

  /** Picking a template can invalidate the frequency — a one-off has only one. */
  function chooseTemplate(next: AutomationTemplate) {
    setTemplate(next)
    const allowed = allowedFrequencies(next)
    if (!allowed.includes(frequency)) setFrequency(allowed[0])
  }

  const cleanOptions = options.map(o => o.trim()).filter(Boolean)
  const canSave = Boolean(
    template && name.trim() && instruction.trim()
    && (frequency !== 'weekly' || days.length > 0)
    && (template !== 'proactive_ask' || cleanOptions.length > 0)
    && dayFlags.every(f => f.label.trim() && f.days.length > 0),
  )

  async function submit() {
    if (!template || !canSave) return
    setBusy(true)
    setError(null)
    const schedule: AutomationDraft['schedule'] = { frequency, at }
    if (frequency === 'weekly') schedule.days = days
    if (frequency === 'monthly') schedule.dom = dom
    if (frequency === 'once') schedule.date = date
    const draft: AutomationDraft = {
      agent_id: agentId, template, name: name.trim(), schedule,
      instruction: instruction.trim(),
    }
    if (template === 'proactive_ask') {
      draft.options = cleanOptions
      draft.every_minutes = everyMinutes
      draft.max_asks = maxAsks
    }
    // Always sent, including as [] — omitting the key on an edit would leave a
    // removed flag still living in the stored spec.
    draft.day_flags = dayFlags
      .map(f => ({ label: f.label.trim(), days: f.days }))
      .filter(f => f.label && f.days.length > 0)
    const err = await onSave(draft)
    setBusy(false)
    if (err) setError(err)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 22, maxWidth: 720 }}>
      <SettingsSection title={editing ? a.editTitle : a.newTitle} first />

      {/* Step 1 — the kind. In edit mode the choice is shown but locked: a
          briefing and a proactive ask fire in different output modes, so
          switching would silently rewrite the delivery mechanics of a live
          automation. Delete and rebuild is the honest path. */}
      <SettingsField label={a.stepType}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 10 }}>
          {TEMPLATES.map(tpl => {
            const on = template === tpl
            const label = { briefing: a.tplBriefing, reminder: a.tplOnce, proactive_ask: a.tplAsk }[tpl]
            const desc = { briefing: a.tplBriefingDesc, reminder: a.tplOnceDesc, proactive_ask: a.tplAskDesc }[tpl]
            return (
              <button
                key={tpl}
                onClick={() => !editing && chooseTemplate(tpl)}
                disabled={editing && !on}
                className="hb-tile"
                style={{
                  textAlign: 'left', padding: '13px 15px', cursor: editing ? 'default' : 'pointer',
                  background: on ? 'rgba(var(--hb-accent-rgb),0.1)' : 'rgba(255,255,255,0.03)',
                  border: `1px solid ${on ? 'rgba(var(--hb-accent-rgb),0.34)' : 'rgba(255,255,255,0.08)'}`,
                  color: 'var(--hb-text)',
                  opacity: editing && !on ? 0.32 : 1,
                }}
              >
                <div style={{ fontSize: '0.9375rem', color: on ? 'var(--hb-cyan-bright)' : 'var(--hb-text)' }}>
                  {label}
                </div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', marginTop: 4, lineHeight: 1.45 }}>
                  {desc}
                </div>
              </button>
            )
          })}
        </div>
      </SettingsField>

      {/* Everything below stays hidden until a kind is picked — the rest of the
          form does not mean anything without one. */}
      {template && (
        <>
          <SettingsField label={a.nameLabel}>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder={a.namePlaceholder}
              style={fieldStyle}
            />
          </SettingsField>

          <SettingsField label={a.stepAgent} hint={a.stepAgentHint}>
            <select value={agentId} onChange={e => setAgentId(e.target.value)} style={fieldStyle}>
              {agents.map(ag => (
                <option key={ag.agent_id} value={ag.agent_id}>
                  {ag.name} — {ag.domain}
                </option>
              ))}
            </select>
          </SettingsField>

          <SettingsSection title={a.stepWhen} />

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 200px' }}>
              <SettingsField label={a.frequency}>
                <select
                  value={frequency}
                  onChange={e => setFrequency(e.target.value as AutomationFrequency)}
                  disabled={freqs.length === 1}
                  style={{ ...fieldStyle, opacity: freqs.length === 1 ? 0.55 : 1 }}
                >
                  {freqs.map(f => <option key={f} value={f}>{a.freq[f]}</option>)}
                </select>
              </SettingsField>
            </div>
            <div style={{ flex: '0 1 160px' }}>
              <SettingsField label={a.time}>
                <input type="time" value={at} onChange={e => setAt(e.target.value)} style={fieldStyle} />
              </SettingsField>
            </div>
          </div>

          {frequency === 'weekly' && (
            <SettingsField label={a.weekdays}>
              <WeekPicker value={days} onChange={setDays} labels={a.dayShort} />
            </SettingsField>
          )}

          {frequency === 'monthly' && (
            <SettingsField
              label={a.dayOfMonth}
              hint={dom >= 29 ? a.shortMonthWarning : undefined}
            >
              <input
                type="number" min={1} max={31} value={dom}
                onChange={e => setDom(Math.min(31, Math.max(1, Number(e.target.value) || 1)))}
                style={{ ...fieldStyle, maxWidth: 160 }}
              />
            </SettingsField>
          )}

          {frequency === 'once' && (
            <SettingsField label={a.date}>
              {/* min=today: a date already gone compiles to a workflow that is
                  live, green, and can never fire. */}
              <input
                type="date" min={todayISO()} value={date}
                onChange={e => setDate(e.target.value)}
                style={{ ...fieldStyle, maxWidth: 220 }}
              />
            </SettingsField>
          )}

          <SettingsSection title={a.stepIntent} />

          <SettingsField label={a.stepIntent} hint={a.intentHint}>
            <textarea
              value={instruction}
              onChange={e => setInstruction(e.target.value)}
              placeholder={a.intentPlaceholder}
              rows={5}
              style={{ ...fieldStyle, height: 'auto', padding: '13px 16px', lineHeight: 1.55, resize: 'vertical' }}
            />
          </SettingsField>

          {/* What the polisher made of it, once it has run. Read-only: editing
              the machine's rendering instead of his own sentence would compound
              on every save. */}
          {editing && existing?.intent_status === 'polished' && existing.instruction
            && existing.instruction !== existing.instruction_raw && (
            <SettingsField label={a.instructionLabel}>
              <div style={{
                ...fieldStyle, height: 'auto', padding: '13px 16px',
                whiteSpace: 'pre-wrap', lineHeight: 1.55,
                fontSize: '0.845rem', color: 'var(--hb-text-faint)', maxHeight: 220, overflowY: 'auto',
              }}>
                {existing.instruction}
              </div>
            </SettingsField>
          )}

          {template === 'proactive_ask' && (
            <>
              <SettingsField label={a.answerButtons} hint={a.answerButtonsHint}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {options.map((opt, i) => (
                    <div key={i} style={{ display: 'flex', gap: 8 }}>
                      <input
                        value={opt}
                        onChange={e => setOptions(options.map((o, j) => (j === i ? e.target.value : o)))}
                        style={fieldStyle}
                      />
                      <PillBtn
                        tone="danger"
                        onClick={() => setOptions(options.filter((_, j) => j !== i))}
                      >
                        ×
                      </PillBtn>
                    </div>
                  ))}
                  <div>
                    <PillBtn onClick={() => setOptions([...options, ''])}>{a.addButton}</PillBtn>
                  </div>
                </div>
              </SettingsField>

              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <div style={{ flex: '1 1 180px' }}>
                  <SettingsField label={a.repeatEvery}>
                    <input
                      type="number" min={1} value={everyMinutes}
                      onChange={e => setEveryMinutes(Math.max(1, Number(e.target.value) || 1))}
                      style={fieldStyle}
                    />
                  </SettingsField>
                </div>
                <div style={{ flex: '1 1 180px' }}>
                  <SettingsField label={a.maxAsks}>
                    <input
                      type="number" min={1} value={maxAsks}
                      onChange={e => setMaxAsks(Math.max(1, Number(e.target.value) || 1))}
                      style={fieldStyle}
                    />
                  </SettingsField>
                </div>
              </div>
            </>
          )}

          <SettingsField label={a.dayFlags} hint={a.dayFlagsHint}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {dayFlags.map((flag, i) => (
                <div key={i} style={{
                  display: 'flex', flexDirection: 'column', gap: 8,
                  padding: '12px 14px',
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(255,255,255,0.07)',
                }}>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <input
                      value={flag.label}
                      placeholder={a.flagLabelPlaceholder}
                      onChange={e => setDayFlags(dayFlags.map((f, j) =>
                        j === i ? { ...f, label: e.target.value } : f))}
                      style={fieldStyle}
                    />
                    <PillBtn tone="danger" onClick={() => setDayFlags(dayFlags.filter((_, j) => j !== i))}>
                      ×
                    </PillBtn>
                  </div>
                  <WeekPicker
                    value={flag.days}
                    onChange={v => setDayFlags(dayFlags.map((f, j) => (j === i ? { ...f, days: v } : f)))}
                    labels={a.dayShort}
                  />
                </div>
              ))}
              <div>
                <PillBtn onClick={() => setDayFlags([...dayFlags, { label: '', days: [] }])}>
                  {a.addFlag}
                </PillBtn>
              </div>
            </div>
          </SettingsField>

          {error && (
            <div style={{
              fontSize: '0.845rem', color: '#e5897c', lineHeight: 1.5,
              padding: '11px 14px', background: 'rgba(216,72,60,0.08)',
              border: '1px solid rgba(216,72,60,0.28)',
            }}>
              {error}
            </div>
          )}

          <div style={{ display: 'flex', gap: 10 }}>
            <PillBtn tone="accent" onClick={() => { if (canSave && !busy) void submit() }}>
              {busy ? a.saving : editing ? a.save : a.create}
            </PillBtn>
            <PillBtn onClick={onCancel}>{a.cancel}</PillBtn>
          </div>
        </>
      )}
    </div>
  )
}
