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
    <div style={{ borderRadius: 'var(--radius-card)', overflow: 'hidden', margin: '0.75rem 0', border: '1px solid var(--border)' }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        background: 'var(--bg-code-header)',
        padding: '0.4rem 0.75rem',
        borderBottom: '1px solid var(--border)',
      }}>
        <span style={{
          fontSize: '0.72rem', color: 'var(--text-muted)',
          fontFamily: "'JetBrains Mono','Fira Code',monospace",
          letterSpacing: '0.03em',
        }}>
          {lang}
        </span>
        <button
          onClick={copy}
          style={{
            display: 'flex', alignItems: 'center', gap: '0.3rem',
            fontSize: '0.72rem', color: copied ? '#4ade80' : 'var(--text-secondary)',
            background: 'none', border: 'none', cursor: 'pointer',
            padding: '0.15rem 0.35rem', borderRadius: 'var(--radius-xs)',
            transition: 'color 0.15s',
          }}
          onMouseEnter={e => { if (!copied) (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-primary)' }}
          onMouseLeave={e => { if (!copied) (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)' }}
        >
          {copied
            ? <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12"/></svg>
            : <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
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
          padding: '0.875rem 1rem',
          background: 'var(--bg-code)',
          fontSize: '0.84rem',
          lineHeight: 1.6,
          borderRadius: 0,
        }}
        codeTagProps={{ style: { fontFamily: "'JetBrains Mono','Fira Code',monospace" } }}
      >
        {children}
      </SyntaxHighlighter>
    </div>
  )
}
