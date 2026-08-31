// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * AircraftBlock — live ADS-B position card (desktop parity with the Android
 * ui/prose/AircraftBlock.kt).
 *
 * Triggered by ```aircraft code blocks in markdown. Speda emits JSON matching
 * the fence contract in prompts/core/06_visual_output.md, produced by the
 * track_aircraft tool. Rendered with the same MapLibre Stark style as
 * MapBlock — reuses its exported STARK_STYLE/useIgor/webglAvailable/cssVar
 * rather than duplicating them.
 *
 * Unlike MapBlock's routes/places, this card is LIVE: after the initial fence
 * renders the model's snapshot, the client polls GET /aircraft/track/{tail}
 * on its own (~15s, well under airplanes.live's 1 req/s guidance) and moves
 * the marker — no further tool call, no id-store, just the tail number the
 * fence already carries. See app/services/aircraft.py for why this is a
 * stateless passthrough rather than the routeId/placesId snapshot pattern.
 *
 * ── Spec format ────────────────────────────────────────────────────────────
 * {
 *   "tail": "N12345", "icao24"?, "callsign"?, "aircraftType"?,
 *   "lat", "lng", "altitudeFt"?, "onGround"?, "groundSpeedKt"?,
 *   "headingDeg"?, "squawk"?
 * }
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { looksIncomplete } from '../lib/partialJson'
import { Skeleton, SkeletonText } from './Skeleton'
import { STARK_STYLE, cssVar, useIgor, webglAvailable } from './MapBlock'

/* ── Spec type ───────────────────────────────────────────────────────────── */

interface AircraftSpec {
  tail: string
  icao24?: string
  callsign?: string
  aircraftType?: string
  lat: number
  lng: number
  altitudeFt?: number | null
  onGround?: boolean
  groundSpeedKt?: number | null
  headingDeg?: number | null
  squawk?: string | null
  emergency?: string | null
}

const EMERGENCY_SQUAWKS = new Set(['7500', '7600', '7700'])

// Landing auto-pauses the card — once the feed confirms the aircraft is
// down, further movement is gate/apron-scale and not worth a standing poll.
// 20 consecutive misses (~5min) auto-pauses it the other way: a permanently
// lost signal (landed with the transponder off before we caught the ground
// report, out of coverage for good) rather than a plane that's genuinely
// still up. Either is just the DEFAULT guess though — the owner has the
// final say via the header button, which un-pauses (with a fresh miss count)
// or pauses on demand regardless of what auto-pause decided. Without either
// of these, a chat window left open would poll every 15s forever.
const GIVE_UP_AFTER_MISSES = 20

interface LiveTrack {
  spec: AircraftSpec | null
  stale: boolean
  paused: boolean
  autoPaused: boolean
  toggle: () => void
}

/** Live-polls the tail number this card is about. Starts from the fence's own
 * snapshot; a failed poll (404 = no current signal, or a network hiccup)
 * keeps the last-known position rather than blanking the card. Polling can
 * stop two ways — the card deciding on its own (`autoPaused`, see
 * GIVE_UP_AFTER_MISSES above) or the owner clicking the header button
 * (`userPaused`) — `toggle` always resumes from either. */
function useLiveTrack(seed: AircraftSpec | null): LiveTrack {
  const igor = useIgor()
  const [live, setLive] = useState<AircraftSpec | null>(null)
  const [misses, setMisses] = useState(0)
  const [autoPaused, setAutoPaused] = useState(false)
  const [userPaused, setUserPaused] = useState(false)

  useEffect(() => { setLive(null); setMisses(0); setAutoPaused(false); setUserPaused(false) }, [seed?.tail])

  const paused = autoPaused || userPaused

  useEffect(() => {
    if (!seed?.tail || paused) return
    let alive = true
    const poll = async (): Promise<void> => {
      const body = await igor(`/aircraft/track/${encodeURIComponent(seed.tail)}`)
      if (!alive) return
      if (body && typeof body.lat === 'number' && typeof body.lng === 'number') {
        setLive(body as AircraftSpec)
        setMisses(0)
        if (body.onGround) setAutoPaused(true)
      } else {
        setMisses(m => {
          const next = m + 1
          if (next >= GIVE_UP_AFTER_MISSES) setAutoPaused(true)
          return next
        })
      }
    }
    const id = setInterval(poll, 15000)
    return () => { alive = false; clearInterval(id) }
  }, [seed?.tail, igor, paused])

  const toggle = (): void => {
    if (paused) {
      // Resuming always gets a clean slate, whether the owner is overriding
      // an auto-pause or just un-pausing manually — no reason to make a
      // fresh "try again" carry the old miss count into GIVE_UP_AFTER_MISSES.
      setAutoPaused(false)
      setUserPaused(false)
      setMisses(0)
    } else {
      setUserPaused(true)
    }
  }

  const spec = live ?? seed
  // Three consecutive misses (~45s) before the card admits the feed went
  // quiet — one dropped poll is normal network noise, not a lost signal.
  return { spec, stale: misses >= 3, paused, autoPaused, toggle }
}

/* ── Component ───────────────────────────────────────────────────────────── */

export default function AircraftBlock({ children }: { children: string }): React.ReactElement {
  const parsed = useMemo<AircraftSpec | null>(() => {
    try {
      const s = JSON.parse(children) as AircraftSpec
      return s && typeof s.tail === 'string' && typeof s.lat === 'number' && typeof s.lng === 'number'
        ? s
        : null
    } catch {
      return null
    }
  }, [children])

  const { spec, stale, paused, autoPaused, toggle } = useLiveTrack(parsed)

  if (!parsed) return looksIncomplete(children) ? <Materializing /> : <ParseError raw={children} />
  if (!spec) return <Materializing />

  const emergency =
    (!!spec.emergency && spec.emergency !== 'none') ||
    (!!spec.squawk && EMERGENCY_SQUAWKS.has(spec.squawk))

  return (
    <AircraftPanel spec={spec} stale={stale} emergency={emergency} paused={paused} onToggle={toggle}>
      <AircraftCanvas spec={spec} />
      <StatusReadout spec={spec} stale={stale} paused={paused} autoPaused={autoPaused} emergency={emergency} />
    </AircraftPanel>
  )
}

/* ── Header panel ────────────────────────────────────────────────────────── */

function AircraftPanel({ spec, stale, emergency, paused, onToggle, children }: {
  spec: AircraftSpec; stale: boolean; emergency: boolean; paused: boolean; onToggle: () => void
  children: React.ReactNode
}): React.ReactElement {
  const borderColor = emergency ? 'rgba(224,83,61,0.55)' : 'var(--hb-edge)'
  return (
    <div className="hb-glass-sm hb-widget" style={{
      position: 'relative', background: 'rgba(6, 14, 22, 0.6)',
      backdropFilter: 'var(--hb-holo-blur)', WebkitBackdropFilter: 'var(--hb-holo-blur)',
      border: `1px solid ${borderColor}`, boxShadow: 'var(--hb-holo-shadow)',
      margin: '0.75rem 0', overflow: 'hidden', animation: 'widgetEntrance 0.35s ease both',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        height: 28, padding: '0 0.4rem 0 0.75rem',
        background: emergency ? 'rgba(224,83,61,0.14)' : 'rgba(var(--hb-accent-rgb),0.1)',
        boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.14)',
        borderBottom: `1px solid ${emergency ? 'rgba(224,83,61,0.35)' : 'rgba(var(--hb-accent-rgb),0.22)'}`,
        fontFamily: "'Rajdhani', sans-serif", fontSize: '0.82rem', fontWeight: 700,
        letterSpacing: '0.2em', textTransform: 'uppercase', userSelect: 'none',
      }}>
        <span>
          <span style={{ color: '#fff' }}>{spec.tail}</span>
          {spec.callsign && <span style={{ color: 'var(--hb-cyan)' }}> · {spec.callsign}</span>}
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{
            color: emergency ? '#e0533d' : stale ? 'var(--hb-amber)' : 'var(--hb-cyan-bright)',
            fontSize: '0.72rem', letterSpacing: '0.1em',
          }}>
            {emergency ? 'EMERGENCY' : stale ? 'SIGNAL LOST' : spec.onGround ? 'ON GROUND' : 'AIRBORNE'}
          </span>
          <LiveToggle paused={paused} onToggle={onToggle} />
        </span>
      </div>
      <div style={{ padding: '0.6rem 0' }}>{children}</div>
    </div>
  )
}

/** The owner's own on/off switch for the 15s poll — always available
 * regardless of why polling is or isn't running (still tracking, landed,
 * gave up after GIVE_UP_AFTER_MISSES, or a manual pause from a prior
 * click). Resuming always restarts with a clean miss count. */
function LiveToggle({ paused, onToggle }: { paused: boolean; onToggle: () => void }): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onToggle}
      title={paused ? 'Resume live tracking' : 'Pause live tracking'}
      style={{
        display: 'flex', alignItems: 'center', gap: '0.3rem',
        padding: '0.15rem 0.45rem', borderRadius: 999,
        background: paused ? 'rgba(255,255,255,0.06)' : 'rgba(var(--hb-accent-rgb),0.16)',
        border: `1px solid ${paused ? 'var(--hb-edge)' : 'rgba(var(--hb-accent-rgb),0.4)'}`,
        color: paused ? 'var(--hb-text-faint)' : 'var(--hb-cyan-bright)',
        fontFamily: "'Rajdhani', sans-serif", fontSize: '0.68rem', fontWeight: 700,
        letterSpacing: '0.08em', textTransform: 'uppercase', cursor: 'pointer',
        lineHeight: 1,
      }}
    >
      <span style={{
        width: 6, height: 6, borderRadius: '50%',
        background: paused ? 'var(--hb-text-faint)' : 'var(--hb-cyan-bright)',
        boxShadow: paused ? 'none' : '0 0 5px var(--hb-cyan-bright)',
      }} />
      {paused ? 'Paused' : 'Live'}
    </button>
  )
}

/* ── Live position canvas ────────────────────────────────────────────────── */

function AircraftCanvas({ spec }: { spec: AircraftSpec }): React.ReactElement {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const markerRef = useRef<maplibregl.Marker | null>(null)
  const iconRef = useRef<HTMLDivElement | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

  /* Build once. */
  useEffect(() => {
    if (!containerRef.current) return
    if (!webglAvailable()) {
      setFailed('WebGL unavailable — too many live maps, or no GPU acceleration.')
      return
    }
    const accentBright = cssVar('--hb-cyan-bright', '#5fcce6')

    let map: maplibregl.Map
    try {
      map = new maplibregl.Map({
        container: containerRef.current,
        style: STARK_STYLE,
        interactive: true,
        attributionControl: { compact: true },
        center: [spec.lng, spec.lat],
        zoom: 9,
      })
    } catch (e) {
      setFailed(e instanceof Error ? e.message : String(e))
      return
    }
    mapRef.current = map
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')

    const icon = document.createElement('div')
    icon.style.cssText =
      `width:0;height:0;border-left:7px solid transparent;border-right:7px solid transparent;` +
      `border-bottom:16px solid ${accentBright};filter:drop-shadow(0 0 6px ${accentBright});` +
      'transform-origin:50% 65%;'
    iconRef.current = icon
    // Rotation is applied to this inner element by hand (see the live-update
    // effect below), NOT via Marker's own rotation option — that rotates the
    // marker's root element, which is also where Marker writes its position
    // transform, and the two would fight over the same CSS property.
    const wrap = document.createElement('div')
    wrap.appendChild(icon)
    markerRef.current = new maplibregl.Marker({ element: wrap })
      .setLngLat([spec.lng, spec.lat])
      .addTo(map)

    return () => {
      markerRef.current = null
      iconRef.current = null
      mapRef.current = null
      try { map.remove() } catch { /* already gone */ }
    }
    // Built once — subsequent position/heading updates move the existing
    // marker instead of tearing down the WebGL context (see below).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* Live position/heading → move the existing marker, pan gently to follow. */
  useEffect(() => {
    const map = mapRef.current
    const marker = markerRef.current
    if (!map || !marker) return
    marker.setLngLat([spec.lng, spec.lat])
    if (iconRef.current && spec.headingDeg != null) {
      iconRef.current.style.transform = `rotate(${spec.headingDeg}deg)`
    }
    map.easeTo({ center: [spec.lng, spec.lat], duration: 800 })
  }, [spec.lat, spec.lng, spec.headingDeg])

  return (
    <div style={{ position: 'relative', height: 220, margin: '0 0.25rem' }}>
      <div style={{
        position: 'absolute', inset: 0, pointerEvents: 'none',
        background: 'linear-gradient(160deg, var(--hb-petrol), var(--hb-void))',
      }} />
      <div ref={containerRef} style={{ position: 'absolute', inset: 0, borderRadius: 4, overflow: 'hidden' }} />
      {failed && (
        <div style={{
          position: 'absolute', inset: 0, display: 'flex',
          alignItems: 'center', justifyContent: 'center', textAlign: 'center',
          padding: '0 1rem', fontFamily: "'Rajdhani', sans-serif",
          fontSize: '0.875rem', letterSpacing: '0.1em', color: 'var(--hb-text-faint)',
        }}>
          Map canvas unavailable<br />{failed.slice(0, 90)}
        </div>
      )}
    </div>
  )
}

/* ── Status readout ──────────────────────────────────────────────────────── */

function StatusReadout({ spec, stale, paused, autoPaused, emergency }: {
  spec: AircraftSpec; stale: boolean; paused: boolean; autoPaused: boolean; emergency: boolean
}): React.ReactElement {
  const rows: [string, string][] = []
  rows.push(['TYPE', spec.aircraftType || '—'])
  rows.push(['ALT', spec.onGround ? 'GROUND' : spec.altitudeFt != null ? `${spec.altitudeFt.toLocaleString()} FT` : '—'])
  if (!spec.onGround) {
    rows.push(['SPEED', spec.groundSpeedKt != null ? `${Math.round(spec.groundSpeedKt)} KT` : '—'])
    rows.push(['HEADING', spec.headingDeg != null ? `${Math.round(spec.headingDeg)}°` : '—'])
  }
  rows.push(['SQUAWK', spec.squawk || '—'])

  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: '0.4rem 1rem',
      margin: '0.6rem 0.75rem 0', fontFamily: "'Rajdhani', sans-serif",
    }}>
      {rows.map(([label, value]) => (
        <div key={label} style={{ lineHeight: 1.3 }}>
          <div style={{ fontSize: '0.72rem', letterSpacing: '0.12em', color: 'var(--hb-text-faint)' }}>{label}</div>
          <div style={{ fontSize: '0.875rem', color: 'var(--hb-text)' }}>{value}</div>
        </div>
      ))}
      {emergency && (
        <div style={{
          flexBasis: '100%', marginTop: 4, padding: '0.4rem 0.55rem', borderRadius: 6,
          background: 'rgba(224,83,61,0.12)', border: '1px solid rgba(224,83,61,0.4)',
          color: '#e0533d', fontSize: '0.8125rem', letterSpacing: '0.04em',
        }}>
          ⚠ Emergency squawk{spec.squawk ? ` ${spec.squawk}` : ''}{spec.emergency && spec.emergency !== 'none' ? ` — ${spec.emergency}` : ''}
        </div>
      )}
      {!emergency && (paused || stale) && (
        <div style={{
          flexBasis: '100%', marginTop: 4, fontSize: '0.8125rem', color: 'var(--hb-amber)',
        }}>
          {paused
            ? spec.onGround
              ? 'Landed — paused automatically. Tap Live to keep watching it taxi.'
              : autoPaused
                ? 'Signal lost — paused automatically after 5 minutes of silence. Tap Live to try again.'
                : 'Paused. Tap Live to resume.'
            : 'No recent update — the last known position may be stale.'}
        </div>
      )}
    </div>
  )
}

/* ── Fallbacks ───────────────────────────────────────────────────────────── */

function ParseError({ raw }: { raw: string }): React.ReactElement {
  return (
    <div style={{
      margin: '0.75rem 0', padding: '0.6rem 0.75rem',
      background: 'rgba(200,74,58,0.09)', border: '1px solid rgba(200,74,58,0.35)',
      fontFamily: "'Rajdhani', sans-serif", fontSize: '0.72rem', letterSpacing: '0.05em', color: '#c84a3a',
    }}>Aircraft — could not parse<div style={{ color: 'var(--hb-text-faint)', marginTop: 4 }}>{raw.slice(0, 120)}</div></div>
  )
}

function Materializing(): React.ReactElement {
  return (
    <div className="hb-glass-sm" style={{
      margin: '0.75rem 0', padding: '0.75rem', border: '1px solid var(--hb-edge)',
      background: 'rgba(6,14,22,0.55)',
    }}>
      <Skeleton height={200} radius={8} />
      <div style={{ marginTop: 10 }}>
        <SkeletonText lines={1} lastWidth="42%" lineHeight={11} />
      </div>
    </div>
  )
}
