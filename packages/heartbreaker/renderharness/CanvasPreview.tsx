import { useEffect, useRef, useState } from 'react'
import VoiceCanvas from '../src/renderer/src/components/VoiceCanvas'
import MovableVoiceOrb from '../src/renderer/src/components/MovableVoiceOrb'
import { splitPanels, type VoicePanel } from '../src/renderer/src/lib/voicePanels'

const fence = (kind: string, body: string) => `\`\`\`${kind}\n${body}\n\`\`\``
const silence = () => 0
export default function CanvasPreview({ fixtures, onClose }: { fixtures: [string, string][]; onClose: () => void }) {
  const board = useRef<HTMLDivElement>(null)
  const [[width, height], setSize] = useState([1200, 700])
  const [reflow, setReflow] = useState(0)
  const [dock, setDock] = useState({ dx: 0, dy: 0, scale: 1 })
  const [commits, setCommits] = useState(0)
  const orbSize = Math.round(220 * dock.scale)
  const orbLeft = width - orbSize * .8 - 16 + dock.dx
  const orbTop = height - orbSize * .8 - 16 + dock.dy
  useEffect(() => {
    const el = board.current!
    const resize = () => setSize([el.clientWidth, el.clientHeight])
    const observer = new ResizeObserver(resize); observer.observe(el); resize()
    return () => observer.disconnect()
  }, [])
  const source = [fixtures[2][1], fixtures[0][1], fence('chart | Attendance overview', JSON.stringify({ type: 'bar', xKey: 'course', series: [{ key: 'hours', label: 'Hours' }], data: [
    { course: 'Logistics', hours: 3 }, { course: 'Marketing Principles', hours: 7 }, { course: 'Data Structures', hours: 9 }, { course: 'Workplace Application', hours: 16 },
  ] })), fixtures[3][1], fixtures[1][1],
    fence('stat | Focus', 'THIS WEEK\nFriday morning\nAttend Marketing'),
    fence('stat | Coursework', 'MARKETING / MAR411\nTwo courses\nKeep the full names readable'),
    fence('svg | Tuesday timeline', '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1100 190"><path d="M40 90H1060" stroke="#36abca"/><text x="40" y="60" font-size="14" fill="#dbe6ec">09:00 — Workplace Application I</text><text x="520" y="130" font-size="14" fill="#dbe6ec">16:20 — Appointment at Etlik Hospital</text><circle cx="520" cy="90" r="5" fill="#36abca"/></svg>'),
    fence('card | Appointment details', 'Oncology — Internal Medicine\nTime: 16:20\nLocation: Varlık, Halil Sezai Erkut Cd. No:5, Yenimahalle\nNote: Full address remains readable.'),
  ].join('\n\n')
  const panels: VoicePanel[] = [
    ...splitPanels(source),
    { id: 'narration', kind: 'narration', title: 'NARRATION_', source: 'Start of the briefing.\n\nFriday morning is non-negotiable: attend Marketing, then use the review gap before Logistics.\n\n' + 'A readable canvas can continue below the screen. Scroll to explore without shrinking the evidence.\n\n'.repeat(6) },
  ]
  return <div style={{ position: 'fixed', inset: 0, background: 'var(--hb-void)', color: 'var(--hb-text)' }}>
    <header className="hb-canvas-preview-header" style={{ height: 64, display: 'flex', alignItems: 'center', gap: 12, padding: '0 12px' }}>
      <strong style={{ fontSize: 14 }}>Canvas</strong><span style={{ color: 'var(--hb-text-dim)', fontSize: 13 }}>Scroll, resize, or expand a panel</span>
      <button className="hb-btn" onClick={() => setReflow(n => n + 1)}>Reflow</button>
      <span data-orb-commits>{commits} saved gestures</span>
      <button className="hb-btn" onClick={onClose}>Chat preview</button>
    </header>
    <div ref={board} style={{ position: 'absolute', top: 64, bottom: 12, left: 0, right: 0 }}>
      <VoiceCanvas panels={panels} width={width} height={height} reserveX={orbLeft} reserveY={orbTop} reflow={reflow} stagger={0} />
      <MovableVoiceOrb left={orbLeft} top={orbTop} size={orbSize} dock={dock} docked animate={false}
        title="Drag to move · scroll to scale" onDock={d => { setDock(d); setCommits(n => n + 1) }} state="idle" amplitude={silence} zoom={1.4} />
    </div>
  </div>
}
