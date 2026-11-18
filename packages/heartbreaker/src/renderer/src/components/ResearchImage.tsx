// SPDX-License-Identifier: AGPL-3.0-or-later

import { useContext, useEffect, useState } from 'react'
import type { Components } from 'react-markdown'
import { useT } from '../lib/i18n'
import type { AppConfig } from '../lib/types'
import { loadBoardImage } from '../lib/voice'
import { ChatContext } from '../store/chat'

function publicUrl(value: string): string | null {
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null
  } catch {
    return null
  }
}

interface ImageResult {
  config: AppConfig
  url: string
  blob: string | null
}

export function ImagePreview({ src = '', alt = '', height, fit = 'contain', showStatus = false, describeFailure = true }: {
  src?: string
  alt?: string
  height?: string | number
  fit?: 'contain' | 'cover'
  showStatus?: boolean
  describeFailure?: boolean
}) {
  const config = useContext(ChatContext)?.state.config ?? null
  const t = useT().researchImage
  const url = publicUrl(src)
  const [result, setResult] = useState<ImageResult>()
  const blob = result && result.url === url && result.config === config ? result.blob : undefined

  useEffect(() => {
    if (!config || !url) return
    const controller = new AbortController()
    let objectUrl: string | null = null
    void loadBoardImage(config, url, controller.signal).then(loaded => {
      if (controller.signal.aborted) {
        if (loaded) URL.revokeObjectURL(loaded)
        return
      }
      objectUrl = loaded
      setResult({ config, url, blob: loaded })
    })
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [config, url])

  if (!blob) {
    if (!showStatus) return null
    const failed = !config || !url || blob === null
    return (
      <span role="status" style={{ display: 'inline-block', color: 'var(--hb-text-dim)', fontSize: '0.85em' }}>
        {failed ? t.unavailable : t.loading}{failed && describeFailure && alt ? ` · ${alt}` : ''}
      </span>
    )
  }

  return (
    <img src={blob} alt={alt} decoding="async"
      onError={() => { if (config && url) setResult({ config, url, blob: null }) }}
      style={{
        display: 'block', width: '100%', height: height ?? 'auto',
        maxHeight: height ?? 480, objectFit: fit,
        border: '1px solid var(--hb-line)', borderRadius: 6, background: 'var(--hb-petrol)',
      }}
    />
  )
}

export function MarkdownImage({ src, alt }: { src?: string; alt?: string }) {
  return <ImagePreview src={src} alt={alt} showStatus />
}

export const MarkdownImageLink: NonNullable<Components['a']> = ({ node, href, children, ...props }) => {
  const linkedImage = node?.children.some(child => child.type === 'element' && child.tagName === 'img')
  const external = linkedImage && href && publicUrl(href)
  return <a {...props} href={external || href} target={external ? '_blank' : props.target}
    rel={external ? 'noreferrer' : props.rel}>{children}</a>
}

export function ResearchPicture({ src, flow, title }: { src: string; flow?: boolean; title?: string }) {
  const t = useT().researchImage
  const [image = '', ...lines] = src.split('\n').map(line => line.trim()).filter(Boolean)
  const sourceLine = /^Source:\s*(.*)$/i.exec(lines.at(-1) ?? '')
  const source = sourceLine && publicUrl(sourceLine[1])
  const caption = (source ? lines.slice(0, -1) : lines).join(' ')
  return (
    <figure style={{ margin: 0, height: flow ? 'auto' : '100%', display: 'flex', flexDirection: 'column', gap: '0.45rem' }}>
      {flow && title && <strong>{title}</strong>}
      <div style={{ flex: '1 1 auto', minHeight: 0 }}>
        <ImagePreview src={image} alt={caption || title || t.image} height={flow ? undefined : '100%'} showStatus describeFailure={false} />
      </div>
      {(caption || source) && (
        <figcaption style={{ fontSize: '0.8rem', color: 'var(--hb-text-dim)', lineHeight: 1.45, overflowWrap: 'anywhere' }}>
          {caption}
          {source && <div><a href={source} target="_blank" rel="noreferrer">{t.openSource}</a></div>}
        </figcaption>
      )}
    </figure>
  )
}
