import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { api, ReviewCandidate } from '../api'
import { FaceAvatar } from '../components/FaceAvatar'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState } from '../components/ui/EmptyState'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { Confidence } from '../components/ui/Confidence'
import { Card } from '../components/ui/Card'
import { LABELS } from '../lib/labels'

/** Platform accounts to show UNDER the identity line, without repeating it.
 *  - Drops a handle identical to the shown name/id (the name-less case).
 *  - Collapses "whatsapp:Jane Doe" → "whatsapp" when the id just repeats the name. */
function extraHandles(name: string | null, handles: string[], display: string): string[] {
  const out: string[] = []
  for (const h of handles) {
    if (h === display) continue
    const idx = h.indexOf(':')
    const src = idx === -1 ? h : h.slice(0, idx)
    const id = idx === -1 ? '' : h.slice(idx + 1)
    out.push(name && id === name ? src : h)
  }
  return [...new Set(out)]
}

/**
 * Review queue — the triage home for identity resolution. Lists global
 * same-person review candidates (highest probability first) with both faces side
 * by side. Each user Merge / Not-same decision both acts and trains the calibrated
 * scorer (captured server-side into identity_labels).
 */
export default function ReviewPage() {
  const [candidates, setCandidates] = useState<ReviewCandidate[] | null>(null)
  const [msg, setMsg] = useState('')
  const [hideSamePlatform, setHideSamePlatform] = useState(false)
  const [expandedCandidate, setExpandedCandidate] = useState<string | null>(null)

  const load = useCallback(() => {
    api.getReviewCandidates(55).then((d) => setCandidates(d.candidates)).catch(() => setCandidates([]))
  }, [])
  useEffect(() => { load() }, [load])

  const confirm = async (c: ReviewCandidate) => {
    try {
      await api.mergeEntities([c.entity_a, c.entity_b], 'Same person from Review queue')
      setMsg('Merged by reviewer; labeled as same person'); load()
    } catch (e: any) { setMsg(`Merge failed: ${e.message}`) }
  }
  const dismiss = async (c: ReviewCandidate) => {
    try {
      await api.dismissMatch(c.entity_a, c.entity_b)
      setMsg('Dismissed — labeled as different'); load()
    } catch (e: any) { setMsg(`Dismiss failed: ${e.message}`) }
  }

  return (
    <div>
      <PageHeader
        title="Review"
        description="Review needed: probable same-person pairs, highest probability first. No automatic merge occurred; merge or reject each pair to teach the system."
      />
      {msg && <div className="mb-3 text-sm text-text-secondary">{msg}</div>}

      {!candidates ? (
        <LoadingSpinner label="Loading review candidates…" />
      ) : candidates.length === 0 ? (
        <EmptyState
          title="Nothing to review right now"
          description="Probable same-person pairs appear here when the pipeline finds accounts that need human review before any merge."
        />
      ) : (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2 mb-2 px-1 text-sm text-text-secondary">
            <input 
              type="checkbox" 
              id="hideSamePlatform"
              checked={hideSamePlatform}
              onChange={(e) => setHideSamePlatform(e.target.checked)}
              className="rounded border-border bg-surface text-accent focus:ring-accent focus:ring-offset-background"
            />
            <label htmlFor="hideSamePlatform" className="cursor-pointer select-none">
              Hide same-platform pairs (weak)
            </label>
          </div>
          {candidates.filter(c => !hideSamePlatform || !c.same_platform).map((c, i) => {
            const key = `${c.entity_a}-${c.entity_b}-${i}`
            const isExpanded = expandedCandidate === key
            
            return (
            <Card
              key={key}
              className={`flex flex-col gap-4 ${c.same_platform ? 'opacity-60' : ''} ${isExpanded ? 'ring-1 ring-border/60' : 'cursor-pointer hover:bg-surface-hover/30'}`}
              onClick={(e) => {
                // Don't toggle if clicking a button or link
                if ((e.target as HTMLElement).closest('button') || (e.target as HTMLElement).closest('a')) return;
                setExpandedCandidate(isExpanded ? null : key)
              }}
            >
              <div className="flex items-center justify-between gap-4">
                <div className="flex items-center gap-1">
                  <FaceAvatar url={c.face_a} name={c.name_a} size={44} />
                  <FaceAvatar url={c.face_b} name={c.name_b} size={44} />
                  <div className="ml-2 min-w-0">
                    <div className="font-medium">
                      <Link to={`/entities/${c.entity_a}`} title="Open to compare">{c.display_a}</Link>
                      {' '}&harr;{' '}
                      <Link to={`/entities/${c.entity_b}`} title="Open to compare">{c.display_b}</Link>
                    </div>
                    {/* Extra platform accounts, only when they add info beyond the
                        name/id shown above (no pointless repeat of "whatsapp:<name>"). */}
                    {(() => {
                      const exA = extraHandles(c.name_a, c.handles_a, c.display_a)
                      const exB = extraHandles(c.name_b, c.handles_b, c.display_b)
                      if (!exA.length && !exB.length) return null
                      return (
                        <div className="mt-0.5 text-xs text-text-muted">
                          {[exA.join(', '), exB.join(', ')].filter(Boolean).join('  ·  ')}
                        </div>
                      )
                    })()}
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-text-muted">
                      <Confidence score={c.score} />
                      {c.same_platform ? (
                        <span
                          className="rounded-sm border border-border px-1.5 py-0.5 text-xs uppercase tracking-wide text-text-muted"
                          title="Same-platform pairs are usually different people with similar names/handles. Kept visible for the rare burner-account case."
                        >
                          same-platform · weak
                        </span>
                      ) : (
                        <span className="text-xs text-text-secondary">· cross-platform</span>
                      )}
                      {c.signals.length > 0 && (
                        <span>· {c.signals.map((s) => LABELS.signalType[s.type] ?? s.type.replace(/_/g, ' ')).join(', ')}</span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex shrink-0 gap-1">
                  <button className="primary" onClick={() => confirm(c)}>Merge as same person</button>
                  <button onClick={() => dismiss(c)} style={{ borderColor: 'var(--color-orange)', color: 'var(--color-orange)' }}>
                    Not same
                  </button>
                </div>
              </div>
              
              {isExpanded && (
                <div className="mt-2 flex flex-col gap-4 border-t border-border pt-4 text-sm animate-in fade-in slide-in-from-top-1 duration-200">
                  <div className="flex gap-8">
                    <div className="flex flex-1 flex-col items-center gap-2">
                      <div className="font-medium text-text-secondary">Entity A</div>
                      <FaceAvatar url={c.face_a} name={c.name_a} size={80} />
                      <div className="mt-2 text-center text-xs text-text-muted">
                        {c.handles_a.map(h => <div key={h}>{h}</div>)}
                      </div>
                    </div>
                    
                    <div className="flex flex-col items-center justify-center text-text-muted px-4 border-x border-border/50">
                      <div className="text-center font-medium mb-2">{c.cross_platform ? "Cross-Platform" : "Same-Platform"}</div>
                      <Confidence score={c.score} />
                    </div>

                    <div className="flex flex-1 flex-col items-center gap-2">
                      <div className="font-medium text-text-secondary">Entity B</div>
                      <FaceAvatar url={c.face_b} name={c.name_b} size={80} />
                      <div className="mt-2 text-center text-xs text-text-muted">
                        {c.handles_b.map(h => <div key={h}>{h}</div>)}
                      </div>
                    </div>
                  </div>
                  
                  {c.signals.length > 0 && (
                    <div className="rounded-md bg-surface p-3">
                      <h4 className="mb-2 font-medium text-text-secondary text-xs uppercase tracking-wider">Evidence signals</h4>
                      <ul className="flex flex-col gap-2">
                        {c.signals.map((s, idx) => (
                          <li key={idx} className="flex justify-between items-center bg-background rounded px-3 py-2 text-xs">
                            <span className="font-medium">{LABELS.signalType[s.type] ?? s.type.replace(/_/g, ' ')}</span>
                            <div className="flex items-center gap-2">
                                <span className="text-text-muted">{Math.round(s.confidence)}%</span>
                                <div className="h-1.5 w-16 bg-surface-hover rounded-full overflow-hidden">
                                  <div className="h-full bg-accent" style={{ width: `${Math.round(s.confidence)}%` }}></div>
                                </div>
                            </div>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </Card>
          )})}
        </div>
      )}
    </div>
  )
}
