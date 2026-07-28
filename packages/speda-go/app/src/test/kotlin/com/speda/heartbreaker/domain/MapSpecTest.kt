package com.speda.heartbreaker.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Parser/decoder gate for the ```map fence. Covers the streaming-safety contract
 * (half JSON → null, not a crash), lenient field defaulting, and the Google
 * polyline decode against a published reference vector.
 */
class MapSpecTest {

    @Test
    fun parses_full_spec_with_routes_and_markers() {
        val raw = """
        {
          "title": "ROUTE_HOME",
          "center": { "lat": 41.04, "lng": 29.01 },
          "zoom": 12,
          "markers": [
            { "lat": 41.04, "lng": 29.01, "label": "YOU", "kind": "origin" },
            { "lat": 41.11, "lng": 29.02, "label": "HOME", "kind": "destination" }
          ],
          "routes": [
            { "polyline": "abc", "label": "VIA D-100", "durationMin": 34,
              "noTrafficMin": 22, "distanceKm": 18.4, "mode": "drive", "primary": true }
          ],
          "navigate": { "lat": 41.11, "lng": 29.02, "mode": "drive", "label": "HOME" },
          "autoNavigate": true
        }
        """.trimIndent()

        val spec = parseMapSpec(raw)!!
        assertEquals("ROUTE_HOME", spec.title)
        assertEquals(2, spec.markers.size)
        assertEquals("origin", spec.markers[0].kind)
        assertEquals(1, spec.routes.size)
        assertTrue(spec.autoNavigate)
        assertEquals(41.11, spec.navigate!!.lat, 1e-9)

        val primary = spec.primaryRoute!!
        assertTrue(primary.primary)
        assertEquals(12, primary.trafficDelayMin)   // 34 − 22
    }

    @Test
    fun marker_kind_defaults_to_pin_and_unknown_keys_ignored() {
        val spec = parseMapSpec(
            """{ "markers": [ { "lat": 1.0, "lng": 2.0, "wat": "x" } ] }"""
        )!!
        assertEquals("pin", spec.markers[0].kind)
    }

    @Test
    fun half_streamed_json_returns_null_not_crash() {
        assertNull(parseMapSpec("""{ "markers": [ { "lat": 41.0, "lng": 2"""))
        assertTrue(looksIncomplete("""{ "routes": [ { "polyline": "ab"""))
    }

    @Test
    fun empty_but_valid_json_is_null() {
        // Valid JSON with nothing renderable → null so the fence shows the code block,
        // not an empty map card.
        assertNull(parseMapSpec("""{ "title": "X" }"""))
    }

    @Test
    fun route_without_polyline_is_dropped() {
        val spec = parseMapSpec(
            """{ "markers":[{"lat":1.0,"lng":2.0}], "routes": [ { "label": "no geometry" } ] }"""
        )!!
        assertEquals(0, spec.routes.size)
        assertEquals(1, spec.markers.size)
    }

    @Test
    fun traffic_delay_null_when_no_congestion() {
        val spec = parseMapSpec(
            """{ "routes":[{"polyline":"a","durationMin":20,"noTrafficMin":22,"primary":true}] }"""
        )!!
        // duration <= noTraffic → no positive delay to report.
        assertNull(spec.primaryRoute!!.trafficDelayMin)
    }

    @Test
    fun decodes_google_reference_polyline() {
        // Google's canonical example: "_p~iF~ps|U_ulLnnqC_mqNvxq`@" →
        // (38.5,-120.2), (40.7,-120.95), (43.252,-126.453).
        val pts = decodePolyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
        assertEquals(3, pts.size)
        assertEquals(38.5, pts[0].lat, 1e-5)
        assertEquals(-120.2, pts[0].lng, 1e-5)
        assertEquals(40.7, pts[1].lat, 1e-5)
        assertEquals(-120.95, pts[1].lng, 1e-5)
        assertEquals(43.252, pts[2].lat, 1e-5)
        assertEquals(-126.453, pts[2].lng, 1e-5)
    }

    @Test
    fun malformed_polyline_tail_stops_cleanly() {
        // A truncated encoded string must not throw — a half-streamed fence.
        val pts = decodePolyline("_p~iF~ps|U_ulL")
        assertTrue(pts.isNotEmpty())
    }
}
