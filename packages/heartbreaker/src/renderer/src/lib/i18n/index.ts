// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useSettings } from '../../store/settings'
import en from './en'
import tr from './tr'
import type { Dict } from './en'

export type Locale = 'tr' | 'en'

export const LOCALES: { value: Locale; label: string }[] = [
  { value: 'tr', label: 'Türkçe' },
  { value: 'en', label: 'English' },
]

const DICTS: Record<Locale, Dict> = { en, tr }

/** The dictionary for the owner's chosen interface language — Turkish by
 *  default. `t.sidebar.newChat` instead of `t('sidebar.newChat')`: every call
 *  site is type-checked and autocompleted, and a renamed/removed key is a
 *  compile error at every place that used it, not a silent blank string. */
export function useT(): Dict {
  const { settings } = useSettings()
  return DICTS[settings.locale]
}
