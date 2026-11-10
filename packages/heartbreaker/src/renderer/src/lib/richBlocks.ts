// SPDX-License-Identifier: AGPL-3.0-or-later

export interface TableData { headers: string[]; rows: string[][] }

/** Repair a fence attached to prose without rewriting code inside a fence. */
export function normalizeCodeFences(text: string): string {
  let fence = ''
  return text.split('\n').map(line => {
    if (fence) {
      if (new RegExp(`^[ \\t]*${fence[0]}{${fence.length},}[ \\t]*$`).test(line)) fence = ''
      return line
    }
    const opening = /^[ \t]{0,3}(`{3,}|~{3,})/.exec(line)
    if (opening) { fence = opening[1]; return line }
    const attached = /^(.*\S)(```[\w-]+(?:\s*\|[^\n]*)?)$/.exec(line)
    if (attached && !attached[1].endsWith('`')) { fence = '```'; return `${attached[1]}\n${attached[2]}` }
    return line
  }).join('\n')
}

/** Split pipes outside code spans; preserve escaped pipes as cell content. */
function cells(line: string): string[] {
  const out: string[] = []
  let cell = '', ticks = 0
  const source = line.trim().replace(/^\|/, '').replace(/(?<!\\)\|$/, '')
  for (let i = 0; i < source.length; i++) {
    const ch = source[i]
    if (ch === '\\' && source[i + 1] === '|') { cell += '|'; i++; continue }
    if (ch === '`') {
      const run = /^`+/.exec(source.slice(i))![0].length
      ticks = ticks === run ? 0 : ticks === 0 ? run : ticks
      cell += '`'.repeat(run); i += run - 1; continue
    }
    if (ch === '|' && !ticks) { out.push(cell.trim()); cell = '' } else cell += ch
  }
  out.push(cell.trim())
  return out
}

export function parseTable(raw: string): TableData | null {
  const lines = raw.trim().split('\n').map(s => s.trim()).filter(Boolean)
  if (lines.length < 2 || !lines.every(s => s.includes('|'))) return null
  const headers = cells(lines[0])
  if (headers.length < 2) return null
  const rows = lines.slice(1).map(line => {
    const row = cells(line)
    if (row.length <= headers.length) return row
    // Recover an unescaped pipe in notation like P(A | B) only if it fixes the width.
    const repaired = line.replace(/\([^()]*\)/g, group => group.replace(/(?<!\\)\|/g, '\\|'))
    const repairedRow = cells(repaired)
    return repairedRow.length === headers.length ? repairedRow : row
  }).filter(row => !row.every(s => /^:?-{3,}:?$/.test(s)))
  if (!rows.length || rows.some(row => row.length !== headers.length)) return null
  return { headers, rows }
}

export function parseTimeline(raw: string): { when: string; what: string }[] {
  return raw.trim().split('\n').map(s => s.trim()).filter(Boolean).map(line => {
    const text = line.replace(/^[-*]\s+/, '')
    // A colon in 14:30 and a hyphen in an ISO date are part of the label.
    const match = /^(.{1,64}?)\s+(?:[—–-]|\|)\s+(.+)$/.exec(text)
      ?? /^(.{1,64}?)\s*:\s+(.+)$/.exec(text)
    return match ? { when: match[1], what: match[2] } : { when: '', what: text }
  })
}

export function recoverRichBlock(language: string, raw: string): { language: string; source: string; title: string } | null {
  if (!['', 'text', 'txt', 'plaintext', 'md', 'markdown'].includes(language)) return null
  const marker = /^\s*(table|timeline)\s*\|\s*([^\n]*)\n([\s\S]+)$/i.exec(raw)
  if (marker) return { language: marker[1].toLowerCase(), title: marker[2].trim(), source: marker[3] }
  const table = parseTable(raw)
  // Unlabelled code needs an actual GFM delimiter. Explicit text/markdown may
  // also contain the separator-free tables older replies used to emit.
  if (table && (language !== '' || /\|\s*:?-{3,}/.test(raw))) {
    return { language: 'table', source: raw, title: '' }
  }
  return null
}

interface MarkdownNode {
  type: string
  lang?: string
  meta?: string
  position?: { start: { offset?: number }; end: { offset?: number } }
  data?: { hProperties?: Record<string, string | boolean> }
  children?: MarkdownNode[]
}

/** Preserve fence captions and completion without making raw HTML executable. */
export function remarkRichBlocks() {
  return (tree: MarkdownNode, file: { value: unknown }) => {
    const visit = (node: MarkdownNode) => {
      if (node.type === 'code') {
        const source = String(file.value).slice(node.position?.start.offset, node.position?.end.offset)
        const opening = /^\s*(`{3,}|~{3,})/.exec(source)?.[1]
        const closed = !opening || new RegExp(`\\n[ \\t]*${opening[0]}{${opening.length},}[ \\t]*$`).test(source)
        node.data = { ...node.data, hProperties: {
          ...node.data?.hProperties,
          'data-title': (node.meta ?? '').replace(/^\s*\|\s*/, '').trim(),
          'data-pending': !closed,
        } }
      }
      node.children?.forEach(visit)
    }
    visit(tree)
  }
}
