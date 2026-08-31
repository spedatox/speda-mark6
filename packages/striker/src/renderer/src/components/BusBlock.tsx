// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * BusBlock — EGO "Otobüs Nerede?" arrivals board (desktop parity with the
 * Android ui/prose/BusBlock.kt).
 *
 * Triggered by ```bus code blocks in markdown. Speda emits JSON matching the
 * fence contract in prompts/core/06_visual_output.md, produced by the
 * bus_arrivals tool (app/services/transit.py). Unlike AircraftBlock, this is
 * a STATIC snapshot — no polling after render, since the whole board is
 * stale within seconds regardless and there is no single position worth
 * animating.
 *
 * ── Spec format ────────────────────────────────────────────────────────────
 * {
 *   "stopNumber": "12219",
 *   "entries": [
 *     { "line", "route", "live": true, "eta"?, "speedKmh"?, "plate"?,
 *       "stopIndex"?, "totalStops"?, "tags"? },
 *     { "line", "route", "live": false, "nextDeparture"?, "inWords"? }
 *   ]
 * }
 */

import { useMemo } from 'react'
import { looksIncomplete } from '../lib/partialJson'

/* ── Types ────────────────────────────────────────────────────────────────── */
interface BusEntry {
  line: string
  route: string
  live: boolean
  eta?: string | null
  speedKmh?: number | null
  plate?: string | null
  stopIndex?: number | null
  totalStops?: number | null
  tags?: string[]
  nextDeparture?: string | null
  inWords?: string | null
}
interface BusStopSpec {
  stopNumber: string
  entries: BusEntry[]
}

/* ── One arrival row ─────────────────────────────────────────────────────── */
function BusRow({ e }: { e: BusEntry }) {
  const etaColor = !e.live
    ? 'var(--hb-text-faint)'
    : e.eta === 'Geldi'
      ? 'var(--hb-green)'
      : e.eta === 'Gidiyor'
        ? 'var(--hb-amber)'
        : 'var(--hb-cyan-bright)'

  const sub = [e.plate, e.speedKmh != null ? `${e.speedKmh} km/h` : null, ...(e.tags ?? [])]
    .filter(Boolean)
    .join(' · ')

  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      gap: 10, padding: '8px 10px', marginBottom: 6, borderRadius: 8,
      background: 'rgba(var(--hb-accent-rgb),0.06)',
    }}>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{
          fontSize: '0.8125rem', fontWeight: 700, letterSpacing: '0.02em',
          color: 'var(--hb-cyan-bright)',
        }}>
          {e.line}
        </div>
        <div style={{
          fontSize: '0.78rem', lineHeight: 1.3, color: 'var(--hb-text)',
          overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box',
          WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
        }}>
          {e.route}
        </div>
        {e.live && sub && (
          <div style={{ fontSize: '0.68rem', color: 'var(--hb-text-faint)', marginTop: 2 }}>{sub}</div>
        )}
      </div>
      <div style={{ textAlign: 'right', flexShrink: 0 }}>
        <div style={{ fontSize: '0.875rem', fontWeight: 700, color: etaColor }}>
          {e.live ? (e.eta ?? '—') : (e.nextDeparture ?? '—')}
        </div>
        <div style={{ fontSize: '0.65rem', color: 'var(--hb-text-faint)' }}>
          {e.live
            ? (e.stopIndex != null && e.totalStops != null ? `stop ${e.stopIndex}/${e.totalStops}` : '')
            : (e.inWords ?? '')}
        </div>
      </div>
    </div>
  )
}

/* ── Main export ──────────────────────────────────────────────────────────── */
export default function BusBlock({ children }: { children: string }): React.ReactElement {
  const spec = useMemo<BusStopSpec | null>(() => {
    try {
      const s = JSON.parse(children) as BusStopSpec
      return typeof s.stopNumber === 'string' && Array.isArray(s.entries) && s.entries.length > 0
        ? s
        : null
    } catch {
      return null
    }
  }, [children])

  if (!spec) return looksIncomplete(children) ? <Materializing /> : <ParseError raw={children} />

  const liveCount = spec.entries.filter(e => e.live).length

  return (
    <div className="hb-widget" style={{ position: 'relative', margin: '0.85rem 0', animation: 'widgetEntrance 0.4s ease both' }}>
      <div className="hb-holo" style={{ position: 'relative', overflow: 'hidden', padding: '18px 20px' }}>
        <div style={{
          display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
          marginBottom: 12, gap: 12,
        }}>
          <div style={{
            fontFamily: "'Rajdhani', sans-serif", fontWeight: 600, fontSize: '1.02rem',
            letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--hb-text)',
          }}>
            BUS STOP {spec.stopNumber}
          </div>
          <div style={{
            fontSize: '0.72rem', letterSpacing: '0.1em',
            color: liveCount > 0 ? 'var(--hb-green)' : 'var(--hb-text-faint)',
          }}>
            {liveCount > 0 ? `${liveCount} LIVE` : 'SCHEDULE'}
          </div>
        </div>
        {spec.entries.map((e, i) => <BusRow key={i} e={e} />)}
      </div>
    </div>
  )
}

/* ── Parse error fallback ─────────────────────────────────────────────────── */
function ParseError({ raw }: { raw: string }) {
  return (
    <div style={{
      padding: '0.5rem 0.75rem',
      background: 'rgba(200,74,58,0.09)',
      border: '1px solid rgba(200,74,58,0.35)',
      fontSize: '0.875rem', color: '#e88a7c',
      margin: '0.5rem 0',
    }}>
      Bus — could not parse<br />
      <span style={{ color: 'var(--hb-text-faint)', fontSize: '0.8125rem' }}>{raw.slice(0, 120)}</span>
    </div>
  )
}

/* ── Materializing — quiet placeholder while the JSON is still streaming ──── */
function Materializing() {
  return (
    <div className="hb-holo" style={{
      position: 'relative', overflow: 'hidden', margin: '0.85rem 0',
      padding: '1.1rem 0.95rem', display: 'flex', alignItems: 'center',
      gap: '0.55rem', animation: 'widgetEntrance 0.3s ease both',
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
        background: 'var(--hb-cyan-bright)', animation: 'skeletonPulse 1.4s ease-in-out infinite',
      }} />
      <span style={{ fontSize: '0.875rem', color: 'var(--hb-text-faint)' }}>
        Loading the bus board…
      </span>
    </div>
  )
}
