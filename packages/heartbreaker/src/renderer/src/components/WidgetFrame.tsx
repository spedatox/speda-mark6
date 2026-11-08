// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { buildWidgetDocument, WIDGET_THEME_DEFAULTS } from '../lib/widgetDocument'
import CodeBlock from './CodeBlock'
import { useRenderSurface } from '../lib/renderSurface'

const CONTROL_HINTS = ['Drag the slider', 'Enter a value', 'Choose an option', 'Use the buttons', 'Open details']
const ZOOM_STEPS = [.5, .75, 1, 1.25, 1.5, 2, 3, 4, 6, 8]
const FIT = ZOOM_STEPS.indexOf(1)

export default function WidgetFrame({ language, children }: { language: string; children: string }) {
  const { canvas } = useRenderSurface()
  const vector = language === 'svg' || /^<svg[\s>]/i.test(children.trimStart())
  const inspect = vector || canvas
  const [height, setHeight] = useState(180)
  const [ratio, setRatio] = useState(1.6)
  const [controls, setControls] = useState(0)
  const [viewport, setViewport] = useState({ width: 0, height: 0 })
  const [zoom, setZoom] = useState(FIT)
  const [pan, setPan] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [showCode, setShowCode] = useState(false)
  const [copied, setCopied] = useState(false)
  const [theme, setTheme] = useState(WIDGET_THEME_DEFAULTS)
  const [revision, setRevision] = useState(0)
  const host = useRef<HTMLDivElement>(null)
  const frame = useRef<HTMLIFrameElement>(null)
  const preview = useRef<HTMLDivElement>(null)
  const anchor = useRef<{ x: number; y: number }>()
  const drag = useRef<{ x: number; y: number; left: number; top: number }>()
  const copyTimer = useRef<ReturnType<typeof setTimeout>>()
  const document = useMemo(() => buildWidgetDocument(children, theme, vector), [children, theme, vector])
  const channel = useMemo(() => `widget-${crypto.randomUUID()}`, [document, revision])
  const hostUrl = useMemo(() => new URL(`${import.meta.env.BASE_URL}widget-host.html`, window.location.href).href, [])

  useEffect(() => {
    if (!host.current) return
    const style = getComputedStyle(host.current)
    const next = Object.fromEntries(Object.entries(WIDGET_THEME_DEFAULTS).map(([key, fallback]) => [key, style.getPropertyValue(key).trim() || fallback]))
    setTheme(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next)
  }, [children])

  useLayoutEffect(() => {
    const element = preview.current
    if (!element || !vector) return
    const observer = new ResizeObserver(() => {
      const width = element.clientWidth, height = element.clientHeight
      if (width && height) setViewport(previous => previous.width === width && previous.height === height ? previous : { width, height })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [vector])

  const changeZoom = (next: number) => {
    const element = preview.current
    if (vector && element && next !== zoom) {
      anchor.current = {
        x: (element.scrollLeft + element.clientWidth / 2) / element.scrollWidth,
        y: (element.scrollTop + element.clientHeight / 2) / element.scrollHeight,
      }
    }
    if (next <= FIT) setPan(false)
    setZoom(next)
  }
  useLayoutEffect(() => {
    const element = preview.current, point = anchor.current
    if (!element || !point) return
    element.scrollTo(point.x * element.scrollWidth - element.clientWidth / 2,
                     point.y * element.scrollHeight - element.clientHeight / 2)
    anchor.current = undefined
  }, [zoom])

  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.data?.channel !== channel) return
      if (event.data.type === 'speda-widget-resize' && Number.isFinite(event.data.height)) {
        setHeight(Math.min(1400, Math.max(48, event.data.height)))
        const measuredRatio = event.data.ratio
        if (Number.isFinite(measuredRatio) && measuredRatio > 0) setRatio(Math.max(.001, Math.min(1000, measuredRatio)))
        if (Number.isInteger(event.data.controls)) setControls(event.data.controls & 31)
        setLoaded(true)
      }
      if (event.data.type === 'speda-widget-error') { setError(String(event.data.message)); setLoaded(true) }
    }
    window.addEventListener('message', receive)
    return () => window.removeEventListener('message', receive)
  }, [channel])
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
    frame.current?.contentWindow?.postMessage({ type: 'speda-widget-init', channel, document }, '*')
  }
  const reset = () => { setLoaded(false); setRevision(n => n + 1) }
  useEffect(() => { setLoaded(false); sendDocument() }, [document, channel])
  useEffect(() => {
    if (!vector && loaded) {
      frame.current?.contentWindow?.postMessage({ type: 'speda-widget-scale', channel, scale: ZOOM_STEPS[zoom] }, '*')
    }
  }, [channel, loaded, vector, zoom])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(children); setCopied(true)
      clearTimeout(copyTimer.current); copyTimer.current = setTimeout(() => setCopied(false), 1800)
    } catch { setError('Copy unavailable. Open the source to select it.') }
  }
  const scale = ZOOM_STEPS[zoom]
  const fitWidth = Math.max(1, Math.min(viewport.width, viewport.height * ratio))
  const vectorWidth = fitWidth * scale, vectorHeight = vectorWidth / ratio
  const hints = CONTROL_HINTS.filter((_, index) => (controls & (1 << index)) !== 0)
  const label = vector ? 'Visualization' : controls ? 'Interactive' : 'Preview'
  return <div ref={host} className="hb-inline-widget" data-source={showCode}>
    <div className="hb-widget-toolbar">
      <div className="hb-widget-description">
        <span className="hb-widget-kind"><span aria-hidden>{vector ? '◇' : controls ? '✦' : '□'}</span>{label}</span>
        {!!hints.length && <span className="hb-widget-hint">{hints.join(' · ')}</span>}
      </div>
      <div className="hb-widget-actions">
        {inspect && <div className="hb-widget-zoom" role="group" aria-label="Artifact zoom">
          <button onClick={() => changeZoom(zoom - 1)} disabled={zoom === 0} aria-label="Zoom out">−</button>
          <output aria-label="Zoom level">{Math.round(scale * 100)}%</output>
          <button onClick={() => changeZoom(zoom + 1)} disabled={zoom === ZOOM_STEPS.length - 1} aria-label="Zoom in">+</button>
          <button onClick={() => changeZoom(FIT)} aria-label="Fit to frame">Fit</button>
          {vector && <button disabled={zoom <= FIT} aria-pressed={pan} onClick={() => setPan(active => !active)}
            title="Drag to move around the zoomed artifact">Pan</button>}
        </div>}
        <button onClick={() => setShowCode(s => !s)} aria-expanded={showCode}>{showCode ? 'Hide source' : 'Source'}</button>
        <button onClick={copy} title="Copy artifact source">{copied ? 'Copied' : 'Copy'}</button>
        <button onClick={reset} title="Reset artifact to its initial state">Reset</button>
      </div>
    </div>
    <div className="hb-widget-preview" ref={preview} data-vector={vector}
      style={vector && !canvas ? { aspectRatio: ratio } : undefined}
      role={inspect ? 'region' : undefined} aria-label={vector ? 'Visualization preview' : canvas ? 'Artifact preview' : undefined}
      tabIndex={inspect ? 0 : undefined}>
      {!loaded && <div className="hb-widget-loading" role="status">Preparing preview…</div>}
      <div className={vector ? 'hb-widget-plane' : undefined}
        style={vector ? { width: vectorWidth, height: vectorHeight } : undefined}>
        <iframe ref={frame} src={hostUrl} sandbox="allow-scripts" title={vector ? 'Inline visualization' : 'Interactive widget'}
          onLoad={sendDocument} style={{
            width: vector ? vectorWidth : '100%',
            height: vector ? vectorHeight : height,
            opacity: loaded ? 1 : 0,
          }} />
        {pan && <div className="hb-widget-pan" data-dragging={dragging} aria-hidden
          onPointerDown={event => {
            const element = preview.current
            if (event.button !== 0 || !element) return
            event.currentTarget.setPointerCapture(event.pointerId)
            drag.current = { x: event.clientX, y: event.clientY, left: element.scrollLeft, top: element.scrollTop }
            setDragging(true)
          }}
          onPointerMove={event => {
            const start = drag.current
            if (start) preview.current?.scrollTo(start.left + start.x - event.clientX, start.top + start.y - event.clientY)
          }}
          onLostPointerCapture={() => { drag.current = undefined; setDragging(false) }} />}
      </div>
    </div>
    {error && <div className="hb-widget-error" role="status">{error}</div>}
    {showCode && <div className="hb-widget-source"><CodeBlock language={language}>{children}</CodeBlock></div>}
  </div>
}
