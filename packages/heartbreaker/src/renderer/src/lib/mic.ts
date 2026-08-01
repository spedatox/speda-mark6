/**
 * Microphone capture for voice mode.
 *
 * The mirror of voice.ts: that one cuts a reply into sentences so speech starts
 * before the turn ends, this one cuts the mic into utterances so a turn starts
 * before the owner says "send". Neither direction waits for an explicit gesture,
 * which is the whole difference between a voice assistant and a dictation box.
 *
 * Segmentation happens HERE, not on the server. The decision "they have stopped
 * talking" has to be made where the waveform already is — streaming audio to the
 * backend to find that out would spend bandwidth and Azure audio-hours on the
 * pauses between sentences, which is most of a conversation.
 *
 * Three things are easy to get wrong and are handled explicitly:
 *
 *   - PRE-ROLL. Speech is detected only once it is already loud, so by the time
 *     the gate opens the first consonant is gone. A rolling pre-buffer is kept
 *     at all times and prepended to every utterance; without it "Sentinel" is
 *     transcribed as "entinel".
 *   - Capture runs in an AudioWorklet, off the main thread. Voice mode drives a
 *     WebGL orb at 60fps, and that is exactly the load under which a main-thread
 *     ScriptProcessor starts dropping audio blocks.
 *   - Resampling to 16 kHz averages the samples it spans rather than picking
 *     one. Picking is a decimation with no anti-alias filter, which folds
 *     sibilance down into the speech band as a lisp the recogniser then has to
 *     guess through.
 */

import type { AppConfig } from './types'
import { authHeaders } from './api'

/** What Azure's recogniser wants. Resampling here rather than sending 48 kHz
 *  cuts the upload by two thirds for audio the engine would downsample anyway. */
const TARGET_RATE = 16000

/** Frames the worklet accumulates before posting. At 48 kHz this is ~21ms —
 *  fine-grained enough for responsive gating, coarse enough that postMessage
 *  is not called three hundred times a second. */
const BLOCK = 1024

/* ── Voice activity detection ─────────────────────────────────────────────── */

/** RMS above which a block counts as speech. Low enough for a quiet room and a
 *  laptop mic; the hangover below is what actually prevents false triggers. */
const SPEECH_RMS = 0.014
/** Silence this long ends an utterance. Shorter and it cuts people off mid
 *  sentence at a comma; longer and every reply feels like it is buffering. */
const HANGOVER_MS = 750
/** Audio kept before speech onset, so the first phoneme survives the gate. */
const PREROLL_MS = 300
/** Utterances shorter than this are a cough, a chair, a door. Dropped without
 *  a round trip — Azure would bill for them and return NoMatch anyway. */
const MIN_UTTERANCE_MS = 320
/** Hard ceiling. Azure's short-audio endpoint stops at 60s; cutting at 45
 *  leaves room and turns a stuck gate into a sent turn rather than a lost one. */
const MAX_UTTERANCE_MS = 45000

/** The worklet is a dumb forwarder: accumulate BLOCK frames, post them, repeat.
 *  All gating lives on the main thread, where the state it depends on already
 *  is. Delivered as a Blob URL so there is no separate asset to bundle, copy to
 *  the packaged app, and get wrong in exactly one build configuration. */
const WORKLET_SRC = `
class Capture extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(${BLOCK}); this.n = 0 }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0]
    if (!ch) return true
    for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = ch[i]
      if (this.n === ${BLOCK}) {
        this.port.postMessage(this.buf.slice(0))
        this.n = 0
      }
    }
    return true
  }
}
registerProcessor('speda-capture', Capture)
`

/* ── Encoding ─────────────────────────────────────────────────────────────── */

/**
 * Resample to TARGET_RATE by averaging each source span.
 *
 * Deliberately not a nearest-sample pick: that is decimation without an
 * anti-alias filter, and everything above 8 kHz folds back into the speech band
 * as artificial sibilance. Averaging the span is a crude box filter, but a crude
 * low-pass is the difference between clean consonants and a recogniser guessing.
 */
function resample(input: Float32Array, from: number): Float32Array {
  if (from === TARGET_RATE) return input
  const ratio = from / TARGET_RATE
  const out = new Float32Array(Math.floor(input.length / ratio))
  for (let i = 0; i < out.length; i++) {
    const lo = Math.floor(i * ratio)
    const hi = Math.min(input.length, Math.floor((i + 1) * ratio))
    let sum = 0
    for (let j = lo; j < hi; j++) sum += input[j]
    out[i] = hi > lo ? sum / (hi - lo) : 0
  }
  return out
}

/** Wrap 16-bit PCM in a WAV container — the one format Azure's short-audio
 *  endpoint reads without a transcoding dependency on the server. */
function encodeWav(samples: Float32Array, rate: number): Blob {
  const buf = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buf)
  const ascii = (off: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i))
  }
  ascii(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  ascii(8, 'WAVE')
  ascii(12, 'fmt ')
  view.setUint32(16, 16, true)          // PCM chunk size
  view.setUint16(20, 1, true)           // format: PCM
  view.setUint16(22, 1, true)           // mono
  view.setUint32(24, rate, true)
  view.setUint32(28, rate * 2, true)    // byte rate
  view.setUint16(32, 2, true)           // block align
  view.setUint16(34, 16, true)          // bits per sample
  ascii(36, 'data')
  view.setUint32(40, samples.length * 2, true)
  let off = 44
  for (let i = 0; i < samples.length; i++, off += 2) {
    // Clamp before scaling: a value past ±1 wraps to the opposite rail as a
    // click, which the recogniser hears as a plosive.
    const s = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true)
  }
  return new Blob([buf], { type: 'audio/wav' })
}

/* ── Recognition ──────────────────────────────────────────────────────────── */

export async function recognize(
  config: AppConfig,
  wav: Blob,
  opts: { locale?: string; signal?: AbortSignal } = {},
): Promise<string> {
  const form = new FormData()
  form.append('audio', wav, 'utterance.wav')
  if (opts.locale) form.append('locale', opts.locale)

  const res = await fetch(`${config.apiBase}/voice/listen`, {
    method: 'POST',
    // No Content-Type: the browser sets the multipart boundary itself, and
    // overriding it here produces a body the server cannot parse.
    headers: authHeaders(config),
    body: form,
    signal: opts.signal,
  })
  // 503 is voice being unconfigured or Azure refusing. A dropped utterance is a
  // better failure than an error card mid-conversation — the owner can retype.
  if (!res.ok) return ''
  return ((await res.json()).text as string) ?? ''
}

/* ── Session ──────────────────────────────────────────────────────────────── */

export type MicState = 'off' | 'listening' | 'hearing' | 'recognizing'

export interface MicOptions {
  locale?: string
  /** A finished utterance came back with text. Empty results never reach here. */
  onTranscript: (text: string) => void
  /** Speech started while the mic was open. This is the BARGE-IN signal: the
   *  owner talking over the agent means stop talking, and it has to fire on
   *  onset rather than on the transcript, which is a round trip too late. */
  onSpeechStart?: () => void
  onState?: (s: MicState) => void
  /** Recognition or permission failed in a way worth telling the owner about. */
  onError?: (message: string) => void
}

/**
 * An open microphone. Construct on the gesture that turns the mic on (getUserMedia
 * needs one), `await start()`, and `stop()` when leaving.
 *
 * Utterances are recognised concurrently: the owner can begin a second sentence
 * while the first is still being transcribed, and transcripts are emitted in the
 * order the utterances were spoken, not the order the responses come back.
 */
export class MicSession {
  private ctx: AudioContext | null = null
  private stream: MediaStream | null = null
  private node: AudioWorkletNode | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private abort = new AbortController()

  private preroll: Float32Array[] = []
  private prerollFrames = 0
  private capture: Float32Array[] = []
  private captureFrames = 0
  private speaking = false
  private silentFrames = 0
  private level = 0
  private stopped = false
  /** Recognition is concurrent but delivery is ordered — the same guarantee
   *  playback makes in the other direction, for the same reason: out-of-order
   *  sentences are worse than slightly later ones. */
  private tail: Promise<void> = Promise.resolve()
  /** Set while the agent is speaking, so onset can be reported as barge-in
   *  rather than as an ordinary utterance start. */
  private guard = false

  constructor(private config: AppConfig, private opts: MicOptions) {}

  /** Live input level, 0..1 — lets the orb react to the OWNER's voice too, not
   *  only the agent's. Smoothed on read so the caller can poll it per frame. */
  amplitude(): number {
    return Math.min(1, this.level * 3.2)
  }

  /** Tell the mic the agent is speaking, so speech onset counts as barge-in. */
  setAgentSpeaking(on: boolean): void {
    this.guard = on
  }

  async start(): Promise<void> {
    // echoCancellation is what stops the agent's own voice, coming out of the
    // speakers, from being heard as the owner interrupting — without it every
    // spoken reply barges in on itself and the conversation deadlocks.
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
    })
    if (this.stopped) { this.teardown(); return }

    const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    this.ctx = new Ctor()
    const url = URL.createObjectURL(new Blob([WORKLET_SRC], { type: 'application/javascript' }))
    try {
      await this.ctx.audioWorklet.addModule(url)
    } finally {
      URL.revokeObjectURL(url)
    }
    if (this.stopped) { this.teardown(); return }

    this.source = this.ctx.createMediaStreamSource(this.stream)
    this.node = new AudioWorkletNode(this.ctx, 'speda-capture')
    this.node.port.onmessage = e => this.onBlock(e.data as Float32Array)
    this.source.connect(this.node)
    // The worklet produces no output, but an unconnected node is not guaranteed
    // to be pulled. A zero gain keeps it in the graph without echoing the mic
    // back through the speakers.
    const mute = this.ctx.createGain()
    mute.gain.value = 0
    this.node.connect(mute)
    mute.connect(this.ctx.destination)

    this.opts.onState?.('listening')
  }

  private get rate(): number {
    return this.ctx?.sampleRate ?? 48000
  }

  private onBlock(block: Float32Array): void {
    if (this.stopped) return

    let sum = 0
    for (let i = 0; i < block.length; i++) sum += block[i] * block[i]
    const rms = Math.sqrt(sum / block.length)
    // Attack fast, release slow — same shaping as the orb's amplitude, so the
    // two read as one level rather than two independent meters.
    this.level += (rms - this.level) * (rms > this.level ? 0.5 : 0.1)

    const loud = rms > SPEECH_RMS

    if (!this.speaking) {
      // Keep a rolling window of what came just before, so the utterance does
      // not start at the first loud sample and lose its own first consonant.
      this.preroll.push(block)
      this.prerollFrames += block.length
      while (this.prerollFrames > (PREROLL_MS / 1000) * this.rate) {
        this.prerollFrames -= this.preroll.shift()!.length
      }
      if (!loud) return

      this.speaking = true
      this.silentFrames = 0
      this.capture = this.preroll.slice()
      this.captureFrames = this.prerollFrames
      this.preroll = []
      this.prerollFrames = 0
      this.opts.onState?.('hearing')
      // Fires on ONSET, not on the transcript: barge-in that waits for
      // recognition arrives a full round trip after the owner started talking,
      // by which point the agent has spoken over them anyway.
      if (this.guard) this.opts.onSpeechStart?.()
      return
    }

    this.capture.push(block)
    this.captureFrames += block.length
    this.silentFrames = loud ? 0 : this.silentFrames + block.length

    const silentMs = (this.silentFrames / this.rate) * 1000
    const totalMs = (this.captureFrames / this.rate) * 1000
    if (silentMs >= HANGOVER_MS || totalMs >= MAX_UTTERANCE_MS) this.flush()
  }

  private flush(): void {
    const blocks = this.capture
    const frames = this.captureFrames
    this.speaking = false
    this.capture = []
    this.captureFrames = 0
    this.silentFrames = 0
    this.opts.onState?.('listening')

    const ms = (frames / this.rate) * 1000
    if (ms < MIN_UTTERANCE_MS) return       // a cough, not a sentence

    // Flatten, trim the trailing silence, resample, encode.
    const flat = new Float32Array(frames)
    let off = 0
    for (const b of blocks) { flat.set(b, off); off += b.length }
    const keep = Math.max(
      1,
      flat.length - Math.floor((HANGOVER_MS / 1000) * this.rate * 0.6),
    )
    const wav = encodeWav(resample(flat.subarray(0, keep), this.rate), TARGET_RATE)

    this.opts.onState?.('recognizing')
    const pending = recognize(this.config, wav, {
      locale: this.opts.locale,
      signal: this.abort.signal,
    }).catch(() => '')

    // Chain onto the tail so transcripts are delivered in spoken order even
    // when a short second utterance is recognised before a long first one.
    this.tail = this.tail.then(async () => {
      const text = await pending
      if (this.stopped) return
      this.opts.onState?.(this.speaking ? 'hearing' : 'listening')
      const t = text.trim()
      if (t) this.opts.onTranscript(t)
    })
  }

  /** Close the mic and release the device. The recording indicator staying lit
   *  after leaving voice mode is the worst failure this feature can have. */
  stop(): void {
    if (this.stopped) return
    this.stopped = true
    this.abort.abort()
    this.teardown()
    this.opts.onState?.('off')
  }

  private teardown(): void {
    try { this.node?.port.close() } catch { /* already closed */ }
    try { this.node?.disconnect() } catch { /* not connected */ }
    try { this.source?.disconnect() } catch { /* not connected */ }
    this.stream?.getTracks().forEach(t => t.stop())
    void this.ctx?.close().catch(() => {})
    this.node = null
    this.source = null
    this.stream = null
    this.ctx = null
  }
}

/** Whether this machine can capture audio at all. Checked before offering the
 *  control, so a missing device shows as an absent button rather than a
 *  permission dialog that resolves to nothing. */
export function micAvailable(): boolean {
  return !!navigator.mediaDevices?.getUserMedia && typeof AudioWorkletNode !== 'undefined'
}
