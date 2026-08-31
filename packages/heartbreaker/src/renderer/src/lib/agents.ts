// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

/** Shared agent identity bits for comms UI — colors mirror each backend
 *  profile's DocTheme accent (app/profiles/*.py). */

export const AGENT_COLORS: Record<string, string> = {
  speda: '#7fa4c4', sentinel: '#d99c44', nightcrawler: '#9165e6',
  ultron: '#8a93a6', centurion: '#d8483c', atomix: '#3fae74',
  optimus: '#2f4f8f', orion: '#e0703a', all: '#f2b75c', warroom: '#f2b75c',
}

/** Every in-process agent, commander first — drives the agent switcher. */
export const ROSTER = ['speda', 'sentinel', 'nightcrawler', 'ultron', 'centurion', 'atomix', 'optimus', 'orion']

/**
 * Who assembles for the House Party Protocol: Speda and the Superior Six.
 *
 * Orion is deliberately absent. It is Mark VI's own maintenance and memory
 * custodian — it keeps the record straight and the host healthy — not a field
 * agent, so it does not get pulled into an all-hands on the owner's work. Use
 * this, never ROSTER, for anything war-room or protocol shaped.
 */
export const PARTY_ROSTER = ROSTER.filter(id => id !== 'orion')

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
