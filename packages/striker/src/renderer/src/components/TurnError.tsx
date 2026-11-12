// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import type { CSSProperties } from 'react'

export function TurnError({ error, onRetry, retryLabel = 'Try again', style }: {
  error: string
  onRetry?: () => void
  retryLabel?: string
  style?: CSSProperties
}) {
  return (
    <div style={{
      display: 'flex', alignItems: 'baseline', gap: '0.5rem', minWidth: 0,
      fontSize: '0.875rem', lineHeight: 1.45, color: 'var(--hb-text-dim)',
      ...style,
    }}>
      <span role="alert" style={{
        display: 'inline-flex', alignItems: 'baseline', gap: '0.5rem',
        minWidth: 0, userSelect: 'text',
      }}>
        <span aria-hidden="true" style={{
          width: 6, height: 6, flex: '0 0 6px', borderRadius: '50%', background: 'var(--hb-red)',
          alignSelf: 'flex-start', marginTop: '0.47em',
        }} />
        <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>{error}</span>
      </span>
      {onRetry && (
        <button type="button" className="hb-btn-ghost" onClick={onRetry} style={{
          flexShrink: 0, minHeight: 32, padding: '0 0.375rem', fontSize: 'inherit', color: 'var(--hb-text)',
          textDecoration: 'underline', textUnderlineOffset: '0.18em',
        }}>
          {retryLabel}
        </button>
      )}
    </div>
  )
}
