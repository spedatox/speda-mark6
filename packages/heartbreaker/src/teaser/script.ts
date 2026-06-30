/**
 * The teaser timeline — beat-centric so the settings panel can edit each beat's
 * duration and the whole schedule (captions included) re-lays-out from it.
 *
 * Each beat owns its captions at times RELATIVE to its own start, so stretching
 * a beat slides everything after it without desyncing the voiceover. The six
 * agent captions are generated from the beat duration so they always line up
 * with the visual slots.
 */

export type BeatId =
  | 'cold' | 'ignition' | 'capabilities' | 'owner' | 'six'
  | 'proactivity' | 'collaboration' | 'resolve'

export interface RelCaption { t: number; dur: number; text: string; sub?: string }
export interface BeatDef { id: BeatId; label: string; dur: number; captions: RelCaption[] }

export interface Beat { id: BeatId; label: string; t0: number; t1: number; dur: number }
export interface Caption { t0: number; t1: number; text: string; sub?: string }

/** The six, in introduction order (keys match BRANDS agentIds). */
export const SIX_ORDER = ['sentinel', 'nightcrawler', 'ultron', 'centurion', 'atomix', 'optimus'] as const
export const SIX_NAME: Record<string, string> = {
  sentinel: 'Sentinel', nightcrawler: 'NightCrawler', ultron: 'Ultron',
  centurion: 'Centurion', atomix: 'Atomix', optimus: 'Optimus',
}
export const SIX_DOMAIN: Record<string, string> = {
  sentinel: 'FINANCE', nightcrawler: 'RESEARCH · OPEN WEB', ultron: 'ACADEMIA',
  centurion: 'CYBERSECURITY', atomix: 'HEALTH', optimus: 'SYSTEMS · CODE',
}

/** Default per-beat durations + captions (relative seconds). User-editable. */
export const TIMELINE: BeatDef[] = [
  { id: 'cold', label: 'Cold open', dur: 8, captions: [
    { t: 3.2, dur: 4.4, text: 'Hello.' },
  ] },
  { id: 'ignition', label: 'Ignition', dur: 10, captions: [
    { t: 0.4, dur: 4.6, text: 'Allow me to introduce myself.' },
    { t: 5.2, dur: 4.6, text: 'I am SPEDA, Mark VI — a proactive artificial intelligence.' },
  ] },
  { id: 'capabilities', label: 'Capabilities', dur: 21, captions: [
    { t: 0.5, dur: 6.0, text: "And I'm here to assist you with a variety of tasks as best I can," },
    { t: 7.0, dur: 13.0, text: 'twenty-four hours a day, seven days a week.' },
  ] },
  { id: 'owner', label: 'Owner', dur: 8, captions: [
    { t: 0.4, dur: 3.4, text: "One person. That's who I'm for." },
    { t: 4.0, dur: 3.8, text: 'I learn your rhythm, I anticipate what’s coming, and I hold everything to your standard.' },
  ] },
  // six captions (intro + per-agent) are generated from the duration.
  { id: 'six', label: 'The Six', dur: 30, captions: [
    { t: 0.5, dur: 4.0, text: "And I don’t work alone. Under my command, six specialists — each master of a single domain." },
  ] },
  { id: 'proactivity', label: 'Proactivity (n8n)', dur: 14, captions: [
    { t: 0.6, dur: 4.2, text: "You don’t need to be here for me to work." },
    { t: 5.0, dur: 4.4, text: 'The brief written, the numbers checked, the day in order — before you ever ask.' },
    { t: 9.6, dur: 4.0, text: 'By the time you sit down, it’s already done.' },
  ] },
  { id: 'collaboration', label: 'Collaboration', dur: 11, captions: [
    { t: 0.4, dur: 5.0, text: 'They speak to one another, pass work between them —' },
    { t: 5.6, dur: 4.9, text: 'and when a task is large enough, the six move as one.' },
  ] },
  { id: 'resolve', label: 'Resolve', dur: 9, captions: [
    { t: 0.6, dur: 3.8, text: 'I am SPEDA, Mark VI.' },
    { t: 4.7, dur: 3.9, text: 'Ready when you are.' },
  ] },
]

/** Extra editable timing knobs (relative seconds within their beat). */
export interface TeaserParams {
  /** when the SPEDA wordmark assembles within the Ignition beat — align to the
   *  "I am SPEDA, Mark VI" line, not "allow me to introduce myself". */
  wordmarkAt: number
}
export const DEFAULT_PARAMS: TeaserParams = { wordmarkAt: 5.2 }

/** Within the Six beat: intro hold, then six equal agent slots. Shared by the
 *  scene and the colour-morph driver so visuals + captions + hue stay locked. */
export function sixTiming(dur: number): { intro: number; slot: number } {
  const intro = Math.min(4.5, dur * 0.16)
  return { intro, slot: (dur - intro) / 6 }
}

export type Durations = Partial<Record<BeatId, number>>

/** Lay out the absolute timeline from (optionally overridden) durations. */
export function resolveTimeline(durs: Durations = {}): {
  beats: Beat[]; captions: Caption[]; duration: number
} {
  let t = 0
  const beats: Beat[] = []
  const captions: Caption[] = []

  for (const b of TIMELINE) {
    const dur = durs[b.id] ?? b.dur
    const t0 = t
    const t1 = t + dur
    beats.push({ id: b.id, label: b.label, t0, t1, dur })

    for (const c of b.captions) {
      captions.push({ t0: t0 + c.t, t1: t0 + Math.min(c.t + c.dur, dur), text: c.text, sub: c.sub })
    }
    // generate per-agent captions for the six, synced to the visual slots
    if (b.id === 'six') {
      const { intro, slot } = sixTiming(dur)
      SIX_ORDER.forEach((id, i) => {
        const s = t0 + intro + i * slot
        captions.push({ t0: s + 0.2, t1: s + slot, text: SIX_NAME[id] + '.', sub: SIX_DOMAIN[id] })
      })
    }
    t = t1
  }
  return { beats, captions, duration: t }
}
