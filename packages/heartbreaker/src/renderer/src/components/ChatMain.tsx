import { useCallback, useEffect, useRef, useState } from 'react'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { streamChat, fetchSessions } from '../lib/api'
import { useProfile } from './Sidebar'
import MessageList from './MessageList'
import InputBar from './InputBar'
import type { AppConfig, ImageBlock, DocBlock, UploadedFile } from '../lib/types'

function makeId() {
  return Math.random().toString(36).slice(2, 10)
}

function WelcomeView({ onSend }: { onSend: (msg: string) => void }) {
  const profile = useProfile()
  const { settings } = useSettings()
  const hour = new Date().getHours()
  const salutation = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
  const displayName = settings.userName.trim() || profile?.userName || ''
  const fullGreeting = (displayName ? `${salutation}, ${displayName}` : salutation).toUpperCase()

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
      justifyContent: 'center', padding: '0 1.5rem 1rem', gap: 0,
    }}>
      {/* Clock + date — compact, top of the stack */}
      <p className="hb-num-thin" style={{
        fontSize: 'clamp(1.6rem, 7vw, 3.2rem)', color: 'var(--hb-text)',
        marginBottom: '0.15rem', whiteSpace: 'nowrap',
        textShadow: '0 0 30px rgba(var(--hb-accent-rgb), 0.15)',
        animation: 'hbRise 0.5s ease both',
      }}>
        {clock}
      </p>
      <p style={{
        fontFamily: "'Rajdhani', sans-serif",
        fontSize: '0.6rem', fontWeight: 600, letterSpacing: '0.3em',
        color: 'var(--hb-text-faint)',
        marginBottom: '2rem',
        animation: 'fadeIn 0.4s 0.1s ease both',
      }}>
        {dateLine}
      </p>

      {/* Agent name + mark — the hero, biggest element on screen */}
      <div data-brand-text style={{
        display: 'flex', alignItems: 'baseline', gap: '0.7rem',
        marginBottom: '0.5rem',
        animation: 'fadeSlideIn 0.5s 0.15s ease both',
      }}>
        <span style={{
          fontFamily: "'Rajdhani', sans-serif",
          fontSize: 'clamp(2.4rem, 10vw, 5rem)', fontWeight: 800,
          letterSpacing: '0.3em', textTransform: 'uppercase',
          color: 'var(--hb-cyan)',
          textShadow: '0 0 40px rgba(var(--hb-accent-rgb), 0.3)',
          lineHeight: 1,
        }}>
          {profile?.name}
        </span>
        <span style={{
          fontFamily: "'Rajdhani', sans-serif",
          fontSize: 'clamp(1.2rem, 4.5vw, 2.2rem)', fontWeight: 700,
          letterSpacing: '0.24em', textTransform: 'uppercase',
          color: 'var(--hb-cyan-dim)',
          lineHeight: 1,
        }}>
          {profile?.modelNumber}
        </span>
      </div>

      {/* Domain tagline */}
      <p data-brand-text style={{
        fontFamily: "'Share Tech Mono', monospace",
        fontSize: '0.68rem', letterSpacing: '0.22em', textTransform: 'uppercase',
        color: 'var(--hb-text-faint)',
        marginBottom: '2.2rem',
        animation: 'fadeIn 0.4s 0.25s ease both',
      }}>
        {profile?.tagline}
      </p>

      {/* Greeting typewriter — below the agent identity */}
      <h1 style={{
        fontFamily: "'Rajdhani', sans-serif",
        fontSize: 'clamp(1.1rem, 4.5vw, 1.7rem)', fontWeight: 500, color: '#ecf6f9',
        textAlign: 'center', letterSpacing: '0.22em',
        minHeight: '2.3rem',
      }}>
        {typed}
        <span style={{
          display: 'inline-block', width: '0.5em', height: '0.95em',
          background: 'rgba(var(--hb-cyan-bright-rgb),0.55)', marginLeft: '3px',
          verticalAlign: 'text-bottom',
          opacity: done ? 0 : 1,
          transition: 'opacity 0.5s',
          animation: done ? 'none' : 'blink 0.8s step-end infinite',
        }} />
      </h1>
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

  interface SendOpts {
    images?: ImageBlock[]
    documents?: DocBlock[]  // non-image files — backend extracts their text
    uploads?: UploadedFile[]  // display chips for the attached documents
    keepMessages?: number   // regenerate/edit: keep the first N stored messages
    regenerate?: boolean    // re-run without adding a new user message
  }

  const send = useCallback(async (text: string, opts: SendOpts = {}) => {
    if (state.isStreaming) return

    // Regenerate re-runs the existing last user turn — no new user bubble.
    if (!opts.regenerate) {
      const displayImages = (opts.images ?? []).map(b => `data:${b.media_type};base64,${b.data}`)
      dispatch({
        type: 'ADD_USER_MESSAGE',
        payload: {
          id: makeId(), role: 'user', content: text, tools: [],
          isStreaming: false, isError: false,
          ...(displayImages.length ? { images: displayImages } : {}),
          ...(opts.uploads && opts.uploads.length ? { uploads: opts.uploads } : {}),
        },
      })
    }

    const assistantId = makeId()
    dispatch({
      type: 'ADD_ASSISTANT_MESSAGE',
      payload: { id: assistantId, role: 'assistant', content: '', tools: [], isStreaming: true, isError: false, status: 'Connecting' },
    })

    const ctrl = new AbortController()
    abortRef.current = ctrl
    forceUpdate(n => n + 1)

    // ── Watchdog ────────────────────────────────────────────────────────────
    // Real status, not looped filler — and a hard stop if the backend goes
    // quiet. We track the last activity instant; the ticker escalates the
    // status line and finally aborts so the UI never spins forever.
    const STALL_MS = 9000    // no events this long → tell the user it's slow
    const DEAD_MS = 45000    // no events this long → give up, surface an error
    let lastActivity = Date.now()
    let gotContent = false
    let gotTool = false
    let timedOut = false
    let settled = false  // did we emit a terminal (done/error/abort) for this message?

    const watchdog = setInterval(() => {
      const idle = Date.now() - lastActivity
      if (gotContent) return  // tokens are flowing — the cursor is the status now
      if (idle >= DEAD_MS) {
        timedOut = true
        ctrl.abort()
      } else if (idle >= STALL_MS && !gotTool) {
        dispatch({ type: 'SET_STATUS', payload: { id: assistantId, status: 'Still working — the backend is taking longer than usual' } })
      }
    }, 1000)

    try {
      for await (const event of streamChat(
        opts.regenerate ? '' : text,
        state.activeSessionId,
        config,
        ctrl.signal,
        {
          model: settings.model,
          systemPrompt: settings.systemPrompt || undefined,
          images: opts.images,
          documents: opts.documents,
          keepMessages: opts.keepMessages,
          regenerate: opts.regenerate,
        },
      )) {
        lastActivity = Date.now()
        if (event.type === 'start') {
          dispatch({ type: 'SET_STATUS', payload: { id: assistantId, status: 'Thinking' } })
        } else if (event.type === 'chunk') {
          gotContent = true
          dispatch({ type: 'APPEND_CHUNK', payload: { id: assistantId, chunk: event.data as string } })
        } else if (event.type === 'tool') {
          gotTool = true
          dispatch({ type: 'ADD_TOOL', payload: { id: assistantId, tool: event.data as import('../lib/types').ToolBadge } })
        } else if (event.type === 'tool_result') {
          const d = event.data as { id: string; result: string }
          dispatch({ type: 'SET_TOOL_RESULT', payload: { id: assistantId, toolId: d.id, result: d.result } })
        } else if (event.type === 'file') {
          dispatch({ type: 'ADD_FILE', payload: { id: assistantId, file: event.data as import('../lib/types').FileMeta } })
        } else if (event.type === 'done') {
          settled = true
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
          settled = true
          dispatch({ type: 'ERROR_MESSAGE', payload: { id: assistantId, error: event.data as string } })
        }
      }
      // Stream ended. If the backend closed it without a terminal event (e.g. it
      // crashed mid-turn), finalize anyway so the message never stays stuck
      // "thinking" with no way out. Keep whatever text streamed.
      if (!settled) {
        settled = true
        dispatch({ type: 'FINISH_MESSAGE', payload: { id: assistantId, sessionId: state.activeSessionId ?? 0 } })
      }
    } catch (err: unknown) {
      settled = true
      if (timedOut) {
        dispatch({ type: 'ERROR_MESSAGE', payload: { id: assistantId, error: "SPEDA isn't responding. Check that the backend is running, then try again." } })
      } else if (err instanceof Error && err.name === 'AbortError') {
        // User-initiated stop — keep whatever streamed so far.
        dispatch({ type: 'FINISH_MESSAGE', payload: { id: assistantId, sessionId: state.activeSessionId ?? 0 } })
      } else if (err instanceof Error) {
        dispatch({ type: 'ERROR_MESSAGE', payload: { id: assistantId, error: err.message } })
      }
    } finally {
      clearInterval(watchdog)
      abortRef.current = null
      forceUpdate(n => n + 1)
    }
  }, [state.activeSessionId, state.isStreaming, config, settings.model, settings.systemPrompt, dispatch])

  const stop = useCallback(() => { abortRef.current?.abort() }, [])

  const handleDelete = useCallback((id: string) => {
    dispatch({ type: 'DELETE_MESSAGE', payload: { id } })
  }, [dispatch])

  // Regenerate: keep everything up to and including the user turn, drop the old
  // answer, and re-run on that clean history (keepMessages = the answer's index).
  // The backend truncates its DB rows to match, so the model sees the prompt
  // fresh instead of being handed its previous reply.
  const handleRegenerate = useCallback((assistantId: string) => {
    if (state.isStreaming) return
    const idx = state.messages.findIndex(m => m.id === assistantId)
    if (idx <= 0) return
    const userMsg = state.messages[idx - 1]
    if (!userMsg || userMsg.role !== 'user') return
    dispatch({ type: 'TRUNCATE_FROM', payload: { id: assistantId } })
    send('', { keepMessages: idx, regenerate: true })
  }, [state.messages, state.isStreaming, dispatch, send])

  // Edit & resend: drop the old user turn + its answer (keepMessages = the
  // user turn's index), then send the edited prompt as a brand-new turn.
  const handleEditAndResend = useCallback((userId: string, newContent: string) => {
    if (state.isStreaming) return
    const idx = state.messages.findIndex(m => m.id === userId)
    if (idx < 0) return
    dispatch({ type: 'TRUNCATE_FROM', payload: { id: userId } })
    send(newContent, { keepMessages: idx })
  }, [state.messages, state.isStreaming, dispatch, send])

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
