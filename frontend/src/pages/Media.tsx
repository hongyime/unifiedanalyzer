import { useState } from 'react'
import {
  MapPin, FileText, ScanFace, Layers, X, ImageOff,
} from 'lucide-react'
import { useMediaStats, useMediaCoverage, useMediaFilters, useMediaBrowse } from '../hooks'
import { MediaItem, MediaBrowseParams, MediaCoverageItem } from '../api'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState } from '../components/ui/EmptyState'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { LABELS } from '../lib/labels'

const PER_PAGE = 48

function StatTile({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="text-2xl font-semibold">{typeof value === 'number' ? value.toLocaleString() : value}</div>
      <div className="text-xs text-muted">{label}</div>
    </div>
  )
}

function CoverageTile({ item }: { item: MediaCoverageItem }) {
  const secondary = item.processed !== item.count ? `${item.processed.toLocaleString()} processed` : item.basis
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="mb-1 flex items-center justify-between gap-2">
        <div className="truncate text-sm font-medium">{item.label}</div>
        <span className={item.status === 'covered' ? 'text-xs text-green' : 'text-xs text-muted'}>
          {item.status}
        </span>
      </div>
      <div className="text-2xl font-semibold">{item.count.toLocaleString()}</div>
      <div className="truncate text-xs text-muted" title={secondary}>{secondary}</div>
    </div>
  )
}

/** A single broken/un-renderable thumbnail falls back to a placeholder. */
function Thumb({ item }: { item: MediaItem }) {
  const [failed, setFailed] = useState(false)
  if (failed) {
    return (
      <div className="flex aspect-square w-full items-center justify-center bg-bg text-muted">
        <ImageOff size={28} />
      </div>
    )
  }
  return (
    <img
      src={item.thumbnail_url}
      alt={item.analysis_type}
      loading="lazy"
      onError={() => setFailed(true)}
      className="aspect-square w-full bg-bg object-cover"
    />
  )
}

function badgeRow(item: MediaItem) {
  return (
    <div className="flex flex-wrap gap-1">
      {item.has_gps && <MapPin size={12} className="text-green" />}
      {item.has_text && <FileText size={12} className="text-accent" />}
      {item.has_face && <ScanFace size={12} className="text-orange" />}
      {item.is_derived && <Layers size={12} className="text-muted" />}
    </div>
  )
}

function DetailModal({ item, onClose }: { item: MediaItem; onClose: () => void }) {
  const [failed, setFailed] = useState(false)

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6"
      onClick={onClose}
    >
      <div
        className="max-h-[85vh] w-full max-w-2xl overflow-auto rounded-lg border border-border bg-card p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between">
          <div>
            <div className="font-semibold">{item.analysis_type}</div>
            <div className="text-xs text-muted">
              {item.source} · {item.content_type}
              {item.is_derived && ' · derived'}
            </div>
          </div>
          <button onClick={onClose} className="!p-1.5" aria-label="Close">
            <X size={16} />
          </button>
        </div>

        {failed ? (
          <div className="mb-4 flex h-48 items-center justify-center rounded border border-border bg-bg text-muted">
            <ImageOff size={28} />
          </div>
        ) : ['video', 'story_video', 'reel'].includes(item.content_type) ? (
          <video
            src={`/api/media/${item.id}/file`}
            poster={item.thumbnail_url}
            controls
            preload="metadata"
            playsInline
            className="mb-4 max-h-72 w-full rounded border border-border bg-black object-contain"
            onError={() => setFailed(true)}
          />
        ) : (
          <img
            src={item.thumbnail_url}
            alt={item.analysis_type}
            className="mb-4 max-h-72 rounded border border-border object-contain"
            onError={() => setFailed(true)}
          />
        )}

        <dl className="grid grid-cols-2 gap-2 text-sm">
          {item.gps_lat != null && (
            <>
              <dt className="text-muted">GPS</dt>
              <dd>
                <a
                  href={`https://www.openstreetmap.org/?mlat=${item.gps_lat}&mlon=${item.gps_lon}#map=15/${item.gps_lat}/${item.gps_lon}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  {item.gps_lat.toFixed(5)}, {item.gps_lon!.toFixed(5)}
                </a>
              </dd>
            </>
          )}
          {item.taken_at && (
            <>
              <dt className="text-muted">Taken</dt>
              <dd>{new Date(item.taken_at).toLocaleString()}</dd>
            </>
          )}
          {item.perceptual_hash && (
            <>
              <dt className="text-muted">pHash</dt>
              <dd className="font-mono text-xs">{item.perceptual_hash}</dd>
            </>
          )}
          {item.model_version && (
            <>
              <dt className="text-muted">Model</dt>
              <dd className="text-xs">{item.model_version}</dd>
            </>
          )}
          <dt className="text-muted">Processed</dt>
          <dd>{item.processed_at ? new Date(item.processed_at).toLocaleString() : '-'}</dd>
        </dl>

        {item.text_preview && (
          <div className="mt-4">
            <div className="mb-1 text-xs text-muted">Extracted text</div>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-bg p-3 text-xs">
              {item.text_preview}
            </pre>
          </div>
        )}
      </div>
    </div>
  )
}

export default function MediaPage() {
  const [params, setParams] = useState<MediaBrowseParams>({ page: 1, per_page: PER_PAGE })
  const [selected, setSelected] = useState<MediaItem | null>(null)

  const stats = useMediaStats()
  const coverage = useMediaCoverage()
  const filters = useMediaFilters()
  const browse = useMediaBrowse(params)

  const set = (patch: Partial<MediaBrowseParams>) =>
    setParams((p) => ({ ...p, ...patch, page: 1 }))

  const totals = stats.data?.totals
  const total = browse.data?.total ?? 0
  const page = params.page ?? 1
  const items = browse.data?.data ?? []

  return (
    <div>
      <PageHeader
        title="Media"
        description="Photos, videos and documents we've analyzed — extracted text, locations, faces and image fingerprints. Filter by what each item contains."
      />

      {totals && (
        <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
          <StatTile label="media items" value={totals.items_total} />
          <StatTile label="analysis rows" value={totals.rows_total} />
          <StatTile label="with GPS" value={totals.with_gps} />
          <StatTile label="with text" value={totals.with_text} />
          <StatTile label="with face" value={totals.with_face} />
          <StatTile label="derived" value={totals.derived} />
        </div>
      )}

      {coverage.data && (
        <div className="mb-6">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-semibold">Production Coverage</h2>
            <div className="flex flex-wrap gap-2 text-xs text-muted">
              <span>{coverage.data.derived_rows.toLocaleString()} derived</span>
              <span>{coverage.data.phash_rows.toLocaleString()} pHash</span>
              <span>{coverage.data.contact_signals.total.toLocaleString()} contacts</span>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
            {coverage.data.coverage.map((item) => (
              <CoverageTile key={item.key} item={item} />
            ))}
          </div>
        </div>
      )}

      {/* Filter bar */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <input
          type="search"
          placeholder="Search extracted text…"
          defaultValue={params.q ?? ''}
          onKeyDown={(e) => {
            if (e.key === 'Enter') set({ q: (e.target as HTMLInputElement).value || undefined })
          }}
          className="min-w-56 flex-1"
        />
        <select
          value={params.analysis_type ?? ''}
          onChange={(e) => set({ analysis_type: e.target.value || undefined })}
        >
          <option value="">All types</option>
          {filters.data?.analysis_types.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <select value={params.source ?? ''} onChange={(e) => set({ source: e.target.value || undefined })}>
          <option value="">All sources</option>
          {filters.data?.sources.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select
          value={params.content_type ?? ''}
          onChange={(e) => set({ content_type: e.target.value || undefined })}
        >
          <option value="">All content</option>
          {filters.data?.content_types.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        {([
          ['has_gps', 'GPS'],
          ['has_text', 'Text'],
          ['has_face', 'Face'],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => set({ [key]: params[key] ? undefined : true } as Partial<MediaBrowseParams>)}
            className={params[key] ? '!border-accent !text-accent' : ''}
          >
            {label}
          </button>
        ))}
      </div>

      {browse.isLoading ? (
        <LoadingSpinner label="Loading media…" />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<ImageOff className="h-10 w-10" />}
          title="No media matches these filters"
          description={`Try clearing filters, or check the ${LABELS.signalType['media_perceptual_match'] ? 'photo' : 'media'} pipeline in Runs.`}
        />
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
          {items.map((item) => (
            <button
              key={item.id}
              onClick={() => setSelected(item)}
              className="!block overflow-hidden rounded-lg !border-border !bg-card !p-0 text-left"
            >
              <Thumb item={item} />
              <div className="p-2">
                <div className="mb-1 flex items-center justify-between">
                  <span className="truncate text-[0.7rem] text-muted">{item.source}</span>
                  {badgeRow(item)}
                </div>
                <div className="truncate text-xs">{item.analysis_type}</div>
              </div>
            </button>
          ))}
        </div>
      )}

      <div className="mt-5 flex items-center justify-between">
        <span className="text-sm text-muted">{total.toLocaleString()} results</span>
        <div className="flex gap-1">
          <button disabled={page <= 1} onClick={() => setParams((p) => ({ ...p, page: page - 1 }))}>
            Prev
          </button>
          <button
            disabled={page * PER_PAGE >= total}
            onClick={() => setParams((p) => ({ ...p, page: page + 1 }))}
          >
            Next
          </button>
        </div>
      </div>

      {selected && <DetailModal item={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}
