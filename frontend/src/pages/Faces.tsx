import { useState } from 'react'
import { ScanFace, Users, Images, Film, AlertCircle } from 'lucide-react'
import { useFaceStats, useFaceIdentities } from '../hooks'

/**
 * Faces / Identities page (facetracker merge — Stage 4).
 *
 * Reads the face engine via /api/face (mounted by src/api/face_mount.py). Until
 * the collector source media is restored (task B1) and the face re-index (R6)
 * runs, the engine holds zero faces, so this page renders an explanatory empty
 * state rather than blank tiles.
 *
 * TODO(F4): face thumbnails + per-identity face grid once faces exist; identity
 * rename/merge/split actions (endpoints already live under /api/face/identities).
 */
function StatTile({ label, value, icon: Icon }: { label: string; value: number | string; icon: typeof ScanFace }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="mb-1 flex items-center gap-2 text-muted">
        <Icon size={15} />
        <span className="text-xs">{label}</span>
      </div>
      <div className="text-2xl font-semibold">
        {typeof value === 'number' ? value.toLocaleString() : value}
      </div>
    </div>
  )
}

export default function FacesPage() {
  const [page, setPage] = useState(1)
  const stats = useFaceStats()
  const identities = useFaceIdentities(page)

  // The whole face API is mounted-but-empty until B1/R6; a failing fetch most
  // likely means the analyzer API wasn't restarted after the mount landed.
  if (stats.isError) {
    return (
      <div>
        <h2 className="mb-4 text-xl font-bold">Faces</h2>
        <div className="flex items-center gap-2 rounded-lg border border-border bg-card p-4 text-sm text-red">
          <AlertCircle size={16} />
          Face API unreachable (/api/face). Restart the analyzer API to pick up the mount.
        </div>
      </div>
    )
  }

  const s = stats.data
  const list = identities.data
  const hasFaces = (s?.total_faces ?? 0) > 0

  return (
    <div>
      <h2 className="mb-1 text-xl font-bold">Faces</h2>
      <p className="mb-5 text-sm text-muted">
        InsightFace (ArcFace 512-dim) embeddings indexed from collector media.
      </p>

      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="Faces" value={s?.total_faces ?? 0} icon={ScanFace} />
        <StatTile label="Identities" value={s?.total_identities ?? 0} icon={Users} />
        <StatTile label="Images" value={s?.total_images ?? 0} icon={Images} />
        <StatTile label="Videos" value={s?.total_videos ?? 0} icon={Film} />
      </div>

      {!hasFaces ? (
        <div className="rounded-lg border border-border bg-card p-6 text-sm text-muted">
          <div className="mb-1 font-medium text-fg">No faces indexed yet</div>
          The face index is empty. It populates once collector source media is
          restored and the face re-index runs. Until then this page stays empty
          by design.
        </div>
      ) : (
        <>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted">
                <th className="py-2 pr-4 font-medium">Identity</th>
                <th className="py-2 pr-4 font-medium">Faces</th>
                <th className="py-2 pr-4 font-medium">Avg quality</th>
                <th className="py-2 pr-4 font-medium">Updated</th>
              </tr>
            </thead>
            <tbody>
              {list?.identities.map((id) => (
                <tr key={id.identity_id} className="border-b border-border/50 hover:bg-hover">
                  <td className="py-2 pr-4">{id.name || <span className="text-muted">unnamed #{id.identity_id}</span>}</td>
                  <td className="py-2 pr-4">{id.face_count}</td>
                  <td className="py-2 pr-4">{id.avg_quality_score.toFixed(2)}</td>
                  <td className="py-2 pr-4 text-muted">{id.updated_at?.slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {list && list.total > list.page_size && (
            <div className="mt-4 flex items-center gap-3 text-sm">
              <button
                className="rounded-md border border-border px-3 py-1 disabled:opacity-40"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Prev
              </button>
              <span className="text-muted">
                Page {list.page} · {list.total.toLocaleString()} identities
              </span>
              <button
                className="rounded-md border border-border px-3 py-1 disabled:opacity-40"
                disabled={page * list.page_size >= list.total}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
