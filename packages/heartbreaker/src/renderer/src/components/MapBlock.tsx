/**
 * MapBlock — Stark inline map + navigation renderer (desktop parity with the
 * Android ui/prose/MapBlock.kt).
 *
 * Triggered by ```map code blocks in markdown. Speda emits JSON matching the
 * fence contract in prompts/core/06_visual_output.md. Rendered with MapLibre GL
 * (our own Stark dark style over OpenFreeMap vector tiles — no Google Play
 * Services, no Maps key on the client).
 *
 * The card is a working map, not a picture of one: routes switch from a header
 * strip, the drawn line is coloured by live congestion the way Google's is,
 * every place is tappable and opens its own record (address, hours, phone,
 * website, its own NAVIGATE), and the turn list is one click away. Handing off
 * to Google Maps stays available — it is no longer the only thing you can do.
 *
 * ── Spec format ────────────────────────────────────────────────────────────
 * {
 *   "title": "ROUTE_HOME",
 *   "center": { "lat": 41.04, "lng": 29.01 }, "zoom": 12,
 *   "markers": [{ "lat", "lng", "label"?, "kind": "origin|destination|poi|pin", "subtitle"? }],
 *   "routes":  [{ "routeId": "r_…", "label"?, "durationMin"?, "noTrafficMin"?,
 *                 "distanceKm"?, "mode": "drive", "primary": true }],
 *   "places":  "pl_…",                      // whole find_places result set, by id
 *   "navigate": { "lat", "lng", "mode": "drive", "label"? }, "autoNavigate": false
 * }
 *
 * Geometry, congestion, turn lists and place records are all fetched from Igor
 * by id — never transcribed by the model. See app/models/route.py and
 * app/models/place.py for why that rule exists.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { looksIncomplete } from '../lib/partialJson'
import { useChatContext } from '../store/chat'
import { Skeleton, SkeletonText } from './Skeleton'

/* ── Spec types ─────────────────────────────────────────────────────────────── */

interface LatLng { lat: number; lng: number }
interface MapMarker extends LatLng { label?: string; kind?: string; subtitle?: string }

/** One congestion band, addressing points of the DECODED polyline. */
interface TrafficBand { start: number; end: number; speed: 'NORMAL' | 'SLOW' | 'TRAFFIC_JAM' }
interface RouteStep { instruction: string; maneuver?: string; distanceM?: number; durationMin?: number }

interface MapRoute {
  /** Inline geometry. Absent on current fences — see routeId. */
  polyline?: string
  /**
   * Server-held geometry reference ("r_1a2b3c4d"), resolved via
   * GET /navigation/route/{id}. The polyline stopped travelling through the
   * model because one mistyped character in 500 still parses, still looks like
   * a road, and ends somewhere else entirely (app/models/route.py).
   */
  routeId?: string
  label?: string; durationMin?: number; noTrafficMin?: number
  distanceKm?: number; mode?: string; primary?: boolean
  /** Filled in by the routeId fetch, never by the model. */
  traffic?: TrafficBand[]
  steps?: RouteStep[]
}

/** A find_places result, fetched whole from GET /navigation/places/{id}. */
interface MapPlace {
  placeId?: string; name: string; lat: number; lng: number
  address?: string; category?: string; summary?: string; status?: string
  rating?: number; reviews?: number; openNow?: boolean; hours?: string[]
  priceLevel?: string; phone?: string; website?: string; mapsUri?: string
  distanceKm?: number
}

interface MapNavigate extends LatLng { mode?: string; label?: string }
interface MapSpec {
  title?: string; center?: LatLng; zoom?: number; height?: number
  markers?: MapMarker[]; routes?: MapRoute[]; navigate?: MapNavigate; autoNavigate?: boolean
  /** placesId — the entire result set of one find_places call. */
  places?: string
}

/* ── Traffic palette ─────────────────────────────────────────────────────────── */

/**
 * Google's own colours, near enough that the owner reads them without a key:
 * green is moving, amber is slow, red is stopped. Deliberately NOT the agent
 * accent — congestion is the one thing on this card that must mean the same
 * regardless of which agent is speaking.
 */
const TRAFFIC_COLORS: Record<string, string> = {
  NORMAL: '#3fd08a',
  SLOW: '#e8b53c',
  TRAFFIC_JAM: '#e0533d',
}
const TRAFFIC_WORDS: Record<string, string> = {
  NORMAL: 'CLEAR', SLOW: 'SLOW', TRAFFIC_JAM: 'JAM',
}

/* ── Fence repair ────────────────────────────────────────────────────────────── */

/**
 * Make `polyline` values JSON-safe before parsing the fence.
 *
 * Google's encoded polylines legitimately contain `\` — byte 92 sits inside the
 * encoding alphabet, and a real Bursa route came back as `...KgB\}KJeC...`. Old
 * fences (written before routeIds) copied the polyline verbatim, so the `\`
 * lands in the JSON string raw. Two things then go wrong:
 *
 *   `\}`  → invalid escape  → JSON.parse throws  → MAP // PARSE ERROR
 *   `\t`  → VALID escape    → parses "fine"      → polyline silently corrupted,
 *                                                  and a WRONG route is drawn
 *
 * The second is the dangerous one: it fails silently, ~650 m off, with no error.
 * So we cannot simply try/catch — the value has to be treated as a literal.
 *
 * We rewrite only `"polyline": "..."` values, doubling every backslash so the
 * parser hands back the exact bytes the encoder produced. Every other field
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

/**
 * Cut a decoded line into one GeoJSON feature per congestion band.
 *
 * Bands are half-open point ranges over this exact coordinate array, so they are
 * only meaningful next to the geometry they were computed against — the reason
 * both arrive from the same fetch. Each slice keeps its neighbour's first point
 * (`end + 1`) so the coloured segments join instead of showing hairline gaps at
 * every speed change.
 *
 * Out-of-range or inverted bands are dropped rather than clamped: a band that
 * doesn't fit the line is a sign the two came from different routes, and drawing
 * it anyway would paint congestion onto the wrong road.
 */
function trafficFeatures(coords: [number, number][], bands: TrafficBand[]): GeoJSON.Feature[] {
  const out: GeoJSON.Feature[] = []
  for (const band of bands) {
    const start = Math.max(0, band.start)
    const end = Math.min(coords.length - 1, band.end)
    if (!(end > start)) continue
    out.push({
      type: 'Feature',
      properties: { speed: band.speed },
      geometry: { type: 'LineString', coordinates: coords.slice(start, end + 1) },
    })
  }
  return out
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
    // Street names appear once the owner zooms in far enough to be choosing a
    // turn rather than reading a shape. Below z13 they are noise over the route.
    { id: 'road-labels', type: 'symbol', source: 'openmaptiles', 'source-layer': 'transportation_name',
      minzoom: 13,
      layout: { 'text-field': '{name}', 'text-font': ['Noto Sans Regular'], 'text-size': 11,
        'symbol-placement': 'line' },
      paint: { 'text-color': '#6f97a8', 'text-halo-color': '#050a0e', 'text-halo-width': 1.1 } },
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

/** "1.4 km" / "600 m" — metres below a kilometre, where the turn actually is. */
function fmtDistance(metres?: number): string {
  if (metres == null) return ''
  return metres >= 1000 ? `${(metres / 1000).toFixed(1)} km` : `${Math.round(metres)} m`
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

/** Igor call with the session's key. Null on any failure — every caller here
 * degrades to "that part of the card is missing", never to a wrong drawing. */
function useIgor(): (path: string) => Promise<any | null> {
  const { state } = useChatContext()
  const apiBase = state.config?.apiBase
  const apiKey = state.config?.apiKey
  return useCallback(async (path: string) => {
    if (!apiBase) return null
    try {
      const res = await fetch(`${apiBase}${path}`, {
        headers: apiKey ? { 'X-API-Key': apiKey } : {},
      })
      return res.ok ? await res.json() : null
    } catch {
      return null
    }
  }, [apiBase, apiKey])
}

/**
 * Fills in geometry, congestion and turn lists for routes that arrived as a
 * routeId. Returns null while a fetch is outstanding so the card renders once,
 * complete, rather than drawing a half-empty map and then jumping.
 *
 * A failed fetch yields a route with no polyline, which draws no line — the
 * honest outcome. Never fabricate geometry to fill the gap.
 */
function useResolvedRoutes(spec: MapSpec | null): MapSpec | null {
  const igor = useIgor()
  const [resolved, setResolved] = useState<MapSpec | null>(null)

  const pending = !!spec?.routes?.some(r => !r.polyline && r.routeId)

  useEffect(() => {
    if (!spec) { setResolved(null); return }
    if (!pending) { setResolved(spec); return }
    let alive = true
    ;(async () => {
      const routes = await Promise.all(
        (spec.routes ?? []).map(async r => {
          if (r.polyline || !r.routeId) return r
          const body = await igor(`/navigation/route/${r.routeId}`)
          if (!body) return r
          return {
            ...r,
            polyline: (body.polyline as string) || '',
            traffic: (body.traffic as TrafficBand[]) ?? [],
            steps: (body.steps as RouteStep[]) ?? [],
            // The fence's figures win — they are what the model told the owner
            // in prose, and a card disagreeing with the sentence above it is
            // worse than a card missing a field. The store only fills the gaps.
            label: r.label ?? (body.label as string | undefined),
            distanceKm: r.distanceKm ?? (body.distanceKm as number | undefined),
            durationMin: r.durationMin ?? (body.durationMin as number | undefined),
            noTrafficMin: r.noTrafficMin ?? (body.noTrafficMin as number | undefined),
          }
        }),
      )
      if (alive) setResolved({ ...spec, routes })
    })()
    return () => { alive = false }
  }, [spec, pending, igor])

  return resolved
}

/**
 * Resolves a placesId into the full result set.
 *
 * Undefined = nothing to fetch; null = fetch failed or is still running. A
 * failure leaves the card with its routes and markers and no place list, which
 * is exactly what it looked like before places existed — never a set of pins
 * with invented names.
 */
function usePlaces(placesId?: string): MapPlace[] | null | undefined {
  const igor = useIgor()
  const [places, setPlaces] = useState<MapPlace[] | null | undefined>(placesId ? null : undefined)

  useEffect(() => {
    if (!placesId) { setPlaces(undefined); return }
    let alive = true
    setPlaces(null)
    ;(async () => {
      const body = await igor(`/navigation/places/${placesId}`)
      if (!alive) return
      const list = body?.places
      setPlaces(Array.isArray(list) ? (list as MapPlace[]) : null)
    })()
    return () => { alive = false }
  }, [placesId, igor])

  return places
}

/* ── Component ───────────────────────────────────────────────────────────────── */

export default function MapBlock({ children }: { children: string }): React.ReactElement {
  const parsed = useMemo<MapSpec | null>(() => {
    try {
      const s = JSON.parse(repairFence(children)) as MapSpec
      const hasContent =
        (s.markers?.length ?? 0) > 0 || (s.routes?.length ?? 0) > 0 || !!s.center || !!s.places
      return hasContent ? s : null
    } catch {
      return null
    }
  }, [children])

  const spec = useResolvedRoutes(parsed)
  const places = usePlaces(parsed?.places)

  // Which route the card is currently about. Starts on the model's pick and
  // then belongs to the owner — every readout, the drawn line, the turn list
  // and NAVIGATE all follow this one number.
  const routes = spec?.routes ?? []
  const initial = Math.max(0, routes.findIndex(r => r.primary))
  const [selected, setSelected] = useState(0)
  useEffect(() => { setSelected(initial) }, [initial, spec])

  const [openPlace, setOpenPlace] = useState<number | null>(null)
  useEffect(() => { setOpenPlace(null) }, [places])

  const [expanded, setExpanded] = useState(false)
  const [showSteps, setShowSteps] = useState(false)

  if (!parsed) return looksIncomplete(children) ? <Materializing /> : <ParseError raw={children} />
  if (!spec) return <Materializing />   // fetching route geometry

  const active = routes[selected] ?? routes[0]
  const place = places && openPlace != null ? places[openPlace] : undefined
  const focus = focusPoint(spec)

  // NAVIGATE targets whatever the card is currently about: an opened place
  // first, then the fence's own target, then a destination marker. An ORIGIN
  // marker is never a fallback — a places card carries one for "you are here",
  // and offering to navigate the owner to where they already stand is worse
  // than offering nothing.
  const navTarget: MapNavigate | undefined = place
    ? { lat: place.lat, lng: place.lng, mode: active?.mode, label: place.name }
    : spec.navigate
      ?? (spec.markers?.find(m => m.kind === 'destination') as MapNavigate | undefined)
      ?? (spec.markers?.find(m => m.kind !== 'origin') as MapNavigate | undefined)

  return (
    <MapPanel title={spec.title} route={active} placeCount={places?.length}>
      {routes.length > 1 && (
        <RouteTabs routes={routes} selected={selected} onSelect={setSelected} />
      )}

      <MapCanvas
        spec={spec}
        places={places ?? undefined}
        selectedRoute={selected}
        openPlace={openPlace}
        onPlaceClick={setOpenPlace}
        expanded={expanded}
        onToggleExpand={() => setExpanded(v => !v)}
      />

      {active?.traffic?.length ? <TrafficBar route={active} /> : null}

      {place
        ? <PlaceDetail place={place} onClose={() => setOpenPlace(null)} />
        : focus && <CoordinateFooter lat={focus.lat} lng={focus.lng} />}

      {places && places.length > 0 && (
        <PlaceList places={places} open={openPlace} onSelect={setOpenPlace} />
      )}

      {active?.steps?.length ? (
        <StepList
          steps={active.steps}
          open={showSteps}
          onToggle={() => setShowSteps(v => !v)}
        />
      ) : null}

      <MapActions target={navTarget} place={place} />
    </MapPanel>
  )
}

/* ── Header + route switcher ─────────────────────────────────────────────────── */

function MapPanel({ title, route, placeCount, children }: {
  title?: string; route?: MapRoute; placeCount?: number; children: React.ReactNode
}): React.ReactElement {
  const idx = title ? title.indexOf('_') : -1
  const main = idx > -1 ? title!.slice(0, idx) : title
  const sub = idx > -1 ? title!.slice(idx) : ''
  const readout = route
    ? [route.distanceKm != null ? `${route.distanceKm} KM` : '', route.durationMin != null ? `${route.durationMin} MIN` : '']
        .filter(Boolean).join(' · ')
    : placeCount != null ? `${placeCount} FOUND` : ''

  return (
    <div className="hb-glass-sm hb-widget" style={{
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

/**
 * The route switcher. Every option get_route returned, side by side with the
 * one number that decides between them (ETA) and the one that explains it
 * (delay vs free-flow). Picking one re-draws the map and re-points NAVIGATE —
 * comparing routes was the whole reason to ask for alternatives, and a card
 * that only ever showed the winner threw that away.
 */
function RouteTabs({ routes, selected, onSelect }: {
  routes: MapRoute[]; selected: number; onSelect: (i: number) => void
}): React.ReactElement {
  const fastest = routes.reduce(
    (best, r, i) => (r.durationMin != null && (routes[best].durationMin == null || r.durationMin < routes[best].durationMin!) ? i : best),
    0,
  )
  return (
    <div style={{
      display: 'flex', gap: 6, padding: '0 0.75rem 0.6rem',
      overflowX: 'auto', scrollbarWidth: 'thin',
    }}>
      {routes.map((r, i) => {
        const on = i === selected
        const delay = r.durationMin != null && r.noTrafficMin != null
          ? r.durationMin - r.noTrafficMin : null
        const heavy = delay != null && r.noTrafficMin! > 0 && delay / r.noTrafficMin! > 0.25
        return (
          <button
            key={r.routeId ?? i}
            onClick={() => onSelect(i)}
            title={r.label || undefined}
            style={{
              flex: '1 1 0', minWidth: 108, textAlign: 'left', cursor: 'pointer',
              padding: '0.4rem 0.55rem', borderRadius: 7,
              background: on ? 'rgba(var(--hb-accent-rgb),0.16)' : 'rgba(255,255,255,0.03)',
              border: `1px solid rgba(var(--hb-accent-rgb),${on ? 0.5 : 0.16})`,
              fontFamily: "'Rajdhani', sans-serif", lineHeight: 1.25,
            }}
          >
            <div style={{
              display: 'flex', alignItems: 'baseline', gap: 5,
              color: on ? 'var(--hb-cyan-bright)' : 'var(--hb-text-dim)',
            }}>
              <span style={{ fontSize: '0.92rem', fontWeight: 700 }}>
                {r.durationMin != null ? r.durationMin : '?'}
              </span>
              <span style={{ fontSize: '0.8125rem', letterSpacing: '0.12em' }}>MIN</span>
              {i === fastest && routes.length > 1 && (
                <span style={{ fontSize: '0.78rem', letterSpacing: '0.1em', color: TRAFFIC_COLORS.NORMAL }}>
                  FASTEST
                </span>
              )}
            </div>
            <div style={{
              fontSize: '0.8125rem', letterSpacing: '0.06em', color: 'var(--hb-text-faint)',
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }}>
              {[r.distanceKm != null ? `${r.distanceKm} km` : '', r.label || '']
                .filter(Boolean).join(' · ') || 'ROUTE'}
            </div>
            {delay != null && delay > 0 && (
              <div style={{
                fontSize: '0.78rem', letterSpacing: '0.08em',
                color: heavy ? 'var(--hb-amber)' : 'var(--hb-text-faint)',
              }}>+{delay} MIN TRAFFIC</div>
            )}
          </button>
        )
      })}
    </div>
  )
}

/* ── The map surface ─────────────────────────────────────────────────────────── */

function MapCanvas({ spec, places, selectedRoute, openPlace, onPlaceClick, expanded, onToggleExpand }: {
  spec: MapSpec
  places?: MapPlace[]
  selectedRoute: number
  openPlace: number | null
  onPlaceClick: (i: number | null) => void
  expanded: boolean
  onToggleExpand: () => void
}): React.ReactElement {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const placeMarkers = useRef<maplibregl.Marker[]>([])
  const [failed, setFailed] = useState<string | null>(null)
  const height = expanded ? 460 : (spec.height ?? 240)

  // Latest click handler without rebuilding the map: markers are created once,
  // and a marker holding a stale closure would open the wrong record after any
  // re-render.
  const clickRef = useRef(onPlaceClick)
  clickRef.current = onPlaceClick

  /* Build once per spec/places: sources, layers, markers, camera. */
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
    mapRef.current = map
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')

    // Everything below runs inside MapLibre's own async event dispatch, OUTSIDE
    // React's call stack — a throw here is NOT caught by the ErrorBoundary and
    // becomes an unhandled window error. Contain it here or not at all.
    map.on('load', () => {
      try {
      const bounds = new maplibregl.LngLatBounds()
      let hasBounds = false

      // Routes. Every route gets its full layer stack up front and selection
      // only flips opacity — a switcher that tore down and rebuilt sources on
      // every tap would flicker and re-fit the camera under the owner's hand.
      //
      // Laid down in passes rather than route by route, and that ordering is
      // the point: MapLibre draws in insertion order, so if route 0's layers all
      // went down before route 1's, selecting route 0 would leave it drawn
      // UNDERNEATH route 1's line wherever the two share a road. Every dim line
      // first, then every glow, then every bright line, then every congestion
      // overlay — so whichever route is selected is on top by construction.
      const geometry = (spec.routes ?? []).map(r => decodePolyline(r.polyline ?? ''))

      geometry.forEach((coords, i) => {
        if (coords.length < 2) return
        coords.forEach(c => { bounds.extend(c); hasBounds = true })
        map.addSource(`route-${i}`, {
          type: 'geojson',
          data: { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } },
        })
        map.addLayer({ id: `route-dim-${i}`, type: 'line', source: `route-${i}`,
          layout: { 'line-cap': 'round', 'line-join': 'round' },
          paint: { 'line-color': dim, 'line-opacity': 0.65, 'line-width': 2.5 } })
      })

      geometry.forEach((coords, i) => {
        if (coords.length < 2) return
        map.addLayer({ id: `route-glow-${i}`, type: 'line', source: `route-${i}`,
          layout: { 'line-cap': 'round', 'line-join': 'round' },
          paint: { 'line-color': accent, 'line-opacity': 0, 'line-width': 12 } })
      })

      geometry.forEach((coords, i) => {
        if (coords.length < 2) return
        // With congestion data this becomes the casing under the coloured
        // bands; without it, it IS the route and carries the accent.
        const hasTraffic = !!spec.routes?.[i].traffic?.length
        map.addLayer({ id: `route-line-${i}`, type: 'line', source: `route-${i}`,
          layout: { 'line-cap': 'round', 'line-join': 'round' },
          paint: {
            'line-color': hasTraffic ? '#0a1a22' : accentBright,
            'line-opacity': 0,
            'line-width': hasTraffic ? 7 : 4.5,
          } })
      })

      geometry.forEach((coords, i) => {
        const bands = trafficFeatures(coords, spec.routes?.[i].traffic ?? [])
        if (!bands.length) return
        map.addSource(`traffic-${i}`, {
          type: 'geojson', data: { type: 'FeatureCollection', features: bands },
        })
        map.addLayer({ id: `traffic-line-${i}`, type: 'line', source: `traffic-${i}`,
          layout: { 'line-cap': 'round', 'line-join': 'round' },
          paint: {
            'line-color': ['match', ['get', 'speed'],
              'NORMAL', TRAFFIC_COLORS.NORMAL,
              'SLOW', TRAFFIC_COLORS.SLOW,
              'TRAFFIC_JAM', TRAFFIC_COLORS.TRAFFIC_JAM,
              accentBright],
            'line-width': 5, 'line-opacity': 0,
          } })
      })

      // Fence markers — coloured HTML dots by kind.
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

      // Place markers — numbered to match the list below, and clickable, which
      // is the whole difference between "dots on a map" and a card the owner
      // can shop from.
      //
      // Two elements, not one: MapLibre owns the outer element's `transform`
      // and rewrites it on every camera frame, so any highlight scale written
      // there is erased the moment the map moves. The pin lives in a child the
      // library never touches.
      placeMarkers.current = (places ?? []).map((p, i) => {
        const wrap = document.createElement('div')
        const pin = document.createElement('div')
        pin.style.cssText =
          'width:22px;height:22px;border-radius:50%;cursor:pointer;' +
          'display:flex;align-items:center;justify-content:center;' +
          "font-family:'Rajdhani',sans-serif;font-size:11px;font-weight:700;" +
          `color:#04121a;background:${accent};` +
          'border:1.6px solid rgba(255,255,255,0.85);transition:transform .12s ease'
        pin.textContent = String(i + 1)
        pin.title = p.name
        pin.addEventListener('click', ev => {
          ev.stopPropagation()          // the map's own click clears the selection
          clickRef.current(i)
        })
        wrap.appendChild(pin)
        const marker = new maplibregl.Marker({ element: wrap }).setLngLat([p.lng, p.lat]).addTo(map)
        bounds.extend([p.lng, p.lat]); hasBounds = true
        return marker
      })

      // Tapping empty map closes the open record — the gesture everyone expects
      // from a bottom sheet.
      map.on('click', () => clickRef.current(null))

      // Camera: explicit centre wins; else fit everything.
      if (!spec.center && hasBounds) {
        try { map.fitBounds(bounds, { padding: 44, maxZoom: 15, duration: 0 }) } catch { /* pre-layout */ }
      }
      } catch (e) {
        console.error('[MapBlock] failed to draw route/marker layers', e)
        setFailed(e instanceof Error ? e.message : String(e))
      }
    })

    // Release the WebGL context on unmount. Browsers cap concurrent contexts, so
    // leaking one per map would eventually make every later map fail to build.
    return () => {
      placeMarkers.current = []
      mapRef.current = null
      try { map.remove() } catch { /* already gone */ }
    }
  }, [spec, places])

  /* Selection → paint. Cheap enough to run on every tap; no source churn. */
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const apply = () => {
      ;(spec.routes ?? []).forEach((_route, i) => {
        if (!map.getLayer(`route-line-${i}`)) return
        const on = i === selectedRoute
        map.setPaintProperty(`route-dim-${i}`, 'line-opacity', on ? 0 : 0.65)
        map.setPaintProperty(`route-glow-${i}`, 'line-opacity', on ? 0.26 : 0)
        map.setPaintProperty(`route-line-${i}`, 'line-opacity', on ? 1 : 0)
        if (map.getLayer(`traffic-line-${i}`)) {
          map.setPaintProperty(`traffic-line-${i}`, 'line-opacity', on ? 1 : 0)
        }
      })
    }
    if (map.isStyleLoaded() && map.getLayer('route-line-0')) apply()
    else map.once('idle', apply)
  }, [spec, selectedRoute])

  /* Opened place → fly to it and lift its marker. */
  useEffect(() => {
    placeMarkers.current.forEach((marker, i) => {
      const pin = marker.getElement().firstElementChild as HTMLElement | null
      if (!pin) return
      const on = i === openPlace
      pin.style.transform = on ? 'scale(1.35)' : 'scale(1)'
      pin.style.boxShadow = on ? '0 0 14px rgba(var(--hb-accent-rgb),0.9)' : 'none'
      marker.getElement().style.zIndex = on ? '3' : '1'
    })
    const map = mapRef.current
    if (map && openPlace != null && placeMarkers.current[openPlace]) {
      map.easeTo({ center: placeMarkers.current[openPlace].getLngLat(), duration: 420 })
    }
  }, [openPlace])

  /* The container resized under a live canvas — MapLibre only notices if told. */
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const t = setTimeout(() => { try { map.resize() } catch { /* gone */ } }, 60)
    return () => clearTimeout(t)
  }, [expanded])

  return (
    <div style={{ position: 'relative', height, margin: '0 0.25rem', transition: 'height 0.2s ease' }}>
      {/* Ground behind the map, so a dead tile server still reads as design.
          This was a 28px wireframe GRID — a banned prop, and the one piece of
          set dressing the map still carried. The deck draws the same idea as a
          flat dark ground, which the canvas covers anyway when it loads. */}
      <div style={{
        position: 'absolute', inset: 0, pointerEvents: 'none',
        background: 'linear-gradient(160deg, var(--hb-petrol), var(--hb-void))',
      }} />
      <div ref={containerRef} style={{ position: 'absolute', inset: 0, borderRadius: 4, overflow: 'hidden' }} />
      {!failed && (
        <button
          onClick={onToggleExpand}
          title={expanded ? 'Collapse map' : 'Expand map'}
          className="glass-round"
          style={{
            position: 'absolute', left: 20, top: 20, zIndex: 2, cursor: 'pointer',
            display: 'flex', alignItems: 'center', gap: 8, height: 34, padding: '0 14px',
            background: 'linear-gradient(160deg, rgba(255,255,255,0.08), rgba(255,255,255,0.02))',
            border: '1px solid rgba(255,255,255,0.12)',
            backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)',
            color: 'var(--hb-text)', fontSize: '0.845rem',
          }}
        >{expanded ? 'Shrink' : 'Expand'}</button>
      )}
      {failed && (
        // The wireframe behind still reads as design, so this stays a quiet
        // caption rather than a full error panel — the route figures below
        // (distance, duration, NAVIGATE) remain usable without the canvas.
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

/* ── Readouts ────────────────────────────────────────────────────────────────── */

/**
 * The congestion bar: the same green/amber/red as the line, as one proportional
 * strip. It answers "how bad, and is it bad the whole way or in one place" at a
 * glance — which the +N MIN number alone never could.
 */
function TrafficBar({ route }: { route: MapRoute }): React.ReactElement | null {
  const bands = route.traffic ?? []
  const spans: Record<string, number> = { NORMAL: 0, SLOW: 0, TRAFFIC_JAM: 0 }
  bands.forEach(b => { spans[b.speed] = (spans[b.speed] ?? 0) + Math.max(b.end - b.start, 0) })
  const total = spans.NORMAL + spans.SLOW + spans.TRAFFIC_JAM
  if (!total) return null

  const delay = route.durationMin != null && route.noTrafficMin != null
    ? route.durationMin - route.noTrafficMin : null
  const heavy = delay != null && route.noTrafficMin! > 0 && delay / route.noTrafficMin! > 0.25

  return (
    <div style={{ padding: '0.5rem 0.75rem 0', fontFamily: "'Rajdhani', sans-serif" }}>
      <div style={{ display: 'flex', height: 5, borderRadius: 3, overflow: 'hidden', gap: 1 }}>
        {(['NORMAL', 'SLOW', 'TRAFFIC_JAM'] as const).map(s => spans[s] > 0 && (
          <div key={s} style={{ flex: spans[s], background: TRAFFIC_COLORS[s] }} title={TRAFFIC_WORDS[s]} />
        ))}
      </div>
      <div style={{
        display: 'flex', gap: 10, marginTop: 5, flexWrap: 'wrap',
        fontSize: '0.8125rem', letterSpacing: '0.1em', color: 'var(--hb-text-faint)',
      }}>
        {(['NORMAL', 'SLOW', 'TRAFFIC_JAM'] as const).map(s => spans[s] > 0 && (
          <span key={s} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 7, height: 7, borderRadius: 2, background: TRAFFIC_COLORS[s] }} />
            {Math.round((100 * spans[s]) / total)}% {TRAFFIC_WORDS[s]}
          </span>
        ))}
        {delay != null && delay > 0 && (
          <span style={{ color: heavy ? 'var(--hb-amber)' : 'var(--hb-text-dim)' }}>
            +{delay} MIN VS FREE-FLOW
          </span>
        )}
      </div>
    </div>
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
          <span style={{ color: 'var(--hb-text-dim)', fontSize: '0.875rem' }}>{address}</span>
        )}
      </div>
    </div>
  )
}

/* ── Places ──────────────────────────────────────────────────────────────────── */

/** Rating, open state, price, distance — the line that decides between two
 * otherwise identical results. */
function placeMeta(p: MapPlace): React.ReactElement {
  const bits: React.ReactNode[] = []
  if (p.rating != null) {
    bits.push(<span key="r" style={{ color: 'var(--hb-cyan-bright)' }}>
      {p.rating.toFixed(1)}★{p.reviews ? ` (${p.reviews})` : ''}
    </span>)
  }
  if (p.openNow != null) {
    bits.push(<span key="o" style={{ color: p.openNow ? TRAFFIC_COLORS.NORMAL : 'var(--hb-amber)' }}>
      {p.openNow ? 'OPEN' : 'CLOSED'}
    </span>)
  }
  if (p.distanceKm != null) bits.push(<span key="d">{p.distanceKm} km</span>)
  if (p.priceLevel) bits.push(<span key="p">{p.priceLevel}</span>)
  if (p.status) bits.push(<span key="s" style={{ color: 'var(--hb-amber)' }}>{p.status.replace(/_/g, ' ').toLowerCase()}</span>)
  return (
    <span style={{ display: 'inline-flex', gap: 8, flexWrap: 'wrap', fontSize: '0.8125rem', letterSpacing: '0.06em', color: 'var(--hb-text-faint)' }}>
      {bits}
    </span>
  )
}

/**
 * The result list. Numbered to match the map pins, so "the third one" means the
 * same thing in the chat, on the list and on the map. Clicking a row is the same
 * gesture as clicking its pin.
 */
function PlaceList({ places, open, onSelect }: {
  places: MapPlace[]; open: number | null; onSelect: (i: number | null) => void
}): React.ReactElement {
  return (
    <div style={{
      margin: '0.55rem 0.5rem 0', maxHeight: 190, overflowY: 'auto',
      fontFamily: "'Rajdhani', sans-serif", scrollbarWidth: 'thin',
    }}>
      {places.map((p, i) => {
        const on = i === open
        return (
          <button
            key={p.placeId ?? `${p.lat},${p.lng},${i}`}
            onClick={() => onSelect(on ? null : i)}
            style={{
              display: 'flex', alignItems: 'flex-start', gap: 8, width: '100%',
              textAlign: 'left', cursor: 'pointer', padding: '0.4rem 0.5rem',
              background: on ? 'rgba(var(--hb-accent-rgb),0.14)' : 'transparent',
              border: 'none', borderLeft: `2px solid ${on ? 'var(--hb-cyan-bright)' : 'transparent'}`,
              borderRadius: 4,
            }}
          >
            <span style={{
              flex: '0 0 auto', width: 17, height: 17, borderRadius: '50%',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: on ? 'var(--hb-cyan-bright)' : 'rgba(var(--hb-accent-rgb),0.28)',
              color: on ? '#04121a' : 'var(--hb-cyan-bright)',
              fontSize: '0.78rem', fontWeight: 700, marginTop: 1,
            }}>{i + 1}</span>
            <span style={{ minWidth: 0, lineHeight: 1.3 }}>
              <span style={{
                display: 'block', fontSize: '0.78rem', color: on ? '#fff' : 'var(--hb-text-dim)',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              }}>{p.name}</span>
              {placeMeta(p)}
            </span>
          </button>
        )
      })}
    </div>
  )
}

/** The opened record: everything the owner needs before deciding to go, and the
 * actions to act on it — call, open the site, navigate there. */
function PlaceDetail({ place, onClose }: { place: MapPlace; onClose: () => void }): React.ReactElement {
  const [hoursOpen, setHoursOpen] = useState(false)
  return (
    <div style={{
      margin: '0.55rem 0.5rem 0', padding: '0.55rem 0.65rem', borderRadius: 8,
      background: 'rgba(var(--hb-accent-rgb),0.09)',
      border: '1px solid rgba(var(--hb-accent-rgb),0.28)',
      fontFamily: "'Rajdhani', sans-serif",
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
        <div style={{ flex: 1, minWidth: 0, lineHeight: 1.35 }}>
          <div style={{ color: '#fff', fontSize: '0.92rem', fontWeight: 700 }}>{place.name}</div>
          {place.category && (
            <div style={{ fontSize: '0.8125rem', letterSpacing: '0.1em', color: 'var(--hb-cyan)', textTransform: 'uppercase' }}>
              {place.category}
            </div>
          )}
          <div style={{ marginTop: 2 }}>{placeMeta(place)}</div>
          {place.address && (
            <div style={{ marginTop: 3, fontSize: '0.875rem', color: 'var(--hb-text-dim)' }}>{place.address}</div>
          )}
          {place.summary && (
            <div style={{ marginTop: 3, fontSize: '0.875rem', color: 'var(--hb-text-faint)' }}>{place.summary}</div>
          )}
        </div>
        <button onClick={onClose} title="Close" style={{
          cursor: 'pointer', background: 'transparent', border: 'none',
          color: 'var(--hb-text-faint)', fontSize: '0.85rem', lineHeight: 1, padding: 2,
        }}>✕</button>
      </div>

      {place.hours && place.hours.length > 0 && (
        <div style={{ marginTop: 5 }}>
          <button onClick={() => setHoursOpen(v => !v)} style={{
            cursor: 'pointer', background: 'transparent', border: 'none', padding: 0,
            color: 'var(--hb-cyan)', fontFamily: "'Rajdhani', sans-serif",
            fontSize: '0.8125rem', letterSpacing: '0.12em', fontWeight: 700,
          }}>{hoursOpen ? '▾ HOURS' : '▸ HOURS'}</button>
          {hoursOpen && (
            <div style={{ marginTop: 3, fontSize: '0.845rem', color: 'var(--hb-text-dim)', lineHeight: 1.5 }}>
              {place.hours.map((h, i) => <div key={i}>{h}</div>)}
            </div>
          )}
        </div>
      )}

      <div style={{ display: 'flex', gap: 6, marginTop: 7, flexWrap: 'wrap' }}>
        {place.phone && (
          <ActionChip onClick={() => openExternal(`tel:${place.phone!.replace(/\s/g, '')}`)}>
            ☏ {place.phone}
          </ActionChip>
        )}
        {place.website && <ActionChip onClick={() => openExternal(place.website!)}>⧉ WEBSITE</ActionChip>}
        {place.mapsUri && <ActionChip onClick={() => openExternal(place.mapsUri!)}>◎ GOOGLE PAGE</ActionChip>}
      </div>
    </div>
  )
}

/* ── Turn-by-turn ────────────────────────────────────────────────────────────── */

/** The turn list, collapsed by default: the card answers "how long" first, and
 * "which way, exactly" only when asked. */
function StepList({ steps, open, onToggle }: {
  steps: RouteStep[]; open: boolean; onToggle: () => void
}): React.ReactElement {
  return (
    <div style={{ margin: '0.55rem 0.75rem 0', fontFamily: "'Rajdhani', sans-serif" }}>
      <button onClick={onToggle} style={{
        cursor: 'pointer', background: 'transparent', border: 'none', padding: 0,
        color: 'var(--hb-cyan)', fontFamily: "'Rajdhani', sans-serif",
        fontSize: '0.8125rem', letterSpacing: '0.14em', fontWeight: 700,
      }}>{open ? '▾' : '▸'} {steps.length} {steps.length === 1 ? 'STEP' : 'STEPS'}</button>
      {open && (
        <ol style={{
          margin: '5px 0 0', padding: 0, listStyle: 'none',
          maxHeight: 210, overflowY: 'auto', scrollbarWidth: 'thin',
        }}>
          {steps.map((s, i) => (
            <li key={i} style={{
              display: 'flex', gap: 8, padding: '3px 0',
              borderTop: i ? '1px solid rgba(var(--hb-accent-rgb),0.10)' : 'none',
            }}>
              <span style={{ flex: '0 0 auto', width: 20, color: 'var(--hb-text-faint)', fontSize: '0.8125rem' }}>
                {i + 1}
              </span>
              <span style={{ flex: 1, fontSize: '0.72rem', color: 'var(--hb-text-dim)', lineHeight: 1.35 }}>
                {s.instruction}
              </span>
              <span style={{ flex: '0 0 auto', fontSize: '0.8125rem', color: 'var(--hb-text-faint)' }}>
                {fmtDistance(s.distanceM)}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

/* ── Actions ─────────────────────────────────────────────────────────────────── */

function MapActions({ target, place }: {
  target?: MapNavigate; place?: MapPlace
}): React.ReactElement | null {
  if (!target) return null
  // Naming the destination matters once a place is open: the same button now
  // means "take me to THIS one", and an unlabelled NAVIGATE would be a coin flip.
  const label = place ? `▸ NAVIGATE · ${place.name.toUpperCase()}` : '▸ NAVIGATE'
  return (
    <div style={{ display: 'flex', gap: 8, padding: '0.6rem 0.75rem 0' }}>
      <ActionChip filled grow onClick={() => openExternal(mapsDirUrl(target))}>{label}</ActionChip>
      <ActionChip grow onClick={() => openExternal(mapsDirUrl(target))}>⧉ OPEN IN MAPS</ActionChip>
    </div>
  )
}

function ActionChip({ filled, grow, onClick, children }: {
  filled?: boolean; grow?: boolean; onClick: () => void; children: React.ReactNode
}): React.ReactElement {
  return (
    <button onClick={onClick} style={{
      flex: grow ? 1 : '0 0 auto', cursor: 'pointer',
      padding: grow ? '0.55rem 0.5rem' : '0.35rem 0.6rem',
      maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
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
    }}>Map — could not parse<div style={{ color: 'var(--hb-text-faint)', marginTop: 4 }}>{raw.slice(0, 120)}</div></div>
  )
}

function Materializing(): React.ReactElement {
  return (
    <div className="hb-glass-sm" style={{
      margin: '0.75rem 0', padding: '0.75rem', border: '1px solid var(--hb-edge)',
      background: 'rgba(6,14,22,0.55)',
    }}>
      <Skeleton height={220} radius={8} />
      <div style={{ marginTop: 10 }}>
        <SkeletonText lines={1} lastWidth="42%" lineHeight={11} />
      </div>
    </div>
  )
}
