import { useEffect, useState, useRef, useCallback } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { api, TriageData, ReviewCandidate } from '../api'
import { FaceAvatar } from '../components/FaceAvatar'

/**
 * Triage home — the investigation workspace landing. Three lanes (merge
 * decisions, change alerts as evidence, new high-value entities) + a one-row
 * coverage strip. Keyboard-driven on the merge lane: j/k move, e open, m merge,
 * x dismiss, s snooze.
 */
export default function TriagePage() {
  const nav = useNavigate()
  const [data, setData] = useState<TriageData | null>(null)
  const [sel, setSel] = useState(0)
  const [msg, setMsg] = useState('')
  const [hidden, setHidden] = useState<Set<string>>(new Set()) // local snooze/dismiss
  const rowRefs = useRef<(HTMLDivElement | null)[]>([])

  const load = useCallback(() => {
    api.getTriage().then((d) => { setData(d); setSel(0) }).catch(() => setData(null))
  }, [])
  useEffect(() => { load() }, [load])

  const key = (c: ReviewCandidate) => c.entity_a + c.entity_b
  const candidates = (data?.merge_candidates ?? []).filter((c) => !hidden.has(key(c)))

  const doMerge = useCallback(async (c: ReviewCandidate) => {
    try { await api.mergeEntities([c.entity_a, c.entity_b], 'Triage merge'); setMsg(`Merged ${c.name_a || ''}`); load() }
    catch (e: any) { setMsg(`Merge failed: ${e.message}`) }
  }, [load])

  const doDismiss = useCallback(async (c: ReviewCandidate) => {
    try { await api.dismissMatch(c.entity_a, c.entity_b); setMsg('Dismissed — labeled different'); setHidden((s) => new Set(s).add(key(c))) }
    catch (e: any) { setMsg(`Dismiss failed: ${e.message}`) }
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      const list = candidates
      if (list.length === 0) return
      const i = Math.min(sel, list.length - 1)
      if (e.key === 'j') setSel((s) => Math.min(list.length - 1, s + 1))
      else if (e.key === 'k') setSel((s) => Math.max(0, s - 1))
      else if (e.key === 'e') nav(`/entities/${list[i].entity_b}`)
      else if (e.key === 'm') doMerge(list[i])
      else if (e.key === 'x') doDismiss(list[i])
      else if (e.key === 's') setHidden((h) => new Set(h).add(key(list[i])))
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [candidates, sel, nav, doMerge, doDismiss])

  useEffect(() => { rowRefs.current[sel]?.scrollIntoView({ block: 'nearest' }) }, [sel])

  if (!data) return <div className="empty-state">Loading…</div>
  const cov = data.coverage

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-1 rounded-lg border border-border bg-card px-4 py-2 text-sm">
        <span><b>{cov.entities.toLocaleString()}</b> <span className="text-muted">entities</span></span>
        <span><b>{cov.with_faces_pct}%</b> <span className="text-muted">with faces</span></span>
        <span><b>{cov.multi_platform_pct}%</b> <span className="text-muted">multi-platform</span></span>
        <span><b>{cov.merge_backlog}</b> <span className="text-muted">merge backlog</span></span>
        <span><b>{cov.unread_alerts.toLocaleString()}</b> <span className="text-muted">unread alerts</span></span>
        <span className="ml-auto text-xs text-muted">j/k move · e open · m merge · x dismiss · s snooze</span>
      </div>

      {msg && <div className="mb-2 text-sm text-muted">{msg}</div>}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <div className="mb-2 text-sm font-semibold">Merge decisions <span className="text-muted">({candidates.length})</span></div>
          {candidates.length === 0 ? (
            <div className="empty-state">Backlog clear 🎉</div>
          ) : (
            <div className="flex flex-col gap-1.5">
              {candidates.map((c, i) => (
                <div
                  key={key(c)}
                  ref={(el) => { rowRefs.current[i] = el }}
                  onClick={() => setSel(i)}
                  className={`flex items-center gap-3 rounded-lg border bg-card p-2.5 ${i === sel ? 'border-accent' : 'border-border'}`}
                >
                  <FaceAvatar url={c.face_a} name={c.name_a} size={40} />
                  <FaceAvatar url={c.face_b} name={c.name_b} size={40} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">
                      {c.name_a || c.entity_a.slice(0, 8)} <span className="text-muted">&harr;</span> {c.name_b || c.entity_b.slice(0, 8)}
                    </div>
                    <div className="truncate text-xs text-muted">
                      {Math.round((c.score ?? 0) * 100)}% · {c.cross_platform ? 'cross-platform' : 'same-platform'}
                      {c.signals.length > 0 && ' · ' + c.signals.map((s) => s.type).join(', ')}
                    </div>
                  </div>
                  <div className="flex gap-1">
                    <button onClick={(e) => { e.stopPropagation(); doMerge(c) }}>Same</button>
                    <button onClick={(e) => { e.stopPropagation(); doDismiss(c) }} style={{ borderColor: 'var(--color-orange)', color: 'var(--color-orange)' }}>Not</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-4">
          <div>
            <div className="mb-2 flex items-center justify-between text-sm font-semibold">
              <span>Change alerts</span>
              <Link to="/alerts" className="text-xs text-muted">all &rarr;</Link>
            </div>
            <div className="flex flex-col gap-1.5">
              {data.alerts.map((a) => (
                <Link
                  key={a.id}
                  to={a.entity_id ? `/entities/${a.entity_id}` : '/alerts'}
                  className="flex items-center gap-2 rounded-lg border border-border bg-card p-2 hover:bg-hover"
                >
                  {a.entity_id && <FaceAvatar url={a.face} name={a.entity_name} size={28} />}
                  <div className="min-w-0">
                    <div className="truncate text-xs font-medium">{a.title || a.alert_type}</div>
                    <div className="truncate text-xs text-muted">{a.entity_name || ''}{a.detail ? ' · ' + a.detail : ''}</div>
                  </div>
                </Link>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-2 text-sm font-semibold">New high-value</div>
            <div className="flex flex-wrap gap-1.5">
              {data.new_entities.map((n) => (
                <Link
                  key={n.id}
                  to={`/entities/${n.id}`}
                  className="flex items-center gap-1.5 rounded-full border border-border bg-card py-1 pl-1 pr-2.5 text-xs hover:bg-hover"
                >
                  <FaceAvatar url={n.face} name={n.canonical_name} size={22} />
                  <span className="max-w-[120px] truncate">{n.canonical_name || n.id.slice(0, 8)}</span>
                  <span className="text-muted">{n.platforms}p</span>
                </Link>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
