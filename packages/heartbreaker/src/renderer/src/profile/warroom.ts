import type { AppProfile } from './types'
import { deriveAccents } from './theme'

/**
 * The House Party Protocol brand — deliberately NOT in BRANDS: it must never
 * appear in the agent switcher. While the protocol is engaged the app itself
 * transforms into this profile (App.tsx swaps it in exactly like an agent
 * switch), so the war room reads as just another agent on the console — same
 * sidebar, same hero, same chat stack — addressed to the backend "warroom"
 * agent_id. The amber accent is only the resting base; the party colour
 * cycle (theme.ts startPartyCycle) owns the palette while engaged.
 */
export const WARROOM_PROFILE: AppProfile = {
  agentId: 'warroom', name: 'HOUSE PARTY', modelNumber: 'PROTOCOL',
  userName: 'Ahmet Erol',
  tagline: 'All-Hands Command — Full Roster Engaged',
  avatarInitial: 'W', accent: '#f2b75c',
  accentHover: deriveAccents('#f2b75c').bright,
}
