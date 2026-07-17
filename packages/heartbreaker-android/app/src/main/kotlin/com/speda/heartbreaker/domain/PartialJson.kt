package com.speda.heartbreaker.domain

/**
 * A streamed ```chart / ```calendar fence is valid JSON only once the model
 * finishes emitting it — every partial reveal in between is, by definition,
 * unparseable. Treating every parse failure as an error flashes a scary
 * "PARSE ERROR" for the ~1s it takes to stream, which then vanishes.
 *
 * This distinguishes the two cases cheaply (no parse) by checking whether
 * braces/brackets/strings are balanced. Unbalanced = still streaming, show a
 * quiet placeholder. Balanced but unparseable = a genuinely malformed spec.
 *
 * Literal port of lib/partialJson.ts.
 */
fun looksIncomplete(s: String): Boolean {
    var depth = 0
    var inString = false
    var escaped = false
    for (ch in s) {
        if (inString) {
            when {
                escaped -> escaped = false
                ch == '\\' -> escaped = true
                ch == '"' -> inString = false
            }
            continue
        }
        when (ch) {
            '"' -> inString = true
            '{', '[' -> depth++
            '}', ']' -> depth--
        }
    }
    return inString || depth != 0
}
