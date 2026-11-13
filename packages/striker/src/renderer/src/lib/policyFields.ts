/** Schema-backed controls keep policy fields typed without exposing document syntax. */
export type PolicyValue = null | boolean | number | string | PolicyValue[] | { [key: string]: PolicyValue }
export interface PolicySchema {
  'x-options'?: PolicyOption[]; 'x-key-options'?: PolicyOption[]
  'x-thinking-options'?: Record<string, string[]>; 'x-route-defaults'?: { main: string; background: string }
  $ref?: string; type?: string; title?: string; description?: string; default?: PolicyValue
  enum?: PolicyValue[]; anyOf?: PolicySchema[]; properties?: Record<string, PolicySchema>
  additionalProperties?: PolicySchema | boolean; items?: PolicySchema; required?: string[]
  minimum?: number; maximum?: number; exclusiveMinimum?: number; exclusiveMaximum?: number
  minLength?: number; maxLength?: number; minItems?: number; maxItems?: number
  $defs?: Record<string, PolicySchema>
}
export interface PolicyOption { value: string | number; label: string }

/** Intersect native thinking choices when a role applies to several routes. */
export function thinkingOptions(path: string, root: PolicySchema, document: Record<string, PolicyValue>): string[] | undefined {
  const match = /^models\.(models|agents|workers|roles)\.(.+)$/.exec(path)
  if (!match || !root['x-thinking-options']) return undefined
  const [, group, key] = match
  const map = (name: string) => (document[name] ?? {}) as Record<string, string>
  const main = String(document.main_model || root['x-route-defaults']?.main || '')
  let refs = [key]
  if (group === 'agents') refs = [map('agent_models')[key] || main, map('telegram_models')[key]].filter(Boolean)
  if (group === 'workers') refs = map('worker_models')[key] ? [map('worker_models')[key]]
    : [main, ...Object.values(map('agent_models')), ...Object.values(map('telegram_models'))]
  if (group === 'roles') {
    refs = key === 'background' ? [String(document.background_model || root['x-route-defaults']?.background || main)]
      : [...Object.values(map('agent_models')), ...Object.values(map('telegram_models'))]
    if (!refs.length) refs = [main]
    if (key === 'worker') refs.push(...Object.values(map('worker_models')))
  }
  const available = refs.map(ref => root['x-thinking-options']![ref] ?? ['default'])
  const result = available[0]?.filter(option => available.every(list => list.includes(option))) ?? ['default']
  return group === 'workers' || (group === 'roles' && key === 'worker') ? [...result, 'inherit'] : result
}
export function thinkingValue(option: string): PolicyValue {
  return { mode: option === 'default' ? 'provider_default' : option.startsWith('budget:') ? 'budget'
    : ['off', 'enabled', 'inherit'].includes(option) ? option : 'effort',
    value: option.startsWith('budget:') ? Number(option.slice(7)) : ['default', 'off', 'enabled', 'inherit'].includes(option) ? null : option }
}
export function thinkingSelection(value: PolicyValue | undefined): string {
  const request = (value ?? {}) as Record<string, PolicyValue>
  return request.mode === 'budget' ? `budget:${request.value}` : request.mode === 'effort' ? String(request.value)
    : !request.mode || request.mode === 'provider_default' ? 'default' : String(request.mode)
}

export function resolveSchema(schema: PolicySchema, root: PolicySchema): PolicySchema {
  return schema.$ref ? { ...root.$defs?.[schema.$ref.split('/').pop()!], ...schema, $ref: undefined } : schema
}
export function choices(schema: PolicySchema, root: PolicySchema): PolicySchema[] {
  const resolved = resolveSchema(schema, root)
  return (resolved.anyOf ?? [resolved]).map(s => resolveSchema(s, root)).filter(s => s.type !== 'null')
}
export function schemaFor(schema: PolicySchema, root: PolicySchema, value: PolicyValue | undefined): PolicySchema {
  const options = choices(schema, root)
  return options.find(s => s.type === (Array.isArray(value) ? 'array' : typeof value) || (s.type === 'integer' && typeof value === 'number')) ?? options[0]
}
export function defaultValue(schema: PolicySchema, root: PolicySchema): PolicyValue {
  const s = choices(schema, root)[0]
  if (s.default !== undefined && s.default !== null) return structuredClone(s.default)
  if (s.enum) return s.enum[0]
  if (s['x-options']?.length) return s['x-options'][0].value
  switch (s.type) {
    case 'object': return Object.fromEntries(Object.entries(s.properties ?? {}).map(([k, v]) => [k, resolveSchema(v, root).default === null ? null : defaultValue(v, root)]))
    case 'array': return []
    case 'boolean': return false
    case 'integer': case 'number': return s.minimum ?? (s.exclusiveMinimum !== undefined ? s.exclusiveMinimum + (s.type === 'integer' ? 1 : 0.001) : 0)
    default: return ''
  }
}

const labels: Record<string, [string, string]> = {
  off: ['Off', 'Kapalı'], enabled: ['Enabled', 'Açık'], minimal: ['Minimal', 'En düşük'],
  low: ['Low', 'Düşük'], medium: ['Medium', 'Orta'], high: ['High', 'Yüksek'],
  xhigh: ['Extra high', 'Çok yüksek'], max: ['Maximum', 'Azami'],
  run: ['Per-run limits', 'Çalıştırma sınırları'], tokens: ['Reserved token limit', 'Ayrılan token sınırı'],
  calls: ['Model call limit', 'Model çağrısı sınırı'], microdollars: ['Spending limit (microdollars)', 'Harcama sınırı (mikrodolar)'],
  seconds: ['Time limit (seconds)', 'Süre sınırı (saniye)'], contributions: ['Agent contribution limit', 'Ajan katkısı sınırı'],
  concurrency: ['Simultaneous model calls', 'Eşzamanlı model çağrıları'], paused: ['Pause spending', 'Harcamayı duraklat'],
  enforce_limits: ['Enforce spending limits', 'Harcama sınırlarını uygula'], prices: ['Model prices', 'Model fiyatları'],
  microdollars_per_token: ['Price per token (microdollars)', 'Token başına fiyat (mikrodolar)'],
  extra_output_ceiling: ['Additional reasoning token allowance', 'Ek düşünme token hakkı'],
  image_token_ceiling: ['Token allowance per image', 'Görsel başına token hakkı'],
  main_model: ['Main model', 'Ana model'], background_model: ['Background model', 'Arka plan modeli'],
  agent_models: ['Models by agent', 'Ajana göre modeller'], telegram_models: ['Telegram models', 'Telegram modelleri'],
  worker_models: ['Models by worker', 'Çalışana göre modeller'], workers: ['Worker thinking', 'Çalışan düşünme ayarları'],
  models: ['Model thinking', 'Model düşünme ayarları'], cyber_programs: ['Daybreak by model', 'Modele göre Daybreak'],
  roles: ['Role thinking', 'Rol düşünme ayarları'],
  agents: ['Agent thinking', 'Ajan düşünme ayarları'], descriptors: ['Model capabilities', 'Model yetenekleri'],
  fallbacks: ['Fallback models (in order)', 'Yedek modeller (sıralı)'], show_thinking: ['Show thinking', 'Düşünmeyi göster'],
  budget_mode: ['Budget mode', 'Tasarruf modu'], daily_calls: ['Daily model call limit', 'Günlük model çağrısı sınırı'],
  daily_tokens: ['Daily reserved token limit', 'Günlük ayrılan token sınırı'], daily_microdollars: ['Daily spending limit (microdollars)', 'Günlük harcama sınırı (mikrodolar)'],
  lifetime_calls: ['Lifetime model call limit', 'Toplam model çağrısı sınırı'], lifetime_tokens: ['Lifetime token limit', 'Toplam token sınırı'],
  lifetime_microdollars: ['Lifetime spending limit (microdollars)', 'Toplam harcama sınırı (mikrodolar)'],
  concurrent_calls: ['Simultaneous model calls', 'Eşzamanlı model çağrıları'], external_prices: ['Speech resource prices', 'Konuşma kaynak fiyatları'],
  run_seconds: ['Run time limit (seconds)', 'Çalıştırma süresi sınırı (saniye)'], units_per_run: ['Units per maintenance run', 'Bakım çalıştırması başına birim'],
  telegram_enabled: ['Enable Telegram notifications', 'Telegram bildirimlerini etkinleştir'], quiet_start: ['Quiet hours start (0–23)', 'Sessiz saat başlangıcı (0–23)'], quiet_end: ['Quiet hours end (0–23)', 'Sessiz saat bitişi (0–23)'],
}
export function policyLabel(key: string, tr = false): string {
  return labels[key]?.[tr ? 1 : 0] ?? key.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())
}
export function policyHint(path: string, schema: PolicySchema, tr: boolean): string {
  if (path === 'coordination.run.tokens') return tr
    ? 'Bir çalıştırmadaki tüm model çağrılarının toplam ayrılmış token sınırı. İstek boyutu, azami yanıt ve düşünme payını içerir. Harcama sınırları kapalıyken de uygulanır.'
    : 'Total token reservations across all model calls in one run, including request size, maximum response and reasoning allowance. Still applies when spending limits are off.'
  if (path === 'coordination.enforce_limits') return tr ? 'Fiyat ve harcama denetimlerini açar. Çalıştırma başına çağrı, token ve süre sınırları her zaman uygulanır.' : 'Controls price and spending checks. Per-run call, token and time limits always apply.'
  if (path.endsWith('microdollars') || path.endsWith('microdollars_per_token')) return tr ? '1.000.000 mikrodolar = 1 ABD doları.' : '1,000,000 microdollars = US$1.'
  return schema.description ?? ''
}

/** Validate hidden sections too; clearing a number must never silently turn it into zero. */
export function validatePolicy(schema: PolicySchema, root: PolicySchema, value: PolicyValue | undefined, path = ''): string[] {
  const resolved = resolveSchema(schema, root)
  if (value === null && resolved.anyOf?.some(s => s.type === 'null')) return []
  if (resolved.anyOf) {
    if (resolved.anyOf.some(s => validatePolicy(s, root, value, path).length === 0)) return []
    return [`${path}: Choose a valid value.`]
  }
  const fail = (message: string) => [`${path}: ${message}`]
  if (resolved.type === 'null') return value === null ? [] : fail('Choose a valid value.')
  if (resolved.enum && !resolved.enum.includes(value!)) return fail('Choose an available option.')
  if (resolved.type === 'object') {
    if (!value || Array.isArray(value) || typeof value !== 'object') return fail('Complete this group.')
    return [...(resolved.required ?? []).filter(k => !(k in value)).flatMap(k => fail(`Missing ${policyLabel(k)}.`)),
      ...Object.entries(value).flatMap(([k, v]) => {
        const child = resolved.properties?.[k] ?? (typeof resolved.additionalProperties === 'object' ? resolved.additionalProperties : undefined)
        return child ? validatePolicy(child, root, v, path ? `${path} / ${policyLabel(k)}` : policyLabel(k)) : []
      })]
  }
  if (resolved.type === 'array') {
    if (!Array.isArray(value)) return fail('Enter a list.')
    if (value.length < (resolved.minItems ?? 0) || value.length > (resolved.maxItems ?? Infinity)) return fail('Check the number of entries.')
    return value.flatMap((v, i) => validatePolicy(resolved.items ?? {}, root, v, `${path} / ${i + 1}`))
  }
  if (resolved.type === 'integer' || resolved.type === 'number') {
    if (typeof value !== 'number' || !Number.isFinite(value) || (resolved.type === 'integer' && !Number.isSafeInteger(value))) return fail('Enter a valid number.')
    if (value < (resolved.minimum ?? -Infinity) || value > (resolved.maximum ?? Infinity) || value <= (resolved.exclusiveMinimum ?? -Infinity) || value >= (resolved.exclusiveMaximum ?? Infinity)) return fail('Value is outside the allowed range.')
  } else if (resolved.type === 'string') {
    if (typeof value !== 'string' || value.length < (resolved.minLength ?? 0) || value.length > (resolved.maxLength ?? Infinity)) return fail('Enter valid text.')
  } else if (resolved.type === 'boolean' && typeof value !== 'boolean') return fail('Choose on or off.')
  return []
}
