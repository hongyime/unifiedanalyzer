import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { api, ReviewCandidate } from '../api'
import { FaceAvatar } from '../components/FaceAvatar'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState } from '../components/ui/EmptyState'
import { Confidence } from '../components/ui/Confidence'
import { signalLabel } from '../lib/labels'

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
        <div className="empty-state">Loading…</div>
      ) : candidates.length === 0 ? (
        <EmptyState
          title="Nothing to review right now"
          description="Candidate pairs appear here as the pipeline finds accounts that might be the same person."
        />
      ) : (
        <div className="flex flex-col gap-2">
          {candidates.map((c, i) => (
            <div key={`${c.entity_a}-${c.entity_b}-${i}`} className="card flex-between" style={{ alignItems: 'center', gap: '1rem' }}>
              <div className="flex gap-1" style={{ alignItems: 'center' }}>
                <FaceAvatar url={c.face_a} name={c.name_a} size={44} />
                <FaceAvatar url={c.face_b} name={c.name_b} size={44} />
                <div style={{ marginLeft: '0.5rem' }}>
                  <div style={{ fontWeight: 500 }}>
                    <Link to={`/entities/${c.entity_a}`} title="Open entity to compare">{c.display_a}</Link>
                    {' '}&harr;{' '}
                    <Link to={`/entities/${c.entity_b}`} title="Open entity to compare">{c.display_b}</Link>
                  </div>
                  {/* Platform accounts on each side — so you can actually tell who
                      these are (and compare) when there's no canonical name. */}
                  <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                    <span>{c.handles_a.length ? c.handles_a.join(', ') : '—'}</span>
                    {'  vs  '}
                    <span>{c.handles_b.length ? c.handles_b.join(', ') : '—'}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-text-muted">
                    <Confidence score={c.score} />
                    <span>· {c.cross_platform ? 'different platforms' : 'same platform'}</span>
                    {c.signals.length > 0 && (
                      <span>· {c.signals.map((s) => signalLabel(s.type)).join(', ')}</span>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex gap-1">
                <button onClick={() => confirm(c)}>Same &rarr; merge</button>
                <button onClick={() => dismiss(c)} style={{ borderColor: 'var(--color-orange)', color: 'var(--color-orange)' }}>
                  Not same
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
