import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { GitFork, RefreshCw } from 'lucide-react'
import { api, GraphExplainEdge, GraphNodesEdges, GraphPivots } from '../api'
import { PageHeader } from '../components/ui/PageHeader'
import { Card } from '../components/ui/Card'
import { MetricCard } from '../components/ui/MetricCard'
import { StatusBadge } from '../components/ui/StatusBadge'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { ErrorState } from '../components/ui/ErrorState'
import GraphRenderer from '../components/GraphRenderer'

type Overview = Awaited<ReturnType<typeof api.getGraphOverview>>
type Confidence = 'all' | 'hard' | 'strong' | 'weak' | 'context-only'

const CONFIDENCE: Confidence[] = ['all', 'hard', 'strong', 'weak', 'context-only']
const DEFAULT_FILTERS = {
  confidence: 'all' as Confidence,
  relationshipType: '',
  source: '',
  includeContextOnly: false,
}

function bucketStatus(bucket: string): Parameters<typeof StatusBadge>[0]['status'] {
  if (bucket === 'hard' || bucket === 'strong') return 'success'
  if (bucket === 'weak') return 'warning'
  if (bucket === 'context-only') return 'idle'
  return 'idle'
}

function fmt(iso: string | null) {
  return iso ? new Date(iso).toLocaleString() : '-'
}

function edgeKey(edge: GraphExplainEdge, index: number) {
  return edge.id || `${edge.from_entity_id}:${edge.to_entity_id}:${edge.relationship_type}:${index}`
}

function EdgeList({ edges }: { edges: GraphExplainEdge[] }) {
  if (edges.length === 0) return <div className="text-sm text-text-muted">No edges for this request.</div>
  return (
    <div className="space-y-2">
      {edges.map((edge, index) => (
        <div key={edgeKey(edge, index)} className="rounded-md border border-border bg-background p-3">
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="text-sm font-medium">{edge.relationship_type.replace(/_/g, ' ')}</span>
            <StatusBadge status={bucketStatus(edge.confidence_bucket)} label={edge.confidence_bucket} />
          </div>
          <div className="text-xs text-text-muted">
            {edge.source || 'unknown source'} · weight {edge.weight} · last seen {fmt(edge.last_seen_at)}
          </div>
          {edge.why && <div className="mt-2 text-sm text-text-secondary">{edge.why}</div>}
          {edge.evidence_refs?.length > 0 && (
            <div className="mt-2 text-xs text-text-muted">{edge.evidence_refs.length} evidence refs</div>
          )}
        </div>
      ))}
    </div>
  )
}

export default function GraphPage() {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filters, setFilters] = useState(() => {
    try {
      return { ...DEFAULT_FILTERS, ...JSON.parse(window.localStorage.getItem('ua:graph:filters') || '{}') }
    } catch {
      return DEFAULT_FILTERS
    }
  })
  const [pathForm, setPathForm] = useState({ from: '', to: '', hops: '3' })
  const [pathResult, setPathResult] = useState<{ path: GraphExplainEdge[]; hops: number; found: boolean } | null>(null)
  const [pivotEntity, setPivotEntity] = useState('')
  const [pivots, setPivots] = useState<GraphPivots | null>(null)
  const [actionError, setActionError] = useState('')

  // ── WebGL graph state ──────────────────────────────────────────────────────
  const [graphLimit, setGraphLimit] = useState(300)
  const [graphMinWeight, setGraphMinWeight] = useState(0)
  const [graphRelType, setGraphRelType] = useState('')
  const [graphData, setGraphData] = useState<GraphNodesEdges | null>(null)
  const [graphLoading, setGraphLoading] = useState(false)
  const [graphError, setGraphError] = useState('')

  const loadGraph = () => {
    setGraphLoading(true)
    setGraphError('')
    api.getGraphNodesEdges({
      limit: graphLimit,
      min_weight: graphMinWeight,
      relationship_type: graphRelType || undefined,
    })
      .then(setGraphData)
      .catch((e: unknown) => setGraphError(e instanceof Error ? e.message : String(e)))
      .finally(() => setGraphLoading(false))
  }

  const load = () => {
    setLoading(true)
    setError('')
    api.getGraphOverview()
      .then(setOverview)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  // Auto-load overview and sigma graph on mount
  useEffect(load, [])
  useEffect(() => {
    setGraphLoading(true)
    api.getGraphNodesEdges({ limit: 300, min_weight: 0 })
      .then(setGraphData)
      .catch((e: unknown) => setGraphError(e instanceof Error ? e.message : String(e)))
      .finally(() => setGraphLoading(false))
  }, [])
  useEffect(() => {
    window.localStorage.setItem('ua:graph:filters', JSON.stringify(filters))
  }, [filters])

  const topConnections = useMemo(() => {
    const rows = overview?.top_connections ?? []
    const type = filters.relationshipType.trim().toLowerCase()
    return rows.filter((row) => !type || row.type.toLowerCase().includes(type))
  }, [overview, filters.relationshipType])

  const runPath = async () => {
    setActionError('')
    setPathResult(null)
    if (!pathForm.from.trim() || !pathForm.to.trim()) {
      setActionError('Both entity IDs are required for path explanation.')
      return
    }
    try {
      const result = await api.getGraphPath(
        pathForm.from.trim(),
        pathForm.to.trim(),
        filters.includeContextOnly,
        Math.max(1, Number(pathForm.hops) || 3),
        {
          confidence_bucket: filters.confidence === 'all' ? undefined : filters.confidence,
          relationship_type: filters.relationshipType || undefined,
          source: filters.source || undefined,
        },
      )
      setPathResult(result)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    }
  }

  const runPivots = async () => {
    setActionError('')
    setPivots(null)
    if (!pivotEntity.trim()) {
      setActionError('Entity ID is required for pivots.')
      return
    }
    try {
      const result = await api.getGraphPivots(
        pivotEntity.trim(),
        filters.includeContextOnly,
        {
          confidence_bucket: filters.confidence === 'all' ? undefined : filters.confidence,
          relationship_type: filters.relationshipType || undefined,
          source: filters.source || undefined,
        },
      )
      setPivots(result)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    }
  }

  if (loading) return <LoadingSpinner label="Loading graph overview..." />
  if (error) return <ErrorState message={`Failed to load graph overview: ${error}`} onRetry={load} />

  return (
    <div>
      <PageHeader
        title="Graph"
        description="Connection overview, path explanations, pivots, confidence filters, and browser-local view state."
        actions={<button type="button" onClick={load}><RefreshCw className="mr-1 inline h-3.5 w-3.5" />Refresh</button>}
      />

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MetricCard label="Relationships" value={overview?.total_relationships ?? 0} icon={<GitFork className="h-4 w-4" />} />
        <MetricCard label="People in graph" value={overview?.entities_in_graph ?? 0} />
        <MetricCard label="WhatsApp co-members" value={overview?.whatsapp_co_members ?? 0} />
        <MetricCard label="Top connections" value={overview?.top_connections.length ?? 0} />
      </div>

      {/* ── WebGL Relationship Graph (sigma.js + graphology) ──────────────── */}
      <Card className="mb-6">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold">WebGL Relationship Graph</span>
            <span className="text-xs text-text-muted">sigma.js · graphology · click node → entity</span>
          </div>
          {graphData && (
            <span className="text-xs text-text-muted" data-testid="graph-node-count">
              {graphData.nodes.length} nodes · {graphData.edges.length} edges
            </span>
          )}
        </div>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <label className="text-sm text-text-secondary">Limit</label>
          <input
            type="number"
            value={graphLimit}
            onChange={(e) => setGraphLimit(Math.max(1, Math.min(1000, Number(e.target.value) || 300)))}
            min={1}
            max={1000}
            className="w-24 rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          />
          <label className="text-sm text-text-secondary">Min weight</label>
          <input
            type="number"
            value={graphMinWeight}
            onChange={(e) => setGraphMinWeight(Math.max(0, Number(e.target.value) || 0))}
            min={0}
            step={1}
            className="w-20 rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          />
          <input
            value={graphRelType}
            onChange={(e) => setGraphRelType(e.target.value)}
            placeholder="relationship type filter"
            className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          />
          <button type="button" onClick={loadGraph}>Load Graph</button>
        </div>
        {graphLoading ? (
          <LoadingSpinner label="Loading graph data..." />
        ) : graphError ? (
          <div className="text-sm text-error">{graphError}</div>
        ) : (
          <GraphRenderer nodes={graphData?.nodes ?? []} edges={graphData?.edges ?? []} />
        )}
      </Card>

      <Card className="mb-6">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <select
            value={filters.confidence}
            onChange={(e) => setFilters({ ...filters, confidence: e.target.value as Confidence })}
            aria-label="Confidence filter"
          >
            {CONFIDENCE.map((bucket) => <option key={bucket} value={bucket}>{bucket}</option>)}
          </select>
          <input
            value={filters.relationshipType}
            onChange={(e) => setFilters({ ...filters, relationshipType: e.target.value })}
            placeholder="relationship type"
            className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          />
          <input
            value={filters.source}
            onChange={(e) => setFilters({ ...filters, source: e.target.value })}
            placeholder="source"
            className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          />
          <label className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1.5 text-sm text-text-secondary">
            <input
              type="checkbox"
              checked={filters.includeContextOnly}
              onChange={(e) => setFilters({ ...filters, includeContextOnly: e.target.checked })}
            />
            context-only
          </label>
        </div>
        <div className="text-xs text-text-muted">Filters are saved in this browser and apply to path and pivot requests.</div>
      </Card>

      <div className="mb-6 grid gap-3 lg:grid-cols-2">
        <Card>
          <div className="mb-3 text-sm font-semibold">Path explanation</div>
          <div className="mb-3 grid gap-2 md:grid-cols-[1fr_1fr_0.4fr_auto]">
            <input
              value={pathForm.from}
              onChange={(e) => setPathForm({ ...pathForm, from: e.target.value })}
              placeholder="from entity id"
              className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            />
            <input
              value={pathForm.to}
              onChange={(e) => setPathForm({ ...pathForm, to: e.target.value })}
              placeholder="to entity id"
              className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            />
            <input
              value={pathForm.hops}
              onChange={(e) => setPathForm({ ...pathForm, hops: e.target.value })}
              type="number"
              min={1}
              max={5}
              className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            />
            <button type="button" onClick={runPath}>Explain</button>
          </div>
          {pathResult && (
            <div>
              <div className="mb-2 flex items-center gap-2 text-sm">
                <StatusBadge status={pathResult.found ? 'success' : 'idle'} label={pathResult.found ? `${pathResult.hops} hops` : 'not found'} />
              </div>
              <EdgeList edges={pathResult.path} />
            </div>
          )}
        </Card>

        <Card>
          <div className="mb-3 text-sm font-semibold">Pivots</div>
          <div className="mb-3 grid gap-2 md:grid-cols-[1fr_auto]">
            <input
              value={pivotEntity}
              onChange={(e) => setPivotEntity(e.target.value)}
              placeholder="entity id"
              className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            />
            <button type="button" onClick={runPivots}>Load pivots</button>
          </div>
          {pivots && (
            <div className="space-y-3">
              <div className="text-sm text-text-muted">{pivots.total.toLocaleString()} pivot edges</div>
              {Object.entries(pivots.groups).map(([group, edges]) => (
                <div key={group}>
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">{group.replace(/_/g, ' ')}</div>
                  <EdgeList edges={edges.slice(0, 5)} />
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {actionError && <Card className="mb-6 border-error/40 bg-error/10 text-sm text-error">{actionError}</Card>}

      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <div className="mb-3 text-sm font-semibold">Top explained edges</div>
          {topConnections.length === 0 ? (
            <div className="text-sm text-text-muted">No top connections match the current filter.</div>
          ) : (
            <div className="space-y-2">
              {topConnections.slice(0, 12).map((edge, index) => (
                <div key={`${edge.entity_a.id}:${edge.entity_b.id}:${edge.type}:${index}`} className="rounded-md border border-border bg-background p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-sm font-medium">
                      <Link to={`/entities/${edge.entity_a.id}`}>{edge.entity_a.name || edge.entity_a.id.slice(0, 8)}</Link>
                      {' '}to{' '}
                      <Link to={`/entities/${edge.entity_b.id}`}>{edge.entity_b.name || edge.entity_b.id.slice(0, 8)}</Link>
                    </div>
                    <div className="text-xs text-text-muted">{edge.type.replace(/_/g, ' ')} · {edge.weight}</div>
                  </div>
                  {edge.why && <div className="mt-2 text-xs text-text-muted">{edge.why}</div>}
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <div className="mb-3 text-sm font-semibold">Relationship type coverage</div>
          <div className="overflow-x-auto">
            <table>
              <thead><tr><th>Type</th><th>Edges</th></tr></thead>
              <tbody>
                {Object.entries(overview?.relationship_type_counts ?? {}).map(([type, count]) => (
                  <tr key={type}>
                    <td>{type.replace(/_/g, ' ')}</td>
                    <td>{Number(count).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  )
}
