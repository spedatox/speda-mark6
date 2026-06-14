import { useState, useEffect, useRef, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import type { ChatMessage, FileMeta, ToolBadge } from '../lib/types'
import { useChatContext } from '../store/chat'
import { downloadFile } from '../lib/api'
import CodeBlock from './CodeBlock'
import WidgetFrame from './WidgetFrame'
import ChartBlock from './ChartBlock'

const RENDERABLE_LANGS = new Set(['html', 'svg'])

/* ── Icons ─────────────────────────────────────────────────────────────────── */
function IconCopy()      { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> }
function IconCheck()     { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12"/></svg> }
function IconThumbUp()   { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3z"/><path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg> }
function IconThumbDown() { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3z"/><path d="M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg> }
function IconRefresh()   { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> }
function IconSpeaker()   { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg> }
function IconEdit()      { return <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg> }
function IconTrash()     { return <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg> }

/* ── Working status ──────────────────────────────────────────────────────── */

// Map raw tool names → present-progressive natural-language status.
const TOOL_STATUS: Record<string, string> = {
  read_skill:             'Reviewing capabilities',
  generate_document:      'Preparing the document',
  system_info:            'Checking system status',
  text_to_speech:         'Generating audio',
  speech_to_text:         'Transcribing audio',
  send_push_notification: 'Sending a notification',
  web_search:             'Searching the web',
  WebSearch:              'Searching the web',
  web_fetch:              'Reading the page',
  WebFetch:               'Reading the page',
  Task:                   'Spawning a sub-agent',
}

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
        fontFamily: "'Share Tech Mono', monospace", fontSize: '0.68rem',
        letterSpacing: '0.06em',
        color: isSearchTool(tool.name) ? '#5fcce6' : '#7ab8c8',
      }}>
        <span style={{ color: '#1e4a5a' }}>▸</span>{name}
      </div>
      {inputRows.length > 0 && (
        <div style={{ paddingLeft: '0.9rem', display: 'flex', flexDirection: 'column', gap: '1px' }}>
          {inputRows.map(([k, v]) => (
            <div key={k} style={{
              fontFamily: "'Share Tech Mono', monospace", fontSize: '0.63rem',
              color: '#46818f', lineHeight: 1.45, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              <span style={{ color: '#2e5260' }}>{k}:</span> {v}
            </div>
          ))}
        </div>
      )}
      {tool.result && (
        <details style={{ paddingLeft: '0.9rem' }}>
          <summary style={{
            cursor: 'pointer', fontFamily: "'Share Tech Mono', monospace",
            fontSize: '0.6rem', letterSpacing: '0.08em', color: '#2e5260',
            textTransform: 'uppercase',
          }}>result</summary>
          <pre style={{
            margin: '0.2rem 0 0', fontFamily: "'Share Tech Mono', monospace",
            fontSize: '0.62rem', color: '#5d7f8a', lineHeight: 1.5,
            whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 180, overflow: 'auto',
          }}>{tool.result}</pre>
        </details>
      )}
    </div>
  )
}

function ToolDisclosure({ tools }: { tools: ToolBadge[] }) {
  const [open, setOpen] = useState(false)
  if (!tools.length) return null

  const searchTools  = tools.filter(t => isSearchTool(t.name))
  const otherTools   = tools.filter(t => !isSearchTool(t.name))
  const hasSearch    = searchTools.length > 0

  // Friendly label for the collapsed row
  const label = hasSearch
    ? `Searched the web${searchTools.length > 1 ? ` (${searchTools.length}×)` : ''}`
    : `Used ${tools.length} tool${tools.length !== 1 ? 's' : ''}`

  const friendlyName = (raw: string) =>
    raw.replace(/_/g, ' ').replace(/-/g, ' ').toLowerCase()

  return (
    <div style={{ marginBottom: '0.6rem' }}>
      <button
        onClick={() => setOpen(v => !v)}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: '0.4rem',
          background: 'transparent', border: 'none',
          padding: '0.15rem 0',
          cursor: 'pointer',
          fontFamily: "'Share Tech Mono', monospace",
          fontSize: '0.68rem', letterSpacing: '0.08em',
          color: '#3a6472',
          transition: 'color 0.12s',
        }}
        onMouseEnter={e => (e.currentTarget.style.color = '#5fcce6')}
        onMouseLeave={e => (e.currentTarget.style.color = '#3a6472')}
      >
        {/* globe for search, wrench for other */}
        {hasSearch
          ? <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
          : <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
        }
        {label}
        <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
          style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>
          <polyline points="6 9 12 15 18 9"/>
        </svg>
      </button>

      {open && (
        <div style={{
          marginTop: '0.25rem',
          padding: '0.55rem 0.7rem',
          background: 'rgba(6,14,20,0.7)',
          border: '1px solid rgba(95,165,188,0.15)',
          borderLeft: '2px solid rgba(95,165,188,0.3)',
          animation: 'fadeSlideIn 0.15s ease',
          display: 'flex', flexDirection: 'column', gap: '0.6rem',
        }}>
          {[...searchTools, ...otherTools].map(t => (
            <ToolDetail key={t.id} tool={t} />
          ))}
        </div>
      )}
    </div>
  )
}

function statusLabel(toolName: string): string {
  return TOOL_STATUS[toolName] ?? `Using ${toolName.replace(/_/g, ' ')}`
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
  const lastTool = tools.length ? tools[tools.length - 1].name : null

  // Real status only — an active tool drives the label; otherwise the live
  // phase the stream reports (Connecting → Thinking → slow/timeout). No looped
  // filler: if nothing's happening it says so, and the watchdog eventually
  // turns this into an error rather than spinning forever.
  const label = lastTool ? statusLabel(lastTool) : (status ?? 'Thinking')

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
          'linear-gradient(180deg, #ffffff 0%, #eafaff 45%, rgba(150,225,245,0.9) 75%, rgba(95,204,230,0.85) 100%)',
        backgroundSize: '100% 220%',
        boxShadow: '0 0 6px 1px rgba(190,235,250,0.55), 0 0 14px 2px rgba(95,204,230,0.3)',
        animation: 'caretBreathe 1.15s ease-in-out infinite, caretSheen 2.4s linear infinite',
      }}
    />
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
    const lang = /language-(\w+)/.exec(className || '')?.[1] ?? ''
    const code = String(children).replace(/\n$/, '')
    if (!inline && (lang || code.includes('\n'))) {
      if (lang === 'chart') {
        return <ChartBlock>{code}</ChartBlock>
      }
      if (RENDERABLE_LANGS.has(lang)) {
        return <WidgetFrame language={lang}>{code}</WidgetFrame>
      }
      return <CodeBlock language={lang}>{code}</CodeBlock>
    }
    return (
      <code style={{
        background: 'rgba(54,171,202,0.1)',
        border: '1px solid rgba(95,165,188,0.18)',
        padding: '0.05em 0.4em', fontSize: '0.83em',
        color: '#7fd4e8',
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

/* ── Lightbox — full-screen image viewer (click an attachment to open) ───── */
function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
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
                 border: '1px solid rgba(95,200,228,0.3)', boxShadow: '0 12px 60px rgba(0,0,0,0.6)' }} />
      <button onClick={onClose} title="Close (Esc)" style={{
        position: 'fixed', top: 18, right: 18, width: 34, height: 34,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(8,20,28,0.7)', border: '1px solid rgba(95,200,228,0.3)',
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
function fmtBytes(b: number): string {
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`
  return `${(b / 1024 / 1024).toFixed(1)} MB`
}

function FileCard({ file }: { file: FileMeta }) {
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

  return (
    <div
      className="hb-holo"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        position: 'relative',
        display: 'flex', alignItems: 'center', gap: '0.75rem',
        padding: '0.7rem 0.8rem',
        maxWidth: 420,
        borderColor: hover ? 'var(--hb-edge-bright)' : 'var(--hb-edge)',
        boxShadow: hover ? 'var(--hb-holo-shadow-active)' : undefined,
        transition: 'border-color 0.15s, box-shadow 0.15s',
      }}
    >
      {/* doc glyph in a tinted square */}
      <div className="hb-glass-xs" style={{
        width: 38, height: 38, flexShrink: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(54,171,202,0.14)', border: '1px solid rgba(95,165,188,0.28)',
        color: '#5fcce6',
      }}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
        </svg>
      </div>

      {/* title + meta */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontFamily: 'var(--font-read)', fontSize: '0.88rem', fontWeight: 600,
          color: 'var(--text-primary)',
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>{file.title}</div>
        <div style={{
          fontFamily: "'Share Tech Mono', monospace", fontSize: '0.62rem',
          letterSpacing: '0.06em', color: 'var(--text-muted)', marginTop: '2px',
        }}>{file.kind} · {fmtBytes(file.size)}</div>
      </div>

      {/* download button — amber action chip, like the reference tags */}
      <button
        onClick={onDownload}
        title="Download"
        className="hb-glass-xs"
        style={{
          flexShrink: 0, display: 'flex', alignItems: 'center', gap: '0.4rem',
          padding: '0.4rem 0.7rem',
          border: '1px solid rgba(242,183,92,0.45)',
          background: busy ? 'rgba(217,156,68,0.1)' : 'rgba(217,156,68,0.16)',
          backdropFilter: 'var(--hb-holo-blur)',
          WebkitBackdropFilter: 'var(--hb-holo-blur)',
          color: '#f6d9a8', cursor: busy ? 'default' : 'pointer',
          fontFamily: "'Rajdhani',sans-serif", fontSize: '0.72rem', fontWeight: 700,
          letterSpacing: '0.1em', textTransform: 'uppercase',
          boxShadow: 'inset 0 1px 0 0 rgba(255,225,180,0.3)',
          transition: 'background 0.15s',
        }}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
          <polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
        </svg>
        {busy ? '…' : 'Download'}
      </button>
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
  const [hovered, setHovered] = useState(false)
  const [copied, setCopied] = useState(false)
  const [thumbUp, setThumbUp] = useState(false)
  const [thumbDown, setThumbDown] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editValue, setEditValue] = useState(message.content)
  const [lightbox, setLightbox] = useState<string | null>(null)

  // ── Typewriter reveal (rAF, adaptive catch-up) ───────────────────────────
  // A requestAnimationFrame loop reveals characters with exponential easing:
  // the further behind the buffer, the faster it types — so it always catches
  // up smoothly with the model and finishes gracefully when the stream ends,
  // instead of stuttering on a fixed interval or hard-snapping at the end.
  const targetRef = useRef(message.content)
  targetRef.current = message.content

  // Skip the typewriter when a code block is present — slicing mid-fence yields
  // malformed markdown, and WidgetFrame owns the visual reveal for those.
  const hasCodeBlock = message.content.includes('```')

  const revealedRef = useRef<number>(
    message.isStreaming && !hasCodeBlock ? 0 : message.content.length
  )
  const rafRef = useRef<number | null>(null)
  const lastTsRef = useRef<number>(0)
  const [displayLen, setDisplayLen] = useState<number>(revealedRef.current)

  const tick = useCallback((ts: number) => {
    const target = targetRef.current.length
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

    // chars/sec = max(floor, remaining × catch-up). Exponential-approach easing.
    const FLOOR = 45
    const CATCH_UP = 7
    const speed = Math.max(FLOOR, remaining * CATCH_UP)
    revealedRef.current = Math.min(target, revealedRef.current + speed * dt)

    setDisplayLen(Math.floor(revealedRef.current))
    rafRef.current = requestAnimationFrame(tick)
  }, [])

  useEffect(() => {
    if (hasCodeBlock) {
      // Reveal everything at once; WidgetFrame animates the block in.
      if (rafRef.current != null) { cancelAnimationFrame(rafRef.current); rafRef.current = null }
      revealedRef.current = targetRef.current.length
      setDisplayLen(targetRef.current.length)
      return
    }
    // Kick the loop if more content arrived and it isn't already running.
    if (rafRef.current == null && revealedRef.current < targetRef.current.length) {
      lastTsRef.current = 0
      rafRef.current = requestAnimationFrame(tick)
    }
  }, [message.content, hasCodeBlock, tick])

  useEffect(() => () => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
  }, [])

  const fullLen = message.content.length
  const isRevealing = displayLen < fullLen

  const rawVisible = isRevealing
    ? sanitizePartialMarkdown(message.content.slice(0, displayLen))
    : message.content

  // Normalize fence placement, then prepare math (currency-safe, code-safe).
  const visibleContent = prepareMath(normalizeCodeFences(rawVisible))

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
        <div style={{ maxWidth: '75%', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '0.375rem' }}>
          {editing ? (
            <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              <textarea
                autoFocus
                value={editValue}
                onChange={e => setEditValue(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveEdit() } if (e.key === 'Escape') { setEditing(false); setEditValue(message.content) } }}
                rows={3}
                style={{
                  width: '100%', background: 'rgba(8,20,26,0.7)',
                  border: '1px solid var(--border-focus)',
                  padding: '0.625rem 1rem',
                  color: 'var(--text-primary)', fontSize: '0.9375rem',
                  lineHeight: 1.65, fontFamily: 'inherit', resize: 'none',
                  outline: 'none', userSelect: 'text',
                }}
              />
              <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
                <button
                  onClick={() => { setEditing(false); setEditValue(message.content) }}
                  style={{
                    padding: '0.35rem 0.875rem', borderRadius: '0.5rem',
                    border: '1px solid var(--border)', background: 'transparent',
                    color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '0.8rem',
                  }}
                >
                  Cancel
                </button>
                <button
                  onClick={saveEdit}
                  style={{
                    padding: '0.35rem 0.875rem', borderRadius: '0.5rem',
                    border: 'none', background: 'var(--accent)',
                    color: '#fff', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 500,
                  }}
                >
                  Save & Send
                </button>
              </div>
            </div>
          ) : (
            <>
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
              {message.content && (
                <div className="hb-holo" style={{
                  padding: '0.6rem 0.95rem',
                  color: '#dfeef4', fontSize: '0.9375rem',
                  fontFamily: 'var(--font-read)',
                  lineHeight: 1.65, whiteSpace: 'pre-wrap', userSelect: 'text',
                }}>
                  {message.content}
                </div>
              )}
              <div style={{ opacity: hovered ? 1 : 0, transition: 'opacity 0.15s', display: 'flex', alignItems: 'center', gap: '0.125rem' }}>
                {onEditAndResend && (
                  <ActionBtn title="Edit message" onClick={() => { setEditValue(message.content); setEditing(true) }}>
                    <IconEdit />
                  </ActionBtn>
                )}
                <ActionBtn title={copied ? 'Copied!' : 'Copy'} onClick={copy} color={copied ? 'var(--accent)' : undefined}>
                  {copied ? <IconCheck /> : <IconCopy />}
                </ActionBtn>
                {onDelete && (
                  <ActionBtn title="Delete" onClick={onDelete}>
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
        {/* Tool disclosure — appears as soon as first tool fires, stays permanently */}
        {message.tools.length > 0 && (
          <ToolDisclosure tools={message.tools} />
        )}

        {/* Content, error, or live working status */}
        {message.isError ? (
          <div style={{
            color: '#f87171', userSelect: 'text',
            background: 'rgba(248,113,113,0.07)',
            border: '1px solid rgba(248,113,113,0.2)',
            borderRadius: '0.625rem', padding: '0.625rem 0.875rem', fontSize: '0.9rem',
          }}>
            {message.content}
          </div>
        ) : message.content ? (
          <div className="prose" style={{ userSelect: 'text' }}>
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkMath]}
              rehypePlugins={[[rehypeKatex, { throwOnError: false, errorColor: '#f87171' }]]}
              components={mdComponents}
            >
              {visibleContent}
            </ReactMarkdown>
            {/* Cursor: visible while streaming, or while typewriter is still catching up */}
            {(message.isStreaming || (!hasCodeBlock && isRevealing)) && <StreamingCursor />}
          </div>
        ) : message.isStreaming ? (
          // No content yet — show the natural-language working indicator
          <WorkingStatus tools={message.tools} status={message.status} />
        ) : null}

        {/* Downloadable files SPEDA produced this turn */}
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
            <ActionBtn title={copied ? 'Copied!' : 'Copy'} onClick={copy} color={copied ? 'var(--accent)' : undefined}>
              {copied ? <IconCheck /> : <IconCopy />}
            </ActionBtn>
            <ActionBtn title="Good response" onClick={() => { setThumbUp(v => !v); setThumbDown(false) }} color={thumbUp ? 'var(--accent)' : undefined}>
              <IconThumbUp />
            </ActionBtn>
            <ActionBtn title="Bad response" onClick={() => { setThumbDown(v => !v); setThumbUp(false) }} color={thumbDown ? '#f87171' : undefined}>
              <IconThumbDown />
            </ActionBtn>
            <ActionBtn title={speaking ? 'Stop reading' : 'Read aloud'} onClick={readAloud} color={speaking ? 'var(--accent)' : undefined}>
              <IconSpeaker />
            </ActionBtn>
            {onRegenerate && (
              <ActionBtn title="Regenerate response" onClick={onRegenerate}>
                <IconRefresh />
              </ActionBtn>
            )}
            {onDelete && (
              <ActionBtn title="Delete" onClick={onDelete}>
                <IconTrash />
              </ActionBtn>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
