// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Spoken playback for voice mode.
 *
 * The reply arrives as a token stream. There are two ways to turn that into
 * speech, and the difference between them is the difference between an
 * assistant that sounds like a person and one that sounds like a list.
 *
 * ── STREAMING (preferred) ───────────────────────────────────────────────────
 * The text is fed continuously into ElevenLabs' stream-input socket (proxied by
 * the backend, which holds the key) and PCM comes back as it is generated. The
 * engine keeps ONE prosodic context for the whole turn, so a sentence that ends
 * mid-thought gets the rising contour a person would give it instead of a full
 * stop. Samples are scheduled back-to-back on the audio clock, so consecutive
 * chunks splice with no seam at all.
 *
 * ── PER-SENTENCE (fallback) ─────────────────────────────────────────────────
 * Azure and OpenAI synthesize whole utterances only, so for those the stream is
 * cut into sentences and each is converted on its own:
 *   feed()      — accumulates deltas, emits complete sentences
 *   synthesis   — up to MAX_INFLIGHT sentences convert at once
 *   playback    — strictly in order, one at a time
 * Order is preserved even though synthesis is not: sentence 3 finishing before
 * sentence 2 must not let it speak first, so playback awaits each job's promise
 * in sequence rather than racing them. It starts quickly, but every sentence is
 * a standalone utterance with its own terminal contour — audibly a list of
 * sentences rather than a paragraph. That is the cost of the fallback, and the
 * only reason the streaming path is worth a second transport.
 *
 * WHICH ONE a turn gets is decided by the SERVER, on the resolved voice — the
 * client cannot know which engine an agent's profile voice lands on. The
 * session opens the socket optimistically and switches to the per-sentence path
 * if it is refused, so text spoken before the answer arrives is never lost.
 */

import type { AppConfig, ModelInfo } from './types'
import { authHeaders } from './api'

/** Sentences converting at once. Two is enough to keep playback fed without
 *  spending money on audio for a turn the owner is about to interrupt. */
const MAX_INFLIGHT = 2

/* ── Sentence segmentation ────────────────────────────────────────────────── */

/* A period is not a sentence end nearly often enough to matter, and Turkish is
 * the worst case: "3." is an ordinal, so "3. toplantı" is mid-sentence. Guards,
 * in the order they fire:
 *   - a known abbreviation before the dot        ("vb.", "Dr.", "bkz.")
 *   - digits immediately either side             ("3.5", "1.000")
 *   - a bare number before the dot               ("3. madde", "2026. yıl")
 *   - no whitespace after                        (mid-token dot, URLs, files)
 * What remains is a real boundary. */
const ABBREVIATIONS = new Set([
  // Turkish
  'vb', 'vs', 'örn', 'bkz', 'age', 'çev', 'ör', 'hz', 'dr', 'prof', 'doç',
  'av', 'sn', 'yy', 'bl', 'shf', 'tl', 'cad', 'sok', 'apt', 'mah',
  // English / shared
  'mr', 'mrs', 'ms', 'st', 'no', 'tel', 'etc', 'ie', 'eg', 'approx', 'min',
  'max', 'fig', 'vol', 'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug',
  'sep', 'sept', 'oct', 'nov', 'dec',
])

const TERMINATORS = new Set(['.', '!', '?', '…', '\n'])

function isAbbreviationBefore(text: string, dotIndex: number): boolean {
  let i = dotIndex - 1
  let word = ''
  while (i >= 0 && /[\p{L}]/u.test(text[i])) {
    word = text[i] + word
    i--
  }
  return word.length > 0 && ABBREVIATIONS.has(word.toLowerCase())
}

function isNumericBefore(text: string, dotIndex: number): boolean {
  let i = dotIndex - 1
  let seen = false
  while (i >= 0 && /[\d.,]/.test(text[i])) {
    if (/\d/.test(text[i])) seen = true
    i--
  }
  // A bare number ending in a dot is a Turkish ordinal ("3. madde"), not an end.
  return seen && (i < 0 || !/[\p{L}]/u.test(text[i]))
}

/* ── What is speakable ────────────────────────────────────────────────────────
 * An answer in voice mode carries two kinds of content: prose, which is meant to
 * be heard, and ARTEFACTS — a chart's JSON, a LaTeX derivation, a block of HTML —
 * which are meant to be SEEN, on the canvas. Handing an artefact to a speech
 * engine produces exactly what it sounds like: the owner listening to a machine
 * recite `{"type":"line","xKey":"x"` and `\frac{-b \pm \sqrt{b^2-4ac}}{2a}`.
 *
 * So fenced blocks and display math are dropped from the spoken stream entirely.
 * They are not summarised or announced here either — what the model SAYS about
 * its artefacts is the model's job (the backend tells it so in voice mode), and
 * a client-side stand-in would just be a second, worse narrator. */

/** Strip inline math delimiters. `$a = 1$` is worth hearing as "a = 1"; anything
 *  with a TeX command in it is a formula, not a value, and is dropped. */
function inlineMath(line: string): string {
  return line.replace(/\$([^$\n]+)\$/g, (_, body: string) =>
    /\\[a-zA-Z]/.test(body) ? '' : body)
}

/** A row of a pipe table. The table is an artefact — it gets its own window on
 *  the canvas — and a speech engine handed one says "pipe root pipe value pipe". */
function isTableRow(t: string): boolean {
  return t.startsWith('|') && t.endsWith('|') && t.length > 1
}

/**
 * Say the words, not the notation. Even with the backend asking for plain spoken
 * prose, a model will still reach for a heading or a bullet out of habit, and
 * "hash hash Standard Form" is the same failure as reading LaTeX.
 */
function spokenProse(line: string): string {
  return line
    .replace(/^\s{0,3}#{1,6}\s+/, '')          // headings
    .replace(/^\s*[-*+]\s+/, '')               // bullets
    .replace(/^\s*\d+\.\s+/, '')               // numbered items
    .replace(/^\s*>\s?/, '')                   // block quotes
    .replace(/\*\*([^*]+)\*\*/g, '$1')         // bold
    .replace(/(^|\W)\*([^*\n]+)\*/g, '$1$2')   // italics
    .replace(/`([^`\n]+)`/g, '$1')             // inline code
}

/**
 * Split `text` into complete sentences plus the unterminated remainder.
 * The remainder stays buffered until more text arrives or the stream ends —
 * speaking half a sentence is worse than speaking it a moment later.
 */
export function splitSentences(text: string): { sentences: string[]; rest: string } {
  const sentences: string[] = []
  let start = 0

  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (!TERMINATORS.has(ch)) continue

    if (ch === '.') {
      if (/\d/.test(text[i + 1] ?? '')) continue          // 3.5
      if (isAbbreviationBefore(text, i)) continue          // vb.
      if (isNumericBefore(text, i)) continue               // 3. madde
    }

    // Absorb a run of terminators and any closing punctuation ("?!", "…\"").
    let end = i
    while (end + 1 < text.length && (TERMINATORS.has(text[end + 1]) || /["')\]»]/.test(text[end + 1]))) {
      end++
    }

    // A boundary needs whitespace (or the end of the buffer) after it —
    // otherwise it is inside a token: a URL, a filename, a version number.
    const next = text[end + 1]
    if (next !== undefined && !/\s/.test(next)) continue

    const piece = text.slice(start, end + 1).trim()
    if (piece) sentences.push(piece)
    start = end + 1
    i = end
  }

  return { sentences, rest: text.slice(start) }
}

/* ── Synthesis ────────────────────────────────────────────────────────────── */

export async function synthesize(
  config: AppConfig,
  text: string,
  opts: { agentId?: string; voice?: string; locale?: string; signal?: AbortSignal } = {},
): Promise<ArrayBuffer | null> {
  const res = await fetch(`${config.apiBase}/voice/speak`, {
    method: 'POST',
    headers: authHeaders(config, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      text,
      agent_id: opts.agentId ?? config.agentId,
      ...(opts.voice ? { voice: opts.voice } : {}),
      ...(opts.locale ? { locale: opts.locale } : {}),
    }),
    signal: opts.signal,
  })
  // 503 means voice is unconfigured or Azure refused. One silent sentence is a
  // far better failure than a dead turn, so this reports nothing and moves on.
  if (!res.ok) return null
  return res.arrayBuffer()
}

/**
 * The voice catalogue, shaped as ModelInfo so the picker can render it with the
 * same provider grouping and rows it uses for text models — a voice IS a model
 * choice, and giving it a parallel-but-different UI would be gratuitous.
 *
 * Each `id` is a full ref the backend parses (`openai:gpt-4o-mini-tts:nova`,
 * `azure:tr-TR-EmelNeural`), so the client never assembles one itself.
 */
export async function fetchVoices(config: AppConfig): Promise<ModelInfo[]> {
  try {
    const res = await fetch(`${config.apiBase}/voice/voices`, { headers: authHeaders(config) })
    if (!res.ok) return []
    const raw = (await res.json()).voices as Array<Record<string, string>>
    return (raw ?? []).map(v => ({
      id: v.id,
      name: v.display || v.name,
      // Azure names a locale and a gender; OpenAI voices are multilingual and
      // are told apart by which engine they run on instead.
      description: v.provider === 'openai'
        ? v.model
        : [v.locale, v.gender].filter(Boolean).join(' · '),
      provider: v.provider,
    }))
  } catch {
    return []
  }
}

export async function voiceStatus(config: AppConfig): Promise<boolean> {
  try {
    const res = await fetch(`${config.apiBase}/voice/status`, { headers: authHeaders(config) })
    if (!res.ok) return false
    return !!(await res.json()).configured
  } catch {
    return false
  }
}

/* ── Gapless PCM playback ─────────────────────────────────────────────────── */

/**
 * How far ahead of the audio clock the next chunk is scheduled.
 *
 * Scheduling at exactly `currentTime` loses whatever the main thread spends
 * before the audio thread next runs, and that shows up as a click. A lead of a
 * few tens of milliseconds is inaudible as latency and is the whole difference
 * between "continuous" and "nearly continuous".
 */
const PCM_LEAD_S = 0.08

/**
 * Plays a sequence of raw PCM chunks as one continuous sound.
 *
 * The reason this exists rather than `decodeAudioData` per chunk: an MP3 chunk
 * off a stream is a fragment, not a file, and decoding fragments independently
 * gives every one its own encoder padding — the seams the streaming path exists
 * to remove. Raw samples have no framing, so consecutive chunks abut exactly.
 *
 * Each chunk is scheduled at the instant the previous one ends, on the
 * AudioContext's own clock, which is sample-accurate and immune to main-thread
 * jitter. `next` only jumps forward if the network actually starved us.
 */
class PcmPlayer {
  /** Where on the audio clock the next chunk starts. 0 until the first push. */
  private next = 0
  /** A trailing odd byte: a chunk can split a 16-bit sample down the middle. */
  private carry: Uint8Array | null = null
  private live = new Set<AudioBufferSourceNode>()

  constructor(
    private ctx: AudioContext,
    private dest: AudioNode,
    private rate: number,
  ) {}

  /** True once anything has been scheduled and not yet finished playing. */
  get busy(): boolean {
    return this.next > this.ctx.currentTime
  }

  /** Seconds until everything scheduled so far has played out. */
  get remaining(): number {
    return Math.max(0, this.next - this.ctx.currentTime)
  }

  push(bytes: ArrayBuffer): void {
    let raw = new Uint8Array(bytes)
    if (this.carry) {
      const joined = new Uint8Array(this.carry.length + raw.length)
      joined.set(this.carry)
      joined.set(raw, this.carry.length)
      raw = joined
      this.carry = null
    }
    // Keep a dangling byte back rather than dropping it: dropping one byte
    // shifts every following sample by half a word and turns the rest of the
    // turn into noise.
    const usable = raw.length - (raw.length % 2)
    if (usable < raw.length) this.carry = raw.slice(usable)
    if (usable === 0) return

    const samples = usable / 2
    const buffer = this.ctx.createBuffer(1, samples, this.rate)
    const channel = buffer.getChannelData(0)
    // Read explicitly little-endian rather than through an Int16Array view:
    // the view would take the platform's endianness, which is only correct by
    // coincidence, and the copy has to happen anyway to convert to float.
    const view = new DataView(raw.buffer, raw.byteOffset, usable)
    for (let i = 0; i < samples; i++) channel[i] = view.getInt16(i * 2, true) / 32768

    const src = this.ctx.createBufferSource()
    src.buffer = buffer
    src.connect(this.dest)
    src.onended = () => { this.live.delete(src) }

    // Behind the clock means the stream starved — the gap already happened, and
    // scheduling in the past would only make the browser drop the chunk.
    const floor = this.ctx.currentTime + PCM_LEAD_S
    if (this.next < floor) this.next = floor
    src.start(this.next)
    this.next += buffer.duration
    this.live.add(src)
  }

  stop(): void {
    for (const src of this.live) {
      try { src.stop() } catch { /* already ended */ }
    }
    this.live.clear()
    this.carry = null
    this.next = 0
  }
}

/* ── Session ──────────────────────────────────────────────────────────────── */

export type VoiceState = 'idle' | 'thinking' | 'speaking'

/** How the session ended up delivering speech. `pending` while the socket is
 *  still being negotiated — text is held, never dropped, until it resolves. */
type Delivery = 'pending' | 'stream' | 'http'

interface Job {
  text: string
  audio: Promise<ArrayBuffer | null>
}

/**
 * One turn's worth of speech. Construct on entering voice mode (the click is
 * the user gesture that lets an AudioContext start), feed it the stream, then
 * call finish() when the turn ends.
 */
export class VoiceSession {
  private ctx: AudioContext
  private analyser: AnalyserNode
  private gain: GainNode
  private buf = ''
  /** Stream tail below the last newline — not yet judgeable (see feed). */
  private raw = ''
  /** Inside a ``` fence / a display-math block: everything here is for the eye. */
  private inFence = false
  private inMath = false
  private jobs: Job[] = []
  private playIndex = 0
  private draining = false
  private stopped = false
  private ended = false
  private source: AudioBufferSourceNode | null = null
  private abort = new AbortController()
  // Backed by an explicit ArrayBuffer: since TS 5.7 Uint8Array is generic over
  // its buffer, and getByteTimeDomainData rejects the SharedArrayBuffer-capable
  // default.
  private samples: Uint8Array<ArrayBuffer>
  private freq: Uint8Array<ArrayBuffer>

  /* ── Streaming ──────────────────────────────────────────────────────────
   * `delivery` starts 'pending': the socket takes a round trip to negotiate,
   * and the model can easily produce its first line before that finishes. Text
   * written in the meantime is held in `held` and replayed once the answer
   * arrives, down whichever path won — so nothing is ever spoken twice or lost
   * because the negotiation was slower than the model. */
  private delivery: Delivery = 'pending'
  private ws: WebSocket | null = null
  private pcm: PcmPlayer | null = null
  private held = ''
  /** finish() arrived while still negotiating — end the input once we know how. */
  private endPending = false
  /** The engine sent every sample it owed — a close after this is not a failure. */
  private streamComplete = false
  private idleTimer: number | null = null

  /** Notified on every state change so the orb can react. */
  onState: (s: VoiceState) => void = () => {}

  constructor(
    private config: AppConfig,
    private opts: { agentId?: string; locale?: string; voice?: string } = {},
  ) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const Ctor = window.AudioContext || (window as any).webkitAudioContext
    this.ctx = new Ctor()
    this.analyser = this.ctx.createAnalyser()
    this.analyser.fftSize = 1024
    // Smoothing here rather than in the shader: the analyser's own decay is
    // frame-rate independent, so the ring settles at the same rate whatever the
    // display is doing.
    this.analyser.smoothingTimeConstant = 0.72
    this.gain = this.ctx.createGain()
    this.analyser.connect(this.gain)
    this.gain.connect(this.ctx.destination)
    this.samples = new Uint8Array(new ArrayBuffer(this.analyser.fftSize))
    this.freq = new Uint8Array(new ArrayBuffer(this.analyser.frequencyBinCount))
    this.openStream()
  }

  /* ── Negotiating the streaming path ───────────────────────────────────────
   * Opened eagerly in the constructor rather than on the first delta, because
   * the round trip is dead time either way and a turn's first line should not
   * have to wait behind it.
   *
   * Every failure lands in the same place: `settle('http')`, the per-sentence
   * path, which works with any engine. A refusal is the NORMAL case for an
   * Azure or OpenAI voice, so nothing here is reported as an error. */
  private openStream(): void {
    // Concatenated exactly like every other call in this file, not resolved as
    // a URL: an apiBase carrying a path prefix ("…/api") would lose it to a
    // root-relative resolve, and every request here would 404 for that owner.
    const url = `${this.config.apiBase}/voice/stream`.replace(/^http/, 'ws')

    let ws: WebSocket
    try {
      ws = new WebSocket(url)
    } catch {
      this.settle('http')
      return
    }
    this.ws = ws
    ws.binaryType = 'arraybuffer'

    ws.onopen = () => {
      // The key travels in the first frame, not a header or a query parameter:
      // no browser WebSocket can set a handshake header, and a query string
      // would put the key in every access log between here and the server.
      ws.send(JSON.stringify({
        type: 'auth',
        key: this.config.apiKey,
        agent_id: this.opts.agentId ?? this.config.agentId,
        ...(this.opts.voice ? { voice: this.opts.voice } : {}),
        ...(this.opts.locale ? { locale: this.opts.locale } : {}),
      }))
    }

    ws.onmessage = ev => {
      if (this.stopped) return
      if (ev.data instanceof ArrayBuffer) { this.onPcm(ev.data); return }
      let msg: { type?: string; sample_rate?: number }
      try { msg = JSON.parse(ev.data as string) } catch { return }
      if (msg.type === 'ready') {
        this.pcm = new PcmPlayer(this.ctx, this.analyser, msg.sample_rate || 24000)
        this.settle('stream')
      } else if (msg.type === 'done') {
        this.streamComplete = true
        this.finishStreamAudio()
      } else {
        // `unsupported` (wrong engine) and `error` (the engine died) are the
        // same decision from here: speak it the other way.
        this.degrade()
      }
    }

    ws.onerror = () => this.degrade()
    ws.onclose = () => this.degrade()
  }

  /**
   * Fall back to the per-sentence path.
   *
   * Before `ready` this is the ordinary outcome — an Azure or OpenAI voice
   * cannot stream and never could. AFTER `ready` it means the engine dropped
   * mid-reply, and the rest of the turn is switched over rather than lost:
   * whatever was already sent has been spoken (or is still playing out), and
   * the remaining lines take the slower path. The seam is audible, which is
   * still a better answer than the reply stopping halfway through.
   */
  private degrade(): void {
    if (this.stopped) return
    if (this.delivery === 'pending') { this.settle('http'); return }
    if (this.delivery !== 'stream') return
    // A socket closing after the turn's audio is complete is just the server
    // hanging up, not a failure.
    if (this.streamComplete || this.ended) { this.finishStreamAudio(); return }
    this.delivery = 'http'
    this.closeSocket()
  }

  /** Commit to a delivery path and replay whatever was written while waiting. */
  private settle(mode: 'stream' | 'http'): void {
    if (this.delivery !== 'pending' || this.stopped) return
    this.delivery = mode
    if (mode === 'http') this.closeSocket()

    const pending = this.held
    this.held = ''
    if (pending) this.absorb(pending)
    if (this.endPending) {
      this.endPending = false
      this.endInput()
    }
  }

  private onPcm(bytes: ArrayBuffer): void {
    if (!this.pcm || bytes.byteLength === 0) return
    this.pcm.push(bytes)
    this.onState('speaking')
    this.armIdle()
  }

  /** No more audio is coming: go idle exactly when the last sample has played.
   *  Timed off the audio clock rather than a source's `onended` because the
   *  final chunk may still be queued behind several others. */
  private finishStreamAudio(): void {
    if (this.delivery !== 'stream' || this.stopped) return
    this.armIdle()
  }

  private armIdle(): void {
    if (this.idleTimer !== null) window.clearTimeout(this.idleTimer)
    const wait = this.pcm ? this.pcm.remaining : 0
    this.idleTimer = window.setTimeout(() => {
      this.idleTimer = null
      if (this.stopped) return
      if (this.pcm?.busy) { this.armIdle(); return }
      this.onState(this.ended ? 'idle' : 'thinking')
    }, Math.max(50, wait * 1000))
  }

  private closeSocket(): void {
    const ws = this.ws
    this.ws = null
    if (!ws) return
    ws.onopen = ws.onmessage = ws.onerror = ws.onclose = null
    try { ws.close() } catch { /* already closing */ }
  }

  /** Tell whichever path is live that the reply is complete. */
  private endInput(): void {
    if (this.delivery === 'stream') {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'end' }))
      }
      // The tail is still generating; `done` (or the close that follows it)
      // decides when this actually goes idle.
      return
    }
    const tail = this.buf.trim()
    this.buf = ''
    if (tail) this.enqueue(tail)
    if (!this.jobs.length) this.onState('idle')
  }

  /** Current loudness, 0..1 — drives the orb. Reads the live analyser rather
   *  than a timer, so the orb moves with the actual waveform. */
  amplitude(): number {
    this.analyser.getByteTimeDomainData(this.samples)
    let sum = 0
    for (let i = 0; i < this.samples.length; i++) {
      const v = (this.samples[i] - 128) / 128
      sum += v * v
    }
    // RMS is small for speech; scale into a range the eye reads as "loud".
    return Math.min(1, Math.sqrt(sum / this.samples.length) * 3.2)
  }

  /**
   * Fill `out` with the spectrum, one value per element, 0..1.
   *
   * The orb deforms per-angle from this rather than from a single loudness
   * number: a ring that only breathes in and out reads as a progress spinner,
   * while one whose lobes track separate frequency bands reads as a voice.
   * Bins are grouped logarithmically because speech energy is bunched at the
   * bottom of the range, and a linear split would leave most of the ring dead.
   */
  spectrum(out: Float32Array): void {
    this.analyser.getByteFrequencyData(this.freq)
    const n = out.length
    // Speech lives well below Nyquist; the top of the range is silence.
    const usable = Math.floor(this.freq.length * 0.62)
    for (let i = 0; i < n; i++) {
      const lo = Math.floor(Math.pow(i / n, 1.7) * usable)
      const hi = Math.max(lo + 1, Math.floor(Math.pow((i + 1) / n, 1.7) * usable))
      let peak = 0
      for (let j = lo; j < hi; j++) peak = Math.max(peak, this.freq[j])
      out[i] = peak / 255
    }
  }

  /**
   * Feed streamed reply text. Safe to call on every delta.
   *
   * Deltas are held until a line is COMPLETE before being judged: a ``` fence
   * marker, or a `$$`, routinely arrives split across two chunks, and a filter
   * that decides per-delta would speak the first half of the artefact it exists
   * to suppress. Lines are the unit because fences and display math are
   * line-oriented; prose loses nothing by waiting for its newline, since the
   * sentence splitter is already holding the tail anyway.
   */
  feed(delta: string): void {
    if (this.stopped || this.ended) return
    this.raw += delta
    const nl = this.raw.lastIndexOf('\n')
    if (nl === -1) return
    const complete = this.raw.slice(0, nl + 1)
    this.raw = this.raw.slice(nl + 1)
    this.absorb(this.speakable(complete))
  }

  /** The turn is over: speak whatever is left, then stop. */
  finish(): void {
    if (this.stopped || this.ended) return
    this.ended = true
    // The last line never got its newline; it is only speakable if the stream
    // did not end inside an artefact.
    if (this.raw) {
      this.absorb(this.speakable(this.raw + '\n'))
      this.raw = ''
    }
    // Still negotiating: `settle` will end the input once it knows which path
    // to end. Ending here would close a socket that has not been told anything.
    if (this.delivery === 'pending') { this.endPending = true; return }
    this.endInput()
  }

  /**
   * The reply is about to pause — a tool is running.
   *
   * The engine holds text back until it has enough characters to commit to a
   * contour, so without this the last part-sentence before a tool call sits
   * unspoken for as long as the tool takes. Speech would fall silently behind
   * the text on screen, then catch up in a rush when the answer resumed.
   *
   * Only for a genuine pause. Flushing routinely would close off a prosodic
   * unit every time, which is the per-sentence behaviour this path exists to
   * stop doing.
   */
  pause(): void {
    if (this.stopped || this.ended) return
    if (this.delivery === 'stream' && this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'flush' }))
    }
  }

  /** Drop everything that belongs on the canvas rather than in the ear. */
  private speakable(text: string): string {
    let out = ''
    for (const line of text.split('\n')) {
      const t = line.trim()
      if (this.inFence) {
        if (t.startsWith('```')) this.inFence = false
        continue
      }
      if (t.startsWith('```')) { this.inFence = true; continue }
      if (this.inMath) {
        if (t.includes('$$') || t.includes('\\]')) this.inMath = false
        continue
      }
      if (t.startsWith('$$') || t.startsWith('\\[')) {
        // A one-line `$$x$$` opens and closes at once.
        const closed = t.length > 2 && (t.endsWith('$$') || t.endsWith('\\]'))
        this.inMath = !closed
        continue
      }
      if (isTableRow(t)) continue
      out += spokenProse(inlineMath(line)) + '\n'
    }
    return out
  }

  /**
   * Route speakable prose down whichever path this session settled on.
   *
   * Streaming hands it straight over: the engine does its own chunking, and
   * cutting at sentence boundaries first would hand back exactly the isolated
   * utterances the socket exists to avoid. The fallback splits into sentences
   * because its transport has no other unit.
   */
  private absorb(text: string): void {
    if (!text) return
    if (this.delivery === 'pending') { this.held += text; return }
    if (this.delivery === 'stream') {
      // No state change here: audio already in flight is still playing, and
      // announcing 'thinking' on every line would flicker the orb mid-sentence.
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'text', text }))
      }
      return
    }
    this.buf += text
    const { sentences, rest } = splitSentences(this.buf)
    this.buf = rest
    for (const s of sentences) this.enqueue(s)
  }

  private enqueue(text: string): void {
    // Markup is stripped server-side, but a fence or a bare table row would
    // otherwise become an entire request that bills characters and returns
    // nothing useful, so skip the obviously unspeakable here too.
    const t = text.trim()
    if (!t || /^[`|\-*_#>\s]+$/.test(t)) return

    const job: Job = { text: t, audio: this.throttled(t) }
    this.jobs.push(job)
    if (!this.draining) void this.drain()
  }

  /** Hold synthesis to MAX_INFLIGHT by chaining each job behind the one
   *  MAX_INFLIGHT places before it. */
  private throttled(text: string): Promise<ArrayBuffer | null> {
    const gate = this.jobs[this.jobs.length - MAX_INFLIGHT]?.audio ?? Promise.resolve(null)
    return gate
      .catch(() => null)
      .then(() => {
        if (this.stopped) return null
        return synthesize(this.config, text, {
          agentId: this.opts.agentId,
          locale: this.opts.locale,
          // Empty falls through to the agent's profile voice server-side, which
          // is the behaviour that predates the owner being able to pick one.
          voice: this.opts.voice || undefined,
          signal: this.abort.signal,
        }).catch(() => null)
      })
  }

  private async drain(): Promise<void> {
    this.draining = true
    try {
      while (this.playIndex < this.jobs.length) {
        if (this.stopped) return
        const job = this.jobs[this.playIndex++]
        const raw = await job.audio
        if (this.stopped || !raw || raw.byteLength === 0) continue
        this.onState('speaking')
        await this.play(raw)
      }
    } finally {
      this.draining = false
      if (!this.stopped) {
        // More may have arrived while the last clip played.
        if (this.playIndex < this.jobs.length) void this.drain()
        else this.onState(this.ended ? 'idle' : 'thinking')
      }
    }
  }

  private play(raw: ArrayBuffer): Promise<void> {
    return new Promise(resolve => {
      this.ctx.decodeAudioData(
        raw.slice(0),
        buffer => {
          if (this.stopped) return resolve()
          const src = this.ctx.createBufferSource()
          src.buffer = buffer
          src.connect(this.analyser)
          src.onended = () => {
            if (this.source === src) this.source = null
            resolve()
          }
          this.source = src
          src.start()
        },
        () => resolve(),   // undecodable clip — skip it rather than stall
      )
    })
  }

  /** Cut speech immediately: barge-in, leaving voice mode, a new turn. */
  stop(): void {
    if (this.stopped) return
    this.stopped = true
    this.abort.abort()
    try { this.source?.stop() } catch { /* already ended */ }
    this.source = null
    if (this.idleTimer !== null) { window.clearTimeout(this.idleTimer); this.idleTimer = null }
    this.pcm?.stop()
    this.pcm = null
    this.closeSocket()
    this.jobs = []
    this.buf = ''
    this.raw = ''
    this.held = ''
    this.onState('idle')
    void this.ctx.close().catch(() => {})
  }

  /** The AudioContext starts suspended unless created during a gesture. */
  async resume(): Promise<void> {
    if (this.ctx.state === 'suspended') await this.ctx.resume().catch(() => {})
  }
}
