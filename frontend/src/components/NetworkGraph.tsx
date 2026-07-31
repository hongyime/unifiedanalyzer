import { useNavigate } from 'react-router'
import { FaceAvatar } from './FaceAvatar'

/**
 * Ego-network graph (SVG, zero deps). Center entity + neighbours on a ring,
 * edge thickness = relationship strength, faces as nodes (foreignObject reuses
 * FaceAvatar). Click a neighbour to recenter the investigation.
 */
type Net = {
  center: { id: string; name: string | null; face: string | null }
  nodes: {
    id: string
    name: string | null
    weight?: number
    types?: string[]
    face: string | null
    why?: string | null
    out?: { total: number; by_type: Record<string, number>; last_ts: string | null }
    in?: { total: number; by_type: Record<string, number>; last_ts: string | null }
  }[]
}

export function NetworkGraph({ data }: { data: Net }) {
  const nav = useNavigate()
  const nodes = data.nodes.slice(0, 30)
  if (nodes.length === 0) return <div className="text-sm text-muted">No connections found.</div>

  const W = 680, H = 460, cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 64
  const maxW = Math.max(1, ...nodes.map((n) => n.weight ?? Math.max(n.out?.total ?? 0, n.in?.total ?? 0, 0)))
  const TYPE_COLORS: Record<string, string> = {
    reacted: '#f59e0b',
    replied: '#06b6d4',
    commented: '#10b981',
    mentioned: '#8b5cf6',
    tagged: '#ef4444',
    followed: '#3b82f6',
    dm: '#f97316',
    forwarded: '#64748b',
    face_coappear: '#ec4899',
  }
  const placed = nodes.map((n, i) => {
    const a = (i / nodes.length) * Math.PI * 2 - Math.PI / 2
    return { ...n, x: cx + R * Math.cos(a), y: cy + R * Math.sin(a) }
  })
  const dominantType = (counts?: Record<string, number>) =>
    Object.entries(counts || {}).sort((a, b) => b[1] - a[1])[0]?.[0] || 'interaction'
  const edgeColor = (kind?: string) => TYPE_COLORS[kind || 'interaction'] || 'var(--color-border, #2a2a33)'
  const tooltip = (n: Net['nodes'][number]) => {
    if (!n.out && !n.in) {
      return `${n.name || n.id}\n${(n.types || []).join(', ')}${n.why ? `\n${n.why}` : ''}`
    }
    const outText = Object.entries(n.out?.by_type || {}).map(([k, v]) => `${k}:${v}`).join(', ') || 'none'
    const inText = Object.entries(n.in?.by_type || {}).map(([k, v]) => `${k}:${v}`).join(', ') || 'none'
    return `${n.name || n.id}\nout: ${outText}\nin: ${inText}${n.why ? `\n${n.why}` : ''}`
  }
  const offsetLine = (x1: number, y1: number, x2: number, y2: number, delta: number) => {
    const dx = x2 - x1
    const dy = y2 - y1
    const len = Math.hypot(dx, dy) || 1
    const ox = (-dy / len) * delta
    const oy = (dx / len) * delta
    return { x1: x1 + ox, y1: y1 + oy, x2: x2 + ox, y2: y2 + oy }
  }

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: 'block' }}>
      <defs>
        {Object.entries(TYPE_COLORS).map(([kind, color]) => (
          <marker
            key={kind}
            id={`arrow-${kind}`}
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="5"
            markerHeight="5"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill={color} />
          </marker>
        ))}
      </defs>

      {placed.map((n) => {
        if (!n.out && !n.in) {
          return (
            <line
              key={'e' + n.id}
              x1={cx} y1={cy} x2={n.x} y2={n.y}
              stroke="var(--color-border, #2a2a33)"
              strokeWidth={0.5 + 3 * ((n.weight || 0) / maxW)}
              opacity={0.5}
            />
          )
        }
        const outType = dominantType(n.out?.by_type)
        const inType = dominantType(n.in?.by_type)
        const outLine = offsetLine(cx, cy, n.x, n.y, -4)
        const inLine = offsetLine(n.x, n.y, cx, cy, 4)
        return (
          <g key={'e' + n.id}>
            {(n.out?.total || 0) > 0 && (
              <line
                x1={outLine.x1} y1={outLine.y1} x2={outLine.x2} y2={outLine.y2}
                stroke={edgeColor(outType)}
                strokeWidth={1 + 3 * ((n.out?.total || 0) / maxW)}
                opacity={0.9}
                markerEnd={`url(#arrow-${outType})`}
              >
                <title>{tooltip(n)}</title>
              </line>
            )}
            {(n.in?.total || 0) > 0 && (
              <line
                x1={inLine.x1} y1={inLine.y1} x2={inLine.x2} y2={inLine.y2}
                stroke={edgeColor(inType)}
                strokeWidth={1 + 3 * ((n.in?.total || 0) / maxW)}
                opacity={0.6}
                markerEnd={`url(#arrow-${inType})`}
              >
                <title>{tooltip(n)}</title>
              </line>
            )}
            <text
              x={(cx + n.x) / 2}
              y={(cy + n.y) / 2 - 8}
              fontSize={9}
              fill="var(--color-muted, #8a8a99)"
              textAnchor="middle"
            >
              {outType === 'interaction' ? dominantType(n.in?.by_type) : outType}
            </text>
          </g>
        )
      })}

      {placed.map((n) => (
        <g key={n.id} style={{ cursor: 'pointer' }} onClick={() => nav(`/entities/${n.id}`)}>
          <title>{tooltip(n)}</title>
          <foreignObject x={n.x - 16} y={n.y - 16} width={32} height={32}>
            <FaceAvatar url={n.face} name={n.name} size={32} />
          </foreignObject>
          <text x={n.x} y={n.y + 28} fontSize={9} fill="var(--color-muted, #8a8a99)" textAnchor="middle">
            {(n.name || n.id.slice(0, 6)).slice(0, 16)}
          </text>
        </g>
      ))}

      <g>
        <circle cx={cx} cy={cy} r={26} fill="none" stroke="var(--color-accent, #863bff)" strokeWidth={2} />
        <foreignObject x={cx - 24} y={cy - 24} width={48} height={48}>
          <FaceAvatar url={data.center.face} name={data.center.name} size={48} />
        </foreignObject>
        <text x={cx} y={cy + 42} fontSize={11} fontWeight={600} fill="var(--color-fg, #e8e8ea)" textAnchor="middle">
          {(data.center.name || 'center').slice(0, 18)}
        </text>
      </g>
    </svg>
  )
}
