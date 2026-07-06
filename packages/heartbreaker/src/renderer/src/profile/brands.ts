import type { Brand } from './types'

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  THE single source of truth for all front-end branding + colour.
 *
 *  One entry per agent: identity (name, model number, avatar, tagline),
 *  the backend agent it talks to (agentId), and the ONE accent colour that
 *  theme.ts expands into the entire UI palette.
 *
 *  Rebrand a fork  →  change its `accent` here.
 *  Pick a fork     →  set VITE_AGENT=<key> at build/dev time (see ./index.ts).
 *  Tweak how a colour spreads across the UI  →  edit ./theme.ts.
 *
 *  (The back-end personality lives separately in
 *   packages/api/app/prompts/agents/{agentId}/01_identity.md.)
 * ════════════════════════════════════════════════════════════════════════════
 */

export const DEFAULT_AGENT = 'speda'

export const BRANDS: Record<string, Brand> = {
  speda: {
    agentId: 'speda', name: 'SPEDA', modelNumber: 'Mark VI', userName: 'Ahmet Erol',
    tagline: 'Main Assistant',
    avatarInitial: 'S', accent: '#36abca',
  },
  ultron: {
    agentId: 'ultron', name: 'Ultron', modelNumber: 'Mark III', userName: 'Ahmet Erol',
    tagline: 'Academy and Work Operations',
    avatarInitial: 'U', accent: '#8a93a6',
  },
  centurion: {
    agentId: 'centurion', name: 'Centurion', modelNumber: 'Mark I', userName: 'Ahmet Erol',
    tagline: 'Cyber Security & Threat Intelligence',
    avatarInitial: 'C', accent: '#d8483c',
  },
  sentinel: {
    agentId: 'sentinel', name: 'Sentinel', modelNumber: 'Mark II', userName: 'Ahmet Erol',
    tagline: 'Finance & Budget Intelligence',
    avatarInitial: 'S', accent: '#d99c44',
  },
  atomix: {
    agentId: 'atomix', name: 'Atomix', modelNumber: 'Mark I', userName: 'Ahmet Erol',
    tagline: 'Personal Health & Wellness',
    avatarInitial: 'A', accent: '#3fae74',
  },
  nightcrawler: {
    agentId: 'nightcrawler', name: 'NightCrawler', modelNumber: 'Mark III', userName: 'Ahmet Erol',
    tagline: 'OSINT & Web Surveillance',
    avatarInitial: 'N', accent: '#9165e6',
  },
  optimus: {
    agentId: 'optimus', name: 'Optimus', modelNumber: 'Mark I', userName: 'Ahmet Erol',
    tagline: 'Systems, Code & Infrastructure',
    avatarInitial: 'O', accent: '#2eb6ac',
  },
  orion: {
    agentId: 'orion', name: 'Orion', modelNumber: 'Mark I', userName: 'Ahmet Erol',
    tagline: 'Mark VI Maintenance & Memory Custodian',
    avatarInitial: 'O', accent: '#8a7fd6',
  },
}
