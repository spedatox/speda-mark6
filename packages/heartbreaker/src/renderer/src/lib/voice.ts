/**
 * Spoken playback for voice mode.
 *
 * The reply arrives as a token stream, but synthesis is per-utterance, so the
 * whole design turns on one thing: cut the stream into sentences and start
 * speaking the first one while the rest is still being written. Waiting for the
 * turn to finish before speaking adds the entire generation time to the silence
 * before the first word, which is what makes a voice assistant feel dead.
 *
 * Three stages run concurrently and independently:
 *   feed()      — accumulates deltas, emits complete sentences
 *   synthesis   — up to MAX_INFLIGHT sentences convert at once
 *   playback    — strictly in order, one at a time
 *
 * Order is preserved even though synthesis is not: sentence 3 finishing before
 * sentence 2 must not let it speak first, so playback awaits each job's promise
 * in sequence rather than racing them.
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

/* ── Session ──────────────────────────────────────────────────────────────── */

export type VoiceState = 'idle' | 'thinking' | 'speaking'

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

  /** Feed streamed reply text. Safe to call on every delta. */
  feed(delta: string): void {
    if (this.stopped || this.ended) return
    this.buf += delta
    const { sentences, rest } = splitSentences(this.buf)
    this.buf = rest
    for (const s of sentences) this.enqueue(s)
  }

  /** The turn is over: speak whatever is left, then stop. */
  finish(): void {
    if (this.stopped || this.ended) return
    this.ended = true
    const tail = this.buf.trim()
    this.buf = ''
    if (tail) this.enqueue(tail)
    if (!this.jobs.length) this.onState('idle')
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
    this.jobs = []
    this.buf = ''
    this.onState('idle')
    void this.ctx.close().catch(() => {})
  }

  /** The AudioContext starts suspended unless created during a gesture. */
  async resume(): Promise<void> {
    if (this.ctx.state === 'suspended') await this.ctx.resume().catch(() => {})
  }
}
