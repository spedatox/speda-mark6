/**
 * Throwaway dev harness for MapBlock — renders the two map fences (a route with
 * alternatives + live traffic, and a places result set) against a stubbed Igor.
 * Not part of the app; delete once the card is verified.
 */
import { createRoot } from 'react-dom/client'
import MapBlock from '../src/renderer/src/components/MapBlock'
import { ChatContext } from '../src/renderer/src/store/chat'
import '../src/renderer/src/theme/heartbreaker.css'

/* A believable Bursa drive: ~30 points, then Google-encoded like the real API. */
function encode(coords: [number, number][]): string {
  const enc = (v: number): string => {
    let value = v < 0 ? ~(v << 1) : v << 1
    let out = ''
    while (value >= 0x20) { out += String.fromCharCode((0x20 | (value & 0x1f)) + 63); value >>= 5 }
    return out + String.fromCharCode(value + 63)
  }
  let lat = 0, lng = 0, out = ''
  for (const [la, ln] of coords) {
    const rlat = Math.round(la * 1e5), rlng = Math.round(ln * 1e5)
    out += enc(rlat - lat) + enc(rlng - lng)
    lat = rlat; lng = rlng
  }
  return out
}

function line(from: [number, number], to: [number, number], n: number, wobble = 0): [number, number][] {
  return Array.from({ length: n }, (_, i) => {
    const t = i / (n - 1)
    return [
      from[0] + (to[0] - from[0]) * t + Math.sin(t * Math.PI * 3) * wobble,
      from[1] + (to[1] - from[1]) * t,
    ] as [number, number]
  })
}

const ROUTE_A = encode(line([40.2100, 28.9600], [40.2450, 29.0600], 34, 0.004))
const ROUTE_B = encode(line([40.2100, 28.9600], [40.2450, 29.0600], 28, -0.010))

const ROUTES: Record<string, any> = {
  r_aaaa1111: {
    routeId: 'r_aaaa1111', polyline: ROUTE_A, mode: 'drive',
    traffic: [
      { start: 0, end: 11, speed: 'NORMAL' },
      { start: 11, end: 20, speed: 'SLOW' },
      { start: 20, end: 26, speed: 'TRAFFIC_JAM' },
      { start: 26, end: 33, speed: 'NORMAL' },
    ],
    steps: [
      { instruction: 'Head north on Fatih Sultan Mehmet Blv', distanceM: 1200 },
      { instruction: 'Turn right onto D-575', distanceM: 3400 },
      { instruction: 'Keep left at the fork, follow signs for Osmangazi', distanceM: 800 },
      { instruction: 'Turn left onto Ankara Yolu Cd', distanceM: 2600 },
      { instruction: 'Destination will be on the right', distanceM: 140 },
    ],
  },
  r_bbbb2222: {
    routeId: 'r_bbbb2222', polyline: ROUTE_B, mode: 'drive',
    traffic: [
      { start: 0, end: 18, speed: 'NORMAL' },
      { start: 18, end: 27, speed: 'SLOW' },
    ],
    steps: [{ instruction: 'Head west on the coast road', distanceM: 5200 }],
  },
}

const PLACES = {
  placesId: 'pl_cafe0001',
  query: 'berber',
  center: { lat: 40.2100, lng: 28.9600 },
  places: [
    { placeId: 'p1', name: 'Kuaför Emre', lat: 40.2118, lng: 28.9633, address: 'Nilüfer, Bursa',
      category: 'Barber shop', rating: 4.7, reviews: 231, openNow: true, priceLevel: 'moderate',
      phone: '+90 224 000 00 01', website: 'https://example.com',
      mapsUri: 'https://maps.google.com/?cid=1', distanceKm: 0.4,
      hours: ['Monday: 09:00–20:00', 'Tuesday: 09:00–20:00', 'Wednesday: 09:00–20:00'],
      summary: 'Traditional barbershop with hot-towel shaves.' },
    { placeId: 'p2', name: 'Barber House Bursa', lat: 40.2075, lng: 28.9571, address: 'Görükle',
      category: 'Barber shop', rating: 4.2, reviews: 88, openNow: false, distanceKm: 0.6 },
    { placeId: 'p3', name: 'Erkek Kuaförü Mert', lat: 40.2143, lng: 28.9548,
      category: 'Hair salon', rating: 4.9, reviews: 12, openNow: true, distanceKm: 0.9,
      phone: '+90 224 000 00 03' },
    { placeId: 'p4', name: 'Old Town Barber', lat: 40.2061, lng: 28.9682, rating: 3.8,
      reviews: 402, openNow: true, distanceKm: 1.1, status: 'CLOSED_TEMPORARILY' },
  ],
}

// Stub Igor + Nominatim. Everything the card fetches, nothing real.
const realFetch = window.fetch.bind(window)
window.fetch = (async (input: any, init?: any) => {
  const url = String(typeof input === 'string' ? input : input.url)
  const json = (body: any): Response =>
    new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  const route = url.match(/\/navigation\/route\/(\w+)/)
  if (route) return json(ROUTES[route[1]] ?? {})
  if (url.includes('/navigation/places/')) return json(PLACES)
  if (url.includes('nominatim')) return json({ address: { road: 'Ankara Yolu Cd', city: 'Bursa' } })
  return realFetch(input, init)
}) as typeof window.fetch

const ROUTE_FENCE = JSON.stringify({
  title: 'ROUTE_HOME',
  markers: [
    { lat: 40.2100, lng: 28.9600, label: 'YOU', kind: 'origin' },
    { lat: 40.2450, lng: 29.0600, label: 'HOME', kind: 'destination' },
  ],
  routes: [
    { routeId: 'r_aaaa1111', label: 'VIA D-575', durationMin: 34, noTrafficMin: 22, distanceKm: 18.4, mode: 'drive', primary: true },
    { routeId: 'r_bbbb2222', label: 'VIA COAST', durationMin: 41, noTrafficMin: 37, distanceKm: 21.0, mode: 'drive' },
  ],
  navigate: { lat: 40.2450, lng: 29.0600, mode: 'drive', label: 'HOME' },
}, null, 2)

const PLACES_FENCE = JSON.stringify({
  title: 'BARBERS_NEARBY',
  places: 'pl_cafe0001',
  markers: [{ lat: 40.2100, lng: 28.9600, label: 'YOU', kind: 'origin' }],
}, null, 2)

const ctx = {
  state: { config: { apiBase: 'http://localhost:9999', apiKey: 'test' } } as any,
  dispatch: (() => {}) as any,
}

createRoot(document.getElementById('root')!).render(
  <ChatContext.Provider value={ctx}>
    <div style={{ maxWidth: 720, margin: '0 auto', padding: 24, background: '#05090e', minHeight: '100vh' }}>
      <MapBlock>{ROUTE_FENCE}</MapBlock>
      <MapBlock>{PLACES_FENCE}</MapBlock>
    </div>
  </ChatContext.Provider>,
)
