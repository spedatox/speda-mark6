// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useRef, useState } from 'react'
import { AnnotationMode, GlobalWorkerOptions, getDocument } from 'pdfjs-dist'
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { useT } from '../lib/i18n'

GlobalWorkerOptions.workerSrc = workerUrl

export default function PdfPreview({ blob, name, active }: { blob: Blob; name: string; active: boolean }) {
  const t = useT()
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null)
  const [page, setPage] = useState(1)
  const [zoom, setZoom] = useState(1)
  const [width, setWidth] = useState(0)
  const [error, setError] = useState(false)
  const [rendered, setRendered] = useState(false)
  const viewport = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    let cancelled = false
    let task: ReturnType<typeof getDocument> | undefined
    setPdf(null)
    setPage(1)
    setError(false)
    void blob.arrayBuffer().then(buffer => {
      if (cancelled) return
      task = getDocument({ data: new Uint8Array(buffer), enableXfa: false, useWasm: false })
      return task.promise.then(document => { if (!cancelled) setPdf(document) })
    }).catch(() => { if (!cancelled) setError(true) })
    return () => { cancelled = true; void task?.destroy().catch(() => {}) }
  }, [blob])

  useEffect(() => {
    const element = viewport.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) setWidth(Math.floor(entry.contentRect.width))
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const element = canvas.current
    if (!pdf || !element || !width || !active) return
    let cancelled = false
    let task: RenderTask | undefined
    setRendered(false)
    setError(false)
    void pdf.getPage(page).then(source => {
      if (cancelled) return
      const base = source.getViewport({ scale: 1 })
      const fit = Math.max(1, width) / base.width
      const view = source.getViewport({ scale: fit * zoom })
      const pixels = Math.min(window.devicePixelRatio || 1, 2, 4096 / Math.max(view.width, view.height))
      element.width = Math.max(1, Math.floor(view.width * pixels))
      element.height = Math.max(1, Math.floor(view.height * pixels))
      element.style.width = `${view.width}px`
      element.style.height = `${view.height}px`
      task = source.render({ canvas: element, viewport: view, transform: [pixels, 0, 0, pixels, 0, 0],
        annotationMode: AnnotationMode.DISABLE })
      return task.promise.then(() => { if (!cancelled) setRendered(true) })
    }).catch(() => { if (!cancelled) setError(true) })
    return () => { cancelled = true; task?.cancel() }
  }, [pdf, page, zoom, width, active])

  const changePage = (next: number) => {
    setPage(next)
    viewport.current?.scrollTo(0, 0)
  }
  return (
    <>
      {pdf && <div className="hb-preview-toolbar hb-pdf-controls">
        <button type="button" className="hb-preview-button" aria-label={t.sidePane.previousPage}
          disabled={page <= 1} onClick={() => changePage(page - 1)}>‹</button>
        <span aria-live="polite">{page} / {pdf.numPages}</span>
        <button type="button" className="hb-preview-button" aria-label={t.sidePane.nextPage}
          disabled={page >= pdf.numPages} onClick={() => changePage(page + 1)}>›</button>
        <span className="hb-pdf-spacer" />
        <button type="button" className="hb-preview-button" aria-label={t.sidePane.zoomOut}
          disabled={zoom <= 0.5} onClick={() => setZoom(zoom - 0.25)}>−</button>
        <button type="button" className="hb-preview-button" title={t.sidePane.fitWidth}
          onClick={() => setZoom(1)}>{Math.round(zoom * 100)}%</button>
        <button type="button" className="hb-preview-button" aria-label={t.sidePane.zoomIn}
          disabled={zoom >= 2} onClick={() => setZoom(zoom + 0.25)}>+</button>
      </div>}
      <div ref={viewport} className="hb-preview-scroll hb-pdf-scroll">
        {error && <div className="hb-preview-note" role="alert">{t.sidePane.pdfFailed}</div>}
        {!error && !rendered && <div className="hb-preview-note" role="status">{t.sidePane.loading}</div>}
        <canvas ref={canvas} style={{ visibility: rendered && !error ? 'visible' : 'hidden' }}
          role="img" aria-label={`${name} · ${page}`} />
      </div>
    </>
  )
}
