// SPDX-License-Identifier: AGPL-3.0-or-later
import { useId, useMemo, useState } from 'react'
import { useT } from '../lib/i18n'
import { answerMatches, parseNativeArtifact, type Explore, type Practice } from '../lib/nativeArtifacts'
import { useRenderSurface } from '../lib/renderSurface'
import CodeBlock from './CodeBlock'

type Attempt = { text: string; result?: 'correct' | 'retry' | 'revealed' }

function PracticeLab({ spec }: { spec: Practice }) {
  const t = useT().artifact
  const [index, setIndex] = useState(0)
  const [attempts, setAttempts] = useState<Record<number, Attempt>>({})
  const field = useId()
  const item = spec.items[index]
  const attempt = attempts[index] ?? { text: '' }
  const solved = Object.values(attempts).filter(value => value.result === 'correct').length
  const update = (value: Attempt) => setAttempts(previous => ({ ...previous, [index]: value }))
  const shown = attempt.result === 'correct' || attempt.result === 'revealed'
  return <>
    <div className="hb-lab-progress">
      <div className="hb-lab-steps" role="group" aria-label={t.questions}>
        {spec.items.map((_, i) => <button type="button" key={i} aria-current={i === index ? 'step' : undefined}
          aria-label={`${t.question} ${i + 1}${attempts[i]?.result === 'correct' ? ` · ${t.correct}` : ''}`}
          data-complete={attempts[i]?.result === 'correct'} onClick={() => setIndex(i)}>{String(i + 1).padStart(2, '0')}</button>)}
      </div>
      <span className="hb-lab-meta">{solved}/{spec.items.length} {t.solved}</span>
    </div>
    <div className="hb-lab-work">
      <h4>{item.prompt}</h4>
      {item.code && <pre className="hb-lab-code" tabIndex={0}><code>{item.code}</code></pre>}
      <form className="hb-lab-answer" onSubmit={event => {
        event.preventDefault()
        if (attempt.text.trim()) update({ ...attempt, result: answerMatches(attempt.text, item.answer) ? 'correct' : 'retry' })
      }}>
        <div><label htmlFor={field}>{t.yourAnswer}</label>
          <textarea id={field} rows={1} maxLength={240} value={attempt.text} placeholder={t.enterAnswer}
            onChange={event => update({ text: event.target.value })} /></div>
        <button type="submit" className="hb-lab-primary" disabled={!attempt.text.trim()}>{t.check}</button>
      </form>
      <div aria-live="polite">
        {attempt.result === 'retry' && <div className="hb-lab-result" data-result="retry">
          <span>{t.retry}</span>
          <button type="button" onClick={() => update({ ...attempt, result: 'revealed' })}>{t.reveal}</button>
        </div>}
        {shown && <div className="hb-lab-result" data-result={attempt.result}>
          <strong>{attempt.result === 'correct' ? t.correct : t.solution}</strong>
          <code>{item.answer}</code>
          <p>{item.explanation}</p>
        </div>}
      </div>
    </div>
    <div className="hb-lab-footer">
      <button type="button" disabled={!Object.keys(attempts).length} onClick={() => { setAttempts({}); setIndex(0) }}>{t.reset}</button>
      {index < spec.items.length - 1 && <button type="button" onClick={() => setIndex(index + 1)}>{t.next} <span aria-hidden>→</span></button>}
    </div>
  </>
}

function ExploreLab({ spec }: { spec: Explore }) {
  const t = useT().artifact
  const { label, min, max, initial, unit } = spec.parameter
  const [value, setValue] = useState(initial)
  const slider = useId()
  const active = spec.cases.findIndex(step => value <= step.through)
  const format = (number: number) => unit ? `${number} ${unit}` : String(number)
  return <>
    <div className="hb-lab-explore">
      <div className="hb-lab-parameter">
        <div className="hb-lab-parameter-label"><label htmlFor={slider}>{label}</label><output htmlFor={slider}>{format(value)}</output></div>
        <input id={slider} type="range" min={min} max={max} value={value} aria-valuetext={format(value)}
          onChange={event => setValue(Number(event.target.value))} />
        <div className="hb-lab-scale"><span>{min}</span><span>{max}</span></div>
      </div>
      <div className="hb-lab-live" aria-live="polite" aria-atomic="true">
        <span className="hb-lab-meta">{t.activeOutcome}</span>
        <strong>{spec.cases[active].label}</strong>
        <p>{spec.cases[active].detail}</p>
      </div>
    </div>
    <ol className="hb-lab-bands" aria-label={t.outcomes}>
      {spec.cases.map((step, i) => {
        const lower = i === 0 ? min : spec.cases[i - 1].through + 1
        return <li key={step.through} aria-current={active === i ? 'true' : undefined}>
          <span className="hb-lab-band-range">{lower === step.through ? lower : `${lower} – ${step.through}`}</span>
          <span>{step.label}</span>
        </li>
      })}
    </ol>
    <div className="hb-lab-footer"><button type="button" disabled={value === initial} onClick={() => setValue(initial)}>{t.reset}</button></div>
  </>
}

export default function NativeArtifact({ source, title }: { source: string; title?: string }) {
  const t = useT().artifact
  const { canvas } = useRenderSurface()
  const spec = useMemo(() => parseNativeArtifact(source), [source])
  if (!spec) return <details className="hb-lab-fallback"><summary>{t.invalid}</summary><CodeBlock language="json">{source}</CodeBlock></details>
  const practice = spec.kind === 'practice'
  const name = practice ? t.practice : t.explore
  return <section className="hb-native-lab" data-canvas={canvas} aria-label={title ? `${name} · ${title}` : name}>
    <header className="hb-lab-header">
      <span className="hb-lab-emblem" aria-hidden>{practice ? '↳' : '⌁'}</span>
      <div><div className="hb-lab-meta">{name}</div>
        {!canvas && title && <h3>{title}</h3>}
        <p>{spec.description}</p>
      </div>
    </header>
    {practice ? <PracticeLab key={source} spec={spec} /> : <ExploreLab key={source} spec={spec} />}
  </section>
}
