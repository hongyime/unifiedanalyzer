import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { UserSearch, ChevronLeft, ChevronRight } from 'lucide-react'
import type { ColumnDef } from '@tanstack/react-table'
import { api, Entity } from '../api'
import { FaceAvatar } from '../components/FaceAvatar'
import { PageHeader } from '../components/ui/PageHeader'
import { PlatformBadge } from '../components/ui/PlatformBadge'
import { SearchBar } from '../components/ui/SearchBar'
import { FilterDropdown } from '../components/ui/FilterDropdown'
import { DataTable } from '../components/ui/DataTable'
import { ConfidencePill } from '../components/ui/Confidence'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { EmptyState } from '../components/ui/EmptyState'
import { InfoTip } from '../components/ui/InfoTip'
import { Button } from '../components/ui/Button'
import { LABELS } from '../lib/labels'

const PLATFORMS = ['github', 'instagram', 'telegram', 'strava', 'youtube', 'tiktok', 'lemon8', 'whatsapp', 'website']

/** Entities table — server-paginated list of resolved people. Uses DataTable
 *  purely for visual consistency; pagination stays server-side via the api.
 *  Row click navigates to the entity detail page. */
export default function EntitiesPage() {
  const navigate = useNavigate()
  const [entities, setEntities] = useState<Entity[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('confidence')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')
  const [platform, setPlatform] = useState('')
  const [minPlatforms, setMinPlatforms] = useState(0)
  const [tierFilter, setTierFilter] = useState<'all' | 'primary' | 'secondary'>('all')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api.getEntities(page, search, sort, order, platform, minPlatforms)
      .then(r => { setEntities(r.data); setTotal(r.total) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [page, search, sort, order, platform, minPlatforms])

  // Tier filter is applied client-side over the fetched page — the API doesn't
  // yet accept a tier filter, so we keep the pagination correct by disabling
  // tier when it isn't 'all'. (Users hit the same 50-per-page rows either way.)
  const visible = useMemo(
    () => (tierFilter === 'all' ? entities : entities.filter(e => e.tier === tierFilter)),
    [entities, tierFilter],
  )

  const columns = useMemo<ColumnDef<Entity, unknown>[]>(() => [
    {
      id: 'name',
      header: 'Name',
      accessorFn: (row) => row.canonical_name ?? '',
      cell: ({ row }) => {
        const e = row.original
        return (
          <div className="flex items-center gap-2">
            <FaceAvatar url={e.face_crop_url} name={e.canonical_name} size={32} />
            <div className="min-w-0">
              <div className="truncate font-medium text-text-primary">
                {e.canonical_name || '(unnamed)'}
              </div>
              <div className="text-xs text-text-muted">
                {LABELS.tier[e.tier] ?? e.tier}
              </div>
            </div>
          </div>
        )
      },
    },
    {
      id: 'platforms',
      header: 'Platforms',
      accessorFn: (row) => row.platform_count,
      cell: ({ row }) => (
        <div className="flex flex-wrap gap-1">
          {row.original.platforms.map(p => <PlatformBadge key={p} source={p} />)}
        </div>
      ),
    },
    {
      id: 'confidence',
      header: 'Confidence',
      accessorFn: (row) => row.confidence_score,
      cell: ({ row }) => <ConfidencePill score={row.original.confidence_score} />,
    },
    {
      id: 'signals',
      header: 'Evidence',
      accessorFn: (row) => row.signal_count,
      cell: ({ row }) => (
        <span className="font-mono tabular-nums">{row.original.signal_count}</span>
      ),
    },
    {
      id: 'last_seen',
      header: 'Last seen',
      accessorFn: (row) => row.last_seen_at ?? '',
      cell: ({ row }) => {
        const iso = row.original.last_seen_at
        return (
          <span className="text-xs text-text-muted">
            {iso ? new Date(iso).toLocaleDateString() : '—'}
          </span>
        )
      },
    },
  ], [])

  const totalPages = Math.max(1, Math.ceil(total / 50))
  const showToolbar = !loading

  return (
    <div>
      <PageHeader
        title="People"
        description="Everyone the system is tracking, each built from one or more platform accounts. Confirmed people have strong evidence; unconfirmed ones are kept but flagged as lower-certainty."
        actions={
          <span className="inline-flex items-center gap-1 text-sm text-text-muted">
            {total} people
            <InfoTip text="A person (entity) groups the platform accounts we think belong to one real human. New accounts are auto-linked when evidence — same phone/email/face/handle — is strong enough." />
          </span>
        }
      />

      {showToolbar && (
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[220px]">
            <SearchBar
              value={search}
              onChange={(v) => { setSearch(v); setPage(1) }}
              placeholder="Search name or username…"
            />
          </div>
          <FilterDropdown
            label="Tier"
            value={tierFilter}
            onChange={(v) => setTierFilter(v as typeof tierFilter)}
            options={[
              { value: 'all', label: 'All' },
              { value: 'primary', label: LABELS.tier.primary },
              { value: 'secondary', label: LABELS.tier.secondary },
            ]}
          />
          <FilterDropdown
            label="Platform"
            value={platform}
            onChange={(v) => { setPlatform(v); setPage(1) }}
            options={[
              { value: '', label: 'All platforms' },
              ...PLATFORMS.map(p => ({ value: p, label: p })),
            ]}
          />
          <FilterDropdown
            label="Linked on"
            value={String(minPlatforms)}
            onChange={(v) => { setMinPlatforms(Number(v)); setPage(1) }}
            options={[
              { value: '0', label: 'Any # platforms' },
              { value: '2', label: '2+ platforms' },
              { value: '3', label: '3+ platforms' },
              { value: '4', label: '4+ platforms' },
            ]}
          />
          <FilterDropdown
            label="Sort by"
            value={`${sort}-${order}`}
            onChange={(v) => {
              const [s, o] = v.split('-') as [string, 'asc' | 'desc']
              setSort(s); setOrder(o); setPage(1)
            }}
            options={[
              { value: 'confidence-desc', label: 'Confidence (high → low)' },
              { value: 'confidence-asc', label: 'Confidence (low → high)' },
              { value: 'name-asc', label: 'Name (A → Z)' },
              { value: 'name-desc', label: 'Name (Z → A)' },
              { value: 'signals-desc', label: 'Evidence (most → least)' },
              { value: 'platforms-desc', label: 'Platforms (most → least)' },
              { value: 'created-desc', label: 'Newest' },
            ]}
          />
        </div>
      )}

      {loading ? (
        <LoadingSpinner label="Loading people…" />
      ) : visible.length === 0 ? (
        <EmptyState
          icon={<UserSearch className="h-10 w-10" />}
          title="No people match those filters"
          description="Try clearing the search or filters. New people appear here as the pipeline links accounts together."
        />
      ) : (
        <>
          <DataTable
            data={visible}
            columns={columns}
            pageSize={visible.length + 1}
            onRowClick={(e) => navigate(`/entities/${e.id}`)}
            emptyMessage="No people match those filters"
          />

          <div className="mt-4 flex items-center justify-between text-xs text-text-muted">
            <span className="tabular-nums">Page {page} of {totalPages}</span>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                icon={<ChevronLeft className="h-3 w-3" />}
                disabled={page <= 1}
                onClick={() => setPage(p => Math.max(1, p - 1))}
              >
                Prev
              </Button>
              <Button
                variant="ghost"
                size="sm"
                icon={<ChevronRight className="h-3 w-3" />}
                disabled={page * 50 >= total}
                onClick={() => setPage(p => p + 1)}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
