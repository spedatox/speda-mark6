import { Fragment, useState, useEffect, useRef, useCallback, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import type { ChatMessage, FileMeta, ToolBadge, SubagentRun, ReportMeta } from '../lib/types'
import { SubagentPanel } from './SubagentPanel'
import SubagentDetailView from './SubagentDetailView'
import { useChatContext } from '../store/chat'
import { downloadFile } from '../lib/api'
import CodeBlock from './CodeBlock'
import WidgetFrame from './WidgetFrame'
import ChartBlock from './ChartBlock'
import CalendarBlock from './CalendarBlock'
import MapBlock from './MapBlock'
import ErrorBoundary from './ErrorBoundary'
import HousePartyWarning from './HousePartyWarning'
import { useT } from '../lib/i18n'
import type { Dict } from '../lib/i18n/en'

const RENDERABLE_LANGS = new Set(['html', 'svg'])

// House Party authorization is a real SSE event (`house_party_auth`) now, not a
// marker in the text — see ChatMain. This stays as a SALVAGE path only: an older
// transcript, or a model that writes the fence out of habit, must never leave a
// raw code block sitting in the chat. The tag is unreliable (`hpp`,
// `hpp-warning`, …) so match explicit aliases OR detect by content, with
// content-detection gated to ambiguous tags so a real C++ `.hpp` header is
// never mistaken for the card.
const HPP_ALIASES = new Set(['hpp-warning', 'house_party', 'house-party', 'houseparty', 'party-warning'])
const HPP_AMBIGUOUS = new Set(['', 'hpp', 'text', 'txt', 'plaintext', 'md', 'markdown'])
function looksLikeHppWarning(code: string): boolean {
  const trimmed = code.trim()
  return /house\s*party\s*protocol/i.test(code)
    || /passphrase\s+to\s+engage/i.test(code)
    || (/authorization\s+required/i.test(code) && /prototype/i.test(code))
    // The tool's own instructions tell a compliant model to leave the body
    // near-empty — no warning prose, just an optional `objective: ...` line —
    // so the phrase-matching rules above can never fire for a well-behaved
    // response. A real C++ .hpp header never looks like this shape, so it's
    // a safe positive signal on an already-ambiguous tag.
    || trimmed === ''
    || /^objective\s*:\s*.+$/i.test(trimmed)
}

/* ── Icons ─────────────────────────────────────────────────────────────────── */
function IconCopy()      { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> }
function IconCheck()     { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12"/></svg> }
function IconThumbUp()   { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3z"/><path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg> }
function IconThumbDown() { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3z"/><path d="M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg> }
function IconRefresh()   { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> }
function IconSpeaker()   { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg> }
function IconEdit()      { return <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg> }
function IconTrash()     { return <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg> }
function IconBolt()      { return <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg> }

/* ── Working status ──────────────────────────────────────────────────────── */

// Tool names that count as "web search" for the disclosure label
const SEARCH_TOOL_PATTERNS = [
  'tavily', 'exa', 'brave', 'search', 'fetch', 'web',
]
function isSearchTool(name: string): boolean {
  const n = name.toLowerCase()
  return SEARCH_TOOL_PATTERNS.some(p => n.includes(p))
}

/* ── Tool disclosure — "Searched the web ▸" collapsible ─────────────────── */
/* Per-tool detail: friendly name + what it did (input) + what came back (result). */
function ToolDetail({ tool }: { tool: ToolBadge }) {
  const name = tool.name.replace(/_/g, ' ').replace(/-/g, ' ')

  // Pull the most meaningful fields out of the tool input.
  const inputRows: [string, string][] = []
  if (tool.input && typeof tool.input === 'object') {
    for (const [k, v] of Object.entries(tool.input as Record<string, unknown>)) {
      if (v == null || v === '') continue
      const val = typeof v === 'string' ? v : JSON.stringify(v)
      inputRows.push([k, val.length > 400 ? val.slice(0, 400) + '…' : val])
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: '0.4rem',
        fontFamily: "var(--font-mono)", fontSize: '0.68rem',
        letterSpacing: '0.06em',
        color: isSearchTool(tool.name) ? 'var(--hb-cyan-bright)' : 'var(--hb-text-dim)',
      }}>
        <span style={{ color: 'var(--hb-cyan-dim)' }}>▸</span>{name}
      </div>
      {inputRows.length > 0 && (
        <div style={{ paddingLeft: '0.9rem', display: 'flex', flexDirection: 'column', gap: '1px' }}>
          {inputRows.map(([k, v]) => (
            <div key={k} style={{
              fontFamily: "var(--font-mono)", fontSize: '0.63rem',
              color: 'var(--hb-icon)', lineHeight: 1.45, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              <span style={{ color: 'var(--hb-icon-dim)' }}>{k}:</span> {v}
            </div>
          ))}
        </div>
      )}
      {tool.result && (
        <details style={{ paddingLeft: '0.9rem' }}>
          <summary style={{
            cursor: 'pointer', fontFamily: "var(--font-mono)",
            fontSize: '0.6rem', letterSpacing: '0.08em', color: 'var(--hb-icon-dim)',
            textTransform: 'uppercase',
          }}>result</summary>
          <pre style={{
            margin: '0.2rem 0 0', fontFamily: "var(--font-mono)",
            fontSize: '0.62rem', color: 'var(--hb-icon-bright)', lineHeight: 1.5,
            whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 180, overflow: 'auto',
          }}>{tool.result}</pre>
        </details>
      )}
    </div>
  )
}

/* ── Live tool feed — visible per-tool activity, with diffs & command output ─ */

// Keep the last two path segments so long paths stay readable without noise.
function shortPath(p: string): string {
  const parts = p.split(/[/\\]/).filter(Boolean)
  return parts.length <= 2 ? p : '…/' + parts.slice(-2).join('/')
}

// One-line "what it did", Claude-Code style: a verb + a target.
function toolSummary(tool: ToolBadge, t: Dict): { verb: string; target?: string } {
  const inp = (tool.input && typeof tool.input === 'object')
    ? tool.input as Record<string, unknown> : {}
  const str = (k: string) => (typeof inp[k] === 'string' ? inp[k] as string : undefined)
  const path = str('path')
  switch (tool.name) {
    case 'edit_file':   return { verb: t.message.verbEdited, target: path && shortPath(path) }
    case 'write_file':  return { verb: t.message.verbWrote, target: path && shortPath(path) }
    case 'read_file':   return { verb: t.message.verbRead, target: path && shortPath(path) }
    case 'run_command': return { verb: t.message.verbRan, target: str('command') }
    case 'system_ops': {
      const action = str('action')
      if (action === 'read_file')  return { verb: t.message.verbRead, target: path && shortPath(path) }
      if (action === 'write_file') return { verb: t.message.verbWrote, target: path && shortPath(path) }
      return { verb: t.message.verbRan, target: str('command') }   // exec (default)
    }
    case 'graph_query':    return { verb: t.message.verbSearchedGraph, target: str('question') }
    case 'graph_path':     return { verb: t.message.verbTracedGraph }
    case 'graph_overview': return { verb: t.message.verbMappedGraph }
    default:
      if (isSearchTool(tool.name)) return { verb: t.message.verbSearched, target: str('query') || str('question') }
      return { verb: tool.name.replace(/_/g, ' ').replace(/-/g, ' ') }
  }
}

/* ── Chain-row helpers ──────────────────────────────────────────────────────
 * The chain shows what the machine actually did: the tool's own name, the one
 * argument that identifies the call, and what came back. Not a prose paraphrase
 * — when a step goes wrong you want the real name to search for. */

/** The single most identifying argument, rendered as `(key: value)`. */
function primaryArg(tool: ToolBadge): string | null {
  const inp = (tool.input && typeof tool.input === 'object')
    ? tool.input as Record<string, unknown> : {}
  const keys = Object.keys(inp)
  if (!keys.length) return null
  // Preference order: the arg a human would recognise the call by.
  const preferred = ['path', 'file_path', 'command', 'query', 'question', 'url',
                     'name', 'action', 'agent_id', 'prompt']
  const key = preferred.find(k => inp[k] != null && inp[k] !== '') ?? keys[0]
  const raw = inp[key]
  let val = typeof raw === 'string' ? raw : JSON.stringify(raw)
  if (val == null) return null
  if (key === 'path' || key === 'file_path') val = shortPath(val)
  if (val.length > 44) val = val.slice(0, 44) + '…'
  return `(${key}: ${val})`
}

/** A short right-aligned "what came back", taken from the real result text. */
function resultSummary(tool: ToolBadge): string | null {
  if (!tool.result) return null
  const first = tool.result.split('\n').find(l => l.trim()) ?? ''
  const t = first.trim()
  if (!t) return null
  return t.length > 38 ? t.slice(0, 38) + '…' : t
}

/** Done / running / failed — read off the result, never invented. */
function stepState(tool: ToolBadge, live: boolean): 'running' | 'failed' | 'done' {
  if (live && !tool.result) return 'running'
  const r = (tool.result ?? '').toLowerCase()
  if (/^(error|traceback|exception)\b/.test(r) || r.includes('error:')) return 'failed'
  return 'done'
}

// Red/green line diff. edit_file passes old→new; write_file passes new only.
function DiffBlock({ removed, added }: { removed?: string; added?: string }) {
  const rows: { sign: '+' | '-'; text: string }[] = []
  if (removed != null) for (const l of removed.split('\n')) rows.push({ sign: '-', text: l })
  if (added != null)   for (const l of added.split('\n'))   rows.push({ sign: '+', text: l })
  if (!rows.length) return null
  return (
    <div className="hb-well" style={{
      fontFamily: "'JetBrains Mono', 'Fira Code', 'SF Mono', Consolas, monospace",
      fontSize: '0.8125rem', lineHeight: 1.6,
      overflow: 'hidden', maxHeight: 340, overflowY: 'auto',
      border: '1px solid rgba(255,255,255,0.06)',
    }}>
      {rows.map((r, i) => (
        <div key={i} style={{
          display: 'flex', gap: '0.5rem', padding: '0 0.5rem',
          whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          background: r.sign === '+' ? 'rgba(74,222,128,0.09)' : 'rgba(248,113,113,0.09)',
          color:      r.sign === '+' ? 'rgba(134,239,172,0.95)' : 'rgba(252,165,165,0.92)',
        }}>
          <span style={{ opacity: 0.55, userSelect: 'none' }}>{r.sign}</span>
          <span style={{ flex: 1 }}>{r.text || ' '}</span>
        </div>
      ))}
    </div>
  )
}

// Command + its output (exit code / stdout live in the result string).
// The deck renders execution as a real terminal slab: an inset dark well, not
// two loose lines of 10px text. Echoed command in the prompt colour, output in
// reading grey — the same shape the sandbox step uses.
const TERMINAL_WELL: React.CSSProperties = {
  borderRadius: 12,
  background: 'rgba(0, 0, 0, 0.35)',
  border: '1px solid rgba(255, 255, 255, 0.06)',
  padding: '12px 14px',
  fontFamily: "'JetBrains Mono', 'Fira Code', 'SF Mono', Consolas, monospace",
  fontSize: '0.8125rem',
  lineHeight: 1.65,
  overflow: 'auto',
}

function CommandBlock({ command, result }: { command?: string; result?: string }) {
  return (
    <div className="hb-well" style={{ ...TERMINAL_WELL, maxHeight: 300 }}>
      {command && (
        <div style={{ color: 'var(--hb-green)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          <span style={{ opacity: 0.6 }}>$ </span>{command}
        </div>
      )}
      {result && (
        <pre style={{
          margin: command ? '6px 0 0' : 0, font: 'inherit',
          color: 'var(--hb-text)', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        }}>{result}</pre>
      )}
    </div>
  )
}

// One row in the feed: always-visible summary; click to expand diff/output/detail.
function ToolRow({ tool, live }: { tool: ToolBadge; live: boolean }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const { verb, target } = toolSummary(tool, t)
  const inp = (tool.input && typeof tool.input === 'object')
    ? tool.input as Record<string, unknown> : {}
  const action = typeof inp.action === 'string' ? inp.action : undefined
  const isEdit  = tool.name === 'edit_file'
  const isWrite = tool.name === 'write_file' || (tool.name === 'system_ops' && action === 'write_file')
  const isCmd   = tool.name === 'run_command'
    || (tool.name === 'system_ops' && action !== 'read_file' && action !== 'write_file')
  const hasDetail = isEdit || isWrite || isCmd || !!tool.result || Object.keys(inp).length > 0

  const state = stepState(tool, live)
  const arg = primaryArg(tool)
  const summary = resultSummary(tool)

  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      <button
        onClick={() => hasDetail && setOpen(v => !v)}
        style={{
          display: 'flex', alignItems: 'center', gap: 11, width: '100%',
          background: 'transparent', border: 'none', padding: '8px 0',
          cursor: hasDetail ? 'pointer' : 'default', textAlign: 'left',
          fontSize: '0.875rem', color: 'var(--hb-text-dim)',
        }}
      >
        {/* Status glyph — a tick when it landed, a ring while it runs, a cross
            when the result came back an error. Three states, one column. */}
        {state === 'running' ? (
          <span style={{
            width: 15, height: 15, borderRadius: '50%', flexShrink: 0,
            border: '1.5px solid rgba(var(--hb-accent-rgb),0.25)',
            borderTopColor: 'var(--hb-cyan)',
            animation: 'spin 0.7s linear infinite',
          }} />
        ) : state === 'failed' ? (
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--hb-red)"
            strokeWidth="2" strokeLinecap="round" style={{ flexShrink: 0 }}>
            <path d="M18 6L6 18M6 6l12 12"/>
          </svg>
        ) : (
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--hb-green)"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
            <path d="M20 6L9 17l-5-5"/>
          </svg>
        )}

        {/* The tool's real name — searchable when something goes wrong. */}
        <span style={{ color: 'var(--hb-text)', flexShrink: 0 }}>{tool.name}</span>
        {arg && (
          <span style={{
            color: 'var(--hb-text-faint)', overflow: 'hidden', textOverflow: 'ellipsis',
            whiteSpace: 'nowrap', minWidth: 0,
          }}>{arg}</span>
        )}

        <span style={{ flex: 1, minWidth: 8 }} />

        {summary && (
          <span
            title={verb + (target ? ` — ${target}` : '')}
            style={{
              color: 'var(--hb-text-faint)', flexShrink: 0, maxWidth: '38%',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}
          >{summary}</span>
        )}
        {hasDetail && (
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
            style={{
              flexShrink: 0, opacity: 0.45,
              transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s',
            }}>
            <polyline points="6 9 12 15 18 9"/>
          </svg>
        )}
      </button>
      {open && hasDetail && (
        // Indented to clear the status column, exactly as the deck nests a
        // sandbox run under the step that launched it.
        <div style={{ margin: '0 0 10px 26px', animation: 'fadeIn 0.15s ease' }}>
          {isEdit ? (
            <DiffBlock
              removed={typeof inp.old_string === 'string' ? inp.old_string : undefined}
              added={typeof inp.new_string === 'string' ? inp.new_string : undefined}
            />
          ) : isWrite ? (
            <DiffBlock added={typeof inp.content === 'string' ? inp.content : undefined} />
          ) : isCmd ? (
            <CommandBlock
              command={typeof inp.command === 'string' ? inp.command : undefined}
              result={tool.result}
            />
          ) : (
            <ToolDetail tool={tool} />
          )}
        </div>
      )}
    </div>
  )
}

/**
 * THE TOOL CHAIN — one card per run of steps, not a bracketed gutter.
 *
 * The deck treats a chain of tool calls as a single object the answer refers
 * to: a titled card that states how many steps ran and whether they are still
 * running, with the steps as rows inside it and each row's detail nesting
 * underneath. It collapses to its header once you have read it.
 *
 * There is deliberately NO elapsed-time readout in the header even though the
 * reference deck shows one ("5 adım · 14.2 sn") — ToolBadge carries no
 * timestamps, so a duration here would be invented.
 */
function ToolFeed({ tools, streaming }: { tools: ToolBadge[]; streaming: boolean }) {
  const [open, setOpen] = useState(true)
  if (!tools.length) return null

  const liveIdx = streaming ? tools.length - 1 : -1
  const running = streaming && !tools[tools.length - 1].result
  const failed = tools.filter(t => stepState(t, false) === 'failed').length

  return (
    <div className="hb-glass-sm" style={{
      margin: '0 0 4px', overflow: 'hidden',
      border: '1px solid var(--hb-edge)',
      background: 'var(--glass-sheen), var(--glass-fill)',
      backdropFilter: 'blur(24px) saturate(160%)',
      WebkitBackdropFilter: 'blur(24px) saturate(160%)',
      animation: 'fadeSlideIn 0.15s ease',
    }}>
      <button
        onClick={() => setOpen(v => !v)}
        style={{
          display: 'flex', alignItems: 'center', gap: 10, width: '100%',
          padding: '12px 16px', cursor: 'pointer', textAlign: 'left',
          background: 'transparent', border: 'none',
          borderBottom: open ? '1px solid rgba(255,255,255,0.06)' : 'none',
        }}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--hb-cyan)"
          strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
          <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
        </svg>
        <span style={{
          fontFamily: "'Rajdhani', sans-serif", fontSize: '0.875rem', fontWeight: 600,
          letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--hb-text-dim)',
        }}>
          {tools.length} step{tools.length === 1 ? '' : 's'}
          {running && ' · running'}
        </span>
        {failed > 0 && (
          <span style={{ fontSize: '0.8125rem', color: 'var(--hb-red)' }}>
            {failed} failed
          </span>
        )}
        <span style={{ flex: 1 }} />
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--hb-text-faint)"
          strokeWidth="1.5" style={{
            flexShrink: 0,
            transform: open ? 'none' : 'rotate(180deg)', transition: 'transform 0.18s',
          }}>
          <polyline points="6 15 12 9 18 15"/>
        </svg>
      </button>

      {open && (
        <div style={{ display: 'flex', flexDirection: 'column', padding: '6px 16px 14px' }}>
          {tools.map((t, i) => (
            <div key={t.id} style={{
              borderBottom: i === tools.length - 1 ? 'none' : '1px solid rgba(255,255,255,0.04)',
            }}>
              <ToolRow tool={t} live={i === liveIdx} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * THE REPORT CARD — a background job that came back, folded above its answer.
 *
 * A legionnaire finishing, or an agent that was dispatched to reporting in,
 * wakes its caller with a turn the owner never sent. The answer below is what
 * they want; this is the receipt. It reads as one quiet line until opened,
 * because the alternative — the seed prompt rendered as a chat bubble — put a
 * wall of framing and raw findings on top of the reply and attributed it to the
 * owner. Opened, it is the same thing a tool call shows: what was asked, and
 * exactly what came back.
 */
function ReportCard({ report }: { report: ReportMeta }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const who = report.from.toUpperCase()
  const failed = report.status !== 'ok'

  return (
    <div className="hb-glass-sm" style={{
      margin: '0 0 6px', overflow: 'hidden',
      border: `1px solid ${failed ? 'rgba(var(--hb-red-rgb,255,90,90),0.35)' : 'var(--hb-edge)'}`,
      background: 'var(--glass-sheen), var(--glass-fill)',
      backdropFilter: 'blur(24px) saturate(160%)',
      WebkitBackdropFilter: 'blur(24px) saturate(160%)',
      animation: 'fadeSlideIn 0.15s ease',
    }}>
      <button
        onClick={() => setOpen(v => !v)}
        style={{
          display: 'flex', alignItems: 'center', gap: 10, width: '100%',
          padding: '10px 16px', cursor: 'pointer', textAlign: 'left',
          background: 'transparent', border: 'none',
          borderBottom: open ? '1px solid rgba(255,255,255,0.06)' : 'none',
        }}
      >
        {/* Inbound, not outbound: this arrived on its own. */}
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
          stroke={failed ? 'var(--hb-red)' : 'var(--hb-cyan)'}
          strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
          <path d="M20 12H8M12 6l-6 6 6 6"/>
        </svg>
        <span style={{
          fontFamily: "'Rajdhani', sans-serif", fontSize: '0.875rem', fontWeight: 600,
          letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--hb-text-dim)',
        }}>
          {who} {report.kind === 'dispatch' ? t.message.reportedBack : t.message.finished}
        </span>
        {failed && (
          <span style={{ fontSize: '0.8125rem', color: 'var(--hb-red)' }}>{report.status}</span>
        )}
        <span style={{ flex: 1 }} />
        {report.ticket != null && (
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: '0.68rem', color: 'var(--hb-text-faint)',
          }}>#{report.ticket}</span>
        )}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--hb-text-faint)"
          strokeWidth="1.5" style={{
            flexShrink: 0,
            transform: open ? 'none' : 'rotate(180deg)', transition: 'transform 0.18s',
          }}>
          <polyline points="6 15 12 9 18 15"/>
        </svg>
      </button>

      {open && (
        <div style={{ padding: '10px 16px 14px', display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
          {report.task && (
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: '0.63rem', lineHeight: 1.5,
              color: 'var(--hb-icon)', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              <span style={{ color: 'var(--hb-icon-dim)' }}>{t.message.taskLabel}</span> {report.task}
            </div>
          )}
          {report.result && (
            <pre style={{
              margin: 0, fontFamily: 'var(--font-mono)', fontSize: '0.62rem',
              color: 'var(--hb-icon-bright)', lineHeight: 1.5,
              whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              maxHeight: 260, overflow: 'auto',
            }}>{report.result}</pre>
          )}
        </div>
      )}
    </div>
  )
}

/* ── Segment builder — interleave tools into the text at the point they fired ─
 * A message stores flat text + a tool list stamped with afterChars (how many
 * characters had streamed when each tool fired). This turns that back into an
 * ordered sequence of {text} / {tools} segments so the renderer can show tool
 * activity AT the point it happened — "as they speak and execute" — instead of
 * always stacking every tool before the text. `revealedLen` clips to how far
 * the typewriter has revealed, so tools unlock progressively during a live
 * stream exactly in step with the text reaching them. Tools sharing the exact
 * same afterChars (parallel calls, or several fired back-to-back with no text
 * between) group into one feed block instead of one each. */
type Segment = { kind: 'text'; text: string } | { kind: 'tools'; tools: ToolBadge[] }

function buildSegments(fullText: string, tools: ToolBadge[], revealedLen: number): Segment[] {
  const visible = tools
    .filter(t => (t.afterChars ?? 0) <= revealedLen)
    .slice()
    .sort((a, b) => (a.afterChars ?? 0) - (b.afterChars ?? 0))

  const segments: Segment[] = []
  let cursor = 0
  let i = 0
  while (i < visible.length) {
    const pos = Math.min(visible[i].afterChars ?? 0, revealedLen)
    if (pos > cursor) {
      segments.push({ kind: 'text', text: fullText.slice(cursor, pos) })
      cursor = pos
    }
    const group: ToolBadge[] = []
    while (i < visible.length && Math.min(visible[i].afterChars ?? 0, revealedLen) === pos) {
      group.push(visible[i])
      i++
    }
    segments.push({ kind: 'tools', tools: group })
  }
  if (cursor < revealedLen) {
    segments.push({ kind: 'text', text: fullText.slice(cursor, revealedLen) })
  }
  return segments
}

function statusLabel(toolName: string, t: Dict): string {
  return (t.message.toolStatus as Record<string, string>)[toolName] ?? t.message.usingTool(toolName)
}

// Rotating dashed-ring spinner
function Spinner() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }}>
      <circle cx="8" cy="8" r="6" fill="none" stroke="var(--accent)" strokeWidth="1.6" strokeDasharray="1.5 3.2" strokeLinecap="round" />
    </svg>
  )
}

function WorkingStatus({ tools, status }: { tools: { id: string; name: string }[]; status?: string }) {
  const t = useT()
  const lastTool = tools.length ? tools[tools.length - 1].name : null

  // Real status only — an active tool drives the label; otherwise the live
  // phase the stream reports (Connecting → Thinking → slow/timeout). No looped
  // filler: if nothing's happening it says so, and the watchdog eventually
  // turns this into an error rather than spinning forever.
  const label = lastTool ? statusLabel(lastTool, t) : (status ?? t.message.thinking)

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.55rem', padding: '0.15rem 0' }}>
      <Spinner />
      <span
        key={label}
        className="thinking-shimmer"
        style={{
          fontSize: '0.875rem', fontStyle: 'italic', fontWeight: 450,
          animation: 'fadeIn 0.35s ease',
        }}
      >
        {label}…
      </span>
    </div>
  )
}

function StreamingCursor() {
  return (
    <span
      style={{
        display: 'inline-block',
        width: '3px',
        height: '1.05em',
        marginLeft: '3px',
        verticalAlign: 'text-bottom',
        // bright white core over a cold cyan base + a slow light sweep
        background:
          'linear-gradient(180deg, #ffffff 0%, var(--hb-cyan-bright) 45%, rgba(150,225,245,0.9) 75%, rgba(var(--hb-cyan-bright-rgb),0.85) 100%)',
        backgroundSize: '100% 220%',
        boxShadow: '0 0 6px 1px rgba(190,235,250,0.55), 0 0 14px 2px rgba(var(--hb-cyan-bright-rgb),0.3)',
        animation: 'caretBreathe 1.15s ease-in-out infinite, caretSheen 2.4s linear infinite',
      }}
    />
  )
}

/** The retry affordance on a failed turn. Labelled rather than icon-only, and
 *  always visible — an error banner is the one place the owner should not have
 *  to go hunting on hover for the way out. */
function RetryBtn({ onClick }: { onClick: () => void }) {
  const t = useT()
  const [hover, setHover] = useState(false)
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: '0.375rem',
        padding: '0.3rem 0.625rem', borderRadius: '0.375rem',
        border: '1px solid rgba(248,113,113,0.35)',
        background: hover ? 'rgba(248,113,113,0.16)' : 'rgba(248,113,113,0.06)',
        color: '#f87171', fontSize: '0.8rem', fontWeight: 500,
        cursor: 'pointer', transition: 'background 0.1s',
      }}
    >
      <IconRefresh />
      {t.message.tryAgain}
    </button>
  )
}

function ActionBtn({
  title, onClick, color, children,
}: { title: string; onClick: () => void; color?: string; children: React.ReactNode }) {
  const [hover, setHover] = useState(false)
  return (
    <button
      title={title}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center',
        borderRadius: '0.375rem', border: 'none',
        background: hover ? 'var(--bg-hover)' : 'transparent',
        color: color ?? (hover ? 'var(--text-primary)' : 'var(--text-muted)'),
        cursor: 'pointer', transition: 'background 0.1s, color 0.1s',
      }}
    >
      {children}
    </button>
  )
}

/**
 * Stark-style heading: "MAIN_SUB" → <span main> + <span sub>.
 * If no underscore, renders children as-is.
 * Only splits when children is a plain string (e.g. "MAY 2026_REPORT");
 * headings with nested bold/links pass through unchanged.
 */
function StarkHeading({
  tag: Tag,
  children,
}: {
  tag: 'h1' | 'h2' | 'h3' | 'h4'
  children?: React.ReactNode
}) {
  if (typeof children === 'string') {
    const idx = children.indexOf('_')
    if (idx > -1) {
      return (
        <Tag>
          <span className="hb-h-main">{children.slice(0, idx)}</span>
          <span className="hb-h-sub">_{children.slice(idx + 1)}</span>
        </Tag>
      )
    }
  }
  return <Tag>{children}</Tag>
}

/**
 * Detect a "Sources:" paragraph so it can get the source-chip styling.
 * Returns true only when the paragraph literally starts with "Sources" — this
 * avoids hijacking every paragraph that merely begins with bold text.
 */
function isSourcesParagraph(children: React.ReactNode): boolean {
  const arr = Array.isArray(children) ? children : [children]
  const first = arr[0]
  // First child is the <strong>Sources:</strong> element
  if (first && typeof first === 'object' && 'props' in first) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const inner = (first as any).props?.children
    const text = typeof inner === 'string' ? inner : ''
    return /^sources\b/i.test(text.trim())
  }
  return false
}

// Plugin arrays and config are hoisted to module scope so their identity is
// stable across renders — recreating them inline makes react-markdown rebuild
// its remark/rehype processor on every render, defeating its internal reuse.
const REMARK_PLUGINS = [remarkGfm, remarkMath]
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const REHYPE_PLUGINS: any = [[rehypeKatex, { throwOnError: false, errorColor: '#f87171' }]]

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const mdComponents: any = {
  h1({ children }: { children?: React.ReactNode }) { return <StarkHeading tag="h1">{children}</StarkHeading> },
  h2({ children }: { children?: React.ReactNode }) { return <StarkHeading tag="h2">{children}</StarkHeading> },
  h3({ children }: { children?: React.ReactNode }) { return <StarkHeading tag="h3">{children}</StarkHeading> },
  h4({ children }: { children?: React.ReactNode }) { return <StarkHeading tag="h4">{children}</StarkHeading> },
  p({ children }: { children?: React.ReactNode }) {
    if (isSourcesParagraph(children)) {
      return <p className="hb-sources">{children}</p>
    }
    return <p>{children}</p>
  },
  code({ inline, className, children }: { inline?: boolean; className?: string; children?: React.ReactNode }) {
    // Hyphens included — `language-hpp-warning` used to truncate to `hpp`, so
    // the alias set could never match its own primary tag.
    const lang = /language-([\w-]+)/.exec(className || '')?.[1] ?? ''
    const code = String(children).replace(/\n$/, '')
    if (!inline && (lang || code.includes('\n'))) {
      // Rich blocks run third-party renderers (MapLibre/WebGL, charting) on
      // model-authored input — each one is a place a throw can escape and, with
      // no boundary, blank the entire window. Isolate them individually so a bad
      // block degrades to a chip instead of killing the conversation.
      if (lang === 'chart') {
        return <ErrorBoundary label="CHART"><ChartBlock>{code}</ChartBlock></ErrorBoundary>
      }
      if (lang === 'calendar') {
        return <ErrorBoundary label="CALENDAR"><CalendarBlock>{code}</CalendarBlock></ErrorBoundary>
      }
      if (lang === 'map') {
        return <ErrorBoundary label="MAP"><MapBlock>{code}</MapBlock></ErrorBoundary>
      }
      if (HPP_ALIASES.has(lang) || (HPP_AMBIGUOUS.has(lang) && looksLikeHppWarning(code))) {
        return <HousePartyWarning>{code}</HousePartyWarning>
      }
      if (RENDERABLE_LANGS.has(lang)) {
        return <WidgetFrame language={lang}>{code}</WidgetFrame>
      }
      return <CodeBlock language={lang}>{code}</CodeBlock>
    }
    return (
      <code style={{
        background: 'rgba(var(--hb-accent-rgb),0.1)',
        border: '1px solid rgba(var(--hb-accent-rgb),0.18)',
        padding: '0.05em 0.4em', fontSize: '0.83em',
        color: 'var(--hb-cyan-bright)',
      }}>
        {children}
      </code>
    )
  },
}

/* ── Typewriter helpers ──────────────────────────────────────────────────── */

/**
 * Ensure every ``` fence starts on its own line.
 * Models sometimes emit "...sentence.```html" with no preceding newline,
 * which ReactMarkdown ignores (fences must be at the start of a line).
 */
function normalizeCodeFences(text: string): string {
  return text.replace(/([^\n])```/g, '$1\n```')
}

/**
 * Prepare math for KaTeX without breaking currency or code.
 * Operates only on non-code segments (code fences / inline code are preserved):
 *   1. Escape a lone `$` directly before a digit — that's currency ($5, $10.5),
 *      not math. Critical for a financial assistant.
 *   2. Normalise alternate delimiters: \[ \] → $$ $$ (display), \( \) → $ $ (inline),
 *      so math renders regardless of which style the model emits.
 */
function prepareMath(text: string): string {
  // Split keeps code regions at odd indices (``` blocks and `inline` code)
  const parts = text.split(/(```[\s\S]*?```|`[^`]*`)/g)
  return parts
    .map((seg, i) => {
      if (i % 2 === 1) return seg // code — leave untouched
      return seg
        .replace(/(?<![$\\])\$(?=\d)/g, '\\$')                 // currency first
        .replace(/\\\[([\s\S]+?)\\\]/g, (_, m) => `$$${m}$$`)  // \[ \] → $$ $$
        .replace(/\\\(([\s\S]+?)\\\)/g, (_, m) => `$${m}$`)    // \( \) → $ $
    })
    .join('')
}

/**
 * Close any unclosed markdown fences so ReactMarkdown doesn't produce broken
 * output when we're only showing a partial string during streaming.
 */
function sanitizePartialMarkdown(text: string): string {
  // Close unclosed triple-backtick code fences
  const fences = (text.match(/```/g) ?? []).length
  if (fences % 2 !== 0) text += '\n```'
  // Close unclosed single inline-backtick (exclude what's inside fences)
  const stripped = text.replace(/```[\s\S]*?```/g, '')
  const ticks = (stripped.match(/(?<!`)`(?!`)/g) ?? []).length
  if (ticks % 2 !== 0) text += '`'
  return text
}

/** One interleaved text segment (see buildSegments). Its own component so the
 *  markdown parse — the single most expensive thing here — is memoized PER
 *  SEGMENT: a settled earlier segment's `text` prop stays value-equal across
 *  renders and skips re-parsing, while only the live tail segment re-parses as
 *  the typewriter advances. Applies the same partial-markdown-safe pipeline
 *  each individual segment gets (sanitize → normalize fences → math) that the
 *  whole message used to get once, since a tool can in principle fire mid-fence.
 *
 *  Exported because voice mode renders its reply through the SAME pipeline. A
 *  second markdown path is exactly how the orb screen ended up printing raw
 *  ```html fences at the owner instead of rendering them. */
export function TextSegment({ text }: { text: string }) {
  const visible = useMemo(
    () => prepareMath(normalizeCodeFences(sanitizePartialMarkdown(text))),
    [text],
  )
  return useMemo(
    () => (
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS} components={mdComponents}>
        {visible}
      </ReactMarkdown>
    ),
    [visible],
  )
}

/* ── Lightbox — full-screen image viewer (click an attachment to open) ───── */
function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
  const t = useT()
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(2,6,10,0.85)', backdropFilter: 'blur(8px)',
        WebkitBackdropFilter: 'blur(8px)', cursor: 'zoom-out',
        animation: 'fadeIn 0.15s ease',
      }}
    >
      <img src={src} alt="attachment"
        style={{ maxWidth: '92vw', maxHeight: '92vh', objectFit: 'contain',
                 border: '1px solid rgba(var(--hb-cyan-bright-rgb),0.3)', boxShadow: '0 12px 60px rgba(0,0,0,0.6)' }} />
      <button onClick={onClose} title={t.message.closeEsc} style={{
        position: 'fixed', top: 18, right: 18, width: 34, height: 34,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(8,20,28,0.7)', border: '1px solid rgba(var(--hb-cyan-bright-rgb),0.3)',
        color: '#cdeefa', cursor: 'pointer',
      }}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>
    </div>
  )
}

/* ── File card — glassmorphism download card for produced files ──────────── */
/** Gradient per file type for the delivery card's icon tile. Semantic colour —
 *  it identifies the artefact, so it is exempt from the neutral-chrome rule the
 *  way the agent signature colours are. */
const FILE_TINTS: Record<string, [string, string]> = {
  pdf:     ['#e88a7c', '#a9463a'],
  docx:    ['#7fa4c4', '#3d5872'],
  doc:     ['#7fa4c4', '#3d5872'],
  pptx:    ['#e0a86c', '#a06b32'],
  xlsx:    ['#7fc79f', '#3f7a5c'],
  csv:     ['#7fc79f', '#3f7a5c'],
  png:     ['#a9c6dc', '#5b7691'],
  jpg:     ['#a9c6dc', '#5b7691'],
  default: ['#a9c6dc', '#5b7691'],
}

function fmtBytes(b: number): string {
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`
  return `${(b / 1024 / 1024).toFixed(1)} MB`
}

function FileCard({ file }: { file: FileMeta }) {
  const t = useT()
  const { state } = useChatContext()
  const [hover, setHover] = useState(false)
  const [busy, setBusy] = useState(false)

  const onDownload = async () => {
    if (!state.config || busy) return
    setBusy(true)
    try { await downloadFile(state.config, file.url, file.name) }
    catch { /* swallow — user can retry */ }
    finally { setBusy(false) }
  }

  const tint = FILE_TINTS[(file.kind || '').toLowerCase()] ?? FILE_TINTS.default

  return (
    <div
      className="hb-glass-sm"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        position: 'relative',
        display: 'flex', alignItems: 'center', gap: 16,
        padding: '16px 20px',
        maxWidth: 520,
        // A delivered file is the ANSWER, not a footnote — it carries the
        // accent rim, unlike the neutral chain card above it.
        border: `1px solid rgba(var(--hb-accent-rgb),${hover ? 0.44 : 0.28})`,
        background: 'linear-gradient(160deg, rgba(var(--hb-accent-rgb),0.12), rgba(var(--hb-accent-rgb),0.03))',
        backdropFilter: 'var(--hb-holo-blur)',
        WebkitBackdropFilter: 'var(--hb-holo-blur)',
        boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.18), 0 16px 40px rgba(0,0,0,0.35)',
        transition: 'border-color 0.15s',
      }}
    >
      {/* Type tile — the one place a file's own colour is allowed in, so a PDF
          is findable in a long transcript at a glance. */}
      {/* The corner has to come from the class: the theme's blanket
          `border-radius: 0 !important` outranks any inline value, so the
          tile was rendering as a hard square. */}
      <div className="hb-tile" style={{
        width: 52, height: 52, flexShrink: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: `linear-gradient(160deg, ${tint[0]}, ${tint[1]})`,
        boxShadow: `0 8px 20px ${tint[1]}59, inset 0 1px 0 rgba(255,255,255,0.3)`,
      }}>
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#ffffff"
          strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>
          <path d="M14 3v5h5"/><path d="M9 15h6M9 12h3"/>
        </svg>
      </div>

      {/* title + meta */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontFamily: 'var(--font-read)', fontSize: '1rem', fontWeight: 600,
          color: 'var(--hb-text)',
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>{file.title}</div>
        <div style={{ fontSize: '0.845rem', color: 'var(--hb-text-dim)', marginTop: 2 }}>
          {file.kind} · {fmtBytes(file.size)}
        </div>
      </div>

      <button
        onClick={onDownload}
        title={t.message.download}
        className="glass-round"
        style={{
          flexShrink: 0, display: 'flex', alignItems: 'center', gap: 8,
          height: 38, padding: '0 18px',
          border: '1px solid rgba(255,255,255,0.18)',
          background: 'linear-gradient(160deg, rgba(255,255,255,0.12), rgba(255,255,255,0.03))',
          backdropFilter: 'blur(20px) saturate(160%)',
          WebkitBackdropFilter: 'blur(20px) saturate(160%)',
          boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.3)',
          color: 'var(--hb-text)', cursor: busy ? 'default' : 'pointer',
          fontSize: '0.875rem', fontWeight: 600,
        }}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>
        </svg>
        {busy ? '…' : t.message.download}
      </button>
    </div>
  )
}

/* ── Upload chip — a non-downloadable marker for a file the user attached ─── */
function UploadChip({ name, size }: { name: string; size: number }) {
  return (
    <div className="hb-glass-xs" style={{
      display: 'flex', alignItems: 'center', gap: '0.45rem',
      padding: '0.4rem 0.6rem 0.4rem 0.5rem',
      border: '1px solid rgba(var(--hb-accent-rgb),0.28)',
      background: 'rgba(var(--hb-accent-rgb),0.08)',
      maxWidth: 240,
    }}>
      <span style={{ color: 'var(--hb-cyan-bright)', flexShrink: 0, display: 'flex' }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
        </svg>
      </span>
      <div style={{ minWidth: 0 }}>
        <div style={{
          fontFamily: "'SamsungOne','Inter',sans-serif", fontSize: '0.75rem',
          color: 'var(--hb-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>{name}</div>
        {size > 0 && (
          <div style={{
            fontFamily: "var(--font-mono)", fontSize: '0.6rem',
            color: 'var(--hb-icon-dim)', marginTop: '1px',
          }}>{fmtBytes(size)}</div>
        )}
      </div>
    </div>
  )
}

/* ── Props ───────────────────────────────────────────────────────────────── */
interface Props {
  message: ChatMessage
  onDelete?: () => void
  onRegenerate?: () => void
  onEditAndResend?: (newContent: string) => void
}

/* ── Component ───────────────────────────────────────────────────────────── */
export default function Message({ message, onDelete, onRegenerate, onEditAndResend }: Props) {
  const t = useT()
  const [hovered, setHovered] = useState(false)
  const [copied, setCopied] = useState(false)
  const [thumbUp, setThumbUp] = useState(false)
  const [thumbDown, setThumbDown] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editValue, setEditValue] = useState(message.content)
  const [lightbox, setLightbox] = useState<string | null>(null)
  const [selectedSubagent, setSelectedSubagent] = useState<SubagentRun | null>(null)

  // ── Typewriter reveal (rAF, steady pacing) ───────────────────────────────
  // A requestAnimationFrame loop reveals characters at a brisk STEADY rate with a
  // gentle, hard-CAPPED catch-up. When a burst of deltas lands at once — token
  // batches coalesced over the network, a coarse provider chunk — it types them
  // out as a visible sweep instead of flashing the whole paragraph in. The cap is
  // what preserves the real-time feel: an uncapped `remaining × k` reveals a burst
  // in ~150ms, which reads as "wait… wait… paragraph".
  const targetRef = useRef(message.content)
  targetRef.current = message.content
  const streamingRef = useRef(message.isStreaming)
  streamingRef.current = message.isStreaming

  // How far it is SAFE to reveal. Never slice INTO an unclosed ``` fence — that
  // yields malformed markdown mid-stream. (The old code dumped the ENTIRE message
  // the instant any ``` appeared, killing the typewriter for the prose too.) While
  // streaming, hold the reveal at the start of an open fence; prose before/after and
  // any CLOSED block still stream. Once the turn is done, reveal everything.
  const safeTarget = useCallback((text: string): number => {
    if (!streamingRef.current) return text.length
    let idx = -1
    let count = 0
    for (let i = text.indexOf('```'); i !== -1; i = text.indexOf('```', i + 3)) {
      count++
      idx = i
    }
    return count % 2 === 1 ? idx : text.length
  }, [])

  const revealedRef = useRef<number>(
    message.isStreaming ? 0 : message.content.length
  )
  const rafRef = useRef<number | null>(null)
  const lastTsRef = useRef<number>(0)
  const [displayLen, setDisplayLen] = useState<number>(revealedRef.current)

  const tick = useCallback((ts: number) => {
    const target = safeTarget(targetRef.current)
    const dt = lastTsRef.current ? Math.min((ts - lastTsRef.current) / 1000, 0.05) : 0
    lastTsRef.current = ts

    const remaining = target - revealedRef.current
    if (remaining <= 0.5) {
      revealedRef.current = target
      setDisplayLen(target)
      rafRef.current = null
      lastTsRef.current = 0
      return
    }

    // chars/sec: a brisk steady BASE, plus a gentle catch-up when it falls behind
    // a fast model, hard-CAPPED so even a big burst types out as a visible sweep
    // (never an instant flash). Self-balances a few words behind a steady stream.
    const BASE = 90
    const CATCH_UP = 2.5
    const MAX = 700
    const speed = Math.min(MAX, BASE + remaining * CATCH_UP)
    revealedRef.current = Math.min(target, revealedRef.current + speed * dt)

    setDisplayLen(Math.floor(revealedRef.current))
    rafRef.current = requestAnimationFrame(tick)
  }, [safeTarget])

  useEffect(() => {
    // Kick the loop if more (safely-revealable) content arrived and it isn't running.
    if (rafRef.current == null && revealedRef.current < safeTarget(targetRef.current)) {
      lastTsRef.current = 0
      rafRef.current = requestAnimationFrame(tick)
    }
  }, [message.content, message.isStreaming, tick, safeTarget])

  useEffect(() => () => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
  }, [])

  const fullLen = message.content.length
  const isRevealing = displayLen < fullLen

  // Debounce the markdown parse during streaming — the typewriter rAF fires at
  // 60fps but ReactMarkdown + remark/rehype only needs ~12fps to look smooth.
  // This cuts the #1 perf cost (full markdown re-parse on every frame).
  const [renderLen, setRenderLen] = useState(displayLen)
  const renderTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (!isRevealing) {
      if (renderTimer.current) clearTimeout(renderTimer.current)
      setRenderLen(displayLen)
      return
    }
    if (renderTimer.current) return
    renderTimer.current = setTimeout(() => {
      renderTimer.current = null
      setRenderLen(revealedRef.current)
    }, 80)
  }, [displayLen, isRevealing])

  // How far the typewriter has revealed — same debounced cadence the markdown
  // parse always used, now also gating which tools have "unlocked" into the
  // segment list (buildSegments) so they interleave in step with the text
  // actually reaching them, live or on reload.
  const revealedLen = isRevealing ? renderLen : fullLen

  // Ordered text/tools segments — see buildSegments. Each text segment is its
  // own memoized TextSegment, so a settled earlier segment never re-parses;
  // only the live tail segment re-parses as the typewriter advances.
  const segments = useMemo(
    () => buildSegments(message.content, message.tools, revealedLen),
    [message.content, message.tools, revealedLen],
  )

  const copy = () => {
    navigator.clipboard.writeText(message.content).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  const readAloud = () => {
    if (speaking) {
      speechSynthesis.cancel()
      setSpeaking(false)
      return
    }
    const text = message.content.replace(/[#*`>]/g, '').trim()
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.onend = () => setSpeaking(false)
    utterance.onerror = () => setSpeaking(false)
    speechSynthesis.speak(utterance)
    setSpeaking(true)
  }

  const saveEdit = () => {
    setEditing(false)
    if (editValue.trim() && editValue.trim() !== message.content) {
      onEditAndResend?.(editValue.trim())
    }
  }

  /* ── User message ─────────────────────────────────────────────────────── */
  if (message.role === 'user') {
    return (
      <div
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '1rem', animation: 'fadeSlideIn 0.2s ease' }}
      >
        <div style={{ maxWidth: '75%', minWidth: 0, display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '0.375rem' }}>
          {editing ? (
            <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              <textarea
                autoFocus
                value={editValue}
                onChange={e => setEditValue(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveEdit() } if (e.key === 'Escape') { setEditing(false); setEditValue(message.content) } }}
                rows={3}
                className="hb-glass-xs"
                style={{
                  width: '100%', background: 'rgba(10, 22, 30, 0.55)',
                  boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.35), inset 0 -1px 0 0 rgba(255,255,255,0.05)',
                  border: '1px solid var(--border-focus)',
                  padding: '0.625rem 1rem',
                  color: 'var(--text-primary)', fontSize: '0.9375rem',
                  lineHeight: 1.65, fontFamily: 'inherit', resize: 'none',
                  outline: 'none', userSelect: 'text',
                }}
              />
              <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
                <button
                  className="hb-btn"
                  onClick={() => { setEditing(false); setEditValue(message.content) }}
                  style={{ padding: '0.35rem 0.875rem', fontSize: '0.8rem' }}
                >
                  {t.common.cancel}
                </button>
                <button
                  className="hb-btn hb-btn-tint"
                  onClick={saveEdit}
                  style={{
                    padding: '0.35rem 0.875rem', color: 'var(--hb-cyan-bright)',
                    fontSize: '0.8rem', fontWeight: 600,
                  }}
                >
                  {t.message.saveAndSend}
                </button>
              </div>
            </div>
          ) : (
            <>
              {message.trigger && (
                <div style={{
                  display: 'flex', alignItems: 'center', gap: '0.375rem',
                  fontSize: '0.6875rem', letterSpacing: '0.08em', textTransform: 'uppercase',
                  color: 'var(--text-dim)', fontFamily: 'var(--font-mono, monospace)',
                }}>
                  <IconBolt />
                  <span>{message.trigger.label}</span>
                  <span style={{ opacity: 0.55 }}>
                    {/* An inter-agent dispatch already names its sender in the
                        label, so repeating "· agent · silent" after it says
                        nothing a reader didn't just read. Only the House Party
                        variant adds anything. */}
                    {message.trigger.source === 'agent'
                      ? (message.trigger.job === 'house party dispatch' ? t.message.housePartyTag : '')
                      : `· ${message.trigger.source}${message.trigger.output_mode ? ` · ${message.trigger.output_mode}` : ''}`}
                  </span>
                </div>
              )}
              {message.images && message.images.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', justifyContent: 'flex-end', marginBottom: message.content ? '0.4rem' : 0 }}>
                  {message.images.map((src, i) => (
                    <img
                      key={i} src={src} alt="attachment"
                      onClick={() => setLightbox(src)}
                      style={{ maxWidth: 220, maxHeight: 220, objectFit: 'cover', border: '1px solid var(--border)', display: 'block', cursor: 'zoom-in' }}
                    />
                  ))}
                </div>
              )}
              {message.uploads && message.uploads.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', justifyContent: 'flex-end', marginBottom: message.content ? '0.4rem' : 0 }}>
                  {message.uploads.map((u, i) => (
                    <UploadChip key={i} name={u.name} size={u.size} />
                  ))}
                </div>
              )}
              {message.content && (
                <div className="hb-holo hb-bubble-user" style={{
                  padding: '14px 20px',
                  color: 'var(--hb-text)', fontSize: '1rem',
                  fontFamily: 'var(--font-read)',
                  lineHeight: 1.6, whiteSpace: 'pre-wrap', userSelect: 'text',
                  // Break long unbroken strings (URLs, tokens, JSON) so they wrap
                  // inside the bubble instead of stretching it past its max-width.
                  overflowWrap: 'anywhere', wordBreak: 'break-word', maxWidth: '100%',
                }}>
                  {message.content}
                </div>
              )}
              <div style={{ opacity: hovered ? 1 : 0, transition: 'opacity 0.15s', display: 'flex', alignItems: 'center', gap: '0.125rem' }}>
                {onEditAndResend && !message.trigger && (
                  // An automation's seed turn isn't editable: resending it would
                  // re-file it as the owner's own message and lose the origin.
                  <ActionBtn title={t.message.editMessage} onClick={() => { setEditValue(message.content); setEditing(true) }}>
                    <IconEdit />
                  </ActionBtn>
                )}
                <ActionBtn title={copied ? t.message.copied : t.message.copy} onClick={copy} color={copied ? 'var(--accent)' : undefined}>
                  {copied ? <IconCheck /> : <IconCopy />}
                </ActionBtn>
                {onDelete && (
                  <ActionBtn title={t.message.delete} onClick={onDelete}>
                    <span style={{ color: 'inherit' }}><IconTrash /></span>
                  </ActionBtn>
                )}
              </div>
            </>
          )}
        </div>
        {lightbox && <Lightbox src={lightbox} onClose={() => setLightbox(null)} />}
      </div>
    )
  }

  /* ── Assistant message ────────────────────────────────────────────────── */
  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{ display: 'flex', marginBottom: '1.5rem', alignItems: 'flex-start', animation: 'fadeSlideIn 0.2s ease' }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        {/* This turn was not sent by the owner — a background job finished and
            woke the agent to deliver it. The receipt folds above the answer;
            the answer itself renders exactly as any other. */}
        {message.trigger?.report && <ReportCard report={message.trigger.report} />}
        {/* Text and tool activity INTERLEAVED in the order they actually
            happened (see buildSegments) — a tool fired mid-answer shows up
            between the sentences around it, not stacked above the whole
            response. The cursor rides on the last segment only if it's text;
            a still-running tool's own spinner covers the "something's
            happening" signal otherwise. (If the turn errored, a banner sits
            BENEATH all of this — the streamed text and tools stay on screen.) */}
        {message.content || message.tools.length > 0 ? (
          <div className="prose" style={{ userSelect: 'text', overflowWrap: 'anywhere', wordBreak: 'break-word', minWidth: 0, maxWidth: '100%' }}>
            {segments.map((seg, i) => {
              const isLast = i === segments.length - 1
              if (seg.kind === 'tools') {
                return <ToolFeed key={`t${i}`} tools={seg.tools} streaming={!!message.isStreaming} />
              }
              return (
                <Fragment key={`x${i}`}>
                  <TextSegment text={seg.text} />
                  {isLast && (message.isStreaming || isRevealing) && <StreamingCursor />}
                </Fragment>
              )
            })}
          </div>
        ) : message.isStreaming && !message.subagents?.length ? (
          // Nothing at all has streamed yet — show the natural-language working
          // indicator. Suppressed while a delegation is open, which is a better
          // and more specific answer to "is anything happening".
          <WorkingStatus tools={message.tools} status={message.status} />
        ) : null}

        {/* What this turn delegated. Deliberately OUTSIDE the prose block: a
            subagent's report is not the answer, and rendering it inline is what
            made a delegate's write-up read as Optimus's own reply. */}
        {message.subagents?.length ? (
          <SubagentPanel runs={message.subagents} onSelect={setSelectedSubagent} />
        ) : null}

        {/* Subagent detail view — full-screen chat-like thread for one delegation.
            Rendered at the Message level so it overlays the entire chat, not just
            the message bubble. Only one open at a time. */}
        {selectedSubagent && (
          <SubagentDetailView
            run={selectedSubagent}
            onClose={() => setSelectedSubagent(null)}
          />
        )}

        {message.isError && (
          <div style={{
            color: '#f87171', userSelect: 'text',
            background: 'rgba(248,113,113,0.07)',
            border: '1px solid rgba(248,113,113,0.2)',
            borderRadius: '0.625rem', padding: '0.625rem 0.875rem', fontSize: '0.9rem',
            marginTop: message.content || message.tools.length ? '0.5rem' : 0,
          }}>
            {message.errorNote || message.content || t.message.somethingWentWrong}
            {/* Retry lives INSIDE the banner and is never hover-gated: a failed
                turn is exactly where the owner needs this button, and the hover
                action bar below is gated on `content` — which a turn that died
                before streaming anything does not have. */}
            {onRegenerate && (
              <div style={{ marginTop: '0.5rem' }}>
                <RetryBtn onClick={onRegenerate} />
              </div>
            )}
          </div>
        )}

        {/* Downloadable files Speda produced this turn */}
        {message.files && message.files.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '0.75rem' }}>
            {message.files.map((f, i) => <FileCard key={i} file={f} />)}
          </div>
        )}

        {/* Action bar — appears on hover once streaming ends */}
        {!message.isStreaming && message.content && (
          <div style={{
            opacity: hovered ? 1 : 0,
            transition: 'opacity 0.15s',
            marginTop: '0.5rem',
            display: 'flex', alignItems: 'center', gap: '0.125rem',
          }}>
            <ActionBtn title={copied ? t.message.copied : t.message.copy} onClick={copy} color={copied ? 'var(--accent)' : undefined}>
              {copied ? <IconCheck /> : <IconCopy />}
            </ActionBtn>
            <ActionBtn title={t.message.goodResponse} onClick={() => { setThumbUp(v => !v); setThumbDown(false) }} color={thumbUp ? 'var(--accent)' : undefined}>
              <IconThumbUp />
            </ActionBtn>
            <ActionBtn title={t.message.badResponse} onClick={() => { setThumbDown(v => !v); setThumbUp(false) }} color={thumbDown ? '#f87171' : undefined}>
              <IconThumbDown />
            </ActionBtn>
            <ActionBtn title={speaking ? t.message.stopReading : t.message.readAloud} onClick={readAloud} color={speaking ? 'var(--accent)' : undefined}>
              <IconSpeaker />
            </ActionBtn>
            {onRegenerate && (
              <ActionBtn title={t.message.regenerateResponse} onClick={onRegenerate}>
                <IconRefresh />
              </ActionBtn>
            )}
            {onDelete && (
              <ActionBtn title={t.message.delete} onClick={onDelete}>
                <IconTrash />
              </ActionBtn>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
