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
 * same-person merge candidates (highest confidence first) with both faces side
 * by side. Each Merge / Not-same decision both acts AND trains the calibrated
 * scorer (captured server-side into identity_labels).
 */
export default function ReviewPage() {
  const [candidates, setCandidates] = useState<ReviewCandidate[] | null>(null)
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    api.getReviewCandidates(60).then((d) => setCandidates(d.candidates)).catch(() => setCandidates([]))
  }, [])
  useEffect(() => { load() }, [load])

  const confirm = async (c: ReviewCandidate) => {
    try {
      await api.mergeEntities([c.entity_a, c.entity_b], 'Confirmed from Review queue')
      setMsg('Merged — labeled as same person'); load()
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
        description="Pairs of accounts that might be the same person, most likely first. Confirm or reject each — every choice also teaches the system to get better."
      />
      {msg && <div className="mb-3 text-sm text-text-secondary">{msg}</div>}

      {!candidates ? (
        <LoadingSpinner label="Loading review candidates…" />
      ) : candidates.length === 0 ? (
        <EmptyState
          title="Nothing to review right now"
          description="Candidate pairs appear here as the pipeline finds accounts that might be the same person."
        />
      ) : (
        <div className="flex flex-col gap-2">
          {candidates.map((c, i) => (
            <Card
              key={`${c.entity_a}-${c.entity_b}-${i}`}
              className={`flex items-center justify-between gap-4 ${c.same_platform ? 'opacity-60' : ''}`}
            >
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
                <button className="primary" onClick={() => confirm(c)}>Same person</button>
                <button onClick={() => dismiss(c)} style={{ borderColor: 'var(--color-orange)', color: 'var(--color-orange)' }}>
                  Not same
                </button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
