import { useEffect, useRef, useState } from 'react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'

const BASE_STYLES = `
<style>
  *, *::before, *::after { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: transparent; }
  body {
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 14px; line-height: 1.6; color: #cadbe2;
    -webkit-font-smoothing: antialiased;
  }
  :root {
    --bg-primary: #060c0f; --bg-sidebar: #0b1a22;
    --bg-hover: rgba(70,150,175,0.12); --bg-active: rgba(54,171,202,0.16);
    --text-primary: #cadbe2; --text-secondary: #7a96a1; --text-muted: #46626d;
    --border: rgba(95,165,188,0.26); --border-focus: rgba(110,200,228,0.55);
    --accent: #36abca; --accent-hover: #5fcce6;
  }
</style>`

const RESIZE_SCRIPT = `
<script>
  (function() {
    function measure() {
      var b = document.body, d = document.documentElement;
      return Math.max(
        b ? b.scrollHeight : 0, b ? b.offsetHeight : 0,
        d ? d.scrollHeight : 0, d ? d.offsetHeight : 0
      );
    }
    var last = 0;
    function postHeight() {
      var h = measure();
      if (h && h !== last) {
        last = h;
        window.parent.postMessage({ type: 'speda-widget-resize', height: h }, '*');
      }
    }
    window.addEventListener('load', postHeight);
    window.addEventListener('resize', postHeight);
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(postHeight);
      ro.observe(document.documentElement);
      if (document.body) ro.observe(document.body);
    }
    // Aggressive early polling — catches charts/images that render after load
    [50, 150, 300, 600, 1000, 1600, 2400].forEach(function(t) { setTimeout(postHeight, t); });
  })();
</script>`

function buildSrcdoc(raw: string): string {
  const trimmed = raw.trim()
  const isFullDoc = /^<!DOCTYPE\s/i.test(trimmed) || /^<html[\s>]/i.test(trimmed)
  if (isFullDoc) {
    let doc = trimmed
    if (/<head>/i.test(doc)) {
      doc = doc.replace(/<head>/i, `<head>${BASE_STYLES}`)
    } else if (/<body/i.test(doc)) {
      doc = doc.replace(/<body/i, `<head>${BASE_STYLES}</head><body`)
    } else if (/<html[\s>]/i.test(doc)) {
      doc = doc.replace(/(<html[^>]*>)/i, `$1<head>${BASE_STYLES}</head>`)
    } else {
      doc = BASE_STYLES + doc
    }
    // Bulletproof script injection — try </body>, then </html>, else append.
    if (/<\/body>/i.test(doc))      return doc.replace(/<\/body>/i, `${RESIZE_SCRIPT}</body>`)
    if (/<\/html>/i.test(doc))      return doc.replace(/<\/html>/i, `${RESIZE_SCRIPT}</html>`)
    return doc + RESIZE_SCRIPT
  }
  return `<!DOCTYPE html><html><head><meta charset="utf-8">${BASE_STYLES}</head><body>${trimmed}${RESIZE_SCRIPT}</body></html>`
}

/**
 * Cinematic SVG reveal: stroked paths/lines "draw" themselves via stroke-dashoffset,
 * then filled shapes and text fade up in a staggered cascade. Runs once per render.
 */
function animateSvgDrawIn(container: HTMLElement) {
  const svg = container.querySelector('svg')
  if (!svg) return

  const STROKE_SEL = 'path, line, polyline, polygon'
  const FADE_SEL   = 'text, circle, ellipse, rect, image'

  // 1. Draw stroked geometry
  const strokeEls = Array.from(svg.querySelectorAll<SVGGeometryElement>(STROKE_SEL))
  strokeEls.forEach((el, i) => {
    let len = 0
    try { len = el.getTotalLength?.() ?? 0 } catch { len = 0 }
    // Skip degenerate or absurdly long geometry (perf guard)
    if (!len || len > 12000) return
    // Only animate if it actually has a visible stroke
    const stroke = getComputedStyle(el).stroke
    if (!stroke || stroke === 'none') return

    el.style.transition = 'none'
    el.style.strokeDasharray = `${len}`
    el.style.strokeDashoffset = `${len}`
    // next frame → release the offset so it draws
    requestAnimationFrame(() => {
      el.style.transition = `stroke-dashoffset 0.85s cubic-bezier(0.4,0,0.2,1) ${0.04 * i}s`
      el.style.strokeDashoffset = '0'
    })
  })

  // 2. Fade + rise fills, points, labels — after the lines begin drawing
  const fadeEls = Array.from(svg.querySelectorAll<SVGElement>(FADE_SEL))
  fadeEls.forEach((el, i) => {
    el.style.opacity = '0'
    el.style.transform = 'translateY(4px)'
    el.style.transformBox = 'fill-box'
    el.style.transformOrigin = 'center'
    requestAnimationFrame(() => {
      el.style.transition = `opacity 0.45s ease ${0.25 + 0.025 * i}s, transform 0.45s cubic-bezier(0.16,1,0.3,1) ${0.25 + 0.025 * i}s`
      el.style.opacity = '1'
      el.style.transform = 'translateY(0)'
    })
  })
}

interface Props { language: string; children: string }

export default function WidgetFrame({ language, children }: Props) {
  const [height, setHeight]           = useState(200)
  const [frameLoaded, setFrameLoaded] = useState(false)
  const [showCode, setShowCode]       = useState(false)
  const [hovered, setHovered]         = useState(false)
  const [copied, setCopied]           = useState(false)
  const frameId = useRef(`wf-${Math.random().toString(36).slice(2)}`)
  const svgHostRef = useRef<HTMLDivElement>(null)

  const isSvg = language === 'svg' || (language === 'html' && children.trim().startsWith('<svg'))

  // Only commit markup to the DOM once it looks COMPLETE. During streaming the
  // code grows each chunk; injecting every partial frame is what makes the SVG
  // blink/redraw. We hold the last complete version and render only that.
  const isComplete = (() => {
    const t = children.trim()
    if (isSvg) return /<\/svg>/i.test(t)
    if (/^<!DOCTYPE/i.test(t) || /^<html[\s>]/i.test(t)) return /<\/html>/i.test(t)
    return true // plain fragments have no clear terminator — render as-is
  })()
  const [stableContent, setStableContent] = useState(isComplete ? children : '')
  useEffect(() => {
    if (isComplete) setStableContent(children)
  }, [isComplete, children])

  useEffect(() => { setFrameLoaded(false); setHeight(420) }, [stableContent])

  // Animate the draw-in cascade ONCE, when the complete SVG is committed —
  // never on partial streaming frames.
  useEffect(() => {
    if (!isSvg || !stableContent) return
    const id = setTimeout(() => {
      if (svgHostRef.current) animateSvgDrawIn(svgHostRef.current)
    }, 60)
    return () => clearTimeout(id)
  }, [isSvg, stableContent])

  useEffect(() => {
    if (isSvg) return
    const handler = (e: MessageEvent) => {
      if (e.data?.type === 'speda-widget-resize' && typeof e.data.height === 'number')
        setHeight(Math.min(Math.max(e.data.height + 16, 120), 900))
    }
    window.addEventListener('message', handler)
    return () => window.removeEventListener('message', handler)
  }, [isSvg])

  const copy = () => {
    navigator.clipboard.writeText(children).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  const btnBase: React.CSSProperties = {
    display: 'flex', alignItems: 'center', gap: '0.3rem',
    padding: '0.28rem 0.6rem',
    border: '1px solid rgba(95,165,188,0.3)',
    background: 'rgba(6,14,19,0.82)',
    backdropFilter: 'blur(10px)',
    WebkitBackdropFilter: 'blur(10px)',
    color: '#9bbac5',
    fontSize: '0.64rem',
    fontFamily: "'Rajdhani', sans-serif",
    fontWeight: 700,
    cursor: 'pointer',
    letterSpacing: '0.14em',
    textTransform: 'uppercase',
    transition: 'background 0.12s, color 0.12s, border-color 0.12s',
    userSelect: 'none',
  }

  return (
    <div
      style={{ position: 'relative', margin: '0.625rem 0' }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* ── Render area — no chrome, feels like part of the message ── */}
      {isSvg ? (
        stableContent ? (
          <div
            ref={svgHostRef}
            style={{
              overflowX: 'auto',
              borderRadius: '0.75rem',
              animation: 'fadeIn 0.3s ease both',
            }}
            dangerouslySetInnerHTML={{ __html: stableContent }}
          />
        ) : (
          // Streaming — SVG not closed yet. Calm placeholder, no flicker.
          <div style={{
            minHeight: 200, display: 'flex', alignItems: 'center', justifyContent: 'center',
            gap: 6, borderRadius: '0.75rem', background: 'rgba(255,255,255,0.015)',
          }}>
            {[0, 0.18, 0.36].map((delay, i) => (
              <span key={i} style={{
                width: 7, height: 7, borderRadius: '50%', background: 'var(--accent)',
                animation: `skeletonPulse 1.1s ease ${delay}s infinite`,
              }} />
            ))}
          </div>
        )
      ) : (
        <div style={{
          position: 'relative',
          borderRadius: '0.75rem',
          overflow: 'hidden',
          animation: 'widgetEntrance 0.4s cubic-bezier(0.16,1,0.3,1) both',
        }}>
          {/* Skeleton dots while iframe loads */}
          {!frameLoaded && (
            <div style={{
              position: 'absolute', inset: 0, minHeight: 240,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              gap: 6, pointerEvents: 'none', background: 'rgba(255,255,255,0.015)',
              borderRadius: '0.75rem',
            }}>
              {[0, 0.18, 0.36].map((delay, i) => (
                <span key={i} style={{
                  width: 7, height: 7, borderRadius: '50%',
                  background: 'var(--accent)',
                  animation: `skeletonPulse 1.1s ease ${delay}s infinite`,
                }} />
              ))}
            </div>
          )}

          <iframe
            key={frameId.current}
            srcDoc={buildSrcdoc(stableContent || children)}
            sandbox="allow-scripts allow-downloads allow-same-origin allow-forms"
            scrolling="no"
            onLoad={() => setTimeout(() => setFrameLoaded(true), 120)}
            style={{
              width: '100%',
              height: frameLoaded ? height : 240,
              border: 'none',
              display: 'block',
              background: 'transparent',
              borderRadius: '0.75rem',
              opacity: frameLoaded ? 1 : 0,
              transition: 'opacity 0.35s ease, height 0.3s ease',
              overflow: 'hidden',
            }}
            title="widget"
            allowTransparency
          />
        </div>
      )}

      {/* ── Floating action buttons — appear on hover ─────────────── */}
      <div style={{
        position: 'absolute',
        top: 10, right: 10,
        display: 'flex', gap: '6px',
        opacity: hovered ? 1 : 0,
        transform: hovered ? 'translateY(0)' : 'translateY(-4px)',
        transition: 'opacity 0.18s ease, transform 0.18s ease',
        pointerEvents: hovered ? 'auto' : 'none',
        zIndex: 10,
      }}>
        {/* Toggle code */}
        <button
          onClick={() => setShowCode(s => !s)}
          style={{
            ...btnBase,
            color: showCode ? 'var(--hb-cyan-bright)' : '#9bbac5',
            borderColor: showCode ? 'rgba(110,200,228,0.5)' : 'rgba(95,165,188,0.3)',
            background: showCode ? 'rgba(54,171,202,0.14)' : 'rgba(6,14,19,0.82)',
          }}
        >
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
          </svg>
          {showCode ? 'Hide' : 'Code'}
        </button>

        {/* Copy */}
        <button onClick={copy} style={{ ...btnBase, color: copied ? 'var(--hb-green)' : '#9bbac5' }}>
          {copied
            ? <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12"/></svg>
            : <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          }
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>

      {/* ── Code panel — slides in below the render ──────────────── */}
      {showCode && (
        <div style={{
          marginTop: '0.5rem',
          borderRadius: '0.75rem',
          overflow: 'hidden',
          border: '1px solid var(--border)',
          animation: 'fadeSlideIn 0.2s ease both',
        }}>
          <SyntaxHighlighter
            language={language || 'text'}
            style={vscDarkPlus}
            customStyle={{
              margin: 0,
              padding: '1rem 1.125rem',
              background: 'var(--bg-code)',
              fontSize: '0.8rem',
              lineHeight: 1.65,
              borderRadius: 0,
              maxHeight: 360,
              overflow: 'auto',
            }}
            codeTagProps={{ style: { fontFamily: "'JetBrains Mono','Fira Code',monospace" } }}
          >
            {children}
          </SyntaxHighlighter>
        </div>
      )}
    </div>
  )
}
