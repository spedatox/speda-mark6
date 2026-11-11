// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useState } from 'react'

/**
 * Single source of truth for the mobile breakpoint — keep in sync with the
 * `@media (max-width: 767px)` blocks in theme/heartbreaker.css.
 */
const MOBILE_QUERY = '(max-width: 767px)'

export function useIsMobile(): boolean {
  return useViewportMatch(MOBILE_QUERY)
}

export function useViewportMatch(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mql = window.matchMedia(query)
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
    setMatches(mql.matches)
  }, [query])
  return matches
}
