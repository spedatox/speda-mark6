// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState } from 'react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'

interface Props {
  language: string
  children: string
}

export default function CodeBlock({ language, children }: Props) {
  const [copied, setCopied] = useState(false)

  const copy = () => {
    navigator.clipboard.writeText(children).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  const lang = language || 'text'

  return (
    <div className="hb-glass hb-code" style={{
      overflow: 'hidden', margin: '0.75rem 0',
      border: '1px solid var(--hb-edge)',
      background: 'var(--glass-sheen), var(--glass-fill)',
      boxShadow: 'var(--hb-holo-shadow)',
    }}>
      {/* Header. Neutral, like every other panel header on the deck — the old
          accent-washed bar put a coloured stripe on top of every code block in
          a transcript, which is a lot of shouting for a label. */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 16px',
        borderBottom: '1px solid rgba(255,255,255,0.06)',
      }}>
        {/* The language, and nothing else. It used to append a fabricated
            ".PYT document" line — an invented file designation. */}
        <span style={{
          fontFamily: "'Rajdhani', sans-serif", fontSize: '0.8125rem', fontWeight: 600,
          letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--hb-text-dim)',
        }}>
          {lang}
        </span>
        <button
          onClick={copy}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            fontSize: '0.8125rem', color: copied ? 'var(--hb-green)' : 'var(--hb-text-faint)',
            background: 'none', border: 'none', cursor: 'pointer',
            padding: '2px 4px',
            transition: 'color 0.15s',
          }}
          onMouseEnter={e => { if (!copied) (e.currentTarget as HTMLButtonElement).style.color = 'var(--hb-text)' }}
          onMouseLeave={e => { if (!copied) (e.currentTarget as HTMLButtonElement).style.color = 'var(--hb-text-faint)' }}
        >
          {copied
            ? <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"><polyline points="20 6 9 17 4 12"/></svg>
            : <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          }
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>

      {/* Code */}
      <SyntaxHighlighter
        language={lang}
        style={vscDarkPlus}
        customStyle={{
          margin: 0,
          padding: '14px 16px',
          background: 'rgba(0, 0, 0, 0.28)',
          fontSize: '0.875rem',
          lineHeight: 1.65,
          borderRadius: 0,
        }}
        codeTagProps={{ style: { fontFamily: "'JetBrains Mono','Fira Code','SF Mono',Consolas,monospace" } }}
      >
        {children}
      </SyntaxHighlighter>
    </div>
  )
}
