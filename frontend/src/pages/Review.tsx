import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { api, ReviewCandidate } from '../api'
import { FaceAvatar } from '../components/FaceAvatar'

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
      <h2 className="mb-1 text-xl font-bold">Review</h2>
      <p className="mb-4 text-sm text-muted">
        Same-person merge candidates, highest confidence first. Every decision also trains the scorer.
      </p>
      {msg && <div className="mb-3 text-sm text-muted">{msg}</div>}

      {!candidates ? (
        <div className="empty-state">Loading…</div>
      ) : candidates.length === 0 ? (
        <div className="empty-state">No candidates to review 🎉</div>
      ) : (
        <div className="flex flex-col gap-2">
          {candidates.map((c, i) => (
            <div key={`${c.entity_a}-${c.entity_b}-${i}`} className="card flex-between" style={{ alignItems: 'center', gap: '1rem' }}>
              <div className="flex gap-1" style={{ alignItems: 'center' }}>
                <FaceAvatar url={c.face_a} name={c.name_a} size={44} />
                <FaceAvatar url={c.face_b} name={c.name_b} size={44} />
                <div style={{ marginLeft: '0.5rem' }}>
                  <div style={{ fontWeight: 500 }}>
                    <Link to={`/entities/${c.entity_a}`}>{c.name_a || c.entity_a.slice(0, 8)}</Link>
                    {' '}&harr;{' '}
                    <Link to={`/entities/${c.entity_b}`}>{c.name_b || c.entity_b.slice(0, 8)}</Link>
                  </div>
                  <div className="text-sm text-muted">
                    {Math.round((c.score ?? 0) * 100)}% · {c.cross_platform ? 'cross-platform' : 'same-platform'}
                    {c.signals.length > 0 && ' · ' + c.signals.map((s) => s.type).join(', ')}
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
