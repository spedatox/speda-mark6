// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { createContext, useContext, useState } from 'react'

const STORAGE_KEY = 'app_settings_v1'

export interface AppSettings {
  model: string
  systemPrompt: string
  temperature: number
  /** The interface language — Turkish by default. Separate from `voiceLocale`:
   *  the UI you read and the voice you hear are independent choices. */
  locale: 'tr' | 'en'
  sidebarOpen: boolean
  /** The deck's right telemetry column. Defaults on — it is the glanceable
   *  half of the Systems Board — but two 280px columns is a lot of horizontal
   *  room, so the rail can fold it away. */
  telemetryOpen: boolean
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
  /** Where the docked orb sits and how big it is, as the owner put it.
   *
   *  `dx`/`dy` are an offset in px from the computed corner (negative moves it
   *  up and left, off the corner); `scale` multiplies the computed size. All
   *  three are the owner's, set by dragging and scrolling the orb itself.
   *
   *  This exists because "in the corner, and big" is a judgement about a
   *  specific screen, and no amount of deriving it from viewport arithmetic
   *  settles it — the thing has an invisible dust halo, sits over a composer,
   *  and lands differently on every window size. Faster to grab it than to
   *  describe it. */
  voiceOrbDock: { dx: number; dy: number; scale: number }
}

const DEFAULT: AppSettings = {
  model: 'claude-sonnet-4-6',
  systemPrompt: '',
  temperature: 0.7,
  locale: 'tr',
  sidebarOpen: true,
  telemetryOpen: true,
  userName: '',
  forgeCwd: '',
  voiceLocale: 'en-US',
  voiceModel: '',
  voiceOrbDock: { dx: 0, dy: 0, scale: 1 },
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
