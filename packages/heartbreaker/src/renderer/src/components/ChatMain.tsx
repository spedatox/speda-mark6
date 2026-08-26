import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { streamChat, fetchSessions, attachStream, fetchActiveRuns, cancelRun, fetchWelcome, answerAsk } from '../lib/api'
import { useProfile } from './Sidebar'
import MessageList from './MessageList'
import PartyStream from './PartyStream'
import InputBar from './InputBar'
import VoiceMode from './VoiceMode'
import { PermissionPrompt } from './InteractionPrompt'
import AgentMark from './AgentMark'
import { Skeleton } from './Skeleton'
import { useT } from '../lib/i18n'
import { hasMark } from '../lib/agentMarks'
import { VoiceSession, voiceStatus } from '../lib/voice'
import type { MicState } from '../lib/mic'
import type { OrbState } from './VoiceOrb'
import type { AppConfig, ImageBlock, DocBlock, UploadedFile, PendingAsk } from '../lib/types'

function makeId() {
  return Math.random().toString(36).slice(2, 10)
}

/** How often an open session asks the backend whether a turn has started in it
 *  that this client did not send — a background job reporting its result back
 *  into the conversation it was ordered from. `/chat/active` reads an in-memory
 *  dict, so this is a cheap question to keep asking; the interval is the delay
 *  between the answer landing and the owner watching it arrive. */
const WATCH_MS = 4000

/** Rebuild API image blocks from a user bubble's display `data:` URLs, so
 *  retrying a turn the backend never received resends its pictures instead of
 *  quietly dropping them. Attached DOCUMENTS cannot be recovered this way —
 *  state keeps only their chips, never the bytes — so such a retry resends the
 *  text alone, exactly as edit-and-resend already does. */
function imageBlocksFrom(urls?: string[]): ImageBlock[] | undefined {
  if (!urls?.length) return undefined
  const blocks: ImageBlock[] = []
  for (const url of urls) {
    const m = /^data:([^;]+);base64,(.+)$/.exec(url)
    if (m) blocks.push({ media_type: m[1], data: m[2] })
  }
  return blocks.length ? blocks : undefined
}

function WelcomeView({ config }: { onSend: (msg: string) => void; config: AppConfig }) {
  const t = useT()
  const profile = useProfile()
  const { settings } = useSettings()
  const hour = new Date().getHours()
  const salutation = hour < 12 ? t.welcome.goodMorning : hour < 18 ? t.welcome.goodAfternoon : t.welcome.goodEvening
  const displayName = settings.userName.trim() || profile?.userName || ''
  // House Party takeover — the hero speaks protocol, not pleasantries. It
  // states the roster's readiness rather than greeting the owner, because by
  // the time this screen exists the room is already assembled.
  const isWarroom = profile?.agentId === 'warroom'
  // Turkish's dotless ı/İ pair means a locale-blind toUpperCase() mangles
  // "İyi günler" → "IYI GüNLER" — toLocaleUpperCase needs the actual tag.
  const localeTag = settings.locale === 'tr' ? 'tr' : 'en'
  const fullGreeting = (isWarroom
    ? t.welcome.warroomGreeting
    : (displayName ? `${salutation}, ${displayName}` : salutation)
  ).toLocaleUpperCase(localeTag)

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

  // JARVIS remark — a contextual one-liner from the cheapest model, drawn from
  // memory. Fetched async so the greeting above never waits on it; it fades +
  // types in beneath when it arrives. The war-room hero speaks protocol, not
  // pleasantries, so it stays out of there.
  const [remark, setRemark] = useState('')
  const [remarkTyped, setRemarkTyped] = useState('')
  const [remarkLoading, setRemarkLoading] = useState(true)
  useEffect(() => {
    if (!profile?.agentId) return
    setRemarkLoading(true)
    // The war room's line is FIXED. Everywhere else the remark is a
    // memory-aware one-liner from the cheapest model, but here it states how
    // the protocol works, and that must read the same every single time — it
    // is a briefing, not a greeting, and a model-written variant would make
    // the rules of the room look negotiable.
    if (isWarroom) {
      setRemark('All agents will work on one single task under the leadership of Speda.')
      setRemarkLoading(false)
      return
    }
    let alive = true
    fetchWelcome(config, profile.agentId).then(t => {
      if (!alive) return
      setRemark(t)
      setRemarkLoading(false)
    })
    return () => { alive = false }
  }, [config, profile?.agentId, isWarroom])
  useEffect(() => {
    if (!remark) { setRemarkTyped(''); return }
    let i = 0
    const id = setInterval(() => {
      i++
      setRemarkTyped(remark.slice(0, i))
      if (i >= remark.length) clearInterval(id)
    }, 26)
    return () => clearInterval(id)
  }, [remark])

  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  const dateLocale = settings.locale === 'tr' ? 'tr-TR' : 'en-GB'
  const clock = now.toLocaleTimeString(dateLocale, { hour12: false })
  const dateLine = now.toLocaleDateString(dateLocale, {
    weekday: 'long', day: '2-digit', month: 'long', year: 'numeric',
  }).toLocaleUpperCase(localeTag)

  return (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center',
      justifyContent: 'center', padding: '0 1.5rem 1rem', gap: 0,
    }}>
      {/* Clock + date — compact, top of the stack */}
      <p className="hb-num-thin" style={{
        fontSize: 'clamp(1.6rem, 7vw, 4rem)', color: 'var(--hb-text)',
        marginBottom: '0.25rem', whiteSpace: 'nowrap',
        textShadow: '0 0 44px rgba(var(--hb-accent-rgb), 0.22)',
        animation: 'hbRise 0.5s ease both',
      }}>
        {clock}
      </p>
      <p style={{
        fontFamily: "'Rajdhani', sans-serif",
        fontSize: '0.84rem', fontWeight: 600, letterSpacing: '0.32em',
        color: 'var(--hb-text-faint)',
        marginBottom: '1.4rem',
        animation: 'fadeIn 0.4s 0.1s ease both',
      }}>
        {dateLine}
      </p>

      {/* Agent mark — crowns the hero. Only for agents whose art exists; the
          rest fall straight through to the wordmark with no reserved gap. */}
      {profile?.agentId && hasMark(profile.agentId) && (
        <AgentMark
          agentId={profile.agentId}
          size={104}
          title={profile.name}
          style={{
            width: 'clamp(64px, 14vw, 104px)', height: 'auto',
            marginBottom: '1.1rem',
            animation: 'fadeSlideIn 0.5s 0.12s ease both',
          }}
        />
      )}

      {/* Agent name + mark — the hero, biggest element on screen. lang="en"
          because the wordmark is an English proper noun: under the document's
          tr locale, CSS uppercase would render Atomix as ATOMİX. */}
      <div data-brand-text lang="en" style={{
        display: 'flex', alignItems: 'baseline', gap: '0.7rem',
        marginBottom: '0.5rem',
        animation: 'fadeSlideIn 0.5s 0.15s ease both',
      }}>
        {/* The wordmark carries the agent's accent — it IS the brand statement,
            and a white hero reads as generic. (Tried white per the deck; the
            owner called it, and they are right: this screen is the one place
            the colour should be unmissable.) */}
        <span style={{
          fontFamily: "'Rajdhani', sans-serif",
          fontSize: 'clamp(2.4rem, 10vw, 6rem)', fontWeight: 700,
          letterSpacing: '0.14em', textTransform: 'uppercase',
          color: 'var(--hb-cyan)',
          textShadow: '0 0 60px rgba(var(--hb-accent-rgb), 0.32)',
          lineHeight: 1,
        }}>
          {profile?.name}
        </span>
        <span style={{
          fontFamily: "'Rajdhani', sans-serif",
          fontSize: 'clamp(1.2rem, 4.5vw, 2.75rem)', fontWeight: 500,
          letterSpacing: '0.1em', textTransform: 'uppercase',
          color: 'var(--hb-cyan-dim)',
          lineHeight: 1,
        }}>
          {profile?.modelNumber}
        </span>
      </div>

      {/* Domain tagline */}
      <p data-brand-text lang="en" style={{
        fontFamily: "var(--font-mono)",
        fontSize: '0.97rem', letterSpacing: '0.24em', textTransform: 'uppercase',
        color: 'var(--hb-text-faint)',
        marginBottom: '2.75rem',
        animation: 'fadeIn 0.4s 0.25s ease both',
      }}>
        {profile?.tagline}
      </p>

      {/* Greeting typewriter — below the agent identity */}
      <h1 style={{
        fontFamily: "'Rajdhani', sans-serif",
        fontSize: 'clamp(1.3rem, 4.5vw, 2.4rem)', fontWeight: 600, color: 'var(--hb-text)',
        textAlign: 'center', letterSpacing: '0.12em',
        minHeight: '3.1rem',
      }}>
        {typed}
        <span style={{
          display: 'inline-block', width: '0.5em', height: '0.95em',
          background: 'var(--hb-cyan)', marginLeft: '5px',
          verticalAlign: 'text-bottom',
          boxShadow: '0 0 8px var(--hb-cyan)',
          opacity: done ? 0 : 1,
          transition: 'opacity 0.5s',
          animation: done ? 'none' : 'blink 0.8s step-end infinite',
        }} />
      </h1>

      {/* JARVIS remark — the contextual, memory-aware line under the greeting.
          A skeleton line holds its place while it's fetched (agent switches
          re-trigger this) and collapses away if it resolves empty. */}
      {remarkLoading ? (
        <div style={{ marginTop: '0.9rem', width: 'min(360px, 70vw)' }}>
          <Skeleton height={15} width="100%" />
        </div>
      ) : remarkTyped && (
        <p style={{
          // Reads as a sentence, not a readout: the remark is the one piece of
          // prose on this screen, so it drops the HUD letter-spacing.
          fontFamily: 'var(--font-read)',
          fontSize: 'clamp(0.95rem, 2.6vw, 1.2rem)', fontWeight: 400,
          letterSpacing: '0.02em', color: 'var(--hb-text-dim)',
          textAlign: 'center', maxWidth: 680, marginTop: '0.9rem',
          lineHeight: 1.5, animation: 'fadeIn 0.5s ease both',
        }}>
          {remarkTyped}
        </p>
      )}
    </div>
  )
}

/** Chat history arriving for a session with no local cache — a few bubble-shaped
 *  placeholders in the same column MessageList uses, so switching sessions never
 *  flashes the "new chat" welcome screen for a conversation that already has one. */
function HistorySkeleton() {
  const rows = [
    { side: 'user' as const, width: '38%' },
    { side: 'assistant' as const, width: '72%' },
    { side: 'assistant' as const, width: '54%' },
    { side: 'user' as const, width: '30%' },
  ]
  return (
    <div style={{ flex: 1, overflow: 'hidden', padding: '1.5rem 1rem 0.5rem' }}>
      <div className="hb-skeleton-group" style={{ maxWidth: 760, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 22 }}>
        {rows.map((r, i) => (
          <div key={i} style={{
            display: 'flex', justifyContent: r.side === 'user' ? 'flex-end' : 'flex-start',
            ['--hb-skeleton-delay' as string]: `${i * 0.09}s`,
          }}>
            <Skeleton width={r.width} height={r.side === 'user' ? 34 : 58} radius={14} />
          </div>
        ))}
      </div>
    </div>
  )
}

interface Props {
  config: AppConfig
  /** House Party is ENGAGED — the transcript becomes the group room. */
  partyEngaged?: boolean
  onSelectSession: (sessionId: number) => Promise<void>
  /** Voice mode replaces the transcript with the orb; the composer stays. */
  voiceOpen?: boolean
  onCloseVoice?: () => void
  /** Layout set this while a just-selected session's history is in flight and
   *  no local cache existed to show meanwhile — see handleSelectSession. */
  historyLoading?: boolean
}

// `onSelectSession` stays in Props — Layout passes it — but nothing in here
// reads it any more; session selection moved to the sidebar's own handler.
export default function ChatMain({ config, voiceOpen, onCloseVoice, partyEngaged, historyLoading }: Props) {
  const t = useT()
  const { state, dispatch } = useChatContext()
  const { settings, update } = useSettings()
  const profile = useProfile()
  // One card at a time: the peer parks the ask inside a single tool dispatch,
  // so a second gated action cannot be raised until this one is answered.
  const [pendingAsk, setPendingAsk] = useState<PendingAsk | null>(null)

  /* ── Voice mode ────────────────────────────────────────────────────────── */
  // The session owns an AudioContext, so it is created per TURN (on the click
  // or keystroke that starts one) and torn down when speech ends. Keeping one
  // alive across an idle mode would hold an audio device open for nothing.
  const voiceRef = useRef<VoiceSession | null>(null)
  // Which assistant bubble the live VoiceSession is speaking for. Speech is
  // per-turn, so it has to end when that turn leaves the view — see the effect
  // below.
  const spokenIdRef = useRef<string | null>(null)
  const [orbState, setOrbState] = useState<OrbState>('idle')
  const [voiceReady, setVoiceReady] = useState(true)
  // Mic state is tracked separately from the orb's, not folded into it: the two
  // are genuinely concurrent during barge-in, where the agent is still speaking
  // at the instant the owner starts. Collapsing them into one enum would make
  // that moment unrepresentable.
  const [micState, setMicState] = useState<MicState>('off')

  // Read the live locale inside the stream loop without making `send` depend on
  // it — otherwise changing TR/EN mid-turn would rebuild the callback.
  const localeRef = useRef(settings.voiceLocale)
  localeRef.current = settings.voiceLocale
  const voiceModelRef = useRef(settings.voiceModel)
  voiceModelRef.current = settings.voiceModel
  const voiceOpenRef = useRef(!!voiceOpen)
  voiceOpenRef.current = !!voiceOpen

  // Ask once per mode entry whether the backend can actually speak, so an
  // unconfigured key surfaces as a message instead of permanent silence.
  useEffect(() => {
    if (!voiceOpen) return
    let alive = true
    voiceStatus(config).then(ok => { if (alive) setVoiceReady(ok) })
    return () => { alive = false }
  }, [voiceOpen, config])

  const stopSpeaking = useCallback(() => {
    voiceRef.current?.stop()
    voiceRef.current = null
    spokenIdRef.current = null
    setOrbState('idle')
  }, [])

  // A turn's speech belongs to that turn's bubble. When the bubble leaves the
  // transcript — the owner selected another session, or switched agent (which
  // clears the chat) — the answer being spoken is no longer the answer on
  // screen, so cut it. Keyed on the message rather than on the session id
  // because the id is not stable: a fresh chat ADOPTS one when its first turn
  // finishes, and treating that as a switch would silence the reply mid-word.
  useEffect(() => {
    const id = spokenIdRef.current
    if (!id) return
    if (!state.messages.some(m => m.id === id)) stopSpeaking()
  }, [state.messages, stopSpeaking])

  // Stable identity: the orb polls these every frame, so a new function each
  // render would restart its animation loop sixty times a second.
  const voiceAmplitude = useCallback(() => voiceRef.current?.amplitude() ?? 0, [])
  // The owner's own level, so the orb answers to both voices. Routed through a
  // ref the composer fills, because the mic device is owned by the mic button.
  const micLevelRef = useRef<(() => number) | null>(null)
  const voiceInputLevel = useCallback(() => micLevelRef.current?.() ?? 0, [])
  const voiceSpectrum = useCallback((out: Float32Array) => {
    if (voiceRef.current) voiceRef.current.spectrum(out)
    else out.fill(0)
  }, [])

  // Leaving the mode (or unmounting) must silence it — audio outliving its UI
  // is the single worst failure this feature can have.
  useEffect(() => {
    if (!voiceOpen) stopSpeaking()
  }, [voiceOpen, stopSpeaking])
  useEffect(() => () => { voiceRef.current?.stop() }, [])

  const abortRef = useRef<AbortController | null>(null)
  // request_id of the turn currently streaming into the visible session — the
  // stop button cancels THIS run on the backend (dropping the socket no longer
  // does, by design). Set from the `start` event, cleared on terminal.
  const runIdRef = useRef<string | null>(null)
  // request_ids we've already attached/handled, so the re-attach effect never
  // double-attaches to the same live run. Entries are removed when an attach
  // ends WITHOUT a terminal (we left the session) so a later return re-attaches.
  const attachedRef = useRef<Set<string>>(new Set())
  // Which session the in-flight LOCAL send belongs to (from the start event).
  // Turns are per-session but these refs are singletons — this is how the
  // switch-abort effect and the reattach guard tell "ours" from "elsewhere".
  const turnSessionRef = useRef<number | null>(null)
  const [, forceUpdate] = useState(0)

  // Fold a finished turn's token spend into the header readout. The DONE event
  // carries a delta (the backend persists the totals just after emitting it),
  // so this adds rather than replaces. Older backends send `{}` — nothing to do.
  const applyTurnUsage = useCallback((data: unknown) => {
    const usage = (data as { usage?: { input?: number; output?: number } } | null)?.usage
    if (!usage) return
    dispatch({
      type: 'ADD_TOKEN_USAGE',
      payload: { input: usage.input ?? 0, output: usage.output ?? 0 },
    })
  }, [dispatch])

  interface SendOpts {
    images?: ImageBlock[]
    documents?: DocBlock[]  // non-image files — backend extracts their text
    uploads?: UploadedFile[]  // display chips for the attached documents
    keepMessages?: number   // regenerate/edit: keep the first N stored messages
    regenerate?: boolean    // re-run without adding a new user message
  }

  // Always-current mirrors of state and send, so the row action handlers
  // (delete/regenerate/edit) can be given STABLE identities — they read the
  // latest values through these refs instead of closing over `state`/`send`,
  // which change every streamed chunk. Stable handlers are what keep the
  // memoized message rows from re-rendering during streaming.
  const stateRef = useRef(state)
  stateRef.current = state
  const sendRef = useRef<((text: string, opts?: SendOpts) => Promise<void>) | null>(null)

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
      payload: { id: assistantId, role: 'assistant', content: '', tools: [], isStreaming: true, isError: false, status: t.chatMain.statusConnecting },
    })

    const ctrl = new AbortController()
    abortRef.current = ctrl
    forceUpdate(n => n + 1)

    // ── Voice: open a session for THIS turn ──────────────────────────────────
    // Constructed here because a send is a user gesture, which is what lets an
    // AudioContext start; built lazily so entering the mode costs nothing until
    // something is actually said.
    if (voiceOpenRef.current) {
      voiceRef.current?.stop()          // a new turn cuts the previous answer
      const vs = new VoiceSession(config, {
        agentId: config.agentId,
        locale: localeRef.current,
        voice: voiceModelRef.current,
      })
      vs.onState = setOrbState
      voiceRef.current = vs
      spokenIdRef.current = assistantId
      void vs.resume()
      setOrbState('thinking')
    }

    // ── Chunk coalescing ─────────────────────────────────────────────────────
    // Anthropic streams many small text deltas per second. Dispatching each one
    // re-runs the reducer over the whole message list and re-renders every
    // context consumer — the dominant streaming cost. Instead we accumulate
    // deltas in a buffer and flush at most once per animation frame (~60/s cap,
    // usually far fewer), collapsing N dispatches into one. This is invisible to
    // the user: the per-message typewriter rAF still interpolates the reveal
    // character-by-character from whatever content has landed.
    let chunkBuf = ''
    let flushHandle: number | null = null
    // Running count of characters actually dispatched into `content` so far —
    // stamped onto each tool as afterChars (see types.ts) so Message.tsx can
    // interleave tools at the point they really fired instead of stacking them
    // all before the text. Tracked here, not read back from React state, so a
    // stale closure can never desync it from what's about to be flushed.
    let charsSoFar = 0
    const flushChunks = () => {
      flushHandle = null
      if (!chunkBuf) return
      const chunk = chunkBuf
      chunkBuf = ''
      charsSoFar += chunk.length
      dispatch({ type: 'APPEND_CHUNK', payload: { id: assistantId, chunk } })
    }
    const finalizeFlush = () => {
      if (flushHandle != null) { cancelAnimationFrame(flushHandle); flushHandle = null }
      flushChunks()
    }

    // ── Watchdog ────────────────────────────────────────────────────────────
    // Real status, not looped filler — and a hard stop if the backend goes
    // quiet. We track the last activity instant; the ticker escalates the
    // status line and finally aborts so the UI never spins forever.
    const STALL_MS = 15000    // no events this long → tell the user it's slow
    const DEAD_MS = 300000    // no events this long → give up, surface a precise reason
    const startedAt = Date.now()
    let lastActivity = startedAt
    let gotStart = false     // backend acknowledged the request (START event)
    let gotContent = false
    let gotTool = false
    let timedOut = false
    let timeoutReason = ''   // filled at abort so the error says WHY, not filler
    let settled = false  // did we emit a terminal (done/error/abort) for this message?

    // Which model the turn is running on — surfaced in the stall/timeout copy so
    // the message names the actual thing that went quiet (e.g. GLM-5.2).
    const modelName = settings.model ? (settings.model.split(':').pop() || settings.model).toUpperCase() : t.chatMain.modelFallback

    const watchdog = setInterval(() => {
      const idle = Date.now() - lastActivity
      if (gotContent) return  // tokens are flowing — the cursor is the status now
      if (idle >= DEAD_MS) {
        timedOut = true
        const waited = Math.round((Date.now() - startedAt) / 1000)
        // Name the phase it died in — a diagnostic, not "isn't responding".
        if (!gotStart) {
          timeoutReason = t.chatMain.timeoutNoAck(waited)
        } else if (gotTool) {
          timeoutReason = t.chatMain.timeoutToolStuck(waited)
        } else {
          timeoutReason = t.chatMain.timeoutNoStream(modelName, waited)
        }
        ctrl.abort()
      } else if (idle >= STALL_MS && !gotTool) {
        const waited = Math.round((Date.now() - startedAt) / 1000)
        dispatch({ type: 'SET_STATUS', payload: { id: assistantId, status: t.chatMain.waitingOnModel(modelName, waited) } })
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
          // Forge workspace for Optimus jobs; ignored by in-process agents.
          cwd: config.agentId === 'optimus' ? (settings.forgeCwd || undefined) : undefined,
          // Voice mode is a property of the TURN, not the session: the backend
          // asks for a spoken answer with its visuals fenced off, instead of a
          // document that then gets read aloud at the owner.
          voice: voiceOpenRef.current,
        },
      )) {
        lastActivity = Date.now()
        if (event.type === 'start') {
          gotStart = true
          runIdRef.current = event.request_id ?? null
          // Claim it before the session watcher can see it on /chat/active: this
          // turn is already streaming into a bubble, and an attach would paint a
          // second one for the same answer.
          if (event.request_id) attachedRef.current.add(event.request_id)
          // Every SSE event carries session_id — tag this turn (and its bubble)
          // with the session it belongs to, so switching views can tell whether
          // the in-flight stream is ours and SELECT_SESSION can preserve it.
          if (typeof event.session_id === 'number') {
            turnSessionRef.current = event.session_id
            dispatch({ type: 'TAG_MESSAGE_SESSION', payload: { id: assistantId, sessionId: event.session_id } })
          }
          dispatch({ type: 'SET_STATUS', payload: { id: assistantId, status: t.chatMain.statusThinking } })
        } else if (event.type === 'chunk') {
          gotContent = true
          const delta = event.data as string
          chunkBuf += delta
          // Feed speech from the RAW delta, not the coalesced buffer: synthesis
          // is gated on sentence boundaries, so it must see every character in
          // order, independent of how the UI batches its repaints. What the orb
          // screen SHOWS comes from the store like everything else — see the
          // voice-surface memos below.
          voiceRef.current?.feed(delta)
          if (flushHandle == null) flushHandle = requestAnimationFrame(flushChunks)
        } else if (event.type === 'tool') {
          gotTool = true
          // Flush any buffered-but-undispatched text FIRST so charsSoFar reflects
          // everything the owner actually saw before this tool fired — otherwise
          // the tool would appear to fire slightly too early (before text still
          // sitting in the rAF-coalesced buffer).
          finalizeFlush()
          const tool = { ...(event.data as import('../lib/types').ToolBadge), afterChars: charsSoFar }
          dispatch({ type: 'ADD_TOOL', payload: { id: assistantId, tool } })
        } else if (event.type === 'tool_result') {
          const d = event.data as { id: string; result: string }
          dispatch({ type: 'SET_TOOL_RESULT', payload: { id: assistantId, toolId: d.id, result: d.result } })
        } else if (event.type === 'subagent') {
          // A coding peer delegated part of this turn. It goes to its own panel,
          // never into `content`: a delegate's report is not the answer, and its
          // completion is not the turn's.
          dispatch({ type: 'SUBAGENT', payload: { id: assistantId, event: event.data as Record<string, unknown> } })
        } else if (event.type === 'permission_request') {
          // A peer's gate stopped an irreversible operation. The card replaces
          // nothing and blocks nothing — the peer is already counting down and
          // will deny on its own if the owner never answers.
          setPendingAsk(event.data as PendingAsk)
        } else if (event.type === 'house_party_auth') {
          // The backend is asking the owner to authorize House Party. This is a
          // real event, not a marker in the text — Layout opens the passphrase
          // window off it, so the transcript never carries the ask (or a fence).
          const d = (event.data ?? {}) as { objective?: string }
          window.dispatchEvent(new CustomEvent('speda:hpp-authorize', { detail: { objective: d.objective } }))
        } else if (event.type === 'lockdown_auth') {
          // Same shape for containment: the agent asked, the app takes the
          // passphrase — never the transcript.
          const d = (event.data ?? {}) as { reason?: string }
          window.dispatchEvent(new CustomEvent('speda:lockdown-authorize', { detail: { reason: d.reason } }))
        } else if (event.type === 'skyfall_arm') {
          // Speda armed a launch project. The countdown belongs to the app, not
          // to the transcript — the abort has to be a button the owner can hit,
          // and a line of text is not one.
          window.dispatchEvent(new CustomEvent('speda:skyfall-arm', { detail: event.data }))
        } else if (event.type === 'file') {
          dispatch({ type: 'ADD_FILE', payload: { id: assistantId, file: event.data as import('../lib/types').FileMeta } })
        } else if (event.type === 'done') {
          finalizeFlush()  // drain any buffered text before finalizing
          // Speak the trailing fragment — a reply often ends without terminal
          // punctuation, and that last clause would otherwise never be said.
          voiceRef.current?.finish()
          settled = true
          applyTurnUsage(event.data)
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
          finalizeFlush()
          // Cut speech dead rather than finish(): the turn failed, so whatever
          // is still queued belongs to an answer that is not coming.
          voiceRef.current?.stop()
          voiceRef.current = null
          spokenIdRef.current = null
          setOrbState('idle')
          settled = true
          dispatch({ type: 'ERROR_MESSAGE', payload: { id: assistantId, error: event.data as string } })
        }
      }
      // Stream ended. If the backend closed it without a terminal event (e.g. it
      // crashed mid-turn), finalize anyway so the message never stays stuck
      // "thinking" with no way out. Keep whatever text streamed.
      if (!settled) {
        finalizeFlush()
        // Text did stream, so speak the remainder rather than discarding it —
        // an abrupt backend close should not swallow the last clause.
        voiceRef.current?.finish()
        settled = true
        dispatch({ type: 'FINISH_MESSAGE', payload: { id: assistantId, sessionId: state.activeSessionId ?? 0 } })
      }
    } catch (err: unknown) {
      finalizeFlush()  // keep whatever text streamed before the failure/abort
      settled = true
      if (timedOut) {
        // Precise, phase-specific reason built by the watchdog — never filler.
        dispatch({ type: 'ERROR_MESSAGE', payload: { id: assistantId, error: timeoutReason || t.chatMain.timedOutFallback, unsent: !gotStart } })
      } else if (err instanceof Error && err.name === 'AbortError') {
        // User-initiated stop — keep whatever streamed so far.
        dispatch({ type: 'FINISH_MESSAGE', payload: { id: assistantId, sessionId: state.activeSessionId ?? 0 } })
      } else if (err instanceof Error) {
        // Network failures throw a bare TypeError ("Failed to fetch") — name it
        // as unreachable-backend rather than dumping the opaque string.
        const net = /failed to fetch|networkerror|load failed|err_connection/i.test(err.message)
        dispatch({ type: 'ERROR_MESSAGE', payload: { id: assistantId,
          error: net
            ? t.chatMain.networkError
            : err.message,
          // No START event means the turn never landed: whatever the reason, the
          // prompt was not stored and Try again has to send it again.
          unsent: !gotStart } })
      }
    } finally {
      finalizeFlush()  // safety: never leave buffered text undelivered
      clearInterval(watchdog)
      // Only clear the refs if this turn still owns them — the switch-abort
      // effect (or a newer send) may have taken over; a stale finally must
      // never null out a live turn's handles (that broke the Stop button).
      if (abortRef.current === ctrl) {
        abortRef.current = null
        runIdRef.current = null
        turnSessionRef.current = null
      }
      forceUpdate(n => n + 1)
    }
  }, [state.activeSessionId, state.isStreaming, config, settings.model, settings.systemPrompt, settings.forgeCwd, dispatch, t])

  // Mirror the latest `send` into a ref so the stable row handlers below can call
  // it without listing it as a dependency (which would make them change identity
  // every chunk and defeat the memoized message rows).
  sendRef.current = send

  // Stop: cancel the detached run on the backend (dropping the socket alone no
  // longer stops it), then abort the local fetch. The backend persists whatever
  // streamed so far, marked as cancelled.
  const stop = useCallback(() => {
    const rid = runIdRef.current
    if (rid) cancelRun(config, rid).catch(() => {})
    abortRef.current?.abort()
  }, [config])

  // ── Barge-in ──────────────────────────────────────────────────────────────
  // The owner started talking over the agent. Interrupting has to cut BOTH the
  // audio and the turn producing it: silencing playback alone leaves the
  // backend generating (and billing for) an answer nobody is listening to any
  // more, and the queued sentences would resume speaking the moment the next
  // clip decoded. This is the one thing "stop speaking" in the pulled build did
  // not do.
  const bargeIn = useCallback(() => {
    if (!voiceRef.current && !state.isStreaming) return
    stopSpeaking()
    if (state.isStreaming) stop()
  }, [state.isStreaming, stopSpeaking, stop])

  // ── Abort on view switch ──────────────────────────────────────────────────
  // Turns are per-session and DETACHED on the backend (dropping the socket
  // never kills a run) — but the local fetch is a singleton. When the visible
  // session (or agent, via NEW_CHAT) changes away from the streaming turn's
  // session, abort the local fetch so the reattach path below becomes the
  // single source of truth for that session's tail. Defined BEFORE the
  // reattach effect so it runs first in the same commit.
  useEffect(() => {
    const sid = state.activeSessionId
    if (
      abortRef.current &&
      turnSessionRef.current !== null &&
      turnSessionRef.current !== sid
    ) {
      abortRef.current.abort()
      abortRef.current = null
      runIdRef.current = null
      turnSessionRef.current = null
    }
  }, [state.activeSessionId, config.agentId])

  // ── Re-attach ─────────────────────────────────────────────────────────────
  // On entering a session, ask the backend whether a turn is still running there
  // (a job we switched away from, or one that survived an app reload). If so,
  // append a streaming bubble and tail its live stream — the run kept going
  // server-side the whole time, so this picks up mid-flight and finishes cleanly.
  //
  // And then KEEP asking, for as long as the session is open. A turn can begin
  // in a conversation the owner is already looking at without them sending
  // anything: a background legionnaire or a dispatched agent finishing reports
  // back INTO the chat the work was ordered from (app/core/trigger_runner.py).
  // Asking once on entry meant those answers appeared only on the next visit to
  // the session — the owner sat in the very conversation the reply was landing
  // in and saw nothing.
  useEffect(() => {
    const sid = state.activeSessionId
    if (sid == null) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    const ctrl = new AbortController()
    const sleep = (ms: number) => new Promise<void>(r => { timer = setTimeout(r, ms) })

    const tail = async (run: { request_id: string }, status: string) => {
      attachedRef.current.add(run.request_id)

      const assistantId = makeId()
      dispatch({ type: 'ADD_ASSISTANT_MESSAGE', payload: {
        id: assistantId, role: 'assistant', content: '', tools: [],
        isStreaming: true, isError: false, status, sessionId: sid,
      } })
      runIdRef.current = run.request_id

      // Coalesce replayed chunks (they arrive in a burst) at one flush per frame.
      let buf = ''
      let handle: number | null = null
      let charsSoFar = 0  // see the live-stream loop above for why this exists
      const flush = () => {
        handle = null
        if (!buf) return
        const c = buf
        buf = ''
        charsSoFar += c.length
        dispatch({ type: 'APPEND_CHUNK', payload: { id: assistantId, chunk: c } })
      }
      let settled = false  // saw a terminal (done/error) for this attach

      try {
        for await (const event of attachStream(config, run.request_id, ctrl.signal)) {
          if (event.type === 'start') {
            // Who started this turn, since it wasn't us. A background job
            // reporting back carries its receipt here; the card it renders is
            // the one a reload rebuilds from the persisted seed.
            const trig = (event.data as { trigger?: import('../lib/types').TriggerMeta } | null)?.trigger
            if (trig) dispatch({ type: 'SET_MESSAGE_TRIGGER', payload: { id: assistantId, trigger: trig } })
          } else if (event.type === 'chunk') {
            buf += event.data as string
            if (handle == null) handle = requestAnimationFrame(flush)
          } else if (event.type === 'tool') {
            if (handle != null) { cancelAnimationFrame(handle); handle = null }
            flush()
            const tool = { ...(event.data as import('../lib/types').ToolBadge), afterChars: charsSoFar }
            dispatch({ type: 'ADD_TOOL', payload: { id: assistantId, tool } })
          } else if (event.type === 'tool_result') {
            const d = event.data as { id: string; result: string }
            dispatch({ type: 'SET_TOOL_RESULT', payload: { id: assistantId, toolId: d.id, result: d.result } })
          } else if (event.type === 'subagent') {
            dispatch({ type: 'SUBAGENT', payload: { id: assistantId, event: event.data as Record<string, unknown> } })
          } else if (event.type === 'permission_request') {
            // A peer's gate stopped an irreversible operation. The card replaces
            // nothing and blocks nothing — the peer is already counting down and
            // will deny on its own if the owner never answers.
            setPendingAsk(event.data as PendingAsk)
          } else if (event.type === 'house_party_auth') {
            const d = (event.data ?? {}) as { objective?: string }
            window.dispatchEvent(new CustomEvent('speda:hpp-authorize', { detail: { objective: d.objective } }))
          } else if (event.type === 'lockdown_auth') {
            const d = (event.data ?? {}) as { reason?: string }
            window.dispatchEvent(new CustomEvent('speda:lockdown-authorize', { detail: { reason: d.reason } }))
          } else if (event.type === 'skyfall_arm') {
            window.dispatchEvent(new CustomEvent('speda:skyfall-arm', { detail: event.data }))
          } else if (event.type === 'file') {
            dispatch({ type: 'ADD_FILE', payload: { id: assistantId, file: event.data as import('../lib/types').FileMeta } })
          } else if (event.type === 'done') {
            if (handle != null) cancelAnimationFrame(handle)
            flush()
            settled = true
            applyTurnUsage(event.data)
            dispatch({ type: 'FINISH_MESSAGE', payload: { id: assistantId, sessionId: event.session_id } })
          } else if (event.type === 'error') {
            if (handle != null) cancelAnimationFrame(handle)
            flush()
            settled = true
            dispatch({ type: 'ERROR_MESSAGE', payload: { id: assistantId, error: event.data as string } })
          }
        }
        // Stream closed without a terminal (run evicted after the grace window,
        // or the backend died) — finalize like the send path does, so the
        // bubble never sticks on "Reconnecting" forever.
        if (!settled && !cancelled) {
          flush()
          settled = true
          dispatch({ type: 'FINISH_MESSAGE', payload: { id: assistantId, sessionId: sid } })
        }
      } catch { /* attach aborted on leaving the session — the run lives on */ }
      finally {
        if (handle != null) cancelAnimationFrame(handle)
        flush()
        if (runIdRef.current === run.request_id) runIdRef.current = null
        // No terminal seen → we left mid-run; forget the request_id so coming
        // back re-attaches (a sticky entry here made the SECOND return refuse).
        if (!settled) attachedRef.current.delete(run.request_id)
      }
    }

    ;(async () => {
      // The first pass is the old behaviour — a run we already knew about,
      // rejoined on entry. Anything found later is a turn that STARTED while
      // the owner was sitting here, which is an arrival, not a reconnection.
      let entering = true
      while (!cancelled) {
        // Never poll over our own send. The local stream is already painting
        // this session's turn, and attaching to it would draw a second bubble
        // for one answer. abortRef is the live signal (any local send, ours or
        // another session's); runIdRef names the turn it belongs to, which the
        // start handler also files in attachedRef the moment it is known.
        if (!abortRef.current) {
          const runs = await fetchActiveRuns(config, sid)
          if (cancelled) return
          const run = runs.find(
            r => !attachedRef.current.has(r.request_id) && r.request_id !== runIdRef.current,
          )
          entering = entering && runs.length > 0
          if (run) {
            await tail(run, entering ? t.chatMain.statusReconnecting : t.chatMain.statusReportingBack)
            entering = false
            continue          // straight back to watching — jobs can land in a row
          }
        }
        entering = false
        await sleep(WATCH_MS)
      }
    })()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      ctrl.abort()
    }
  }, [state.activeSessionId, config, dispatch, t])

  const handleDelete = useCallback((id: string) => {
    dispatch({ type: 'DELETE_MESSAGE', payload: { id } })
  }, [dispatch])

  // Regenerate: keep everything up to and including the user turn, drop the old
  // answer, and re-run on that clean history (keepMessages = the answer's index).
  // The backend truncates its DB rows to match, so the model sees the prompt
  // fresh instead of being handed its previous reply. Reads live state via
  // stateRef so this handler keeps a STABLE identity across chunks.
  const handleRegenerate = useCallback((assistantId: string) => {
    const st = stateRef.current
    if (st.isStreaming) return
    const idx = st.messages.findIndex(m => m.id === assistantId)
    if (idx <= 0) return
    const userMsg = st.messages[idx - 1]
    if (!userMsg || userMsg.role !== 'user') return

    // A turn that died before the backend acknowledged it left NOTHING stored —
    // its prompt exists only in this list. Regenerating would re-run the
    // PREVIOUS exchange and confidently answer the wrong question, so retrying
    // one means resending: drop the local pair and send the prompt afresh.
    if (st.messages[idx].unsent) {
      dispatch({ type: 'TRUNCATE_FROM', payload: { id: userMsg.id } })
      sendRef.current?.(userMsg.content, {
        keepMessages: idx - 1,
        images: imageBlocksFrom(userMsg.images),
      })
      return
    }

    dispatch({ type: 'TRUNCATE_FROM', payload: { id: assistantId } })
    sendRef.current?.('', { keepMessages: idx, regenerate: true })
  }, [dispatch])

  // Edit & resend: drop the old user turn + its answer (keepMessages = the
  // user turn's index), then send the edited prompt as a brand-new turn.
  const handleEditAndResend = useCallback((userId: string, newContent: string) => {
    const st = stateRef.current
    if (st.isStreaming) return
    const idx = st.messages.findIndex(m => m.id === userId)
    if (idx < 0) return
    dispatch({ type: 'TRUNCATE_FROM', payload: { id: userId } })
    sendRef.current?.(newContent, { keepMessages: idx })
  }, [dispatch])

  const isEmpty = state.messages.length === 0

  /* ── The voice surface's content ──────────────────────────────────────────
   * Read out of the chat store, NOT accumulated in local state. The mode used
   * to keep its own copy of the streamed reply, which meant it showed one thing
   * forever: selecting another session (or another agent) swaps `state.messages`
   * but could not touch that local string, so the orb screen kept displaying the
   * previous conversation's answer. Sourcing it here means the mode inherits
   * every transition the transcript already handles — switch, reattach,
   * regenerate, delete — for free. */
  const voiceReply = useMemo(() => {
    for (let i = state.messages.length - 1; i >= 0; i--) {
      if (state.messages[i].role === 'assistant') return state.messages[i]
    }
    return null
  }, [state.messages])
  const voicePrompt = useMemo(() => {
    for (let i = state.messages.length - 1; i >= 0; i--) {
      if (state.messages[i].role === 'user') return state.messages[i].content
    }
    return ''
  }, [state.messages])

  // Answering is fire-and-forget: the decision goes to Igor, Igor relays it to
  // the peer, and the peer resumes or reports the refusal in its own stream.
  // A failed POST means the ask already expired, which the peer has already
  // treated as a denial — so the card just clears either way.
  const resolveAsk = useCallback(async (approved: boolean, remember: boolean) => {
    const ask = pendingAsk
    setPendingAsk(null)
    if (ask) await answerAsk(config, ask.ask_id, approved, remember)
  }, [pendingAsk, config])

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {voiceOpen ? (
        <VoiceMode
          state={orbState}
          amplitude={voiceAmplitude}
          spectrum={voiceSpectrum}
          inputLevel={voiceInputLevel}
          reply={voiceReply?.content ?? ''}
          streaming={!!voiceReply?.isStreaming}
          prompt={voicePrompt}
          locale={settings.voiceLocale}
          onLocale={locale => update({ voiceLocale: locale })}
          onClose={() => onCloseVoice?.()}
          onStopSpeaking={stopSpeaking}
          micState={micState}
          configured={voiceReady}
          agentName={profile?.name ?? 'Speda'}
          dock={settings.voiceOrbDock}
          onDock={d => update({ voiceOrbDock: d })}
        />
      ) : partyEngaged
        // House Party takes over the transcript, not just its colours: while
        // the protocol is live this is a room with the whole roster in it, so
        // the 1:1 list is replaced by the group stream. Everything else — the
        // composer, sending, streaming, re-attach — is unchanged underneath.
        ? <PartyStream config={config} />
        : isEmpty
        ? (historyLoading ? <HistorySkeleton /> : <WelcomeView onSend={send} config={config} />)
        : (
          <MessageList
            onDelete={handleDelete}
            onRegenerate={handleRegenerate}
            onEditAndResend={handleEditAndResend}
          />
        )
      }
      {pendingAsk && <PermissionPrompt ask={pendingAsk} onResolve={resolveAsk} />}
      <InputBar
        onSend={send}
        onStop={stop}
        config={config}
        voiceMode={!!voiceOpen}
        agentSpeaking={orbState === 'speaking'}
        onSpeechStart={bargeIn}
        onMicState={setMicState}
        micLevelRef={micLevelRef}
      />
    </div>
  )
}
