/** Shared agent identity bits for comms UI — colors mirror each backend
 *  profile's DocTheme accent (app/profiles/*.py). */

export const AGENT_COLORS: Record<string, string> = {
  speda: '#36abca', sentinel: '#d99c44', nightcrawler: '#9165e6',
  ultron: '#8a93a6', centurion: '#d8483c', atomix: '#3fae74',
  optimus: '#2eb6ac', all: '#f2b75c',
}

/** The in-process roster, commander first — drives the war-room rail. */
export const ROSTER = ['speda', 'sentinel', 'nightcrawler', 'ultron', 'centurion', 'atomix', 'optimus']

export function agentColor(id: string): string {
  return AGENT_COLORS[id] ?? 'var(--hb-icon-bright)'
}

export function monogram(id: string): string {
  return id.slice(0, 2).toUpperCase()
}

export function fmtCommTime(iso: string): string {
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
