import { useEffect, useState } from 'react'
import { ScanFace, Users, Images, Film, Search, Upload, ImageOff } from 'lucide-react'
import { useFaceStats, useFaceIdentities } from '../hooks'
import { api, FaceSearchResponse } from '../api'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorState } from '../components/ui/ErrorState'
import { PlatformBadge } from '../components/ui/PlatformBadge'
import { LABELS } from '../lib/labels'

// The "face bridge" is the mechanism this page powers: same face across
// accounts → linked person. Surface the plain-language name in the intro.
const FACE_BRIDGE_LABEL = LABELS.signalType['face_pair_knn']

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

function FaceCrop({ src, alt, className }: { src: string; alt: string; className?: string }) {
  const [failed, setFailed] = useState(false)
  if (failed) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-background text-muted">
        <ImageOff size={18} />
      </div>
    )
  }
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      className={className}
      onError={() => setFailed(true)}
    />
  )
}

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

  // Click a face or upload an image -> pgvector kNN over the corpus.
  const [searchFor, setSearchFor] = useState<string | null>(null)
  const [searchResult, setSearchResult] = useState<FaceSearchResponse | null>(null)
  const [searchError, setSearchError] = useState('')
  const [uploading, setUploading] = useState(false)
  const openSimilar = (faceId: number) => {
    setSearchFor(`face #${faceId}`)
    setSearchResult(null)
    setSearchError('')
    api.searchFacesByFaceId(faceId, 48)
      .then(setSearchResult)
      .catch((e) => setSearchError(e.message || 'Search failed'))
  }
  const uploadSearch = (file: File | null | undefined) => {
    if (!file) return
    setSearchFor(file.name)
    setSearchResult(null)
    setSearchError('')
    setUploading(true)
    api.searchFacesByImage(file, 48)
      .then(setSearchResult)
      .catch((e) => setSearchError(e.message || 'Search failed'))
      .finally(() => setUploading(false))
  }

  if (stats.isError) {
    return (
      <div>
        <PageHeader title="Faces" />
        <ErrorState
          message="Face API unreachable (/api/face). Restart the analyzer API to pick up the mount."
          onRetry={() => stats.refetch()}
        />
      </div>
    )
  }

  const list = identities.data

  return (
    <div>
      <PageHeader
        title="Faces"
        description={`Faces detected across all photos and grouped by who they belong to. When the same face appears on different accounts, it links them to the same person${FACE_BRIDGE_LABEL ? ' — the "' + FACE_BRIDGE_LABEL + '" bridge' : ''}.`}
      />

      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="Faces" value={s?.total_faces ?? 0} icon={ScanFace} />
        <StatTile label="Identities" value={s?.total_identities ?? 0} icon={Users} />
        <StatTile label="Images" value={s?.total_images ?? 0} icon={Images} />
        <StatTile label="Videos" value={s?.total_videos ?? 0} icon={Film} />
      </div>

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card p-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Search size={16} />
          Face search
          {searchResult && (
            <span className="text-xs font-normal text-muted">
              {searchResult.count} matches · {searchResult.took_ms.toFixed(1)} ms · {searchResult.index.method}
            </span>
          )}
        </div>
        <label className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-hover">
          <Upload size={15} />
          {uploading ? 'Searching...' : 'Upload image'}
          <input
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(event) => {
              uploadSearch(event.target.files?.[0])
              event.currentTarget.value = ''
            }}
          />
        </label>
      </div>

      {!hasFaces ? (
        <EmptyState
          icon={<ScanFace className="h-10 w-10" />}
          title="No faces indexed yet"
          description="The face index is empty. It populates as collector source media and the mounted drives are scanned."
        />
      ) : (
        <>
          {searchFor && (
            <div className="mb-3 rounded-lg border border-accent bg-card p-3">
              <div className="mb-2 flex items-center justify-between text-sm font-medium">
                <span>Matches for {searchFor}</span>
                <button onClick={() => setSearchFor(null)} className="text-xs text-muted">close</button>
              </div>
              {!searchResult && !searchError ? (
                <div className="text-sm text-muted">Searching…</div>
              ) : searchError ? (
                <div className="text-sm text-error">{searchError}</div>
              ) : searchResult?.matches.length === 0 ? (
                <div className="text-sm text-muted">No matches.</div>
              ) : (
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {searchResult?.matches.map((m) => (
                    <a key={m.face_id}
                      href={m.entity?.id ? `/entities/${m.entity.id}` : (m.source.url || undefined)}
                      title={`${Math.round(m.similarity * 100)}% · cluster ${m.cluster_id ?? '—'}`}
                      className="grid grid-cols-[76px_minmax(0,1fr)] gap-2 rounded border border-border bg-background p-2 hover:bg-hover">
                      <div className="relative aspect-square overflow-hidden rounded border border-border">
                        <FaceCrop src={m.crop_url} alt={`face ${m.face_id}`} className="h-full w-full object-cover" />
                        <span className="absolute bottom-0 left-0 right-0 bg-black/60 text-center text-[9px] text-white">{Math.round(m.similarity * 100)}%</span>
                      </div>
                      <div className="min-w-0">
                        <div className="mb-1 flex items-center gap-1">
                          <PlatformBadge source={m.source.platform || 'unknown'} />
                          <span className="text-xs text-muted">face #{m.face_id}</span>
                        </div>
                        <div className="truncate text-sm font-medium">
                          {m.entity?.name || m.entity?.id || 'Unlinked face'}
                        </div>
                        <div className="truncate text-xs text-muted">
                          {m.source.filename || m.source.media_item_id || m.source.file_path || 'source unavailable'}
                        </div>
                        <div className="mt-1 text-xs text-muted">
                          cluster {m.cluster_id ?? '—'} · q{m.quality.toFixed(2)}
                        </div>
                      </div>
                    </a>
                  ))}
                </div>
              )}
            </div>
          )}
          <div className="mb-2 text-sm font-medium">
            Detected faces{' '}
            <span className="text-muted">({(gallery?.total ?? 0).toLocaleString()})</span>
            <span className="text-muted"> — click a face to search</span>
          </div>
          <div className="grid grid-cols-4 gap-2 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10">
            {gallery?.faces.map((f) => (
              <div
                key={f.face_id}
                onClick={() => openSimilar(f.face_id)}
                title={`face #${f.face_id} · cluster ${f.cluster_id ?? '—'} · q${f.quality}`}
                className="group relative aspect-square cursor-pointer overflow-hidden rounded-lg border border-border bg-card"
              >
                <FaceCrop
                  src={f.crop_url}
                  alt={`face ${f.face_id}`}
                  className="h-full w-full object-cover transition group-hover:scale-105"
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
