// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Turn an internal tool identifier into a human-readable label.
 *
 * Strips the registry's `mcp_<server>_<slug>_<10-hex-hash>` framing (and
 * Forge's `mcp__<server>__<tool>` framing) and collapses adjacent duplicate
 * words such as `google tasks tasks list` -> `google tasks list`.
 */
export function formatToolName(raw: string): string {
  const trimmed = raw.trim()
  if (!trimmed) return ''
  const isMcp = trimmed.startsWith('mcp_')
  let body = trimmed.startsWith('mcp__')
    ? trimmed.slice(5)
    : isMcp
      ? trimmed.slice(4)
      : trimmed
  if (isMcp) {
    body = body.replace(/_[0-9a-f]{10}$/i, '')
  }
  const words = body.split(/[_\-\s]+/).filter(Boolean)
  const deduped: string[] = []
  for (const w of words) {
    if (deduped.length > 0 && deduped[deduped.length - 1].toLowerCase() === w.toLowerCase()) {
      continue
    }
    deduped.push(w)
  }
  return deduped.join(' ')
}
