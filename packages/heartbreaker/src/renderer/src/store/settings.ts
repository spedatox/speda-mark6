import { createContext, useContext, useState } from 'react'

const STORAGE_KEY = 'app_settings_v1'

export interface AppSettings {
  model: string
  systemPrompt: string
  temperature: number
  sidebarOpen: boolean
  userName: string
  /** Working directory sent to the Forge for Optimus jobs (Cell workspace +
   *  Graphify root). Empty = the peer's own default workspace. */
  forgeCwd: string
  /** Language replies are SPOKEN in. Sent as `locale` on every synthesis call.
   *  Separate from the voice: the roster uses multilingual voices, which are
   *  named en-US-… whatever language they are actually reading. */
  voiceLocale: string
  /** Which voice speaks the replies, as a full ref the backend parses —
   *  "openai:gpt-4o-mini-tts:nova" or "azure:tr-TR-EmelNeural". Deliberately
   *  separate from `model`: the text engine and the voice engine are unrelated
   *  choices, and nothing says the agent thinking on Claude should not speak
   *  with an OpenAI voice. Empty means "whatever the agent's profile or the
   *  backend default says", which is the behaviour that predates this setting. */
  voiceModel: string
}

const DEFAULT: AppSettings = {
  model: 'claude-sonnet-4-6',
  systemPrompt: '',
  temperature: 0.7,
  sidebarOpen: true,
  userName: '',
  forgeCwd: '',
  voiceLocale: 'en-US',
  voiceModel: '',
}

function load(): AppSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? { ...DEFAULT, ...JSON.parse(raw) } : DEFAULT
  } catch {
    return DEFAULT
  }
}

interface SettingsCtx {
  settings: AppSettings
  update: (patch: Partial<AppSettings>) => void
}

export const SettingsContext = createContext<SettingsCtx | null>(null)

export function useSettings(): SettingsCtx {
  const ctx = useContext(SettingsContext)
  if (!ctx) throw new Error('useSettings outside SettingsProvider')
  return ctx
}

export function useSettingsProvider(): SettingsCtx {
  const [settings, set] = useState<AppSettings>(load)
  const update = (patch: Partial<AppSettings>) => {
    set(prev => {
      const next = { ...prev, ...patch }
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)) } catch { /* */ }
      return next
    })
  }
  return { settings, update }
}
