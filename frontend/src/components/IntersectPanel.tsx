import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { MapPin, Plus, Search, Share2, X } from 'lucide-react'
import { api, EntityDetail, IntersectionEntity, IntersectionResponse, DigitalIntersection } from '../api'
import { FaceAvatar } from './FaceAvatar'
import { GeoMap } from './GeoMap'
import { Card } from './ui/Card'
import { Button } from './ui/Button'
import { EmptyState } from './ui/EmptyState'
import { LoadingSpinner } from './ui/LoadingSpinner'
import { PlatformBadge } from './ui/PlatformBadge'

function formatDate(value: string | null | undefined) {
  if (!value) return 'unknown'
  return new Date(value).toLocaleString()
}

function humanize(value: string | null | undefined) {
  if (!value) return 'unknown'
  return value.replace(/_/g, ' ')
}

function inputToIso(value: string) {
  if (!value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString()
}

function resultSortLabel(edge: DigitalIntersection) {
  const parts = [humanize(edge.type)]
  if (edge.source) parts.push(edge.source)
  if (edge.count != null) parts.push(edge.count.toLocaleString())
  return parts.join(' · ')
}

export function IntersectPanel({ entity }: { entity: EntityDetail }) {
  const anchor = useMemo<IntersectionEntity>(() => ({
    id: entity.id,
    name: entity.canonical_name,
    face: entity.face_crop_url ?? null,
  }), [entity.id, entity.canonical_name, entity.face_crop_url])

  const [selected, setSelected] = useState<IntersectionEntity[]>([anchor])
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<IntersectionEntity[]>([])
  const [radiusM, setRadiusM] = useState('200')
  const [windowMinutes, setWindowMinutes] = useState('60')
  const [fromValue, setFromValue] = useState('')
  const [toValue, setToValue] = useState('')
  const [result, setResult] = useState<IntersectionResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setSelected([anchor])
    setResult(null)
    setQuery('')
    setSearchResults([])
  }, [anchor])

  const selectedIds = useMemo(() => new Set(selected.map((item) => item.id)), [selected])

  useEffect(() => {
    const trimmed = query.trim()
    if (trimmed.length < 2) {
      setSearchResults([])
      return
    }
    let cancelled = false
    const timer = window.setTimeout(() => {
      api.searchEntities(trimmed, 10)
        .then((data) => {
          if (cancelled) return
          setSearchResults(data.results
            .filter((item) => !selectedIds.has(item.id))
            .map((item) => ({
              id: item.id,
              name: item.canonical_name,
              face: item.face,
            })))
        })
        .catch(() => {
          if (!cancelled) setSearchResults([])
        })
    }, 180)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [query, selectedIds])

  const runIntersection = async () => {
    if (selected.length < 2) return
    const radius = Math.max(10, Math.min(5000, Number(radiusM) || 200))
    const windowSize = Math.max(1, Math.min(1440, Number(windowMinutes) || 60))
    const from = inputToIso(fromValue)
    const to = inputToIso(toValue)
    setLoading(true)
    setError('')
    try {
      const ids = selected.map((item) => item.id)
      const data = ids.length === 2
        ? await api.getEntityIntersection(ids[0], ids[1], { radius_m: radius, window_minutes: windowSize, from, to })
        : await api.intersectEntities({ ids, radius_m: radius, window_minutes: windowSize, from, to })
      setResult(data)
      setRadiusM(String(radius))
      setWindowMinutes(String(windowSize))
    } catch (e: any) {
      setError(e.message || 'Intersection failed')
      setResult(null)
    } finally {
      setLoading(false)
    }
  }

  const mapData = useMemo(() => {
    const points = (result?.physical ?? []).flatMap((hit) => hit.evidence.map((point) => ({
      lat: point.lat,
      lng: point.lng,
      source: point.source,
      occurred_at: point.occurred_at,
      label: `${point.entity_name || point.entity_id.slice(0, 8)}${point.label ? ` · ${point.label}` : ''}`,
      evidence_type: point.evidence_type,
      evidence_key: point.evidence_key,
      confidence: point.confidence,
      status: point.status,
    })))
    return { routes: [], points, counts: { routes: 0, points: points.length } }
  }, [result])

  return (
    <div className="space-y-3">
      <Card title="Intersect">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_260px]">
          <div>
            <div className="mb-2 flex flex-wrap gap-2">
              {selected.map((item, index) => (
                <div key={item.id} className="flex min-w-0 items-center gap-2 rounded-md border border-border bg-hover px-2 py-1">
                  <FaceAvatar url={item.face} name={item.name} size={26} />
                  <span className="max-w-[210px] truncate text-sm">{item.name || item.id.slice(0, 8)}</span>
                  {index > 0 && (
                    <button
                      type="button"
                      title="Remove"
                      aria-label="Remove"
                      className="rounded p-0.5 text-text-muted hover:bg-white/10 hover:text-text-primary"
                      onClick={() => setSelected((current) => current.filter((candidate) => candidate.id !== item.id))}
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              ))}
            </div>
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-text-muted" />
              <input
                type="text"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search people"
                className="w-full rounded-md border border-border bg-background py-2 pl-8 pr-2 text-sm text-text-primary"
              />
              {searchResults.length > 0 && (
                <div className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-md border border-border bg-surface shadow-xl">
                  {searchResults.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-hover"
                      onClick={() => {
                        setSelected((current) => [...current, item])
                        setQuery('')
                        setSearchResults([])
                      }}
                    >
                      <span className="flex min-w-0 items-center gap-2">
                        <FaceAvatar url={item.face} name={item.name} size={28} />
                        <span className="truncate text-sm">{item.name || item.id.slice(0, 8)}</span>
                      </span>
                      <Plus className="h-4 w-4 shrink-0 text-text-muted" />
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs text-text-muted">
              Radius
              <input
                type="number"
                min={10}
                max={5000}
                value={radiusM}
                onChange={(event) => setRadiusM(event.target.value)}
                className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm text-text-primary"
              />
            </label>
            <label className="text-xs text-text-muted">
              Window
              <input
                type="number"
                min={1}
                max={1440}
                value={windowMinutes}
                onChange={(event) => setWindowMinutes(event.target.value)}
                className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm text-text-primary"
              />
            </label>
            <label className="col-span-2 text-xs text-text-muted">
              From
              <input
                type="datetime-local"
                value={fromValue}
                onChange={(event) => setFromValue(event.target.value)}
                className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm text-text-primary"
              />
            </label>
            <label className="col-span-2 text-xs text-text-muted">
              To
              <input
                type="datetime-local"
                value={toValue}
                onChange={(event) => setToValue(event.target.value)}
                className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm text-text-primary"
              />
            </label>
            <Button
              className="col-span-2"
              icon={<Share2 className="h-4 w-4" />}
              loading={loading}
              disabled={selected.length < 2}
              onClick={runIntersection}
            >
              Run
            </Button>
          </div>
        </div>
        {error && <div className="mt-3 text-sm text-error">{error}</div>}
      </Card>

      {loading && <LoadingSpinner label="Finding intersections..." />}

      {result && (
        <>
          <Card>
            <div className="grid gap-2 sm:grid-cols-4">
              <div>
                <div className="text-xs text-text-muted">People</div>
                <div className="text-lg font-semibold">{result.entity_ids.length}</div>
              </div>
              <div>
                <div className="text-xs text-text-muted">Physical</div>
                <div className="text-lg font-semibold">{result.counts.physical}</div>
              </div>
              <div>
                <div className="text-xs text-text-muted">Digital</div>
                <div className="text-lg font-semibold">{result.counts.digital}</div>
              </div>
              <div>
                <div className="text-xs text-text-muted">Latency</div>
                <div className="text-lg font-semibold">{result.duration_ms.toFixed(1)} ms</div>
              </div>
            </div>
            {result.collector_skipped && (
              <div className="mt-2 text-xs text-warning">Collector reads skipped.</div>
            )}
            {(result.counts.physical_points_suppressed || result.counts.physical_points_from_registry) && (
              <div className="mt-2 text-xs text-text-muted">
                {result.counts.physical_points_suppressed ? `${result.counts.physical_points_suppressed} rejected/suppressed points hidden` : ''}
                {result.counts.physical_points_suppressed && result.counts.physical_points_from_registry ? ' · ' : ''}
                {result.counts.physical_points_from_registry ? `${result.counts.physical_points_from_registry} registry-only points included` : ''}
              </div>
            )}
          </Card>

          <Card title="Physical Overlaps">
            {result.physical.length === 0 ? (
              <EmptyState title="No physical overlaps" description="No co-located points matched the current radius and time window." />
            ) : (
              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_360px]">
                <GeoMap data={mapData} />
                <div className="max-h-[420px] space-y-2 overflow-auto pr-1">
                  {result.physical.map((hit, index) => (
                    <div key={`${hit.locus.lat}-${hit.locus.lng}-${index}`} className="rounded-md border border-border bg-hover p-3">
                      <div className="mb-1 flex items-center justify-between gap-2">
                        <div className="flex items-center gap-1 text-sm font-medium">
                          <MapPin className="h-4 w-4" />
                          {hit.locus.lat.toFixed(5)}, {hit.locus.lng.toFixed(5)}
                        </div>
                        <div className="text-xs text-text-muted">{hit.max_distance_m.toFixed(1)} m</div>
                      </div>
                      <div className="mb-2 text-xs text-text-muted">
                        {hit.time_gap_minutes.toFixed(1)} min · {hit.sources.join(', ')}
                      </div>
                      <div className="space-y-1">
                        {hit.evidence.map((point) => (
                          <div key={`${point.entity_id}-${point.record_id}`} className="text-xs">
                            <span className="font-medium">{point.entity_name || point.entity_id.slice(0, 8)}</span>
                            <span className="text-text-muted"> · {point.source} · {formatDate(point.occurred_at)}</span>
                            {point.label && <div className="truncate text-text-muted">{point.label}</div>}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Card>

          <Card title="Digital Shared Edges">
            {result.digital.length === 0 ? (
              <EmptyState title="No digital overlaps" description="No shared groups, direct interactions, or shared edges matched." />
            ) : (
              <div className="space-y-2">
                {result.digital.map((edge, index) => (
                  <div key={`${edge.type}-${edge.source}-${edge.label}-${index}`} className="rounded-md border border-border bg-hover p-3">
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-2">
                        <PlatformBadge source={edge.source} />
                        <span className="truncate text-sm font-medium">{edge.label || humanize(edge.type)}</span>
                      </div>
                      <span className="text-xs text-text-muted">{resultSortLabel(edge)}</span>
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {edge.entities.map((item) => (
                        <Link
                          key={`${edge.type}-${edge.source}-${index}-${item.id}`}
                          to={`/entities/${item.id}`}
                          className="rounded-full bg-background px-2 py-0.5 text-xs text-text-secondary hover:text-text-primary"
                        >
                          {item.name || item.id.slice(0, 8)}
                        </Link>
                      ))}
                    </div>
                    {(edge.first_seen_at || edge.last_seen_at || edge.peer) && (
                      <div className="mt-2 text-xs text-text-muted">
                        {edge.peer && <>peer: {edge.peer.name || edge.peer.id.slice(0, 8)} · </>}
                        {edge.first_seen_at && <>first: {formatDate(edge.first_seen_at)} · </>}
                        {edge.last_seen_at && <>last: {formatDate(edge.last_seen_at)}</>}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  )
}
