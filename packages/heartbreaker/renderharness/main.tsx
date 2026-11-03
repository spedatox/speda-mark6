import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { TextSegment } from '../src/renderer/src/components/Message'
import { SettingsContext, useSettingsProvider } from '../src/renderer/src/store/settings'
import '../src/renderer/src/theme/heartbreaker.css'
import CanvasPreview from './CanvasPreview'
import MentionPreview from './MentionPreview'

const fence = (language: string, body: string) => `\`\`\`${language}\n${body}\n\`\`\``
const calendar = { title: 'Your week', range: '25–31 Oct 2026', days: [
  { date: '2026-10-25', events: [{ time: '17:00', title: 'Siberay Board Meeting', location: 'Bahçelievler' }] },
  { date: '2026-10-26', events: [{ time: '13:00', title: 'Arel Tarım Reel Upload' }, { time: '15:00', title: 'MIS 243 — General Accounting', location: 'A105' }] },
  { date: '2026-10-27', events: [{ time: '09:00', end: '14:50', title: 'WAP 225 — Workplace Application I', location: 'OSTİM' }, { time: '16:20', title: 'Oncology appointment — Internal Medicine', location: 'Ankara Hospital' }] },
  { date: '2026-10-28', events: [{ time: '09:00', title: 'MIS 215 — Statistics I', location: 'Lab 221' }] },
  { date: '2026-10-29', events: [{ time: '09:00', title: 'MIS 231 — Data Structures and Algorithms', location: 'Classroom 301' }, { time: '13:00', title: 'Arel Tarım Reel Upload' }] },
  { date: '2026-10-30', events: [{ time: '09:00', title: 'MIS 251 — Marketing Principles and E-Commerce', location: 'Classroom 102' }, { time: '14:00', title: 'MAR 411 — Supply Chain Management and Logistics', location: 'Classroom 204' }] },
  { date: '2026-10-31', events: [] },
] }
const calculator = `<!doctype html><html><head><style>.calc{display:grid;gap:16px;padding:16px 4px}.value{font-size:32px;font-weight:600}label{display:flex;justify-content:space-between;gap:12px}</style></head><body>
<section class="calc" aria-label="Study calculator"><label for="minutes">Daily study time <span><output id="current">30</output> min</span></label><input id="minutes" type="range" min="10" max="120" step="5" value="30"><div><output id="total" class="value" aria-live="polite"></output> hours / week</div><button id="details">Show details</button><div id="more" hidden>Seven days of consistent study. Move the slider to explore a different daily commitment.</div></section>
<script>const input=document.getElementById('minutes');function update(){document.getElementById('current').value=input.value;document.getElementById('total').value=(+input.value*7/60).toFixed(1);document.getElementById('more').hidden=+input.value<60}input.oninput=update;document.getElementById('details').onclick=()=>document.getElementById('more').hidden=!document.getElementById('more').hidden;update();</script></body></html>`
const fixtures: [string, string][] = [
  ['Weekly plan', fence('table | The plan', 'DAY | THE PLAN\nMON | Sleep-in to ~10:00 · 13:00 reel · 15:00–17:50 Accounting · 45-min same-day review\nTUE | 09:00–14:50 Workplace App · 16:20 Etlik hospital · evening: rest, early night\nWED | 09:00–11:50 Statistics · 14:00–16:00 stickers (3–5 designs) · 16:00–17:30 DSA prep\nTHU | 09:00–12:50 Data Structures · 13:00 reel · 15:00–16:30 Logistics reading\nFRI | 09:00–11:50 Marketing · 12:00–14:00 review gap · 14:00–16:50 Logistics\nSAT | Morning: Statistics problem set · afternoon open\nSUN | Buffer day · plan next week · sleep reset')],
  ['Tonight', fence('text', 'timeline | Rest of tonight\nNow — Lights low, phone down, no more caffeine\n~05:00 — Drift off whenever it comes, zero pressure\n14:30 — Deliberate wake-up, food, shower\n16:30 — Leave for Bahçelievler\n17:00 — Board meeting')],
  ['Calendar', fence('calendar', JSON.stringify(calendar))],
  ['Try it', fence('html', calculator)],
  ['Two independent widgets', fence('html', '<label for="n">Second slider</label><input id="n" type="range" value="15"><output id="v">15</output><script>n.oninput=()=>v.value=n.value</script>')],
  ['Markdown table', '| Item | Value |\n| --- | --- |\n| **Daily study** | 30 min |\n| `a \\| b` | Escaped \\| pipe |'],
  ['SVG', fence('svg', '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 700 100"><path d="M20 50H680" stroke="#36abca"/><text x="30" y="38" fill="#dbe6ec" font-size="16">Readable, responsive SVG</text></svg>')],
  ['Actual source code', fence('js', 'const x = "a | b";\nconsole.log(x)')],
]
function Harness() {
  const settings = useSettingsProvider()
  const [width, setWidth] = useState(760)
  const [pending, setPending] = useState(false)
  const [canvas, setCanvas] = useState(window.location.hash === '#canvas')
  if (window.location.hash === '#mentions') return <SettingsContext.Provider value={settings}><MentionPreview /></SettingsContext.Provider>
  return <SettingsContext.Provider value={settings}>
    {canvas ? <CanvasPreview fixtures={fixtures} onClose={() => setCanvas(false)} /> : <main style={{ maxWidth: width, width: 'calc(100% - 32px)', margin: '32px 16px', color: 'var(--hb-text)' }}>
      <h1 style={{ fontSize: 24, marginBottom: 16 }}>Inline rendering</h1>
      <nav style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>{[320, 760, 1440].map(w => <button key={w} onClick={() => setWidth(w)}>{w}px</button>)}<button onClick={() => setPending(p => !p)}>{pending ? 'Complete widget' : 'Partial widget'}</button><button onClick={() => setCanvas(true)}>Canvas preview</button></nav>
      {fixtures.map(([title, text]) => <section key={title}><h2 style={{ fontSize: 17, marginTop: 32 }}>{title}</h2><div className="prose"><TextSegment text={text} /></div></section>)}
      <section><h2>Streaming completion</h2><div className="prose"><TextSegment text={pending ? '\`\`\`html\n<button>Still writing</button><script>throw new Error("Must never execute")' : fence('html', '<p>Completed widgets render once their fence closes.</p>')} /></div></section>
    </main>}
  </SettingsContext.Provider>
}
createRoot(document.getElementById('root')!).render(<Harness />)
