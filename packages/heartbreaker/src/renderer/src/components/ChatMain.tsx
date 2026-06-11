import { useCallback, useEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { streamChat, fetchSessions } from '../lib/api'
import { useProfile } from './Sidebar'
import MessageList from './MessageList'
import InputBar from './InputBar'
import type { AppConfig, ImageBlock } from '../lib/types'

function makeId() {
  return Math.random().toString(36).slice(2, 10)
}

const PROMPT_ICONS = [
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1"/><line x1="9" y1="12" x2="15" y2="12"/><line x1="9" y1="16" x2="13" y2="16"/></svg>,
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>,
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>,
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>,
]

function PromptCard({ text, icon, idx, onClick }: { text: string; icon: React.ReactNode; idx: number; onClick: () => void }) {
  const [hover, setHover] = useState(false)
  return (
    <button
      className="hb-holo"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        padding: '0.6rem 0.85rem 0.8rem',
        borderColor: hover ? 'var(--hb-edge-bright)' : 'var(--hb-edge)',
        boxShadow: hover ? 'var(--hb-holo-shadow-active)' : undefined,
        color: hover ? '#d7e7ee' : '#9bb5bf',
        fontSize: '0.84rem', lineHeight: 1.55,
        fontFamily: "'SamsungOne','Inter',sans-serif",
        textAlign: 'left', cursor: 'pointer',
        transition: 'border-color 0.15s, color 0.15s, box-shadow 0.15s, transform 0.15s',
        transform: hover ? 'translateY(-1px)' : 'none',
        display: 'flex', flexDirection: 'column', gap: '0.45rem',
        width: '100%', height: '100%',
      }}
    >
      {/* File-card index row — "F_06.8/2.3" microcopy */}
      <span style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        fontFamily: "'Share Tech Mono', monospace", fontSize: '0.58rem',
        letterSpacing: '0.12em',
        color: hover ? 'var(--hb-amber-bright)' : 'var(--hb-amber)',
        transition: 'color 0.15s',
      }}>
        F_0{idx + 1}.{idx + 2}/2.{idx + 1}
        <span style={{ color: hover ? '#5fcce6' : '#3a6472', transition: 'color 0.15s' }}>{icon}</span>
      </span>
      <span>{text}</span>
    </button>
  )
}

function WelcomeView({ onSend }: { onSend: (msg: string) => void }) {
  const profile = useProfile()
  const { settings } = useSettings()
  const hour = new Date().getHours()
  const salutation = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
  // Settings value takes priority; fall back to profile file default
  const displayName = settings.userName.trim() || profile?.userName || ''
  const fullGreeting = (displayName ? `${salutation}, ${displayName}` : salutation).toUpperCase()

  // Typewriter over the full greeting string
  const [typed, setTyped] = useState('')
  const [done, setDone] = useState(false)

  useEffect(() => {
    setTyped('')
    setDone(false)
    let i = 0
    const id = setInterval(() => {
      i++
      setTyped(fullGreeting.slice(0, i))
      if (i >= fullGreeting.length) { clearInterval(id); setDone(true) }
    }, 42)
    return () => clearInterval(id)
  }, [fullGreeting])

  // Live clock — the "VIDEO CONFERENCE PENDING IN 3:56:09" thin digits
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  const clock = now.toLocaleTimeString('en-GB', { hour12: false })
  const dateLine = now.toLocaleDateString('en-GB', {
    weekday: 'long', day: '2-digit', month: 'long', year: 'numeric',
  }).toUpperCase()

  return (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center',
      justifyContent: 'center', padding: '0 1.5rem 1rem',
    }}>
      {/* Monitor band — ">>: SEARCHING_ ALL _LIBRARY _ARCHIVES" style */}
      <p style={{
        fontFamily: "'Share Tech Mono', monospace",
        fontSize: '0.62rem', letterSpacing: '0.3em',
        color: 'var(--hb-cyan)',
        marginBottom: '1.1rem',
        animation: 'fadeIn 0.6s ease both',
      }}>
        {'>>:'} ALL _SYSTEMS _NOMINAL
      </p>

      {/* Thin oversized clock */}
      <p className="hb-num-thin" style={{
        fontSize: '4.6rem', color: '#cfe9f2',
        marginBottom: '0.35rem',
        textShadow: '0 0 22px rgba(95,204,230,0.22)',
        animation: 'hbRise 0.7s ease both',
      }}>
        {clock}
      </p>
      <p style={{
        fontFamily: "'Rajdhani', sans-serif",
        fontSize: '0.66rem', fontWeight: 600, letterSpacing: '0.34em',
        color: 'var(--hb-text-faint)',
        marginBottom: '2.2rem',
        animation: 'fadeIn 0.6s 0.15s ease both',
      }}>
        {dateLine}
      </p>

      {/* Greeting typewriter — thin uppercase Stark title */}
      <h1 style={{
        fontFamily: "'Rajdhani', sans-serif",
        fontSize: '1.7rem', fontWeight: 500, color: '#ecf6f9',
        marginBottom: '0.5rem', textAlign: 'center',
        letterSpacing: '0.22em',
        minHeight: '2.3rem',
      }}>
        {typed}
        <span style={{
          display: 'inline-block', width: '0.55em', height: '1.02em',
          background: 'rgba(95,204,230,0.65)', marginLeft: '4px',
          verticalAlign: 'text-bottom',
          opacity: done ? 0 : 1,
          transition: 'opacity 0.5s',
          animation: done ? 'none' : 'blink 0.8s step-end infinite',
        }} />
      </h1>

      <p style={{
        fontFamily: "'SamsungOne','Inter',sans-serif",
        fontSize: '0.92rem', color: 'var(--hb-text-dim)',
        marginBottom: '2.4rem', textAlign: 'center',
        animation: 'fadeSlideIn 0.4s 0.2s ease both',
      }}>
        How can I help you today?
      </p>

      {profile && profile.suggestedPrompts.length > 0 && (
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)',
          gridAutoRows: '1fr',
          gap: '0.5rem', width: '100%', maxWidth: 620, marginBottom: '2rem',
          alignItems: 'stretch',
        }}>
          {profile.suggestedPrompts.map((p, i) => (
            <div key={i} style={{ display: 'flex', animation: `hbRise 0.45s ${0.3 + i * 0.09}s ease both` }}>
              <PromptCard text={p} icon={PROMPT_ICONS[i % PROMPT_ICONS.length]} idx={i} onClick={() => onSend(p)} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

interface Props {
  config: AppConfig
  onSelectSession: (sessionId: number) => Promise<void>
}

export default function ChatMain({ config, onSelectSession }: Props) {
  const { state, dispatch } = useChatContext()
  const { settings } = useSettings()
  const abortRef = useRef<AbortController | null>(null)
  const [, forceUpdate] = useState(0)

  const send = useCallback(async (text: string, images?: ImageBlock[]) => {
    if (state.isStreaming) return

    const displayImages = (images ?? []).map(b => `data:${b.media_type};base64,${b.data}`)
    dispatch({
      type: 'ADD_USER_MESSAGE',
      payload: {
        id: makeId(), role: 'user', content: text, tools: [],
        isStreaming: false, isError: false,
        ...(displayImages.length ? { images: displayImages } : {}),
      },
    })

    const assistantId = makeId()
    dispatch({
      type: 'ADD_ASSISTANT_MESSAGE',
      payload: { id: assistantId, role: 'assistant', content: '', tools: [], isStreaming: true, isError: false },
    })

    const ctrl = new AbortController()
    abortRef.current = ctrl
    forceUpdate(n => n + 1)

    try {
      for await (const event of streamChat(
        text,
        state.activeSessionId,
        config,
        ctrl.signal,
        settings.model,
        settings.systemPrompt || undefined,
        images,
      )) {
        if (event.type === 'chunk') {
          flushSync(() => {
            dispatch({ type: 'APPEND_CHUNK', payload: { id: assistantId, chunk: event.data as string } })
          })
        } else if (event.type === 'tool') {
          dispatch({ type: 'ADD_TOOL', payload: { id: assistantId, tool: event.data as import('../lib/types').ToolBadge } })
        } else if (event.type === 'tool_result') {
          const d = event.data as { id: string; result: string }
          dispatch({ type: 'SET_TOOL_RESULT', payload: { id: assistantId, toolId: d.id, result: d.result } })
        } else if (event.type === 'file') {
          dispatch({ type: 'ADD_FILE', payload: { id: assistantId, file: event.data as import('../lib/types').FileMeta } })
        } else if (event.type === 'done') {
          dispatch({ type: 'FINISH_MESSAGE', payload: { id: assistantId, sessionId: event.session_id } })
          fetchSessions(config).then(s => dispatch({ type: 'SET_SESSIONS', payload: s })).catch(() => {})
          // Poll for the title — generate_title is a background task that finishes
          // a few seconds after the SSE stream ends
          const sid = event.session_id
          let attempts = 0
          const pollTitle = async () => {
            attempts++
            if (attempts > 12) return
            try {
              const sessions = await fetchSessions(config)
              const found = sessions.find(s => s.id === sid)
              if (found?.title) {
                dispatch({ type: 'UPDATE_SESSION_TITLE', payload: { sessionId: sid, title: found.title } })
              } else {
                setTimeout(pollTitle, 1500)
              }
            } catch { /* non-fatal */ }
          }
          setTimeout(pollTitle, 1500)
        } else if (event.type === 'error') {
          dispatch({ type: 'ERROR_MESSAGE', payload: { id: assistantId, error: event.data as string } })
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        dispatch({ type: 'FINISH_MESSAGE', payload: { id: assistantId, sessionId: state.activeSessionId ?? 0 } })
      } else if (err instanceof Error) {
        dispatch({ type: 'ERROR_MESSAGE', payload: { id: assistantId, error: err.message } })
      }
    } finally {
      abortRef.current = null
      forceUpdate(n => n + 1)
    }
  }, [state.activeSessionId, state.isStreaming, config, settings.model, settings.systemPrompt, dispatch])

  const stop = useCallback(() => { abortRef.current?.abort() }, [])

  const handleDelete = useCallback((id: string) => {
    dispatch({ type: 'DELETE_MESSAGE', payload: { id } })
  }, [dispatch])

  const handleRegenerate = useCallback((assistantId: string) => {
    if (state.isStreaming) return
    const idx = state.messages.findIndex(m => m.id === assistantId)
    if (idx <= 0) return
    const userMsg = state.messages[idx - 1]
    if (!userMsg || userMsg.role !== 'user') return
    dispatch({ type: 'DELETE_MESSAGE', payload: { id: assistantId } })
    send(userMsg.content)
  }, [state.messages, state.isStreaming, dispatch, send])

  const handleEditAndResend = useCallback((userId: string, newContent: string) => {
    if (state.isStreaming) return
    dispatch({ type: 'TRUNCATE_FROM', payload: { id: userId } })
    send(newContent)
  }, [state.isStreaming, dispatch, send])

  const isEmpty = state.messages.length === 0

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {isEmpty
        ? <WelcomeView onSend={send} />
        : (
          <MessageList
            onDelete={handleDelete}
            onRegenerate={handleRegenerate}
            onEditAndResend={handleEditAndResend}
          />
        )
      }
      <InputBar onSend={send} onStop={stop} config={config} />
    </div>
  )
}
