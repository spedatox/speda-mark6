// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

/** Shared agent identity bits for comms UI — colors mirror each backend
 *  profile's DocTheme accent (app/profiles/*.py). */

export const AGENT_COLORS: Record<string, string> = {
  speda: '#36abca', sentinel: '#d99c44', nightcrawler: '#9165e6', friday: '#9165e6',
  ultron: '#9e9e9e', scourge: '#d8483c', atomix: '#3fae74',
  optimus: '#2f4f8f', orion: '#e0703a', all: '#f2b75c', warroom: '#f2b75c',
}

/** Every in-process agent, commander first — drives the agent switcher. */
export const ROSTER = ['speda', 'sentinel', 'nightcrawler', 'ultron', 'scourge', 'atomix', 'optimus', 'orion']

/**
 * House Party assembles everyone, including Orion, matching the Coordinator's
 * full-roster warroom. This list is presentation/branding only; live membership
 * is always read from the authenticated room.
 */
export const PARTY_ROSTER = ROSTER

export function agentColor(id: string): string {
  return AGENT_COLORS[id] ?? 'var(--hb-icon-bright)'
}

export function monogram(id: string): string {
  return agentDisplayName(id).slice(0, 2).toUpperCase()
}

export function fmtCommTime(iso: string): string {
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** Historical IDs remain storage/transport keys. */
export function agentDisplayName(id: string): string {
  return id === 'nightcrawler' || id === 'friday' ? 'Friday' : id
}
