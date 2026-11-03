// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { Children, Fragment, isValidElement, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import type { ButtonHTMLAttributes, KeyboardEvent, ReactNode } from 'react'
import { createPortal } from 'react-dom'
import '../theme/form-select.css'

interface Option {
  value: string
  label: string
  disabled: boolean
  group?: string
}

function textContent(node: ReactNode): string {
  return Children.toArray(node).map(child => isValidElement<{ children?: ReactNode }>(child)
    ? textContent(child.props.children) : String(child)).join('')
}

// Keep declarative option/optgroup data at the call sites; render only themed DOM.
function readOptions(children: ReactNode, group?: string, disabled = false): Option[] {
  return Children.toArray(children).flatMap(child => {
    if (!isValidElement<{ children?: ReactNode; value?: string | number; label?: string; disabled?: boolean }>(child)) return []
    const props = child.props
    if (child.type === Fragment) return readOptions(props.children, group, disabled)
    if (child.type === 'optgroup') return readOptions(props.children, props.label, disabled || !!props.disabled)
    if (child.type !== 'option') return []
    const label = props.label ?? textContent(props.children)
    return [{ value: String(props.value ?? label), label, group, disabled: disabled || !!props.disabled }]
  })
}

interface Props extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'value' | 'onChange' | 'children'> {
  value: string | number
  onValueChange: (value: string) => void
  children: ReactNode
}

/** A single-choice form field whose popup stays readable across OS themes. */
export default function FormSelect({ value, onValueChange, children, disabled, className = '', style, ...props }: Props) {
  const options = readOptions(children)
  const selected = options.findIndex(option => option.value === String(value))
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [position, setPosition] = useState<React.CSSProperties>({})
  const trigger = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const search = useRef({ text: '', time: 0 })
  const listId = useId()
  const enabled = options.map((option, index) => option.disabled ? -1 : index).filter(index => index >= 0)

  function show(index = selected) {
    if (trigger.current?.matches(':disabled') || !enabled.length) return
    const rect = trigger.current?.getBoundingClientRect()
    if (!rect) return
    const below = window.innerHeight - rect.bottom - 12
    const above = rect.top - 12
    const up = below < 220 && above > below
    const width = Math.min(Math.max(rect.width, 200), window.innerWidth - 16)
    setPosition({
      left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)), width,
      maxHeight: Math.max(0, Math.min(320, up ? above : below)),
      ...(up ? { bottom: window.innerHeight - rect.top + 6 } : { top: rect.bottom + 6 }),
    })
    setActive(enabled.includes(index) ? index : enabled[0])
    search.current = { text: '', time: 0 }
    setOpen(true)
  }

  function choose(index: number) {
    const option = options[index]
    if (!option || option.disabled || trigger.current?.matches(':disabled')) return
    onValueChange(option.value)
    setOpen(false)
  }

  useLayoutEffect(() => {
    if (open) document.getElementById(`${listId}-${active}`)?.scrollIntoView({ block: 'nearest' })
  }, [open, active, listId])

  useEffect(() => {
    if (disabled) setOpen(false)
  }, [disabled])

  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => {
      const target = event.target as Node
      if (!trigger.current?.contains(target) && !menu.current?.contains(target)) setOpen(false)
    }
    const dismiss = (event: Event) => {
      if (!(event.target instanceof Node) || !menu.current?.contains(event.target)) setOpen(false)
    }
    // Capture Escape before the parent settings/modal handler can dismiss itself.
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', outside, true)
    window.addEventListener('keydown', escape, true)
    window.addEventListener('resize', dismiss)
    window.addEventListener('scroll', dismiss, true)
    return () => {
      document.removeEventListener('pointerdown', outside, true)
      window.removeEventListener('keydown', escape, true)
      window.removeEventListener('resize', dismiss)
      window.removeEventListener('scroll', dismiss, true)
    }
  }, [open])

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    props.onKeyDown?.(event)
    if (event.defaultPrevented || event.ctrlKey || event.metaKey) return
    const { key } = event
    if (key === 'Tab') { setOpen(false); return }
    if (key === 'ArrowDown' || key === 'ArrowUp' || key === 'Home' || key === 'End') {
      event.preventDefault()
      if (!open) { show(key === 'End' ? enabled[enabled.length - 1] : key === 'Home' ? enabled[0] : selected); return }
      const index = enabled.indexOf(active)
      setActive(key === 'Home' ? enabled[0] : key === 'End' ? enabled[enabled.length - 1]
        : enabled[(index + (key === 'ArrowDown' ? 1 : -1) + enabled.length) % enabled.length])
    } else if (key === 'Enter' || key === ' ') {
      event.preventDefault()
      if (open) choose(active)
      else show()
    } else if (key.length === 1 && !event.altKey) {
      event.preventDefault()
      const now = Date.now()
      const text = (now - search.current.time < 700 ? search.current.text : '') + key.toLocaleLowerCase()
      search.current = { text, time: now }
      const query = [...text].every(char => char === text[0]) ? text[0] : text
      const start = open ? active : selected
      const ordered = [...enabled.filter(index => index > start), ...enabled.filter(index => index <= start)]
      const match = ordered.find(index => options[index].label.trim().toLocaleLowerCase().startsWith(query))
      if (!open) show(match ?? selected)
      else if (match !== undefined) setActive(match)
    }
  }

  return <>
    <button {...props} ref={trigger} type="button" role="combobox" disabled={disabled || !enabled.length}
      className={`hb-form-select ${className}`} style={style}
      aria-expanded={open} aria-haspopup="listbox" aria-controls={open ? listId : undefined}
      aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
      onClick={event => { props.onClick?.(event); if (!event.defaultPrevented) open ? setOpen(false) : show() }}
      onKeyDown={onKeyDown} onBlur={event => { props.onBlur?.(event); setOpen(false) }}>
      <span className="hb-form-select-value">{options[selected]?.label ?? String(value)}</span>
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path d={open ? 'm6 15 6-6 6 6' : 'm6 9 6 6 6-6'} />
      </svg>
    </button>
    {open && createPortal(<div ref={menu} id={listId} role="listbox"
      aria-label={props['aria-label']} aria-labelledby={props['aria-labelledby'] || (!props['aria-label'] ? props.id : undefined)}
      className="hb-form-select-menu" style={position} onMouseDown={event => event.preventDefault()}>
      {options.map((option, index) => <Fragment key={`${option.value}-${index}`}>
        {option.group && option.group !== options[index - 1]?.group &&
          <div role="presentation" className="hb-form-select-group">{option.group}</div>}
        <div id={`${listId}-${index}`} role="option" aria-selected={index === selected} aria-disabled={option.disabled || undefined}
          aria-label={option.group ? `${option.group}: ${option.label}` : undefined}
          className="hb-form-select-option" data-active={index === active || undefined}
          onPointerMove={() => { if (!option.disabled) setActive(index) }} onClick={() => choose(index)}>
          <span>{option.label}</span><span aria-hidden="true" className="hb-form-select-check">{index === selected ? '✓' : ''}</span>
        </div>
      </Fragment>)}
    </div>, document.body)}
  </>
}
