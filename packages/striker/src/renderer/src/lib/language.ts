// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useCallback } from 'react'
import { useSettings } from '../store/settings'
import { getConfigSnapshot, saveConfig } from './api'
import type { AppConfig } from './types'
import type { Locale } from './i18n'

/** Interface and local speech change immediately. The backend reply-language
 * preference is staged in the versioned configuration store and applies on the
 * explicit Configuration restart action. Per-call speech locales stay explicit. */
const SPEECH_LOCALE: Record<Locale, string> = {
  tr: 'tr-TR',
  en: 'en-US',
}

export function speechLocale(locale: Locale): string {
  return SPEECH_LOCALE[locale] ?? SPEECH_LOCALE.en
}

/** Reduce anything locale-shaped ("tr-TR", "en_GB", `navigator.language`) to a
 *  supported code, falling back to English the way the backend does. */
export function asLocale(code: string | null | undefined): Locale {
  const base = (code ?? '').trim().toLowerCase().replace('_', '-').split('-')[0]
  return base === 'tr' || base === 'en' ? base : 'en'
}

export function useLanguage(config: AppConfig): {
  language: Locale
  setLanguage: (next: Locale) => void
} {
  const { settings, update } = useSettings()

  const setLanguage = useCallback(
    (next: Locale) => {
      if (next === settings.locale) return
      // Local half first, so the switch feels instant and the UI has already
      // changed language by the time the request lands. Both local settings
      // move together — a voice locale left behind is exactly the drift this
      // switch exists to remove.
      update({ locale: next, voiceLocale: speechLocale(next) })
      // Stage the backend preference with a versioned write. Configuration
      // shows the pending change and offers the explicit restart action.
      void getConfigSnapshot(config).then(snapshot =>
        saveConfig(config, { agent_language: next }, snapshot.revision, crypto.randomUUID())
      ).catch(err => console.warn('Backend language preference was not saved', err))
    },
    [config, settings.locale, update],
  )

  return { language: settings.locale, setLanguage }
}
