import { useEffect, useState, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router'
import { api } from '../api'
import { FaceAvatar } from './FaceAvatar'

type Res = { id: string; canonical_name: string | null; tier: string; platforms: number; face: string | null }

/**
 * Cmd-K / Ctrl-K command palette — jump to any person by name or handle from
 * anywhere. Keyboard: arrows move, Enter opens, Esc closes.
 */
export function CommandPalette() {
  const nav = useNavigate()
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [results, setResults] = useState<Res[]>([])
  const [sel, setSel] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((o) => !o)
      } else if (e.key === 'Escape') {
        setOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (open) {
      setQ(''); setResults([]); setSel(0)
      setTimeout(() => inputRef.current?.focus(), 0)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    if (q.trim().length < 1) { setResults([]); return }
    const t = setTimeout(() => {
      api.searchEntities(q.trim()).then((r) => { setResults(r.results); setSel(0) }).catch(() => setResults([]))
    }, 150)
    return () => clearTimeout(t)
  }, [q, open])

  const go = useCallback((r: Res) => { setOpen(false); nav(`/entities/${r.id}`) }, [nav])

  if (!open) return null
  return (
    <div
      onClick={() => setOpen(false)}
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 100, display: 'flex', justifyContent: 'center', alignItems: 'flex-start', paddingTop: '12vh' }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ width: 'min(560px, 92vw)', background: 'var(--color-card, #17171c)', border: '1px solid var(--color-border, #2a2a33)', borderRadius: 12, overflow: 'hidden', boxShadow: '0 20px 60px rgba(0,0,0,0.5)' }}
      >
        <input
          ref={inputRef}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { setSel((s) => Math.min(results.length - 1, s + 1)); e.preventDefault() }
            else if (e.key === 'ArrowUp') { setSel((s) => Math.max(0, s - 1)); e.preventDefault() }
            else if (e.key === 'Enter' && results[sel]) go(results[sel])
          }}
          placeholder="Search people, handles…"
          style={{ width: '100%', padding: '14px 16px', background: 'transparent', border: 'none', outline: 'none', color: 'var(--color-fg, #e8e8ea)', fontSize: 15 }}
        />
        <div style={{ maxHeight: '50vh', overflowY: 'auto', borderTop: '1px solid var(--color-border, #2a2a33)' }}>
          {results.map((r, i) => (
            <div
              key={r.id}
              onClick={() => go(r)}
              onMouseEnter={() => setSel(i)}
              style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', cursor: 'pointer', background: i === sel ? 'var(--color-hover, #22222a)' : 'transparent' }}
            >
              <FaceAvatar url={r.face} name={r.canonical_name} size={30} />
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {r.canonical_name || r.id.slice(0, 8)}
                </div>
                <div style={{ fontSize: 11, color: 'var(--color-muted, #8a8a99)' }}>{r.tier} · {r.platforms} platforms</div>
              </div>
            </div>
          ))}
          {q.trim() && results.length === 0 && (
            <div style={{ padding: 14, fontSize: 13, color: 'var(--color-muted, #8a8a99)' }}>No matches</div>
          )}
        </div>
      </div>
    </div>
  )
}
