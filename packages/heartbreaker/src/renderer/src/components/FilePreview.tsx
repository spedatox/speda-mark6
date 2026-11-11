// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { lazy, Suspense, useEffect, useState } from 'react'
import type { FileMeta } from '../lib/types'
import { fetchFile, saveFileBlob } from '../lib/api'
import { useT } from '../lib/i18n'
import { useChatContext } from '../store/chat'

const PdfPreview = lazy(() => import('./PdfPreview'))
const MAX_PREVIEW = 64 * 1024 * 1024
const MAX_TEXT = 256 * 1024
const IMAGE_TYPES: Record<string, string> = {
  png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp',
  avif: 'image/avif', bmp: 'image/bmp', ico: 'image/x-icon', svg: 'image/svg+xml',
}
const TEXT_TYPES = new Set(['txt', 'md', 'csv', 'tsv', 'json', 'log', 'yaml', 'yml', 'xml', 'html', 'css', 'js', 'ts', 'py', 'sql'])

export type PreviewFile =
  | { source: 'server'; file: FileMeta }
  | { source: 'local'; file: File }
  | { source: 'image'; src: string; name: string }

type Preview =
  | { kind: 'image'; src: string }
  | { kind: 'pdf'; blob: Blob }
  | { kind: 'text'; text: string; truncated: boolean }
  | { kind: 'unsupported' | 'tooLarge' | 'failed' }

export default function FilePreview({ file, active }: { file: PreviewFile; active: boolean }) {
  const t = useT()
  const { state: { config } } = useChatContext()
  const [preview, setPreview] = useState<Preview | null>(null)
  const [blob, setBlob] = useState<Blob | null>(null)
  const [download, setDownload] = useState<'busy' | 'error' | null>(null)
  const [imageError, setImageError] = useState(false)
  const name = file.source === 'image' ? file.name : file.file.name

  useEffect(() => {
    setPreview(null)
    setBlob(null)
    setImageError(false)
    if (file.source === 'image') {
      setPreview({ kind: 'image', src: file.src })
      return
    }
    const ext = name.slice(name.lastIndexOf('.') + 1).toLowerCase()
    const pdf = ext === 'pdf' || ('kind' in file.file && file.file.kind?.toLowerCase() === 'pdf')
      || ('type' in file.file && file.file.type === 'application/pdf')
    const localType = file.source === 'local' ? file.file.type : ''
    const imageType = IMAGE_TYPES[ext] || Object.values(IMAGE_TYPES).find(type => type === localType)
    const text = TEXT_TYPES.has(ext) || ('type' in file.file && file.file.type.startsWith('text/'))
    if (!pdf && !imageType && !text) { setPreview({ kind: 'unsupported' }); return }
    if (file.file.size > MAX_PREVIEW) { setPreview({ kind: 'tooLarge' }); return }

    const controller = new AbortController()
    let url: string | undefined
    async function load() {
      const data = file.source === 'local' ? file.file
        : file.source === 'server' && config ? await fetchFile(config, file.file.url, controller.signal, MAX_PREVIEW)
        : null
      if (controller.signal.aborted) return
      if (!data) throw new Error('File unavailable')
      setBlob(data)
      if (pdf) setPreview({ kind: 'pdf', blob: data })
      else if (imageType) {
        // The download endpoint intentionally returns application/octet-stream.
        url = URL.createObjectURL(data.slice(0, data.size, imageType))
        setPreview({ kind: 'image', src: url })
      } else {
        const content = await data.slice(0, MAX_TEXT).text()
        if (!controller.signal.aborted) setPreview({ kind: 'text', text: content, truncated: data.size > MAX_TEXT })
      }
    }
    void load().catch(() => { if (!controller.signal.aborted) setPreview({ kind: 'failed' }) })
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url) }
  }, [config, file, name])

  async function downloadFile() {
    if (download === 'busy' || file.source === 'image') return
    setDownload('busy')
    try {
      const data = blob ?? (file.source === 'local' ? file.file : config ? await fetchFile(config, file.file.url) : null)
      if (!data) throw new Error('File unavailable')
      saveFileBlob(data, name)
      setDownload(null)
    } catch {
      setDownload('error')
    }
  }

  return (
    <>
      <div className="hb-preview-toolbar">
        <span className="hb-preview-name" title={name}>{name}</span>
        {file.source !== 'image' && <button type="button" className="hb-preview-button" onClick={downloadFile}
          disabled={download === 'busy'}>{t.message.download}</button>}
      </div>
      {download === 'error' && <div className="hb-preview-note" role="alert">{t.sidePane.downloadFailed}</div>}
      {preview?.kind === 'pdf' ? (
        <Suspense fallback={<div className="hb-preview-note" role="status">{t.sidePane.loading}</div>}>
          <PdfPreview blob={preview.blob} name={name} active={active} />
        </Suspense>
      ) : (
        <div className={`hb-preview-scroll${preview?.kind === 'image' ? ' hb-preview-image' : ''}`}>
          {preview?.kind === 'image' && !imageError && <img src={preview.src} alt={name} onError={() => setImageError(true)} />}
          {preview?.kind === 'text' && <>
            <pre className="hb-preview-text">{preview.text}</pre>
            {preview.truncated && <div className="hb-preview-note">{t.sidePane.textTruncated}</div>}
          </>}
          {!preview && <div className="hb-preview-note" role="status">{t.sidePane.loading}</div>}
          {preview?.kind === 'unsupported' && <div className="hb-preview-note">{t.sidePane.unsupported}</div>}
          {preview?.kind === 'tooLarge' && <div className="hb-preview-note">{t.sidePane.tooLarge}</div>}
          {(preview?.kind === 'failed' || imageError) && <div className="hb-preview-note" role="alert">{t.sidePane.previewFailed}</div>}
        </div>
      )}
    </>
  )
}
