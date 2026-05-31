import type { AppProfile } from './types'

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  BRANDING CONFIG — one of the two files you edit to fork an agent.
 *  This drives the ENTIRE front-end: name, model number, colour, tagline,
 *  avatar, welcome prompts. Change values here and the whole UI rebrands.
 *  (The other fork file is the back-end personality: prompts/core/01_identity.md)
 *
 *  Examples:
 *    Sentinel     → accent: '#ef4444'  (red)     avatarInitial: 'S'
 *    Nightcrawler → accent: '#8b5cf6'  (purple)  avatarInitial: 'N'
 *    Ultron       → accent: '#f59e0b'  (amber)   avatarInitial: 'U'
 * ════════════════════════════════════════════════════════════════════════════
 */
const profile: AppProfile = {
  name: 'SPEDA',
  modelNumber: 'Mark VI',
  userName: 'Erol',
  tagline: 'Specialized Personal Executive Digital Assistant',
  avatarInitial: 'S',
  accent: '#36abca',
  accentHover: '#5fcce6',
  suggestedPrompts: [
    'Summarize my tasks and priorities for today',
    'Draft a professional email for me',
    'Research and give me a briefing on a topic',
    'Create a structured document or report',
  ],
}

export default profile
