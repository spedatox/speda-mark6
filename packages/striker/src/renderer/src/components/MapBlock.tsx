/**
 * MapBlock — Stark inline map + navigation renderer (desktop parity with the
 * Android ui/prose/MapBlock.kt).
 *
 * Triggered by ```map code blocks in markdown. SPEDA emits JSON matching the
 * fence contract in prompts/core/06_visual_output.md. Rendered with MapLibre GL
 * (our own Stark dark style over OpenFreeMap vector tiles — no Google Play
 * Services, no Maps key on the client). The map is gesture-locked (a glance);
 * real interaction hands off to Google Maps via NAVIGATE / OPEN IN MAPS.
 *
 * ── Spec format ────────────────────────────────────────────────────────────
 * {
 *   "title": "ROUTE_HOME",
 *   "center": { "lat": 41.04, "lng": 29.01 }, "zoom": 12,
 *   "markers": [{ "lat", "lng", "label"?, "kind": "origin|destination|poi|pin", "subtitle"? }],
 *   "routes":  [{ "polyline": "<encoded>", "label"?, "durationMin"?, "noTrafficMin"?,
 *                 "distanceKm"?, "mode": "drive", "primary": true }],
 *   "navigate": { "lat", "lng", "mode": "drive", "label"? }, "autoNavigate": false
 * }
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { looksIncomplete } from '../lib/partialJson'

/* ── Spec types ─────────────────────────────────────────────────────────────── */

interface LatLng { lat: number; lng: number }
interface MapMarker extends LatLng { label?: string; kind?: string; subtitle?: string }
interface MapRoute {
  polyline: string; label?: string; durationMin?: number; noTrafficMin?: number
  distanceKm?: number; mode?: string; primary?: boolean
}
interface MapNavigate extends LatLng { mode?: string; label?: string }
interface MapSpec {
  title?: string; center?: LatLng; zoom?: number; height?: number
  markers?: MapMarker[]; routes?: MapRoute[]; navigate?: MapNavigate; autoNavigate?: boolean
}

/* ── Fence repair ────────────────────────────────────────────────────────────── */

/**
 * Make `polyline` values JSON-safe before parsing the fence.
 *
 * Google's encoded polylines legitimately contain `\` — byte 92 sits inside the
 * encoding alphabet, and a real Bursa route came back as `...KgB\}KJeC...`. The
 * model copies the polyline verbatim out of get_route (that is what the prompt
 * asks for), so the `\` lands in the JSON string raw. Two things then go wrong:
 *
 *   `\}`  → invalid escape  → JSON.parse throws  → MAP // PARSE ERROR
 *   `\t`  → VALID escape    → parses "fine"      → polyline silently corrupted,
 *                                                  and a WRONG route is drawn
 *
 * The second is the dangerous one: it fails silently, ~650 m off, with no error.
 * So we cannot simply try/catch — the value has to be treated as a literal.
 *
 * We rewrite only `"polyline": "..."` values, doubling every backslash so the
 * parser hands back the exact bytes the encoder produced. This deliberately
 * assumes polylines are copied verbatim and never pre-escaped, which is now
 * stated as a contract in prompts/core/06_visual_output.md. Every other field
 * (labels, titles) keeps normal JSON escaping.
 *
 * NOT idempotent, by nature — it treats its input as unescaped. Apply it exactly
 * once, to the raw fence text, immediately before JSON.parse.
 */
function repairFence(raw: string): string {
  return raw.replace(
    /("polyline"\s*:\s*")((?:[^"\\]|\\.)*)(")/g,
    (_m, head: string, value: string, tail: string) =>
      head + value.replace(/\\/g, '\\\\') + tail,
  )
}

/* ── Google encoded-polyline decoder (port of domain/Polyline.kt) ────────────── */

function decodePolyline(encoded: string): [number, number][] {
  const points: [number, number][] = []
  let index = 0, lat = 0, lng = 0
  while (index < encoded.length) {
    let result = 0, shift = 0, b: number
    do { b = encoded.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5 }
    while (b >= 0x20 && index < encoded.length)
    lat += (result & 1) ? ~(result >> 1) : result >> 1
    result = 0; shift = 0
    do { b = encoded.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5 }
    while (b >= 0x20 && index < encoded.length)
    lng += (result & 1) ? ~(result >> 1) : result >> 1
    points.push([lng / 1e5, lat / 1e5]) // GeoJSON order: [lng, lat]
  }
  return points
}

/* ── Stark style (verified OpenFreeMap endpoints; mirrors the Android asset) ──── */

const STARK_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
  sources: { openmaptiles: { type: 'vector', url: 'https://tiles.openfreemap.org/planet' } },
  layers: [
    { id: 'background', type: 'background', paint: { 'background-color': '#070b10' } },
    { id: 'water', type: 'fill', source: 'openmaptiles', 'source-layer': 'water', paint: { 'fill-color': '#0b1a22' } },
    { id: 'building', type: 'fill', source: 'openmaptiles', 'source-layer': 'building', minzoom: 13,
      paint: { 'fill-color': '#0d141b', 'fill-outline-color': '#13202a' } },
    { id: 'road-secondary', type: 'line', source: 'openmaptiles', 'source-layer': 'transportation',
      filter: ['in', 'class', 'secondary', 'tertiary'],
      paint: { 'line-color': '#243543', 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 0.6, 16, 3.5] } },
    { id: 'road-primary', type: 'line', source: 'openmaptiles', 'source-layer': 'transportation',
      filter: ['in', 'class', 'primary', 'trunk'],
      paint: { 'line-color': '#314654', 'line-width': ['interpolate', ['linear'], ['zoom'], 6, 0.8, 16, 5] } },
    { id: 'road-motorway', type: 'line', source: 'openmaptiles', 'source-layer': 'transportation',
      filter: ['==', 'class', 'motorway'],
      paint: { 'line-color': '#2c5566', 'line-width': ['interpolate', ['linear'], ['zoom'], 5, 1, 16, 6] } },
    { id: 'boundary', type: 'line', source: 'openmaptiles', 'source-layer': 'boundary',
      filter: ['<=', 'admin_level', 2], paint: { 'line-color': '#2a3a45', 'line-width': 0.8, 'line-dasharray': [3, 2] } },
    { id: 'place-labels', type: 'symbol', source: 'openmaptiles', 'source-layer': 'place',
      filter: ['in', 'class', 'city', 'town'],
      layout: { 'text-field': '{name}', 'text-font': ['Noto Sans Regular'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 6, 10, 12, 14] },
      paint: { 'text-color': '#8fb6c6', 'text-halo-color': '#050a0e', 'text-halo-width': 1.2 } },
  ],
}

/* ── Helpers ─────────────────────────────────────────────────────────────────── */

/** Can this renderer still hand out a WebGL2 context right now? */
function webglAvailable(): boolean {
  try {
    const probe = document.createElement('canvas')
    const gl = probe.getContext('webgl2')
    // Hand the probe context straight back — holding it would consume one of the
    // very slots we are checking for.
    gl?.getExtension('WEBGL_lose_context')?.loseContext()
    return !!gl
  } catch {
    return false
  }
}

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

function webMode(mode?: string): string {
  switch (mode) {
    case 'walk': return 'walking'
    case 'bicycle': return 'bicycling'
    case 'transit': return 'transit'
    default: return 'driving'
  }
}

function mapsDirUrl(nav: MapNavigate): string {
  return `https://www.google.com/maps/dir/?api=1&destination=${nav.lat},${nav.lng}&travelmode=${webMode(nav.mode)}`
}

function openExternal(url: string): void {
  if (window.api?.openExternal) window.api.openExternal(url)
  else window.open(url, '_blank')
}

function primaryRoute(spec: MapSpec): MapRoute | undefined {
  return spec.routes?.find(r => r.primary) ?? spec.routes?.[0]
}

/** The point the footer describes: nav target → destination marker → centre → any marker. */
function focusPoint(spec: MapSpec): LatLng | undefined {
  return spec.navigate
    ?? spec.markers?.find(m => m.kind === 'destination')
    ?? spec.center
    ?? spec.markers?.[0]
}

/** Reverse-geocode via OpenStreetMap Nominatim (Electron has no CSP block).
 * Best-effort — returns null on any failure; short-address form. */
async function reverseGeocode(lat: number, lng: number): Promise<string | null> {
  try {
    const r = await fetch(
      `https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=16&lat=${lat}&lon=${lng}`,
    )
    if (!r.ok) return null
    const a = (await r.json())?.address ?? {}
    const parts = [
      a.road ?? a.neighbourhood ?? a.suburb,
      a.city ?? a.town ?? a.village ?? a.county,
      a.state,
    ].filter(Boolean)
    return [...new Set(parts)].slice(0, 2).join(', ') || null
  } catch {
    return null
  }
}

/* ── Component ───────────────────────────────────────────────────────────────── */

export default function MapBlock({ children }: { children: string }): React.ReactElement {
  const spec = useMemo<MapSpec | null>(() => {
    try {
      const s = JSON.parse(repairFence(children)) as MapSpec
      const hasContent = (s.markers?.length ?? 0) > 0 || (s.routes?.length ?? 0) > 0 || !!s.center
      return hasContent ? s : null
    } catch {
      return null
    }
  }, [children])

  if (!spec) return looksIncomplete(children) ? <Materializing /> : <ParseError raw={children} />

  const primary = primaryRoute(spec)
  const focus = focusPoint(spec)
  return (
    <MapPanel title={spec.title} primary={primary}>
      <MapCanvas spec={spec} />
      {focus && <CoordinateFooter lat={focus.lat} lng={focus.lng} />}
      {primary && <TrafficReadout route={primary} />}
      <MapActions spec={spec} />
    </MapPanel>
  )
}

function CoordinateFooter({ lat, lng }: { lat: number; lng: number }): React.ReactElement {
  const [address, setAddress] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    reverseGeocode(lat, lng).then(a => { if (alive) setAddress(a) })
    return () => { alive = false }
  }, [lat, lng])
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      padding: '0.5rem 0.75rem 0', fontFamily: "'Rajdhani', sans-serif",
    }}>
      <span style={{ color: 'var(--hb-cyan-bright)', fontSize: '0.8rem' }}>◎</span>
      <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.35 }}>
        <span style={{ color: 'var(--hb-cyan-bright)', fontSize: '0.72rem', letterSpacing: '0.06em' }}>
          {lat.toFixed(5)}, {lng.toFixed(5)}
        </span>
        {address && (
          <span style={{ color: 'var(--hb-text-dim)', fontSize: '0.7rem' }}>{address}</span>
        )}
      </div>
    </div>
  )
}

function MapCanvas({ spec }: { spec: MapSpec }): React.ReactElement {
  const containerRef = useRef<HTMLDivElement>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const height = spec.height ?? 240

  useEffect(() => {
    if (!containerRef.current) return
    const accent = cssVar('--hb-cyan', '#36abca')
    const accentBright = cssVar('--hb-cyan-bright', '#5fcce6')
    const dim = cssVar('--hb-cyan-dim', '#1d5d70')

    // MapLibre needs a WebGL2 context and browsers cap how many can be live at
    // once; past the cap, construction throws. Probe first, so a chat full of
    // maps degrades to a readable message instead of an exception.
    // (maplibregl.supported() existed in v3 and was removed in v4 — hence the
    // hand-rolled check.)
    if (!webglAvailable()) {
      setFailed('WebGL unavailable — too many live maps, or no GPU acceleration.')
      return
    }

    let map: maplibregl.Map
    try {
      map = new maplibregl.Map({
        container: containerRef.current,
        style: STARK_STYLE,
        interactive: true, // zoom / pan / rotate
        attributionControl: { compact: true },
        center: spec.center ? [spec.center.lng, spec.center.lat] : [0, 0],
        zoom: spec.zoom ?? 12,
      })
    } catch (e) {
      setFailed(e instanceof Error ? e.message : String(e))
      return
    }
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')

    // Everything below runs inside MapLibre's own async event dispatch, OUTSIDE
    // React's call stack — a throw here is NOT caught by the ErrorBoundary and
    // becomes an unhandled window error. Contain it here or not at all.
    map.on('load', () => {
      try {
      const bounds = new maplibregl.LngLatBounds()
      let hasBounds = false

      // Routes — alternatives below, primary (glow + line) on top.
      const routes = [...(spec.routes ?? [])].sort((a, b) => Number(a.primary) - Number(b.primary))
      routes.forEach((route, i) => {
        const coords = decodePolyline(route.polyline)
        if (coords.length < 2) return
        coords.forEach(c => { bounds.extend(c); hasBounds = true })
        map.addSource(`route-${i}`, {
          type: 'geojson',
          data: { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } },
        })
        if (route.primary) {
          map.addLayer({ id: `route-glow-${i}`, type: 'line', source: `route-${i}`,
            layout: { 'line-cap': 'round', 'line-join': 'round' },
            paint: { 'line-color': accent, 'line-opacity': 0.28, 'line-width': 11 } })
          map.addLayer({ id: `route-line-${i}`, type: 'line', source: `route-${i}`,
            layout: { 'line-cap': 'round', 'line-join': 'round' },
            paint: { 'line-color': accentBright, 'line-width': 4.5 } })
        } else {
          map.addLayer({ id: `route-line-${i}`, type: 'line', source: `route-${i}`,
            layout: { 'line-cap': 'round' },
            paint: { 'line-color': dim, 'line-opacity': 0.7, 'line-width': 2.5, 'line-dasharray': [2, 1.5] } })
        }
      })

      // Markers — coloured HTML dots by kind.
      ;(spec.markers ?? []).forEach(m => {
        const color = m.kind === 'origin' ? accentBright : m.kind === 'destination' ? accent : dim
        const size = m.kind === 'destination' ? 16 : m.kind === 'origin' ? 14 : 10
        const el = document.createElement('div')
        el.style.cssText =
          `width:${size}px;height:${size}px;border-radius:50%;background:${color};` +
          'border:1.6px solid rgba(255,255,255,0.85);box-shadow:0 0 8px ' + color
        const marker = new maplibregl.Marker({ element: el }).setLngLat([m.lng, m.lat])
        if (m.label || m.subtitle) {
          marker.setPopup(new maplibregl.Popup({ offset: 12, closeButton: false })
            .setHTML(`<strong>${m.label ?? ''}</strong>${m.subtitle ? '<br>' + m.subtitle : ''}`))
        }
        marker.addTo(map)
        bounds.extend([m.lng, m.lat]); hasBounds = true
      })

      // Camera: explicit centre wins; else fit everything.
      if (!spec.center && hasBounds) {
        try { map.fitBounds(bounds, { padding: 40, maxZoom: 15, duration: 0 }) } catch { /* pre-layout */ }
      }
      } catch (e) {
        console.error('[MapBlock] failed to draw route/marker layers', e)
        setFailed(e instanceof Error ? e.message : String(e))
      }
    })

    // Release the WebGL context on unmount. Browsers cap concurrent contexts, so
    // leaking one per map would eventually make every later map fail to build.
    return () => { try { map.remove() } catch { /* already gone */ } }
  }, [spec])

  return (
    <div style={{ position: 'relative', height, margin: '0 0.25rem' }}>
      {/* Stark wireframe behind the map — a dead tile server still reads as design. */}
      <div style={{
        position: 'absolute', inset: 0,
        backgroundImage:
          'linear-gradient(rgba(var(--hb-accent-rgb),0.06) 1px, transparent 1px),' +
          'linear-gradient(90deg, rgba(var(--hb-accent-rgb),0.06) 1px, transparent 1px)',
        backgroundSize: '28px 28px', pointerEvents: 'none',
      }} />
      <div ref={containerRef} style={{ position: 'absolute', inset: 0, borderRadius: 4, overflow: 'hidden' }} />
      {failed && (
        // The wireframe behind still reads as design, so this stays a quiet
        // caption rather than a full error panel — the route figures below
        // (distance, duration, NAVIGATE) remain usable without the canvas.
        <div style={{
          position: 'absolute', inset: 0, display: 'flex',
          alignItems: 'center', justifyContent: 'center', textAlign: 'center',
          padding: '0 1rem', fontFamily: "'Rajdhani', sans-serif",
          fontSize: '0.7rem', letterSpacing: '0.1em', color: 'var(--hb-text-faint)',
        }}>
          MAP // CANVAS UNAVAILABLE<br />{failed.slice(0, 90)}
        </div>
      )}
    </div>
  )
}

function MapPanel({ title, primary, children }: {
  title?: string; primary?: MapRoute; children: React.ReactNode
}): React.ReactElement {
  const idx = title ? title.indexOf('_') : -1
  const main = idx > -1 ? title!.slice(0, idx) : title
  const sub = idx > -1 ? title!.slice(idx) : ''
  const readout = primary
    ? [primary.distanceKm != null ? `${primary.distanceKm} KM` : '', primary.durationMin != null ? `${primary.durationMin} MIN` : '']
        .filter(Boolean).join(' · ')
    : ''

  return (
    <div className="hb-glass-sm" style={{
      position: 'relative', background: 'rgba(6, 14, 22, 0.6)',
      backdropFilter: 'var(--hb-holo-blur)', WebkitBackdropFilter: 'var(--hb-holo-blur)',
      border: '1px solid var(--hb-edge)', boxShadow: 'var(--hb-holo-shadow)',
      margin: '0.75rem 0', overflow: 'hidden', animation: 'widgetEntrance 0.35s ease both',
    }}>
      {(title || readout) && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          height: 28, padding: '0 0.75rem', background: 'rgba(var(--hb-accent-rgb),0.1)',
          boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.14)',
          borderBottom: '1px solid rgba(var(--hb-accent-rgb),0.22)',
          fontFamily: "'Rajdhani', sans-serif", fontSize: '0.82rem', fontWeight: 700,
          letterSpacing: '0.2em', textTransform: 'uppercase', userSelect: 'none',
        }}>
          <span><span style={{ color: '#fff' }}>{main}</span>{sub && <span style={{ color: 'var(--hb-cyan)' }}>{sub}</span>}</span>
          {readout && <span style={{ color: 'var(--hb-cyan-bright)', fontSize: '0.72rem', letterSpacing: '0.1em' }}>{readout}</span>}
        </div>
      )}
      <div style={{ padding: '0.6rem 0' }}>{children}</div>
    </div>
  )
}

function TrafficReadout({ route }: { route: MapRoute }): React.ReactElement | null {
  if (route.durationMin == null || route.noTrafficMin == null || route.durationMin <= route.noTrafficMin) return null
  const delay = route.durationMin - route.noTrafficMin
  const heavy = route.noTrafficMin > 0 && delay / route.noTrafficMin > 0.25
  return (
    <div style={{
      padding: '0.4rem 0.75rem 0', fontFamily: "'Rajdhani', sans-serif",
      fontSize: '0.72rem', letterSpacing: '0.12em',
      color: heavy ? 'var(--hb-amber)' : 'var(--hb-text-dim)',
    }}>
      TRAFFIC +{delay} MIN{route.label ? ` · ${route.label.toUpperCase()}` : ''}
    </div>
  )
}

function MapActions({ spec }: { spec: MapSpec }): React.ReactElement {
  const nav = spec.navigate
    ?? spec.markers?.find(m => m.kind === 'destination')
    ?? spec.markers?.[0]
  return (
    <div style={{ display: 'flex', gap: 8, padding: '0.6rem 0.75rem 0' }}>
      {nav && (
        <ActionChip filled onClick={() => openExternal(mapsDirUrl(nav as MapNavigate))}>▸ NAVIGATE</ActionChip>
      )}
      {nav && (
        <ActionChip onClick={() => openExternal(mapsDirUrl(nav as MapNavigate))}>⧉ OPEN IN MAPS</ActionChip>
      )}
    </div>
  )
}

function ActionChip({ filled, onClick, children }: {
  filled?: boolean; onClick: () => void; children: React.ReactNode
}): React.ReactElement {
  return (
    <button onClick={onClick} style={{
      flex: 1, cursor: 'pointer', padding: '0.55rem 0',
      background: filled ? 'rgba(var(--hb-accent-rgb),0.16)' : 'transparent',
      border: `1px solid rgba(var(--hb-accent-rgb),${filled ? 0.45 : 0.25})`, borderRadius: 8,
      color: filled ? 'var(--hb-cyan-bright)' : 'var(--hb-text-dim)',
      fontFamily: "'Rajdhani', sans-serif", fontSize: '0.72rem', fontWeight: 700,
      letterSpacing: '0.14em', textTransform: 'uppercase',
    }}>{children}</button>
  )
}

function ParseError({ raw }: { raw: string }): React.ReactElement {
  return (
    <div style={{
      margin: '0.75rem 0', padding: '0.6rem 0.75rem',
      background: 'rgba(200,74,58,0.09)', border: '1px solid rgba(200,74,58,0.35)',
      fontFamily: "'Rajdhani', sans-serif", fontSize: '0.72rem', letterSpacing: '0.05em', color: '#c84a3a',
    }}>MAP // PARSE ERROR<div style={{ color: 'var(--hb-text-faint)', marginTop: 4 }}>{raw.slice(0, 120)}</div></div>
  )
}

function Materializing(): React.ReactElement {
  return (
    <div className="hb-glass-sm" style={{
      margin: '0.75rem 0', padding: '0.6rem 0.75rem', border: '1px solid var(--hb-edge)',
      background: 'rgba(6,14,22,0.55)', fontFamily: "'Rajdhani', sans-serif",
      fontSize: '0.68rem', letterSpacing: '0.14em', color: 'var(--hb-text-faint)',
    }}>MAP // MATERIALIZING</div>
  )
}
