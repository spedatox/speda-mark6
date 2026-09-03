// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useContext, useEffect, useMemo, useRef, useState } from 'react'
import { TextSegment } from './Message'
import { loadBoardImage } from '../lib/voice'
import { ChatContext } from '../store/chat'
import { FRAMED, type PanelKind, type VoicePanel } from '../lib/voicePanels'

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  WHAT A WINDOW LOOKS LIKE.
 *
 *  Voice mode's board is evidence, not prose, and the presentation kinds exist
 *  so a fact can be SHOWN rather than said: a figure as a tile, a source as a
 *  cutting with its photo, a person as a file. The agent authors these blocks
 *  (igor core/surface.py _VOICE_BRIEF); this is where they become something to
 *  look at.
 *
 *  Every parser here is deliberately FORGIVING. These bodies are written by a
 *  language model mid-sentence, under a word budget, while narrating — so a
 *  missing field, a stray blank line, a label the brief never mentioned must
 *  degrade to a window that is merely plainer, never to an empty one or a
 *  crash. The rule throughout: if a line cannot be understood, show it as text.
 * ════════════════════════════════════════════════════════════════════════════
 */

/** `Field: value` lines from the head of a block, plus whatever came after them.
 *  Stops at the first line that is not a field, so a body can lead with metadata
 *  and follow with prose without needing a separator. */
function fields(src: string): { meta: Record<string, string>; rest: string } {
  const lines = src.split('\n')
  const meta: Record<string, string> = {}
  let i = 0
  for (; i < lines.length; i++) {
    const line = lines[i]
    if (!line.trim()) { if (Object.keys(meta).length) { i++; break } else continue }
    const m = /^\s*([A-Za-z][\w .-]{0,40}?)\s*:\s*(.*)$/.exec(line)
    if (!m) break
    meta[m[1].trim().toLowerCase()] = m[2].trim()
  }
  return { meta, rest: lines.slice(i).join('\n').trim() }
}

/** Non-empty lines, trimmed — the shape most of these formats reduce to. */
const rows = (src: string): string[] =>
  src.split('\n').map(l => l.trim()).filter(Boolean)

/** Is this a URL we would put in an <img>? Anything else in an image slot is a
 *  description the model wrote instead of a link, and belongs as a caption. */
const isUrl = (s: string): boolean => /^(https?:\/\/|data:image\/|file:\/\/)/i.test(s.trim())

/**
 * A picture on the board.
 *
 * The URL is NOT handed to the tag. It goes through Igor's proxy and comes back
 * as a blob (lib/voice.ts loadBoardImage): the renderer's CSP forbids remote
 * images outright, and loading one straight from its origin would tell that
 * origin the owner is looking — which, on a board about a person, is the whole
 * thing the board is for.
 *
 * Anything that fails renders NOTHING. A dead link, a host that refuses, a URL
 * the model invented rather than found — all of them leave a window with its
 * fields and no photo, which is the intended degradation. A broken-image icon
 * on a dossier is worse than a dossier without a picture.
 */
function Shot({ src, alt, height }: { src: string; alt: string; height?: number | string }) {
  // Read the context directly rather than through useChatContext, which THROWS
  // when there is no provider. A missing config must cost this window its photo,
  // not tear down the whole board — one unrenderable picture is not a reason to
  // lose the dossier it was attached to.
  const config = useContext(ChatContext)?.state.config ?? null
  const [blob, setBlob] = useState<string | null>(null)

  useEffect(() => {
    setBlob(null)
    if (!config || !isUrl(src)) return
    const ctrl = new AbortController()
    let url: string | null = null
    let live = true
    loadBoardImage(config, src, ctrl.signal).then(u => {
      url = u
      // Revoke immediately if the window went away mid-flight — otherwise the
      // object URL leaks its bytes for the life of the session.
      if (!live) { if (u) URL.revokeObjectURL(u); return }
      setBlob(u)
    })
    return () => {
      live = false
      ctrl.abort()
      if (url) URL.revokeObjectURL(url)
    }
  }, [config, src])

  if (!blob) return null
  return (
    <img
      src={blob}
      alt={alt}
      style={{
        width: '100%', height: height ?? 'auto', maxHeight: '100%',
        objectFit: 'cover', display: 'block',
        border: '1px solid var(--hb-line)', borderRadius: 4,
        background: 'var(--hb-petrol)',
      }}
    />
  )
}

const LABEL_STYLE: React.CSSProperties = {
  fontFamily: "'Rajdhani', sans-serif", fontSize: '0.58rem', fontWeight: 700,
  letterSpacing: '0.18em', color: 'var(--hb-text-faint)', textTransform: 'uppercase',
}

/* ── stat ──────────────────────────────────────────────────────────────────
 * Line 1 the value, line 2 the change, line 3 an optional caption. The one kind
 * whose whole job is to be read from across the room, so the value is set as
 * large as the tile allows and everything else is annotation around it.
 *
 * The change line is coloured by its SIGN rather than by a field the model has
 * to remember to set: a leading `-` or a `↓` is red, a `+` or `↑` green, and
 * anything ambiguous stays neutral rather than guessing — a briefing that
 * colours a neutral figure green has told the owner something untrue. */
function Stat({ src }: { src: string }) {
  const [value = '', delta = '', ...rest] = rows(src)
  const dir = /^[-−↓]|\bdown\b|\bdüş/i.test(delta) ? 'down'
    : /^[+↑]|\bup\b|\bart/i.test(delta) ? 'up' : 'flat'
  const color = dir === 'down' ? 'var(--hb-red)'
    : dir === 'up' ? 'var(--hb-green)' : 'var(--hb-text-dim)'
  return (
    <div style={{
      height: '100%', display: 'flex', flexDirection: 'column',
      justifyContent: 'center', gap: '0.3rem', padding: '0.2rem 0.1rem',
    }}>
      <div style={{
        fontFamily: "'Rajdhani', sans-serif", fontWeight: 700,
        fontSize: 'clamp(1.4rem, 3.4vw, 2.4rem)', lineHeight: 1,
        letterSpacing: '0.01em', color: 'var(--hb-text)', overflowWrap: 'anywhere',
      }}>
        {value}
      </div>
      {delta && (
        <div style={{
          fontFamily: "'Rajdhani', sans-serif", fontWeight: 600,
          fontSize: '0.8rem', letterSpacing: '0.06em', color,
        }}>
          {delta}
        </div>
      )}
      {rest.length > 0 && (
        <div style={{ ...LABEL_STYLE, letterSpacing: '0.1em', lineHeight: 1.4 }}>
          {rest.join(' ')}
        </div>
      )}
    </div>
  )
}

/* ── image ─────────────────────────────────────────────────────────────────
 * A URL on line 1, an optional caption after it. The plain "put this on the
 * screen" window — a photo of a person, a scan, a diagram the agent found
 * rather than drew. */
function Picture({ src }: { src: string }) {
  const [url = '', ...caption] = rows(src)
  const text = caption.join(' ')
  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
      <div style={{ flex: 1, minHeight: 0 }}>
        <Shot src={url} alt={text || 'image'} height="100%" />
      </div>
      {text && (
        <div style={{ fontSize: '0.72rem', color: 'var(--hb-text-dim)', lineHeight: 1.45 }}>
          {text}
        </div>
      )}
    </div>
  )
}

/* ── article ───────────────────────────────────────────────────────────────
 * The newspaper cutting: `title/source/date/url/image`, then the excerpt that
 * mattered. This is the window the whole redesign was argued from — asked to
 * research someone, an agent should put the articles on the wall, not read
 * their summaries out. */
function Article({ src }: { src: string }) {
  const { meta, rest } = fields(src)
  const url = meta.url || ''
  const title = meta.title || meta.headline || ''
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.45rem', height: '100%' }}>
      {meta.image && <div style={{ flexShrink: 0, maxHeight: '45%' }}>
        <Shot src={meta.image} alt={title} height={110} />
      </div>}
      <div style={{ ...LABEL_STYLE, display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        {meta.source && <span style={{ color: 'var(--hb-cyan)' }}>{meta.source}</span>}
        {meta.date && <span>{meta.date}</span>}
      </div>
      {title && (
        <div style={{
          fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: '0.95rem',
          lineHeight: 1.25, color: 'var(--hb-text)',
        }}>
          {/* The headline is the link. A URL printed on a board is noise — it is
              never read aloud, and nobody types one off a screen. */}
          {url ? (
            <a href={url} target="_blank" rel="noreferrer"
               style={{ color: 'inherit', textDecoration: 'none', borderBottom: '1px solid var(--hb-line-bright)' }}>
              {title}
            </a>
          ) : title}
        </div>
      )}
      {rest && (
        <div style={{
          flex: 1, minHeight: 0, overflow: 'auto',
          fontSize: '0.76rem', lineHeight: 1.5, color: 'var(--hb-text-dim)',
        }}>
          {rest}
        </div>
      )}
    </div>
  )
}

/* ── card ──────────────────────────────────────────────────────────────────
 * A name, a photo, and a column of `Field: value`. A person, a company, an
 * aircraft, a place — whatever the turn is actually ABOUT, as a file rather
 * than as a paragraph describing it. */
function Card({ src }: { src: string }) {
  const lines = src.split('\n')
  // The name is the first line only if it is not itself a field — a body that
  // opens straight into `Role: …` is a file with no title, which is fine.
  const headIsField = /^\s*[A-Za-z][\w .-]{0,40}?\s*:/.test(lines[0] ?? '')
  const name = headIsField ? '' : (lines[0] ?? '').trim()
  const { meta, rest } = fields(headIsField ? src : lines.slice(1).join('\n'))
  const photo = meta.image || meta.photo || ''
  const entries = Object.entries(meta).filter(([k]) => k !== 'image' && k !== 'photo')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', height: '100%' }}>
      {photo && <div style={{ flexShrink: 0 }}><Shot src={photo} alt={name} height={120} /></div>}
      {name && (
        <div style={{
          fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: '1rem',
          letterSpacing: '0.04em', color: 'var(--hb-text)',
        }}>
          {name}
        </div>
      )}
      {/* alignContent:start — a grid that is taller than its rows distributes
          the slack BETWEEN them, which turns a four-line file into four lines
          spread down the whole window. The fields belong at the top. */}
      <div style={{
        flex: 1, minHeight: 0, overflow: 'auto',
        display: 'grid', gap: '0.28rem', alignContent: 'start',
      }}>
        {entries.map(([k, v]) => (
          <div key={k} style={{ display: 'grid', gridTemplateColumns: '38% 1fr', gap: '0.5rem' }}>
            <span style={LABEL_STYLE}>{k}</span>
            <span style={{ fontSize: '0.78rem', color: 'var(--hb-text)', overflowWrap: 'anywhere' }}>{v}</span>
          </div>
        ))}
        {rest && (
          <div style={{ fontSize: '0.76rem', lineHeight: 1.5, color: 'var(--hb-text-dim)', marginTop: '0.2rem' }}>
            {rest}
          </div>
        )}
      </div>
    </div>
  )
}

/* ── timeline ──────────────────────────────────────────────────────────────
 * One `date — what happened` per line. A sequence is the thing prose is worst
 * at and a column of dated rows is best at, which is exactly why it is a kind:
 * spoken, six dates in a row are unfollowable. */
function Timeline({ src }: { src: string }) {
  const items = rows(src).map(line => {
    const m = /^(.{1,32}?)\s*[—–\-:|]\s+(.*)$/.exec(line)
    return m ? { when: m[1].trim(), what: m[2].trim() } : { when: '', what: line }
  })
  return (
    <div style={{ height: '100%', overflow: 'auto', display: 'grid', gap: '0.05rem', alignContent: 'start' }}>
      {items.map((it, n) => (
        <div key={n} style={{ display: 'flex', gap: '0.6rem', padding: '0.32rem 0' , borderTop: n ? '1px solid var(--hb-line)' : undefined }}>
          {/* The rail: a marker per event, so the column reads as a sequence
              rather than as a list that happens to start with dates. */}
          <span style={{ color: 'var(--hb-cyan-dim)', fontSize: '0.6rem', lineHeight: 1.9 }}>◆</span>
          <div style={{ minWidth: 0 }}>
            {it.when && <div style={LABEL_STYLE}>{it.when}</div>}
            <div style={{ fontSize: '0.78rem', lineHeight: 1.45, color: 'var(--hb-text)' }}>{it.what}</div>
          </div>
        </div>
      ))}
    </div>
  )
}

/* ── quote ─────────────────────────────────────────────────────────────────
 * The quote, then a line starting `— ` with who said it. Exists so a source's
 * own words can be put on the board verbatim instead of paraphrased into the
 * narration, which is the one thing a research readout must never do. */
function Quote({ src }: { src: string }) {
  const lines = rows(src)
  const attrIdx = lines.findIndex(l => /^[—–-]\s+/.test(l))
  const body = (attrIdx < 0 ? lines : lines.slice(0, attrIdx)).join(' ')
  const attr = attrIdx < 0 ? '' : lines[attrIdx].replace(/^[—–-]\s+/, '')
  return (
    <div style={{
      height: '100%', display: 'flex', flexDirection: 'column',
      gap: '0.55rem', overflow: 'auto',
      // Centred by margin, not by justifyContent: centring a scrollable column
      // splits the overflow above and below it, and the half above is
      // unreachable — a long quote loses its first line for good.
      justifyContent: 'flex-start',
    }}>
      <div style={{ marginTop: 'auto' }} />
      <div style={{
        fontSize: '0.88rem', lineHeight: 1.55, color: 'var(--hb-text)',
        fontStyle: 'italic', borderLeft: '2px solid var(--hb-line-bright)', paddingLeft: '0.7rem',
      }}>
        {body}
      </div>
      {attr && <div style={{ ...LABEL_STYLE, paddingLeft: '0.7rem' }}>— {attr}</div>}
      <div style={{ marginBottom: 'auto' }} />
    </div>
  )
}

const RENDERER: Partial<Record<PanelKind, (p: { src: string }) => JSX.Element>> = {
  stat: Stat, image: Picture, article: Article, card: Card,
  timeline: Timeline, quote: Quote,
}

/**
 * A window's contents. The presentation kinds have their own renderers above;
 * everything else goes through the transcript's own markdown pipeline, so a
 * chart on the board is the same chart it is in chat — one implementation, and
 * no way for the two surfaces to drift apart.
 */
export default function VoicePanelBody({ panel }: { panel: VoicePanel }) {
  const framed = FRAMED.has(panel.kind)
  const ref = useRef<HTMLDivElement>(null)
  const Custom = RENDERER[panel.kind]

  // A window that is still being written rides its own tail — a long article
  // excerpt or a growing timeline should show the end that is arriving, not the
  // beginning that has already been read.
  useEffect(() => {
    const el = ref.current
    if (!el || Custom) return
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [Custom, panel.source])

  const content = useMemo(
    () => (Custom
      ? <Custom src={panel.source} />
      : (
        <div className="prose" style={{ overflowWrap: 'anywhere', minWidth: 0 }}>
          <TextSegment text={panel.source} />
        </div>
      )),
    [Custom, panel.source],
  )

  return (
    <div
      ref={ref}
      className={framed ? 'hb-holo' : undefined}
      style={{
        flex: 1, minHeight: 0, overflow: Custom ? 'hidden' : 'auto',
        padding: framed ? '0.7rem 0.85rem' : 0,
      }}
    >
      {content}
    </div>
  )
}
