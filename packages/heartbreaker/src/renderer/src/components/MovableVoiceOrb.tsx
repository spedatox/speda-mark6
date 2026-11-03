// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useLayoutEffect, useRef } from 'react'
import VoiceOrb, { type OrbState } from './VoiceOrb'
import { createOrbInteraction, type OrbDock } from '../lib/orbInteraction'

interface Props {
  left: number; top: number; size: number; dock: OrbDock; docked: boolean
  animate: boolean; onDock: (dock: OrbDock) => void; title?: string
  state: OrbState; amplitude: () => number; spectrum?: (out: Float32Array) => void
  inputLevel?: () => number; zoom: number
}

export default function MovableVoiceOrb(props: Props) {
  const host = useRef<HTMLDivElement>(null)
  const latest = useRef(props); latest.current = props
  const controller = useRef<ReturnType<typeof createOrbInteraction>>()
  const wasDocked = useRef(props.docked)

  useLayoutEffect(() => {
    controller.current?.sync(props.dock)
    if (wasDocked.current === props.docked) controller.current?.refresh()
  }, [props.dock, props.left, props.top, props.size, props.docked])
  useEffect(() => {
    const el = host.current!
    const interaction = createOrbInteraction({
      dock: latest.current.dock,
      frame: fn => window.requestAnimationFrame(fn), cancelFrame: id => window.cancelAnimationFrame(id),
      delay: (fn, ms) => window.setTimeout(fn, ms), cancelDelay: id => window.clearTimeout(id),
      commit: dock => latest.current.onDock(dock),
      paint: (dock, active) => {
        const base = latest.current
        const factor = dock.scale / base.dock.scale
        const difference = base.size * (factor - 1)
        const x = base.left + dock.dx - base.dock.dx - difference * .8
        const y = base.top + dock.dy - base.dock.dy - difference * .8
        el.style.transition = 'none'
        el.style.transform = `translate3d(${x}px,${y}px,0) scale(${factor})`
        el.style.cursor = active ? 'grabbing' : 'grab'
      },
    })
    controller.current = interaction
    let pointer: number | null = null
    const move = (e: PointerEvent) => { if (e.pointerId === pointer) interaction.move(e.clientX, e.clientY) }
    const end = (e: PointerEvent) => {
      if (e.pointerId !== pointer) return
      pointer = null; interaction.end()
    }
    const begin = (e: PointerEvent) => {
      if (!latest.current.docked || e.button !== 0 || pointer !== null) return
      e.preventDefault(); e.stopPropagation(); pointer = e.pointerId
      el.setPointerCapture(e.pointerId); interaction.begin(e.clientX, e.clientY)
      el.style.transition = 'none'; el.style.cursor = 'grabbing'
    }
    const wheel = (e: WheelEvent) => {
      if (!latest.current.docked) return
      e.preventDefault(); e.stopPropagation(); interaction.zoom(e.deltaY, e.deltaMode)
    }
    el.addEventListener('pointerdown', begin)
    el.addEventListener('pointermove', move)
    el.addEventListener('pointerup', end)
    el.addEventListener('pointercancel', end)
    el.addEventListener('lostpointercapture', end)
    el.addEventListener('wheel', wheel, { passive: false })
    return () => {
      el.removeEventListener('pointerdown', begin); el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerup', end); el.removeEventListener('pointercancel', end)
      el.removeEventListener('lostpointercapture', end); el.removeEventListener('wheel', wheel)
      interaction.dispose(); controller.current = undefined
    }
  }, [])

  // Only the automatic dock/undock flight eases. Hand gestures track directly.
  const flight = props.animate && wasDocked.current !== props.docked
  useLayoutEffect(() => { wasDocked.current = props.docked }, [props.docked])
  return <div ref={host} data-orb-dock title={props.title} style={{
    position: 'absolute', zIndex: 3, left: 0, top: 0,
    width: props.size, height: props.size, transformOrigin: '0 0',
    transform: `translate3d(${props.left}px,${props.top}px,0) scale(1)`,
    transition: flight ? 'transform .5s cubic-bezier(.4,0,.2,1)' : 'none',
    willChange: 'transform', touchAction: 'none',
    pointerEvents: props.docked ? 'auto' : 'none', cursor: props.docked ? 'grab' : 'default',
  }}>
    <VoiceOrb state={props.state} amplitude={props.amplitude} spectrum={props.spectrum} inputLevel={props.inputLevel} zoom={props.zoom} />
  </div>
}
