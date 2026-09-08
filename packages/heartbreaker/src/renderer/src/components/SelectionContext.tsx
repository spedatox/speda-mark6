// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useState } from 'react'

/** Capture only selections wholly inside one transcript message. */
export function useSelectionContext(scope: string, locale: string, focus: () => void) {
  const [selection, setSelection] = useState('')
  const [quotes, setQuotes] = useState<string[]>([])
  const tr = locale === 'tr'
  useEffect(() => { setQuotes([]); setSelection('') }, [scope])
  useEffect(() => {
    const update = () => {
      const selected = window.getSelection()
      const element = (node: Node | null) => node instanceof Element ? node : node?.parentElement
      const start = element(selected?.anchorNode ?? null)?.closest('[data-reply-message]')
      const end = element(selected?.focusNode ?? null)?.closest('[data-reply-message]')
      setSelection(start && start === end && !element(selected?.anchorNode ?? null)?.closest('textarea, input, [contenteditable="true"]') ? selected?.toString().trim() ?? '' : '')
    }
    document.addEventListener('selectionchange', update)
    return () => document.removeEventListener('selectionchange', update)
  }, [scope])
  const panel = <div style={{ color: 'var(--hb-text)', fontSize: '0.85rem' }}>
    {selection && <button type="button" onMouseDown={e => e.preventDefault()} onClick={() => {
      setQuotes(previous => previous.includes(selection) ? previous : [...previous, selection])
      window.getSelection()?.removeAllRanges()
      setSelection('')
      focus()
    }} style={{ cursor: 'pointer', padding: '0.5rem', color: 'var(--hb-text)', background: 'var(--hb-holo-fill)', border: '1px solid var(--hb-edge)' }}>
      {tr ? 'Seçimi bağlam olarak ekle' : 'Add selection context'}
    </button>}
    {quotes.map((quote, index) => <div key={index} style={{ display: 'flex', gap: '0.5rem', padding: '0.5rem', borderLeft: '2px solid var(--hb-text-dim)' }}>
      <div style={{ flex: 1, minWidth: 0 }}><strong>{tr ? 'Yanıt bağlamı' : 'Reply context'}</strong>
        <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 100, overflowY: 'auto' }}>{quote}</div>
      </div>
      <button type="button" aria-label={tr ? 'Bağlamı kaldır' : 'Remove context'} onClick={() => setQuotes(previous => previous.filter((_, i) => i !== index))}>×</button>
    </div>)}
  </div>
  return {
    panel,
    clear: () => setQuotes([]),
    withContext: (text: string) => quotes.length ? `Reply context (selected chat excerpts):\n${quotes.map(q => q.split('\n').map(line => '> ' + line).join('\n')).join('\n\n')}\n\n${text}` : text,
  }
}
