// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { createContext, useContext, useState } from 'react'

const STORAGE_KEY = 'app_settings_v1'

export interface AppSettings {
  model: string
  systemPrompt: string
  temperature: number
  /** THE language. Not just the interface: the switch that writes this
   *  (lib/language.ts) also sets the synthesis locale, the recognition locale,
   *  and the backend's `agent_language` — which stamps a hard contract into
   *  every agent's system prompt. One value, so a Turkish UI can never sit
   *  over an English reply read in a Turkish accent again. */
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
  /** BCP-47 locale replies are SPOKEN in, sent as `locale` on every synthesis
   *  call. DERIVED from `locale` — never set on its own any more: the language
   *  switch (lib/language.ts) moves both together, and `load()` re-derives it
   *  for a deck saved back when the two could drift apart. Still separate from
   *  the VOICE, which is a different axis entirely: the roster uses
   *  multilingual voices, named en-US-… whatever language they are reading. */
  voiceLocale: string
  /** Which voice speaks the replies, as a full ref the backend parses —
   *  "openai:gpt-4o-mini-tts:nova" or "azure:tr-TR-EmelNeural". Deliberately
   *  separate from `model`: the text engine and the voice engine are unrelated
   *  choices, and nothing says the agent thinking on Claude should not speak
   *  with an OpenAI voice. Empty means "whatever the agent's profile or the
   *  backend default says", which is the behaviour that predates this setting. */
  voiceModel: string
  /** Raise the screen lock when the app opens. Off by default — the deck is a
   *  personal desktop app, and a passcode nobody asked for is a daily tax. */
  lockOnLaunch: boolean
  /** SHA-256 of the lock passcode (see lib/lock.ts). Empty = none set, which
   *  makes the lock a privacy veil rather than a barrier. */
  lockPasscodeHash: string
  /** Minutes of no input before the deck locks itself. 0 = never. */
  lockIdleMinutes: number
  /** Seconds idle ON the lock screen before the screensaver takes over.
   *  0 = never — the keypad just stays up. */
  lockScreensaverSeconds: number
  /** How long each agent holds on the screensaver before dissolving into the
   *  next, in milliseconds. */
  lockSaverDwellMs: number
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
  lockOnLaunch: false,
  lockPasscodeHash: '',
  lockIdleMinutes: 0,
  lockScreensaverSeconds: 45,
  lockSaverDwellMs: 2200,
}

function load(): AppSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT
    const stored = { ...DEFAULT, ...JSON.parse(raw) } as AppSettings
    // Migration: `locale` and `voiceLocale` were independent, so a deck saved
    // before the master switch can be holding a Turkish interface and an
    // en-US voice at the same time — which is the exact disagreement the
    // switch was built to end. `locale` wins, because it is the one the switch
    // writes. Derived inline rather than imported from lib/language to keep
    // the store free of a cycle back through the i18n dictionaries.
    stored.voiceLocale = stored.locale === 'tr' ? 'tr-TR' : 'en-US'
    return stored
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
