// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import type { CSSProperties } from 'react'

/**
 * Skeleton — the deck's placeholder-content primitive. A glass bar cut to the
 * shape of the text/row/tile that's arriving, with a shimmer sweep (`.hb-skeleton`
 * in heartbreaker.css), so a settings tab, a session list, or a switched agent's
 * history reads as "this is arriving" instead of a blank pane, a misleading
 * empty-state string, or a bare "Loading…". Use this — not a spinner or plain
 * text — anywhere a component waits on a fetch before it has real content.
 */
export function Skeleton({ width = '100%', height = 14, radius = 4, style }: {
  width?: number | string
  height?: number | string
  radius?: number
  style?: CSSProperties
}) {
  return (
    <span
      aria-hidden
      className="hb-skeleton"
      style={{ display: 'block', width, height, borderRadius: radius, flexShrink: 0, ...style }}
    />
  )
}

/** A paragraph of placeholder lines — the last one shorter, like real text
 *  wrapping. For a remark, a blurb, a description that hasn't arrived yet. */
export function SkeletonText({ lines = 2, lastWidth = '62%', gap = 8, lineHeight = 12 }: {
  lines?: number
  lastWidth?: string | number
  gap?: number
  lineHeight?: number
}) {
  return (
    <div className="hb-skeleton-group" style={{ display: 'flex', flexDirection: 'column', gap }}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          height={lineHeight}
          width={i === lines - 1 ? lastWidth : '100%'}
          style={{ ['--hb-skeleton-delay' as string]: `${i * 0.08}s` }}
        />
      ))}
    </div>
  )
}

/** One list row — an optional mark (icon/avatar tile) plus a title + subtitle
 *  pair. Matches the shape of a session row, a connection row, a roster entry. */
export function SkeletonRow({ mark = true, subtitle = true, markSize = 32 }: {
  mark?: boolean
  subtitle?: boolean
  markSize?: number
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '9px 2px' }}>
      {mark && <Skeleton width={markSize} height={markSize} radius={8} />}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 7 }}>
        <Skeleton width="55%" height={12} />
        {subtitle && <Skeleton width="80%" height={10} />}
      </div>
    </div>
  )
}

/** A stack of `SkeletonRow`s, staggered slightly so they don't pulse in
 *  lockstep — a whole loading list (sessions, connections, roster) in one call. */
export function SkeletonList({ rows = 4, mark = true, subtitle = true, markSize = 32 }: {
  rows?: number
  mark?: boolean
  subtitle?: boolean
  markSize?: number
}) {
  return (
    <div className="hb-skeleton-group">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} style={{ ['--hb-skeleton-delay' as string]: `${i * 0.06}s` }}>
          <SkeletonRow mark={mark} subtitle={subtitle} markSize={markSize} />
        </div>
      ))}
    </div>
  )
}
