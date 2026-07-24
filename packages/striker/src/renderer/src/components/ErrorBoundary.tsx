import { Component, type ReactNode } from 'react'

/**
 * Keeps one bad render block from taking down the whole transcript.
 *
 * A malformed chart, map or code block should degrade to a visible marker in
 * place, not a blank window — the rest of the conversation is still worth
 * reading. `label` names the block that failed so the marker says which one.
 */
export default class ErrorBoundary extends Component<
  { children: ReactNode; label?: string },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    console.error(
      `[ErrorBoundary${this.props.label ? ' ' + this.props.label : ''}]`,
      error,
      info.componentStack,
    )
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div style={{
        margin: '0.75rem 0',
        padding: '0.6rem 0.75rem',
        background: 'rgba(200,74,58,0.09)',
        border: '1px solid rgba(200,74,58,0.35)',
        fontFamily: "'Rajdhani', sans-serif",
        fontSize: '0.72rem',
        letterSpacing: '0.05em',
        color: '#c84a3a',
      }}>
        {this.props.label ?? 'BLOCK'} // RENDER FAILED
        <div style={{ color: 'var(--hb-text-faint)', marginTop: 4 }}>
          {String(error.message ?? error).slice(0, 160)}
        </div>
      </div>
    )
  }
}
