import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router'
import Graph from 'graphology'
import Sigma from 'sigma'
import forceAtlas2 from 'graphology-layout-forceatlas2'
import louvain from 'graphology-communities-louvain'
import type { GraphNode, GraphEdge } from '../api'

// Expose graph node count on window for Playwright/test verification
declare global {
  interface Window {
    __sigmaNodeCount?: number
  }
}

const COMMUNITY_PALETTE = [
  '#6366f1', '#f59e0b', '#10b981', '#ef4444', '#3b82f6',
  '#8b5cf6', '#ec4899', '#14b8a6', '#f97316', '#a3e635',
  '#06b6d4', '#e11d48', '#84cc16', '#f43f5e', '#0ea5e9',
]

function communityColor(community: unknown): string {
  if (community === undefined || community === null) return COMMUNITY_PALETTE[0]
  const idx = typeof community === 'number'
    ? community
    : parseInt(String(community), 10)
  const safe = isNaN(idx) ? 0 : Math.abs(idx)
  return COMMUNITY_PALETTE[safe % COMMUNITY_PALETTE.length]
}

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export default function GraphRenderer({ nodes, edges }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  // Use ref for hovered node so reducers always read fresh value without
  // requiring Sigma to be re-created on every state change.
  const hoveredNodeRef = useRef<string | null>(null)

  useEffect(() => {
    if (!containerRef.current || nodes.length === 0) return

    // ── Build graphology graph ──────────────────────────────────────────────
    const graph = new Graph({ type: 'undirected', multi: false })

    const maxDegree = Math.max(...nodes.map((n) => n.degree), 1)
    for (const n of nodes) {
      graph.addNode(n.id, {
        label: n.label,
        x: Math.random() * 100,
        y: Math.random() * 100,
        size: 4 + (n.degree / maxDegree) * 12,
        color: COMMUNITY_PALETTE[0],
      })
    }

    const seenEdges = new Set<string>()
    const maxWeight = Math.max(...edges.map((e) => e.weight), 1)
    for (const e of edges) {
      // Normalise direction for undirected deduplication
      const key = [e.source, e.target].sort().join('||')
      if (seenEdges.has(key)) continue
      seenEdges.add(key)
      if (!graph.hasNode(e.source) || !graph.hasNode(e.target)) continue
      if (e.source === e.target) continue
      try {
        graph.addEdge(e.source, e.target, {
          weight: e.weight,
          size: 1 + (e.weight / maxWeight) * 3,
          color: '#9ca3af',
        })
      } catch {
        // addEdge can throw on duplicate — safe to skip
      }
    }

    // ── Community detection via Louvain ────────────────────────────────────
    try {
      louvain.assign(graph)
    } catch {
      // May fail on empty or disconnected graphs — skip communities
    }

    graph.forEachNode((nodeId) => {
      const community = graph.getNodeAttribute(nodeId, 'community')
      graph.setNodeAttribute(nodeId, 'color', communityColor(community))
    })

    // ── ForceAtlas2 layout (BOUNDED: 100 iterations) ───────────────────────
    try {
      forceAtlas2.assign(graph, { iterations: 100 })
    } catch {
      // Layout may fail on trivially small graphs — keep random positions
    }

    // ── Expose node count for test verification ───────────────────────────
    window.__sigmaNodeCount = graph.order

    // ── Mount Sigma renderer ───────────────────────────────────────────────
    const renderer = new Sigma(graph, containerRef.current, {
      renderEdgeLabels: false,
      nodeReducer(nodeId, data) {
        const hovered = hoveredNodeRef.current
        if (hovered === null) return data
        const neighbors = new Set(graph.neighbors(hovered))
        if (nodeId === hovered || neighbors.has(nodeId)) return data
        // Dim non-adjacent nodes
        return { ...data, color: '#e5e7eb', size: data.size * 0.6 }
      },
      edgeReducer(edgeId, data) {
        const hovered = hoveredNodeRef.current
        if (hovered === null) return data
        try {
          const [src, tgt] = graph.extremities(edgeId)
          if (src === hovered || tgt === hovered) return data
        } catch {
          // edge extremity lookup failed
        }
        return { ...data, color: '#f3f4f6', size: 0.4 }
      },
    })

    // ── Events ────────────────────────────────────────────────────────────
    renderer.on('clickNode', ({ node }: { node: string }) => {
      navigate(`/entities/${node}`)
    })

    renderer.on('enterNode', ({ node }: { node: string }) => {
      hoveredNodeRef.current = node
      renderer.refresh()
    })

    renderer.on('leaveNode', () => {
      hoveredNodeRef.current = null
      renderer.refresh()
    })

    return () => {
      renderer.kill()
      window.__sigmaNodeCount = undefined
    }
  }, [nodes, edges, navigate])

  if (nodes.length === 0) {
    return (
      <div className="flex h-[600px] w-full items-center justify-center rounded-lg border border-border bg-background text-sm text-text-muted">
        No graph data — try adjusting the filters above and click Load Graph.
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      data-testid="sigma-container"
      className="h-[600px] w-full rounded-lg border border-border bg-background"
    />
  )
}
