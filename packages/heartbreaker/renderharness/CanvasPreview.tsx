import { useEffect, useRef, useState } from 'react'
import VoiceCanvas from '../src/renderer/src/components/VoiceCanvas'
import MovableVoiceOrb from '../src/renderer/src/components/MovableVoiceOrb'
import { splitPanels, type VoicePanel } from '../src/renderer/src/lib/voicePanels'
import primeDiagram from '../../../app/profiles/markvi-diagram.svg?raw'

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
  const practice = fence('artifact | Predict the output', JSON.stringify({
    kind: 'practice', description: 'Trace each line, then check your prediction.',
    items: [
      { prompt: 'What does Java print?', code: 'int a = 12, b = 5;\nSystem.out.println(a / b + " " + a % b);', answer: '2 2',
        explanation: 'Integer division gives the quotient 2. The remainder is 12 − 5 × 2, which is also 2.' },
      { type: 'choice', prompt: 'How many times does the loop run?', code: 'int n = 505, count = 0;\nwhile (n > 0) { count++; n /= 10; }',
        options: ['2 passes', '3 passes', '4 passes', 'It never terminates'], answerIndex: 1,
        explanation: 'Each pass drops one digit. The values are 505, 50, 5, then 0.' },
      { prompt: 'What does the final print show?', code: 'int x = 0;\nwhile (x < 3) x += 2;\nSystem.out.println(x);', answer: '4',
        explanation: 'The loop enters with 0 and 2. After the second pass, 4 fails the condition.' },
    ],
  }))
  const explore = fence('artifact | Grade branch simulator', JSON.stringify({
    kind: 'explore', description: 'Move the score across a boundary to see which condition runs.',
    parameter: { label: 'Score', min: -10, max: 110, initial: 79, unit: 'pts' },
    cases: [
      { through: -1, label: 'Invalid', detail: 'The input guard rejects scores below zero.' },
      { through: 59, label: 'F', detail: 'Every grade condition fails, so the final else runs.' },
      { through: 69, label: 'D', detail: 'The first true condition is score >= 60.' },
      { through: 79, label: 'C', detail: 'The first true condition is score >= 70.' },
      { through: 89, label: 'B', detail: 'The first true condition is score >= 80.' },
      { through: 100, label: 'A', detail: 'The first true condition is score >= 90.' },
      { through: 110, label: 'Invalid', detail: 'The input guard rejects scores above 100 before grading.' },
    ],
  }))
  const source = [practice, explore,
    fence('svg | Prime check input guard', primeDiagram),
    fence('timeline | Tuesday timeline', '09:00 — Workplace Application I\n16:20 — Appointment at Etlik Hospital'),
    fixtures[2][1], fixtures[0][1], fence('chart | Attendance overview', JSON.stringify({ type: 'bar', xKey: 'course', series: [{ key: 'hours', label: 'Hours' }], data: [
    { course: 'Logistics', hours: 3 }, { course: 'Marketing Principles', hours: 7 }, { course: 'Data Structures', hours: 9 }, { course: 'Workplace Application', hours: 16 },
  ] })), fixtures[3][1], fixtures[1][1],
    fence('stat | Focus', 'THIS WEEK\nFriday morning\nAttend Marketing'),
    fence('stat | Coursework', 'MARKETING / MAR411\nTwo courses\nKeep the full names readable'),
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
