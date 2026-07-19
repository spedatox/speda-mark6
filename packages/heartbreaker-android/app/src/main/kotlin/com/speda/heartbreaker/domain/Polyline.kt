package com.speda.heartbreaker.domain

/**
 * Google Encoded Polyline Algorithm Format decoder.
 *
 * get_route (Routes API v2) returns route geometry as an encoded polyline string
 * to keep the ```map fence compact — a 20 km route is ~1–2 KB encoded versus tens
 * of KB as a lat/lng array. This expands it back to points for drawing.
 *
 * Reference: https://developers.google.com/maps/documentation/utilities/polylinealgorithm
 * Precision is the standard 1e-5 (5 decimal places).
 */
fun decodePolyline(encoded: String): List<LatLng> {
    val points = ArrayList<LatLng>()
    var index = 0
    val len = encoded.length
    var lat = 0
    var lng = 0

    while (index < len) {
        lat += decodeChunk(encoded, index).let { (delta, next) -> index = next; delta }
        lng += decodeChunk(encoded, index).let { (delta, next) -> index = next; delta }
        points.add(LatLng(lat / 1e5, lng / 1e5))
    }
    return points
}

/**
 * Reads one varint-style value starting at [start]. Returns the signed delta and
 * the index just past the consumed chunk. Malformed tails stop cleanly rather
 * than throwing — a half-streamed fence must degrade, not crash.
 */
private fun decodeChunk(s: String, start: Int): Pair<Int, Int> {
    var index = start
    var shift = 0
    var result = 0
    while (index < s.length) {
        val b = s[index].code - 63
        index++
        result = result or ((b and 0x1f) shl shift)
        shift += 5
        if (b < 0x20) break
    }
    // zig-zag decode: even → positive, odd → negative
    val delta = if (result and 1 != 0) (result shr 1).inv() else result shr 1
    return delta to index
}
