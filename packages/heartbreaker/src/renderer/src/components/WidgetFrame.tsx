// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useMemo, useRef, useState } from 'react'
import { buildWidgetDocument, WIDGET_THEME_DEFAULTS } from '../lib/widgetDocument'
import CodeBlock from './CodeBlock'
import { useRenderSurface } from '../lib/renderSurface'

export default function WidgetFrame({ language, children }: { language: string; children: string }) {
  const { canvas } = useRenderSurface()
  const svgMinWidth = useMemo(() => {
    if (!canvas || (language !== 'svg' && !children.trim().startsWith('<svg'))) return 0
    const viewBox = /viewBox=["']\s*[\d.-]+[ ,]+[\d.-]+[ ,]+([\d.]+)/i.exec(children)
    const fonts = [...children.matchAll(/font-size\s*(?:=\s*["']|:\s*)([\d.]+)/gi)].map(m => +m[1]).filter(n => n >= 6)
    return Math.min(5000, Math.max(560, (Number(viewBox?.[1]) || 720) * Math.max(1, 14 / Math.min(16, ...fonts))))
  }, [canvas, language, children])
  const [height, setHeight] = useState(180)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [showCode, setShowCode] = useState(false)
  const [copied, setCopied] = useState(false)
  const [theme, setTheme] = useState(WIDGET_THEME_DEFAULTS)
  const [revision, setRevision] = useState(0)
  const host = useRef<HTMLDivElement>(null)
  const frame = useRef<HTMLIFrameElement>(null)
  const copyTimer = useRef<ReturnType<typeof setTimeout>>()
  const channel = useRef(`widget-${crypto.randomUUID()}`)
  const document = useMemo(() => buildWidgetDocument(children, theme), [children, theme])
  const hostUrl = useMemo(() => new URL(`${import.meta.env.BASE_URL}widget-host.html`, window.location.href).href, [])

  useEffect(() => {
    if (!host.current) return
    const style = getComputedStyle(host.current)
    const next = Object.fromEntries(Object.entries(WIDGET_THEME_DEFAULTS).map(([key, fallback]) => [key, style.getPropertyValue(key).trim() || fallback]))
    setTheme(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next)
  }, [children])

  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.data?.channel !== channel.current) return
      if (event.data.type === 'speda-widget-resize' && Number.isFinite(event.data.height)) {
        setHeight(Math.min(1400, Math.max(48, event.data.height)))
        setLoaded(true)
      }
      if (event.data.type === 'speda-widget-error') { setError(String(event.data.message)); setLoaded(true) }
    }
    window.addEventListener('message', receive)
    return () => window.removeEventListener('message', receive)
  }, [])
  useEffect(() => () => clearTimeout(copyTimer.current), [])
  useEffect(() => {
    if (loaded) return
    const timeout = setTimeout(() => {
      setError('Preview did not initialize. Open the source or reset to try again.')
      setLoaded(true)
    }, 8000)
    return () => clearTimeout(timeout)
  }, [document, loaded, revision])

  const sendDocument = () => {
    setError('')
    frame.current?.contentWindow?.postMessage({ type: 'speda-widget-init', channel: channel.current, document }, '*')
  }
  const reset = () => { setLoaded(false); setRevision(n => n + 1); sendDocument() }
  useEffect(() => { setLoaded(false); sendDocument() }, [document])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(children); setCopied(true)
      clearTimeout(copyTimer.current); copyTimer.current = setTimeout(() => setCopied(false), 1800)
    } catch { setError('Copy unavailable. Open the source to select it.') }
  }
  return <div ref={host} className="hb-inline-widget">
    <div className="hb-widget-toolbar">
      <span>{language === 'svg' ? 'Visualization' : 'Interactive'}</span>
      <button onClick={() => setShowCode(s => !s)} aria-expanded={showCode}>{showCode ? 'Hide source' : 'Source'}</button>
      <button onClick={copy}>{copied ? 'Copied' : 'Copy'}</button>
      <button onClick={reset} title="Reset widget to its initial values">Reset</button>
    </div>
    {!loaded && <div className="hb-widget-loading" role="status">Preparing preview…</div>}
    <div className="hb-widget-preview" style={{ overflowX: 'auto' }}>
      <iframe ref={frame} src={hostUrl} sandbox="allow-scripts" title={language === 'svg' ? 'Inline visualization' : 'Interactive widget'}
        onLoad={sendDocument} style={{ width: '100%', minWidth: svgMinWidth, height, border: 0, display: 'block', background: 'transparent', opacity: loaded ? 1 : 0 }} />
    </div>
    {error && <div className="hb-widget-error" role="status">{error}</div>}
    {showCode && <CodeBlock language={language}>{children}</CodeBlock>}
  </div>
}
