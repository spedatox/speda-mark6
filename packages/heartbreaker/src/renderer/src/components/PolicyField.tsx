import { useState } from 'react'
import type { CSSProperties } from 'react'
import { choices, defaultValue, policyHint, policyLabel, resolveSchema, schemaFor, thinkingOptions, thinkingSelection, thinkingValue } from '../lib/policyFields'
import type { PolicySchema, PolicyValue } from '../lib/policyFields'
import { Switch, PillBtn } from './settingsUI'

const inputStyle: CSSProperties = {
  width: '100%', minWidth: 0, height: 44, padding: '0 16px', colorScheme: 'dark',
  background: 'rgba(255,255,255,0.03)', color: 'var(--hb-text)',
  border: '1px solid rgba(255,255,255,0.09)', fontFamily: 'var(--font-read)', fontSize: '.9375rem',
}
const groupStyle: CSSProperties = { border: '1px solid var(--hb-edge)', padding: 14, margin: '10px 0', minWidth: 0 }

export default function PolicyField({ schema, root, value, onChange, path, label, tr, disabled = false, document = {} }: {
  schema: PolicySchema; root: PolicySchema; value: PolicyValue | undefined; onChange: (value: PolicyValue) => void
  path: string; label: string; tr: boolean; disabled?: boolean
  document?: Record<string, PolicyValue>
}) {
  const [entryKey, setEntryKey] = useState('')
  const [addedKey, setAddedKey] = useState('')
  const [keyError, setKeyError] = useState('')
  const resolved = resolveSchema(schema, root)
  const selected = schemaFor(schema, root, value)
  const options = choices(schema, root)
  const nullable = resolved.anyOf?.some(s => s.type === 'null')
  const id = `policy-${path}`
  const hint = policyHint(path, resolved, tr)
  const control = selected.type
  const objectValue = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  const arrayValue = Array.isArray(value) ? value : []
  const keyOptions = selected['x-key-options']
  const availableKeys = keyOptions?.filter(option => !Object.hasOwn(objectValue, option.value))
  const nativeThinking = thinkingOptions(path, root, document)
  const currentThinking = thinkingSelection(value)
  const valueOptions = selected['x-options']
  const updateProperty = (key: string, next: PolicyValue) => onChange({ ...objectValue, [key]: next })
  const removeProperty = (key: string) => onChange(Object.fromEntries(Object.entries(objectValue).filter(([k]) => k !== key)))
  const child = (s: PolicySchema, v: PolicyValue | undefined, change: (v: PolicyValue) => void, key: string, name: string) =>
    <PolicyField key={key} schema={s} root={root} value={v} onChange={change} path={`${path}.${key}`} label={name} tr={tr} disabled={disabled} document={document} />

  return <div style={{ minWidth: 0, marginBottom: 16 }}>
    <label htmlFor={id} style={{ display: 'block', color: 'var(--hb-text-dim)', fontSize: '.845rem', marginBottom: 8 }}>{label}</label>
    {hint && <p id={`${id}-help`} style={{ fontSize: '.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.5, margin: '0 0 8px' }}>{hint}</p>}
    {nullable && !valueOptions && <label style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
      <input type="checkbox" checked={value === null} disabled={disabled} onChange={e => onChange(e.target.checked ? null : defaultValue(schema, root))} />
      {tr ? 'Varsayılanı kullan / geçersiz kılma yok' : 'Use default / no override'}
    </label>}
    {(value !== null || valueOptions) && <>
      {options.length > 1 && <select aria-label={`${label} ${tr ? 'değer türü' : 'value type'}`} style={inputStyle} value={selected.type} disabled={disabled}
        onChange={e => onChange(defaultValue(options.find(s => s.type === e.target.value)!, root))}>
        {options.map(s => <option key={s.type} value={s.type}>{s.type === 'integer' ? (tr ? 'Token sayısı' : 'Token count') : (tr ? 'Düzey adı' : 'Named level')}</option>)}
      </select>}
      {nativeThinking ? <select id={id} aria-label={label} style={inputStyle} disabled={disabled} value={currentThinking} onChange={e => onChange(thinkingValue(e.target.value))}>
        {!nativeThinking.includes(currentThinking) && <option value={currentThinking}>{currentThinking} · {tr ? 'Kayıtlı değer' : 'Saved value'}</option>}
        {nativeThinking.map(option => <option key={option} value={option}>{option === 'default' ? (tr ? 'Model varsayılanı' : 'Model default')
          : option === 'inherit' ? (tr ? 'Üst çalıştırmadan devral' : 'Inherit from parent')
          : option.startsWith('budget:') ? `${option.slice(7)} ${tr ? 'düşünme tokenı' : 'reasoning tokens'}` : policyLabel(option, tr)}</option>)}
      </select> : control === 'object' ? <div style={groupStyle}>
        {Object.entries(selected.properties ?? {}).map(([key, s]) => child(s, objectValue[key], v => updateProperty(key, v), key, policyLabel(key, tr)))}
        {typeof selected.additionalProperties === 'object' && <>
          {Object.entries(objectValue).filter(([k]) => !(k in (selected.properties ?? {}))).map(([key, v]) => <details key={key} open={key === addedKey || undefined} style={groupStyle}>
            <summary style={{ cursor: 'pointer', overflowWrap: 'anywhere' }}>{keyOptions?.find(o => o.value === key)?.label ?? key}</summary>
            {child(selected.additionalProperties as PolicySchema, v, next => updateProperty(key, next), key, keyOptions?.find(o => o.value === key)?.label ?? key)}
            <PillBtn disabled={disabled} onClick={() => removeProperty(key)}>{tr ? 'Kaldır' : 'Remove'} {key}</PillBtn>
          </details>)}
          {Object.keys(objectValue).length === 0 && <p style={{ color: 'var(--hb-text-faint)' }}>{tr ? 'Henüz bir kayıt yok.' : 'No entries yet.'}</p>}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {availableKeys ? <select aria-label={`${label} ${tr ? 'yeni kayıt' : 'new entry'}`} style={{ ...inputStyle, flex: '1 1 180px' }} value={entryKey}
              disabled={disabled || !availableKeys.length} onChange={e => { setEntryKey(e.target.value); setKeyError('') }}>
              <option value="">{availableKeys.length ? (tr ? 'Seçin…' : 'Choose…') : (tr ? 'Tüm seçenekler eklendi' : 'All options added')}</option>
              {availableKeys.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select> : <input aria-label={`${label} ${tr ? 'yeni kayıt adı' : 'new entry name'}`} style={{ ...inputStyle, flex: '1 1 180px' }} value={entryKey}
              placeholder={tr ? 'Kayıt adı' : 'Entry name'} disabled={disabled} onChange={e => { setEntryKey(e.target.value); setKeyError('') }} />}
            <PillBtn disabled={disabled || !entryKey.trim()} onClick={() => {
              const key = entryKey.trim()
              if (Object.hasOwn(objectValue, key)) { setKeyError(tr ? 'Bu kayıt zaten var.' : 'That entry already exists.'); return }
              updateProperty(key, defaultValue(selected.additionalProperties as PolicySchema, root)); setAddedKey(key); setEntryKey('')
            }}>{tr ? 'Kayıt ekle' : 'Add entry'}</PillBtn>
          </div>
          {keyError && <p role="alert">{keyError}</p>}
        </>}
      </div> : control === 'array' ? <div style={groupStyle}>
        {arrayValue.map((v, index) => <div key={index} style={groupStyle}>
          {child(selected.items ?? {}, v, next => onChange(arrayValue.map((item, i) => i === index ? next : item)), String(index), `${label} ${index + 1}`)}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            <PillBtn disabled={disabled || index === 0} onClick={() => {
              const list = [...arrayValue]; [list[index - 1], list[index]] = [list[index], list[index - 1]]; onChange(list)
            }}>{tr ? 'Yukarı taşı' : 'Move up'}</PillBtn>
            <PillBtn disabled={disabled || arrayValue.length <= (selected.minItems ?? 0)} onClick={() => onChange(arrayValue.filter((_, i) => i !== index))}>{tr ? 'Kaldır' : 'Remove'}</PillBtn>
          </div>
        </div>)}
        <PillBtn disabled={disabled || arrayValue.length >= (selected.maxItems ?? Infinity)} onClick={() => onChange([...arrayValue, defaultValue(selected.items ?? {}, root)])}>{tr ? 'Öğe ekle' : 'Add item'}</PillBtn>
      </div> : control === 'boolean' ? <Switch on={value === true} onChange={onChange} disabled={disabled} title={label} />
        : valueOptions ? <select id={id} aria-label={label} aria-describedby={hint ? `${id}-help` : undefined} style={inputStyle} value={String(value ?? '')} disabled={disabled} onChange={e => onChange(nullable && e.target.value === '' ? null : valueOptions.find(o => String(o.value) === e.target.value)!.value)}>
          {nullable && <option value="">{tr ? 'Varsayılanı kullan' : 'Use default'}</option>}
          {value !== null && !valueOptions.some(o => o.value === value) && <option value={String(value ?? '')}>{value ? `${value} · ${tr ? 'Kayıtlı değer' : 'Saved value'}` : (tr ? 'Seçin…' : 'Choose…')}</option>}
          {valueOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select> : selected.enum ? <select id={id} aria-label={label} aria-describedby={hint ? `${id}-help` : undefined} style={inputStyle} value={String(value ?? '')} disabled={disabled} onChange={e => onChange(selected.enum!.find(v => String(v) === e.target.value)!)}>
          {selected.enum.map(v => <option key={String(v)} value={String(v)}>{String(v).replace(/_/g, ' ')}</option>)}
        </select> : <input id={id} aria-label={label} aria-describedby={hint ? `${id}-help` : undefined} style={inputStyle} disabled={disabled}
          type={control === 'integer' || control === 'number' ? 'number' : 'text'} value={String(value ?? '')}
          min={selected.minimum ?? selected.exclusiveMinimum} max={selected.maximum ?? selected.exclusiveMaximum}
          step={control === 'integer' ? 1 : 'any'} minLength={selected.minLength} maxLength={selected.maxLength}
          onChange={e => onChange((control === 'integer' || control === 'number') && e.target.value !== '' ? Number(e.target.value) : e.target.value)} />}
    </>}
  </div>
}
