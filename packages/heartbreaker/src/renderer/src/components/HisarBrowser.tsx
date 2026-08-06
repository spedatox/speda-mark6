/**
 * HisarBrowser — browse the owner's Hisar cloud vault and pick a directory.
 *
 * Replaces the native OS file dialog for the Optimus/Forge workspace picker.
 * The owner navigates the vault (the same one their agents see) and selects a
 * folder; that folder's path becomes the working directory sent to the Forge.
 *
 * Both Mark VI and the Forge are connected to Hisar, so a path picked here is
 * a shared namespace both sides understand without mapping drive letters or
 * translating paths between machines.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { AppConfig } from '../lib/types'
import { authHeaders } from '../lib/api'

interface Props {
  config: AppConfig
  current: string     // currently selected Hisar path (empty = none)
  onSelect: (path: string | null) => void  // null = cancelled
  onClose: () => void
}

interface DirListing {
  path: string
  dirs: string[]
}

/**
 * Build the breadcrumb segments from a vault path.
 * "/" → []; "/Projects" → ["Projects"]; "/Projects/site" → ["Projects", "site"]
 */
function breadcrumbs(path: string): { label: string; target: string }[] {
  const parts = path.replace(/\/+$/, '').split('/').filter(Boolean)
  const crumbs: { label: string; target: string }[] = [{ label: 'Vault', target: '/' }]
  for (let i = 0; i < parts.length; i++) {
    crumbs.push({ label: parts[i], target: '/' + parts.slice(0, i + 1).join('/') })
  }
  return crumbs
}

export default function HisarBrowser({ config, current, onSelect, onClose }: Props) {
  const [path, setPath] = useState(current || '/')
  const [listing, setListing] = useState<DirListing | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchDirs = useCallback(async (p: string) => {
    setLoading(true)
    setError(null)
    try {
      const url = `${config.apiBase}/hisar/dirs?path=${encodeURIComponent(p)}`
      const res = await fetch(url, { headers: authHeaders(config) })
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
        throw new Error((body as { detail?: string }).detail || `HTTP ${res.status}`)
      }
      const data = (await res.json()) as DirListing
      setListing(data)
      setPath(data.path)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to reach Hisar')
      setListing(null)
    } finally {
      setLoading(false)
    }
  }, [config])

  useEffect(() => { fetchDirs(path) }, [fetchDirs, path])

  const navigate = (dir: string) => {
    const parent = path.replace(/\/+$/, '')
    setPath(parent + '/' + dir)
  }

  const goUp = () => {
    const parent = path.replace(/\/+$/, '').replace(/\/[^/]+$/, '') || '/'
    setPath(parent)
  }

  const selectThis = () => onSelect(path)

  const crumbs = useMemo(() => breadcrumbs(path), [path])

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'rgba(0,0,0,0.55)',
      backdropFilter: 'blur(6px)', WebkitBackdropFilter: 'blur(6px)',
    }}>
      <div style={{
        width: 420, maxWidth: '92vw', maxHeight: '70vh',
        background: 'var(--hb-surface, #0d1117)',
        border: '1px solid var(--hb-border, rgba(255,255,255,0.08))',
        borderRadius: 10,
        display: 'flex', flexDirection: 'column',
        boxShadow: '0 0 40px rgba(0,0,0,0.5)',
      }}>
        {/* Title bar */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0.7rem 0.9rem',
          borderBottom: '1px solid var(--hb-border, rgba(255,255,255,0.06))',
          fontFamily: "'Rajdhani', sans-serif",
          fontSize: '0.78rem', fontWeight: 700, letterSpacing: '0.16em',
          textTransform: 'uppercase',
        }}>
          <span style={{ color: 'var(--hb-cyan)' }}>Hisar — Choose Workspace</span>
          <button onClick={onClose} style={{
            border: 'none', background: 'transparent', cursor: 'pointer',
            color: 'var(--hb-text-faint)', fontSize: '1.1rem', padding: '0 0.2rem',
          }}>✕</button>
        </div>

        {/* Breadcrumbs */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: '0.25rem',
          padding: '0.4rem 0.9rem', overflow: 'hidden',
          borderBottom: '1px solid var(--hb-border, rgba(255,255,255,0.04))',
        }}>
          {crumbs.map((c, i) => (
            <span key={c.target} style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
              {i > 0 && (
                <span style={{ color: 'var(--hb-text-faint)', fontSize: '0.55rem' }}>›</span>
              )}
              <button
                onClick={() => setPath(c.target)}
                style={{
                  border: 'none', background: i === crumbs.length - 1 ? 'rgba(255,255,255,0.06)' : 'transparent',
                  cursor: 'pointer', padding: '0.15rem 0.35rem', borderRadius: 4,
                  fontFamily: 'var(--font-mono)', fontSize: '0.62rem', letterSpacing: '0.04em',
                  color: i === crumbs.length - 1 ? 'var(--hb-text)' : 'var(--hb-text-dim)',
                  whiteSpace: 'nowrap',
                }}
              >
                {c.label}
              </button>
            </span>
          ))}
        </div>

        {/* Directory listing */}
        <div style={{
          flex: 1, overflow: 'auto', padding: '0.3rem 0',
          minHeight: 180, maxHeight: 360,
        }}>
          {loading && (
            <div style={{
              padding: '2rem', textAlign: 'center',
              fontFamily: 'var(--font-mono)', fontSize: '0.66rem',
              color: 'var(--hb-text-faint)',
            }}>
              Reading {path}…
            </div>
          )}
          {error && (
            <div style={{
              padding: '1.5rem 1rem', textAlign: 'center',
              fontFamily: 'var(--font-mono)', fontSize: '0.64rem',
              color: 'var(--hb-amber-bright)',
            }}>
              {error}
              <br />
              <button
                onClick={() => fetchDirs(path)}
                style={{
                  marginTop: '0.6rem', border: '1px solid var(--hb-border)',
                  background: 'transparent', cursor: 'pointer',
                  padding: '0.2rem 0.6rem', borderRadius: 4,
                  fontFamily: 'var(--font-mono)', fontSize: '0.6rem',
                  color: 'var(--hb-text-dim)',
                }}
              >
                Retry
              </button>
            </div>
          )}
          {!loading && !error && listing && (
            <>
              {/* Parent directory — skip at vault root */}
              {path !== '/' && (
                <button onClick={goUp} style={{
                  display: 'flex', alignItems: 'center', gap: '0.5rem',
                  width: '100%', border: 'none', background: 'transparent',
                  cursor: 'pointer', padding: '0.4rem 1rem',
                  fontFamily: 'var(--font-mono)', fontSize: '0.66rem',
                  color: 'var(--hb-text-dim)',
                }}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>
                  </svg>
                  ..
                </button>
              )}
              {listing.dirs.length === 0 && (
                <div style={{
                  padding: '1.5rem 1rem', textAlign: 'center',
                  fontFamily: 'var(--font-mono)', fontSize: '0.64rem',
                  color: 'var(--hb-text-faint)',
                }}>
                  No subdirectories — select this folder
                </div>
              )}
              {listing.dirs.map(dir => (
                <button
                  key={dir}
                  onClick={() => navigate(dir)}
                  onDoubleClick={selectThis}
                  style={{
                    display: 'flex', alignItems: 'center', gap: '0.5rem',
                    width: '100%', border: 'none', background: 'transparent',
                    cursor: 'pointer', padding: '0.35rem 1rem',
                    fontFamily: 'var(--font-mono)', fontSize: '0.66rem',
                    color: 'var(--hb-text)',
                    textAlign: 'left',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.04)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                    strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                    style={{ flexShrink: 0, color: 'var(--hb-amber)' }}>
                    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
                  </svg>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {dir}
                  </span>
                </button>
              ))}
            </>
          )}
        </div>

        {/* Footer — action row */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0.55rem 0.9rem',
          borderTop: '1px solid var(--hb-border, rgba(255,255,255,0.06))',
        }}>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: '0.58rem',
            color: 'var(--hb-text-faint)', letterSpacing: '0.04em',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            maxWidth: '60%',
          }}>
            {path}
          </span>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              onClick={onClose}
              style={{
                border: '1px solid var(--hb-border)', background: 'transparent',
                cursor: 'pointer', padding: '0.25rem 0.7rem', borderRadius: 5,
                fontFamily: "'Rajdhani', sans-serif", fontSize: '0.64rem',
                fontWeight: 600, letterSpacing: '0.08em',
                color: 'var(--hb-text-dim)',
              }}
            >
              Cancel
            </button>
            <button
              onClick={selectThis}
              style={{
                border: 'none', background: 'var(--hb-cyan)',
                cursor: 'pointer', padding: '0.25rem 0.9rem', borderRadius: 5,
                fontFamily: "'Rajdhani', sans-serif", fontSize: '0.64rem',
                fontWeight: 700, letterSpacing: '0.08em',
                color: '#000',
              }}
            >
              Select {path === '/' ? 'Vault' : path.split('/').pop()}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
