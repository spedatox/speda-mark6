/**
 * Throwaway dev harness for the voice canvas — renders a staged presentation
 * (the Vanko dossier and a Sentinel-style briefing) against the real splitter,
 * so the presentation kinds and the caption can be seen without a backend, a
 * key, or a spoken turn. Not part of the app; delete once verified.
 */
import { useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import VoiceCanvas from '../src/renderer/src/components/VoiceCanvas'
import { splitPanels, captionOf } from '../src/renderer/src/lib/voicePanels'
import { ChatContext } from '../src/renderer/src/store/chat'
import { SettingsContext, useSettingsProvider } from '../src/renderer/src/store/settings'
import { BoardChatBlock } from '../src/renderer/src/components/VoicePanelBody'
import VoiceActivity from '../src/renderer/src/components/VoiceActivity'
import type { ToolBadge } from '../src/renderer/src/lib/types'
import '../src/renderer/src/theme/heartbreaker.css'

/* A research readout, written the way the brief asks for it. */
const DOSSIER = `Vanko is not a dead end. Here is what the trail actually shows.

\`\`\`card | VANKO / FILE
Ivan Antonovich Vanko
image: https://upload.wikimedia.org/wikipedia/commons/thumb/8/8f/Question_mark_alternate.svg/240px-Question_mark_alternate.svg.png
Born: 1963, Moscow
Field: Plasma physics
Last known: Butyrka, released 2010
Status: Whereabouts unknown
\`\`\`

His father worked the same problem your father did, and that is where the paper trail starts.

\`\`\`timeline | THE TRAIL
1963 — Born in Moscow, son of Anton Vanko
1967 — Anton Vanko deported from the United States
2001 — Convicted of selling weapons-grade plutonium
2010 — Released; travels to Monaco on a forged passport
\`\`\`

The press picked it up twice, and both write-ups say the same thing in different words.

\`\`\`article | MOSCOW TIMES
title: Physicist held over export breach
source: Moscow Times
date: 2010-04-02
url: https://example.com/a

Customs officials confirmed the seizure of components bound for a private buyer
in Monaco. The suspect, a former state laboratory engineer, was released without
charge nine days later.
\`\`\`

\`\`\`quote | ON THE RECORD
If you could make God bleed, people would cease to believe in Him.
— Ivan Vanko, 2010
\`\`\`

That is the assessment: capable, motivated, and not being watched by anyone. I would put a flag on the Monaco entry.`

/* A month-end briefing, written the way Sentinel is briefed to write it. */
const BRIEFING = `The month closed stronger than it looked from the inside.

\`\`\`stat | REVENUE / MONTH
€4.24M
+9.1% vs August
net of refunds
\`\`\`

\`\`\`stat | BURN
€1.81M
-4.0% vs August
\`\`\`

\`\`\`stat | RUNWAY
19 mo
+2 mo
at current burn
\`\`\`

Growth did not come from where you would expect, though — direct is flat and the whole lift is partner.

\`\`\`chart | REVENUE / BY CHANNEL
{"type":"bar","xKey":"channel","series":[{"key":"eur","label":"Revenue"}],"data":[{"channel":"Direct","eur":1120000},{"channel":"Partner","eur":1980000},{"channel":"Marketplace","eur":840000},{"channel":"Other","eur":300000}]}
\`\`\`

One thing worth a decision this week: partner concentration is now high enough that losing the top account would erase the runway gain.`

const SCRIPTS: Record<string, string> = {
  'NIGHTCRAWLER / DOSSIER': DOSSIER,
  'SENTINEL / MONTH END': BRIEFING,
  // The same kinds as they appear in the chat transcript, where a window has no
  // height to fill and has to set its own.
  'CHAT FLOW': '',
  // A research turn in progress — what the owner sees BEFORE any answer exists.
  'ACTIVITY': '',
}

const SCRIPTED_TOOLS: Array<{ at: number; tool: ToolBadge; resultAt?: number }> = [
  { at: 300, resultAt: 2400, tool: { id: 't1', name: 'web_search', input: { query: 'Ivan Vanko physicist Moscow' }, result: '7 results' } },
  { at: 2600, resultAt: 9000, tool: { id: 't2', name: 'browse_page', input: { url: 'https://example.com/moscow-times/vanko', portal: null }, result: 'Physicist held over export breach — Moscow Times' } },
  { at: 9200, tool: { id: 't3', name: 'browse_page', input: { url: 'https://example.com/archive/1967-deportation' } } },
]

/** Replays the chain above in wall-clock time, so the activity window can be
 *  watched behaving rather than inspected as a still. */
function useScriptedTools(running: boolean): ToolBadge[] {
  const [t0] = useState(() => Date.now())
  const [, bump] = useState(0)
  useEffect(() => {
    if (!running) return
    const id = window.setInterval(() => bump(n => n + 1), 120)
    return () => window.clearInterval(id)
  }, [running])
  if (!running) return []
  const ms = Date.now() - t0
  return SCRIPTED_TOOLS
    .filter(s => ms >= s.at)
    .map(s => ({ ...s.tool, result: s.resultAt !== undefined && ms >= s.resultAt ? s.tool.result : undefined }))
}

/** The activity window, driven by the scripted chain. */
function ActivityDemo() {
  const tools = useScriptedTools(true)
  return <VoiceActivity tools={tools} streaming hasText={false} />
}

function Harness() {
  const [which, setWhich] = useState('NIGHTCRAWLER / DOSSIER')
  /** How far into the reply the stream has got — drags the whole assembly, so
   *  the board can be watched building in the order the agent wrote it. */
  const [upto, setUpto] = useState(1)
  const full = SCRIPTS[which]
  const visible = full.slice(0, Math.round(full.length * upto))

  const scripted = useScriptedTools(which !== 'CHAT FLOW' && which !== 'ACTIVITY')
  const staged = useMemo(() => splitPanels(visible), [visible])
  // The activity window leads the board, exactly as VoiceMode assembles it.
  const panels = useMemo(
    () => [{ id: 'activity', kind: 'activity' as const, title: 'ACTIVITY_LIVE', source: '' }, ...staged],
    [staged],
  )
  const caption = useMemo(() => captionOf(visible), [visible])

  // Measured live: the packer is driven by the board's real size, so a harness
  // that read the viewport once at load would test a layout nobody will see.
  const [[W, H], setSize] = useState<[number, number]>(
    () => [window.innerWidth, window.innerHeight - 150],
  )
  useEffect(() => {
    const on = () => setSize([window.innerWidth, window.innerHeight - 150])
    on()
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [])

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'var(--hb-void)', color: 'var(--hb-text)' }}>
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 34, display: 'flex', alignItems: 'center', gap: '0.8rem', padding: '0 0.8rem', zIndex: 50 }}>
        {Object.keys(SCRIPTS).map(k => (
          <button key={k} className="hb-btn" onClick={() => setWhich(k)}
                  style={{ height: 22, fontSize: '0.6rem', letterSpacing: '0.12em', opacity: k === which ? 1 : 0.5 }}>
            {k}
          </button>
        ))}
        <input type="range" min={0} max={1} step={0.01} value={upto}
               onChange={e => setUpto(Number(e.target.value))} style={{ flex: 1, maxWidth: 420 }} />
        <span style={{ fontFamily: 'monospace', fontSize: '0.65rem', color: 'var(--hb-cyan)' }}>
          {panels.length} windows
        </span>
      </div>

      {which === 'ACTIVITY' ? (
        <div style={{ position: 'absolute', top: 60, left: 24, width: 460, height: 320 }}
             className="hb-holo">
          <div style={{ height: '100%', padding: '0.7rem 0.85rem' }}>
            <ActivityDemo />
          </div>
        </div>
      ) : which === 'CHAT FLOW' ? (
        <div style={{ position: 'absolute', top: 46, left: 16, right: 16, bottom: 8, overflow: 'auto' }}>
          <BoardChatBlock kind="stat" source={`€4.24M
+9.1% vs August
net of refunds`} />
          <BoardChatBlock kind="card" source={`Ivan Antonovich Vanko
Born: 1963, Moscow
Field: Plasma physics
Status: Whereabouts unknown`} />
          <BoardChatBlock kind="timeline" source={`1963 — Born in Moscow
2001 — Convicted of selling weapons-grade plutonium
2010 — Released; travels to Monaco`} />
          <BoardChatBlock kind="quote" source={`If you could make God bleed, people would cease to believe in Him.
— Ivan Vanko, 2010`} />
        </div>
      ) : (
      <div style={{ position: 'absolute', top: 40, left: 0, width: W, height: H }}>
        <VoiceCanvas
          panels={panels}
          width={W}
          height={H}
          reserveX={W - 230}
          reserveY={H - 230}
          reflow={0}
          stagger={160}
          activity={<VoiceActivity tools={scripted} streaming hasText={false} />}
        />
        {/* Stand-in for the docked orb, so the keep-out quadrant is visible. */}
        <div style={{
          position: 'absolute', right: -40, bottom: -40, width: 230, height: 230,
          borderRadius: '50%', background: 'radial-gradient(circle, rgba(127,164,196,0.35), transparent 65%)',
        }} />
      </div>

      )}

      {/* The caption strip, three lines, riding its tail. */}
      <div style={{
        position: 'absolute', left: 0, right: 0, bottom: 0, padding: '0.4rem 240px 0.6rem 1.2rem',
        background: 'linear-gradient(to top, rgba(4,8,10,0.92) 42%, rgba(4,8,10,0))',
      }}>
        <div style={{
          maxWidth: 560, maxHeight: '4.65em', overflowY: 'auto', fontSize: '0.86rem',
          lineHeight: 1.55, whiteSpace: 'pre-wrap', scrollbarWidth: 'none',
        }}>
          {caption}
        </div>
      </div>
    </div>
  )
}

/* Board pictures are fetched through Igor's proxy, which needs the API config
 * off the chat store — so the harness supplies a minimal one. With no backend
 * running the fetch simply fails and every window renders without its photo,
 * which is exactly the degradation the real client is built to show. */
const STUB = {
  state: { config: { apiBase: 'http://localhost:8000', apiKey: 'dev-key' } },
  dispatch: () => {},
} as unknown as React.ContextType<typeof ChatContext>

/* The activity window reads the locale through useT, which is only available
 * under the settings provider — in the app that is always true, so this is a
 * harness concern rather than a component one. */
function Providers({ children }: { children: React.ReactNode }) {
  const settings = useSettingsProvider()
  return (
    <SettingsContext.Provider value={settings}>
      <ChatContext.Provider value={STUB}>{children}</ChatContext.Provider>
    </SettingsContext.Provider>
  )
}

createRoot(document.getElementById('root')!).render(
  <Providers><Harness /></Providers>,
)
