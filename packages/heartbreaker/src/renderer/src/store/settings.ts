import { createContext, useContext, useState } from 'react'

const STORAGE_KEY = 'app_settings_v1'

export interface AppSettings {
  model: string
  systemPrompt: string
  temperature: number
  sidebarOpen: boolean
  userName: string
}

const DEFAULT: AppSettings = {
  model: 'claude-sonnet-4-6',
  systemPrompt: '',
  temperature: 0.7,
  sidebarOpen: true,
  userName: '',
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
