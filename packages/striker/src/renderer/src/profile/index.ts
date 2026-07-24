import { deriveAccents } from './theme'
import type { AppProfile } from './types'

/**
 * SPEDA Mark VI Core is single-agent. There is no roster, no switcher and no
 * VITE_AGENT selection — this is the one and only profile the app ever renders.
 * (Heartbreaker keeps the full multi-brand BRANDS map; Core deliberately does
 * not.) The accent stays SPEDA cyan; the neutral-dark palette lives statically
 * in theme/striker.css, so nothing here re-hues the UI at runtime.
 */
const SPEDA_CORE: AppProfile = {
  agentId: 'speda',
  name: 'SPEDA',
  modelNumber: 'Mark VI Core',
  userName: 'Ahmet Erol',
  tagline: 'Main Assistant',
  avatarInitial: 'S',
  accent: '#36abca',
  accentHover: deriveAccents('#36abca').bright,
}

export default SPEDA_CORE
