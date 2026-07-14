/**
 * A streamed ```chart / ```calendar code fence is valid JSON only once the
 * model finishes emitting it — every partial reveal in between is, by
 * definition, unparseable. Naively treating every JSON.parse failure as an
 * error flashes a scary "PARSE ERROR" banner for the ~1s it takes to stream,
 * which then vanishes once it completes. Bad UX for something that isn't
 * actually wrong.
 *
 * This distinguishes the two cases cheaply (no JSON.parse) by checking
 * whether braces/brackets/strings are balanced. Unbalanced = still streaming,
 * show a quiet placeholder. Balanced but still unparseable = a genuine
 * malformed spec from the model, show the real error.
 */
export function looksIncomplete(s: string): boolean {
  let depth = 0
  let inString = false
  let escaped = false
  for (const ch of s) {
    if (inString) {
      if (escaped) escaped = false
      else if (ch === '\\') escaped = true
      else if (ch === '"') inString = false
      continue
    }
    if (ch === '"') inString = true
    else if (ch === '{' || ch === '[') depth++
    else if (ch === '}' || ch === ']') depth--
  }
  return inString || depth !== 0
}
