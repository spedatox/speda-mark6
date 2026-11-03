import { forwardRef, useId, useImperativeHandle, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { renderToStaticMarkup } from 'react-dom/server'
import { mentionChoices, mentionAgentName, mentionQueryAt, type MentionQuery } from '../lib/agentMentions'
import { useT } from '../lib/i18n'
import AgentMark from './AgentMark'
import AgentMentionText from './AgentMentionText'

/** Read chips as their transport text, including native contenteditable line breaks. */
function mentionInputText(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return (node.textContent ?? '').replace(/\u00a0/g, ' ')
  if (node instanceof HTMLElement && node.dataset.mentionText) return node.dataset.mentionText
  if (node.nodeName === 'BR') return '\n'
  if (node.childNodes.length === 1 && node.firstChild?.nodeName === 'BR') return ''
  let text = ''
  for (const child of node.childNodes) {
    if (/^(DIV|P)$/.test(child.nodeName) && text && !text.endsWith('\n')) text += '\n'
    text += mentionInputText(child)
  }
  return text
}

function selectionOffsets(editor: HTMLElement): [number, number] | null {
  const selection = window.getSelection()
  if (!selection?.rangeCount) return null
  const range = selection.getRangeAt(0)
  if (!editor.contains(range.startContainer) || !editor.contains(range.endContainer)) return null
  const prefix = range.cloneRange()
  prefix.selectNodeContents(editor)
  prefix.setEnd(range.startContainer, range.startOffset)
  const start = mentionInputText(prefix.cloneContents()).length
  return [start, start + mentionInputText(range.cloneContents()).length]
}

function setCaret(editor: HTMLElement, offset: number) {
  const range = document.createRange()
  let remaining = offset
  const seek = (parent: Node): boolean => {
    for (const child of parent.childNodes) {
      const length = mentionInputText(child).length
      if (remaining <= length) {
        if (child.nodeType === Node.TEXT_NODE) range.setStart(child, remaining)
        else if (child instanceof HTMLElement && child.dataset.mentionText) {
          if (remaining === 0) range.setStartBefore(child)
          else range.setStartAfter(child)
        } else if (seek(child)) return true
        else range.setStartAfter(child)
        return true
      }
      remaining -= length
    }
    return false
  }
  if (!seek(editor)) { range.selectNodeContents(editor); range.collapse(false) }
  range.collapse(true)
  window.getSelection()?.removeAllRanges()
  window.getSelection()?.addRange(range)
}

interface Props {
  value: string
  onChange: (value: string) => void
  members?: string[]
  placeholder: string
  onKeyDown: (event: React.KeyboardEvent<HTMLDivElement>) => void
  onPaste: (event: React.ClipboardEvent<HTMLDivElement>) => void
  onFocus: () => void
  onBlur: () => void
}

const AgentMentionInput = forwardRef<HTMLDivElement, Props>(function AgentMentionInput({
  value, onChange, members, placeholder, onKeyDown, onPaste, onFocus, onBlur,
}, ref) {
  const t = useT().inputBar
  const editorRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const composing = useRef(false)
  const dismissed = useRef('')
  const [query, setQuery] = useState<MentionQuery | null>(null)
  const [active, setActive] = useState(0)
  const [position, setPosition] = useState({ left: 0, bottom: 0, maxHeight: 300 })
  const listId = useId()
  const choices = query ? mentionChoices(query.query, members) : []
  const activeIndex = Math.min(active, Math.max(0, choices.length - 1))
  useImperativeHandle(ref, () => editorRef.current!, [])

  const updateQuery = () => {
    const editor = editorRef.current
    if (!editor || composing.current) return
    const offsets = selectionOffsets(editor)
    const next = offsets ? mentionQueryAt(mentionInputText(editor), ...offsets) : null
    const key = JSON.stringify(next)
    if (key !== JSON.stringify(query)) setActive(0)
    setQuery(key === dismissed.current ? null : next)
  }

  const changed = () => {
    const editor = editorRef.current!
    const text = mentionInputText(editor)
    dismissed.current = ''
    editor.dataset.empty = String(!text)
    onChange(text)
    updateQuery()
  }

  // Native typing owns the DOM so selection, IME and undo survive React renders.
  // Replace its contents only for an external draft update (send, dictation, /bg).
  useLayoutEffect(() => {
    const editor = editorRef.current!
    if (mentionInputText(editor) !== value) {
      dismissed.current = ''
      const caret = document.activeElement === editor ? selectionOffsets(editor)?.[1] : undefined
      editor.innerHTML = renderToStaticMarkup(<AgentMentionText text={value} />)
      if (caret != null) setCaret(editor, Math.min(caret, value.length))
      setQuery(null)
    }
    editor.dataset.empty = String(!value)
  }, [value])

  useLayoutEffect(() => {
    if (!query) return
    const place = () => {
      const rect = editorRef.current!.getBoundingClientRect()
      setPosition({ left: Math.max(8, Math.min(rect.left, window.innerWidth - 280)),
        bottom: window.innerHeight - rect.top + 10, maxHeight: Math.max(80, Math.min(320, rect.top - 16)) })
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => { window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true) }
  }, [query])

  useLayoutEffect(() => {
    menuRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, query?.query])

  const choose = (agentId: string) => {
    const editor = editorRef.current!
    if (!query) return
    const text = mentionInputText(editor)
    const mention = `@${mentionAgentName(agentId)}`
    const hasSpace = text[query.end] === ' '
    editor.focus()
    setCaret(editor, query.start)
    const range = window.getSelection()!.getRangeAt(0)
    const end = query.end
    // The active query is ordinary text. Extend the range to replace its full word.
    setCaret(editor, end)
    const endRange = window.getSelection()!.getRangeAt(0)
    range.setEnd(endRange.startContainer, endRange.startOffset)
    window.getSelection()!.removeAllRanges()
    window.getSelection()!.addRange(range)
    // Browser insertion retains native undo and never accepts authored HTML.
    const markup = renderToStaticMarkup(<AgentMentionText text={mention} />)
    document.execCommand('insertHTML', false, markup + (hasSpace ? '' : '&nbsp;'))
    if (hasSpace) setCaret(editor, query.start + mention.length + 1)
    dismissed.current = ''
    setQuery(null)
    changed()
  }

  return <>
    <div ref={editorRef} className="hb-mention-input" role="textbox" contentEditable suppressContentEditableWarning
      aria-label={placeholder} aria-multiline="true" aria-autocomplete="list" aria-haspopup="listbox"
      aria-expanded={!!query} aria-controls={query ? listId : undefined}
      aria-activedescendant={query && choices.length ? `${listId}-${activeIndex}` : undefined}
      data-placeholder={placeholder} data-empty="true"
      onInput={() => { if (!composing.current) changed() }}
      onCompositionStart={() => { composing.current = true; setQuery(null) }}
      onCompositionEnd={() => { composing.current = false; changed() }}
      onSelect={updateQuery} onKeyUp={updateQuery} onMouseUp={updateQuery}
      onFocus={() => { onFocus(); updateQuery() }}
      onBlur={event => {
        if (menuRef.current?.contains(event.relatedTarget as Node)) return
        setQuery(null); onBlur()
      }}
      onCopy={event => {
        const selection = window.getSelection()
        if (!selection?.rangeCount) return
        event.preventDefault()
        event.clipboardData.setData('text/plain', mentionInputText(selection.getRangeAt(0).cloneContents()))
      }}
      onPaste={event => {
        onPaste(event)
        if (event.defaultPrevented) return
        event.preventDefault()
        document.execCommand('insertText', false, event.clipboardData.getData('text/plain'))
        changed()
      }}
      onKeyDown={event => {
        if (event.nativeEvent.isComposing || composing.current) return
        if (query) {
          if (event.key === 'Escape') { event.preventDefault(); dismissed.current = JSON.stringify(query); setQuery(null); return }
          if (choices.length && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
            event.preventDefault()
            setActive((activeIndex + (event.key === 'ArrowDown' ? 1 : -1) + choices.length) % choices.length)
            return
          }
          if (choices.length && (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey))) {
            event.preventDefault(); choose(choices[activeIndex]); return
          }
        }
        if (event.key === 'Enter' && event.shiftKey) {
          event.preventDefault(); document.execCommand('insertText', false, '\n'); changed(); return
        }
        onKeyDown(event)
      }}
    />
    {query && createPortal(<div ref={menuRef} id={listId} className="hb-mention-menu" role="listbox"
      aria-label={t.mentionAgents} style={position} onMouseDown={event => event.preventDefault()}>
      <div className="hb-mention-menu-title">{t.mentionAgents}</div>
      {choices.map((id, index) => <button key={id} id={`${listId}-${index}`} type="button" role="option" tabIndex={-1}
        aria-selected={index === activeIndex} onMouseEnter={() => setActive(index)} onClick={() => choose(id)}>
        <AgentMark agentId={id} size={24} finish="flat" />
        <strong lang="en">{mentionAgentName(id)}</strong>
      </button>)}
      {!choices.length && <div className="hb-mention-empty">{t.noMentionAgents}</div>}
    </div>, document.body)}
  </>
})

export default AgentMentionInput
