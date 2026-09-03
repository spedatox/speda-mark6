// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useCallback } from 'react'
import { useSettings } from '../store/settings'
import { saveConfig } from './api'
import type { AppConfig } from './types'
import type { Locale } from './i18n'

/**
 * The master language switch.
 *
 * There used to be three of these and they were allowed to disagree: the
 * interface language (`settings.locale`), the language replies were spoken in
 * (`settings.voiceLocale`), and the backend's own `tts_locale`/`stt_locale` —
 * with nothing at all telling the model which language to WRITE in. The result
 * was a Turkish UI, an English reply, and a Turkish voice reading it, all at
 * once, and no single place to fix that.
 *
 * Now there is one value. Throwing it sets the interface strings, the
 * synthesis locale, the recognition locale, and — through `agent_language` on
 * the backend — the hard contract stamped into every agent's system prompt.
 * Not one word of the other language, on any surface.
 */

/** BCP-47 locale each language speaks and hears in. The backend derives the
 *  same pair from `agent_language`; this copy exists so the client's own
 *  synthesis calls carry a locale without a round-trip first, and the two must
 *  stay in step with services/language.py LANGUAGES. */
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
      // Backend half. Fire-and-forget on purpose: a failed PUT means the
      // agent keeps writing the previous language until the next attempt,
      // which is visible on the very next reply and self-correcting when the
      // owner flips it again — worth far less than blocking the composer on a
      // network round-trip he did not ask for. It is logged, not swallowed.
      saveConfig(config, { agent_language: next }).catch(err => {
        console.warn('language switch did not reach the backend', err)
      })
    },
    [config, settings.locale, update],
  )

  return { language: settings.locale, setLanguage }
}
