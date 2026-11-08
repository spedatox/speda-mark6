// SPDX-License-Identifier: AGPL-3.0-or-later
export interface ExerciseItem {
  prompt: string
  code?: string
  answer: string
  explanation: string
  choice?: { options: string[]; answerIndex: number }
}
export interface Practice { kind: 'practice'; description: string; items: ExerciseItem[] }
export interface Explore {
  kind: 'explore'
  description: string
  parameter: { label: string; min: number; max: number; initial: number; unit?: string }
  cases: { through: number; label: string; detail: string }[]
}

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const text = (value: unknown, max: number): value is string =>
  typeof value === 'string' && value.length <= max && value.trim().length > 0
const integer = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && Math.abs(value) <= 10000

export function parseNativeArtifact(raw: string): Practice | Explore | null {
  if (raw.length > 24000) return null
  let input: unknown
  try { input = JSON.parse(raw) } catch { return null }
  if (!record(input) || !text(input.description, 320)) return null
  if (input.kind === 'practice') {
    if (!Array.isArray(input.items) || input.items.length < 1 || input.items.length > 8) return null
    const items: ExerciseItem[] = []
    for (const item of input.items) {
      if (!record(item) || !text(item.prompt, 320) || !text(item.explanation, 1000)
          || (item.code !== undefined && !text(item.code, 4000))) return null
      const question = { prompt: item.prompt, code: item.code, explanation: item.explanation }
      if (item.type === 'choice') {
        const { options, answerIndex } = item
        if (!Array.isArray(options) || options.length < 2 || options.length > 6 ||
            !options.every(option => text(option, 180)) || new Set(options.map(option => option.trim())).size !== options.length ||
            !integer(answerIndex) || answerIndex < 0 || answerIndex >= options.length) return null
        items.push({ ...question, answer: options[answerIndex], choice: { options, answerIndex } })
      } else {
        if ((item.type !== undefined && item.type !== 'text') || !text(item.answer, 240)) return null
        items.push({ ...question, answer: item.answer })
      }
    }
    return { kind: 'practice', description: input.description, items }
  }
  if (input.kind !== 'explore' || !record(input.parameter)) return null
  const { label, min, max, initial, unit } = input.parameter
  if (!text(label, 100) || !integer(min) || !integer(max) || !integer(initial) || min >= max
      || initial < min || initial > max || (unit !== undefined && !text(unit, 20))) return null
  if (!Array.isArray(input.cases) || input.cases.length < 1 || input.cases.length > 12) return null
  let previous = min - 1
  const cases: Explore['cases'] = []
  for (const step of input.cases) {
    if (!record(step) || !integer(step.through) || step.through <= previous || step.through > max
        || !text(step.label, 100) || !text(step.detail, 600)) return null
    cases.push({ through: step.through, label: step.label, detail: step.detail })
    previous = step.through
  }
  return previous === max ? { kind: 'explore', description: input.description, parameter: { label, min, max, initial, unit }, cases } : null
}

export function answerMatches(answer: string, expected: string): boolean {
  const normalize = (value: string) => value.replace(/\r\n/g, '\n').trim()
  return normalize(answer) === normalize(expected)
}
