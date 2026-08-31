// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * The settings pane's vocabulary.
 *
 * Every tab is built from three shapes and nothing else: a labelled FIELD, a
 * ROW that states a setting in words with its control on the right, and a
 * SECTION heading — plus the two controls those rows carry (a switch and a
 * pill) and the service line the connection tabs use. Drawing from one set is
 * what makes eight panes read as one screen instead of eight separately-styled
 * forms.
 *
 * These live in their own module rather than in SettingsModal because the tabs
 * are separate components: ConfigTab importing a control back out of the modal
 * that renders it is an import cycle, and cycles in a component tree fail late
 * and confusingly.
 *
 * Radii come from CLASSES, never inline: the theme's blanket
 * `border-radius: 0 !important` outranks any inline value.
 */

/** Section heading — Condensed, sentence case, with the rule above it. */
export function SettingsSection({ title, first }: { title: string; first?: boolean }) {
  return (
    <div style={{
      borderTop: first ? 'none' : '1px solid rgba(255,255,255,0.07)',
      paddingTop: first ? 0 : 22, marginTop: first ? 0 : 4,
      fontFamily: 'var(--font-ui)', fontSize: '1.0625rem', fontWeight: 700,
      letterSpacing: '0.04em', color: 'var(--hb-text)',
    }}>
      {title}
    </div>
  )
}

/** A labelled control — label above, the control filling the width below. */
export function SettingsField({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode
}) {
  return (
    <div>
      <div style={{ fontSize: '0.845rem', color: 'var(--hb-text-dim)', marginBottom: 8 }}>
        {label}
      </div>
      {hint && (
        <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', marginBottom: 8, lineHeight: 1.5 }}>
          {hint}
        </div>
      )}
      {children}
    </div>
  )
}

/** A setting stated in words, with its control on the right. */
export function SettingsRow({ title, desc, children }: {
  title: string; desc?: string; children: React.ReactNode
}) {
  return (
    <div className="hb-tile" style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16,
      padding: '16px 18px',
      background: 'rgba(255,255,255,0.03)',
      border: '1px solid rgba(255,255,255,0.07)',
    }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: '0.9375rem', color: 'var(--hb-text)' }}>{title}</div>
        {desc && (
          <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', marginTop: 2 }}>{desc}</div>
        )}
      </div>
      <div style={{ flexShrink: 0 }}>{children}</div>
    </div>
  )
}

/** The deck's switch — a 46×26 track with a white knob. */
export function Switch({ on, onChange, disabled, title }: {
  on: boolean; onChange: (v: boolean) => void; disabled?: boolean; title?: string
}) {
  return (
    <button
      role="switch"
      aria-checked={on}
      disabled={disabled}
      title={title}
      onClick={() => !disabled && onChange(!on)}
      className="hb-round"
      style={{
        width: 46, height: 26, flexShrink: 0, position: 'relative',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.4 : 1,
        border: on ? 'none' : '1px solid rgba(255,255,255,0.12)',
        background: on ? 'var(--hb-cyan)' : 'rgba(255,255,255,0.06)',
        boxShadow: on ? 'inset 0 1px 3px rgba(0,0,0,0.2)' : 'none',
        transition: 'background 0.15s, opacity 0.15s',
      }}
    >
      <span style={{
        position: 'absolute', top: 2, left: on ? 22 : 2,
        width: 22, height: 22, borderRadius: '50%',
        background: on ? '#fff' : 'var(--hb-text-faint)',
        boxShadow: '0 2px 4px rgba(0,0,0,0.3)',
        transition: 'left 0.15s ease, background 0.15s',
      }} />
    </button>
  )
}

/** A pill action — the settings pane's only button shape. */
export function PillBtn({ onClick, children, tone = 'neutral', title }: {
  onClick?: () => void
  children: React.ReactNode
  tone?: 'neutral' | 'accent' | 'danger'
  title?: string
}) {
  const skin = tone === 'accent'
    ? { border: '1px solid rgba(var(--hb-accent-rgb),0.32)', background: 'rgba(var(--hb-accent-rgb),0.12)', color: 'var(--hb-cyan-bright)' }
    : tone === 'danger'
      ? { border: '1px solid rgba(216,72,60,0.35)', background: 'rgba(216,72,60,0.1)', color: '#e5897c' }
      : { border: '1px solid rgba(255,255,255,0.1)', background: 'var(--glass-sheen)', color: 'var(--hb-text)' }
  return (
    <button
      onClick={onClick}
      title={title}
      className="glass-round"
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 8,
        height: 32, padding: '0 14px', flexShrink: 0,
        fontFamily: 'var(--font-read)', fontSize: '0.845rem',
        cursor: 'pointer', ...skin,
      }}
    >
      {children}
    </button>
  )
}

/** A connected-service line: mark tile, name, state on the right. */
export function ServiceRow({ icon, tint, name, desc, children }: {
  icon: React.ReactNode
  /** Signature colour of the service, or undefined for a neutral tile. */
  tint?: string
  name: string
  desc?: string
  children: React.ReactNode
}) {
  return (
    <div className="hb-tile" style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 14,
      padding: '12px 16px',
      background: 'rgba(255,255,255,0.03)',
      border: '1px solid rgba(255,255,255,0.07)',
    }}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
        <span className="hb-tile" style={{
          width: 32, height: 32, flexShrink: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: tint
            ? `linear-gradient(160deg, ${tint}38, ${tint}0f)`
            : 'var(--glass-sheen)',
          border: `1px solid ${tint ? `${tint}52` : 'rgba(255,255,255,0.09)'}`,
          color: tint ?? 'var(--hb-text-dim)',
        }}>
          {icon}
        </span>
        <span style={{ minWidth: 0 }}>
          <span style={{ display: 'block', fontSize: '0.9375rem', color: 'var(--hb-text)' }}>{name}</span>
          {desc && (
            <span style={{ display: 'block', fontSize: '0.8125rem', color: 'var(--hb-text-faint)', marginTop: 2 }}>
              {desc}
            </span>
          )}
        </span>
      </span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>{children}</span>
    </div>
  )
}

/** "Connected", with the dot the whole deck uses for a live thing. */
export function LiveDot({ label = 'Connected' }: { label?: string }) {
  return (
    <span style={{
      display: 'flex', alignItems: 'center', gap: 8,
      fontSize: '0.845rem', color: '#8fdcb3',
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%', background: 'var(--hb-green)',
        boxShadow: '0 0 6px var(--hb-green)',
      }} />
      {label}
    </span>
  )
}

/** Shared look for a text input / textarea inside the settings pane. */
export const fieldStyle: React.CSSProperties = {
  width: '100%',
  background: 'rgba(255,255,255,0.03)',
  border: '1px solid rgba(255,255,255,0.09)',
  padding: '0 16px',
  height: 44,
  color: 'var(--hb-text)',
  fontSize: '0.9375rem',
  fontFamily: 'var(--font-read)',
  outline: 'none',
  transition: 'border-color 0.15s, background 0.15s',
  userSelect: 'text',
}
