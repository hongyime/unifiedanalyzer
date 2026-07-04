import { useEffect, useState } from 'react'
import { ScanFace, Users, Images, Film, AlertCircle } from 'lucide-react'
import { useFaceStats, useFaceIdentities } from '../hooks'
import { api } from '../api'
import { PageHeader } from '../components/ui/PageHeader'

/**
 * Faces / Identities page (facetracker merge — Stage 4).
 *
 * Reads the face engine via /api/face. Shows a grid of actual face crops
 * (served on-the-fly from /api/face/gallery, cropped from the source image via
 * the stored bbox — no pre-generated thumbnails), with a cluster_id badge. The
 * identities table below populates once the engine's identity clustering runs.
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

type Gallery = Awaited<ReturnType<typeof api.getFaceGallery>>

export default function FacesPage() {
  const [page, setPage] = useState(1)
  const stats = useFaceStats()
  const identities = useFaceIdentities(page)

  const s = stats.data
  const hasFaces = (s?.total_faces ?? 0) > 0

  const [gPage, setGPage] = useState(1)
  const [gallery, setGallery] = useState<Gallery | null>(null)
  useEffect(() => {
    if (!hasFaces) return
    api.getFaceGallery(gPage, 64).then(setGallery).catch(() => setGallery(null))
  }, [hasFaces, gPage])

  // "Who is this?" face search: click a face -> kNN similar matches.
  type Sim = Awaited<ReturnType<typeof api.getSimilarFaces>>
  const [simFor, setSimFor] = useState<number | null>(null)
  const [sim, setSim] = useState<Sim | null>(null)
  const openSimilar = (faceId: number) => {
    setSimFor(faceId); setSim(null)
    api.getSimilarFaces(faceId, 48).then(setSim).catch(() => setSim({ matches: [] }))
  }

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

  const list = identities.data

  return (
    <div>
      <PageHeader
        title="Faces"
        description="Faces detected across all photos and grouped by who they belong to. When the same face appears on different accounts, it links them to the same person."
      />

      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="Faces" value={s?.total_faces ?? 0} icon={ScanFace} />
        <StatTile label="Identities" value={s?.total_identities ?? 0} icon={Users} />
        <StatTile label="Images" value={s?.total_images ?? 0} icon={Images} />
        <StatTile label="Videos" value={s?.total_videos ?? 0} icon={Film} />
      </div>

      {!hasFaces ? (
        <div className="rounded-lg border border-border bg-card p-6 text-sm text-muted">
          <div className="mb-1 font-medium text-fg">No faces indexed yet</div>
          The face index is empty. It populates as collector source media and the
          mounted drives are scanned.
        </div>
      ) : (
        <>
          {simFor != null && (
            <div className="mb-3 rounded-lg border border-accent bg-card p-3">
              <div className="mb-2 flex items-center justify-between text-sm font-medium">
                <span>Similar to face #{simFor} <span className="text-muted">— who is this?</span></span>
                <button onClick={() => setSimFor(null)} className="text-xs text-muted">close ✕</button>
              </div>
              {!sim ? (
                <div className="text-sm text-muted">Searching…</div>
              ) : sim.matches.length === 0 ? (
                <div className="text-sm text-muted">No matches.</div>
              ) : (
                <div className="grid grid-cols-4 gap-2 sm:grid-cols-8 md:grid-cols-12">
                  {sim.matches.map((m) => (
                    <a key={m.face_id} href={m.entity_id ? `/entities/${m.entity_id}` : undefined}
                      title={`${Math.round(m.similarity * 100)}% · cluster ${m.cluster_id ?? '—'}${m.entity_name ? ' · ' + m.entity_name : ''}`}
                      className="relative block aspect-square overflow-hidden rounded border border-border">
                      <img src={m.crop_url} loading="lazy" className="h-full w-full object-cover"
                        onError={(e) => { const t = e.currentTarget.closest('a') as HTMLElement | null; if (t) t.style.display = 'none' }} />
                      <span className="absolute bottom-0 left-0 right-0 bg-black/60 text-center text-[9px] text-white">{Math.round(m.similarity * 100)}%</span>
                    </a>
                  ))}
                </div>
              )}
            </div>
          )}
          <div className="mb-2 text-sm font-medium">
            Detected faces{' '}
            <span className="text-muted">({(gallery?.total ?? 0).toLocaleString()})</span>
            <span className="text-muted"> — click a face to find similar</span>
          </div>
          <div className="grid grid-cols-4 gap-2 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10">
            {gallery?.faces.map((f) => (
              <div
                key={f.face_id}
                onClick={() => openSimilar(f.face_id)}
                title={`face #${f.face_id} · cluster ${f.cluster_id ?? '—'} · q${f.quality}`}
                className="group relative aspect-square cursor-pointer overflow-hidden rounded-lg border border-border bg-card"
              >
                <img
                  src={f.crop_url}
                  alt={`face ${f.face_id}`}
                  loading="lazy"
                  className="h-full w-full object-cover transition group-hover:scale-105"
                  onError={(e) => { const t = e.currentTarget.closest('div') as HTMLElement | null; if (t) t.style.display = 'none' }}
                />
                {f.cluster_id != null && (
                  <span className="absolute bottom-1 left-1 rounded bg-black/60 px-1 text-[10px] leading-tight text-white">
                    #{f.cluster_id}
                  </span>
                )}
              </div>
            ))}
          </div>

          {gallery && gallery.total > gallery.page_size && (
            <div className="mt-4 flex items-center gap-3 text-sm">
              <button
                className="rounded-md border border-border px-3 py-1 disabled:opacity-40"
                disabled={gPage <= 1}
                onClick={() => setGPage((p) => Math.max(1, p - 1))}
              >
                Prev
              </button>
              <span className="text-muted">
                Page {gallery.page} · {gallery.total.toLocaleString()} faces
              </span>
              <button
                className="rounded-md border border-border px-3 py-1 disabled:opacity-40"
                disabled={gPage * gallery.page_size >= gallery.total}
                onClick={() => setGPage((p) => p + 1)}
              >
                Next
              </button>
            </div>
          )}

          {list && list.identities.length > 0 && (
            <div className="mt-8">
              <div className="mb-2 text-sm font-medium">Identities</div>
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
                  {list.identities.map((id) => (
                    <tr key={id.identity_id} className="border-b border-border/50 hover:bg-hover">
                      <td className="py-2 pr-4">{id.name || <span className="text-muted">unnamed #{id.identity_id}</span>}</td>
                      <td className="py-2 pr-4">{id.face_count}</td>
                      <td className="py-2 pr-4">{id.avg_quality_score.toFixed(2)}</td>
                      <td className="py-2 pr-4 text-muted">{id.updated_at?.slice(0, 10)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {list.total > list.page_size && (
                <div className="mt-4 flex items-center gap-3 text-sm">
                  <button className="rounded-md border border-border px-3 py-1 disabled:opacity-40" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>Prev</button>
                  <span className="text-muted">Page {list.page} · {list.total.toLocaleString()} identities</span>
                  <button className="rounded-md border border-border px-3 py-1 disabled:opacity-40" disabled={page * list.page_size >= list.total} onClick={() => setPage((p) => p + 1)}>Next</button>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
