import { useNavigate } from 'react-router-dom'
import { FaceAvatar } from './FaceAvatar'

/**
 * Ego-network graph (SVG, zero deps). Center entity + neighbours on a ring,
 * edge thickness = relationship strength, faces as nodes (foreignObject reuses
 * FaceAvatar). Click a neighbour to recenter the investigation.
 */
type Net = {
  center: { id: string; name: string | null; face: string | null }
  nodes: { id: string; name: string | null; weight: number; types: string[]; face: string | null }[]
}

export function NetworkGraph({ data }: { data: Net }) {
  const nav = useNavigate()
  const nodes = data.nodes.slice(0, 30)
  if (nodes.length === 0) return <div className="text-sm text-muted">No connections found.</div>

  const W = 680, H = 460, cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 64
  const maxW = Math.max(1, ...nodes.map((n) => n.weight || 0))
  const placed = nodes.map((n, i) => {
    const a = (i / nodes.length) * Math.PI * 2 - Math.PI / 2
    return { ...n, x: cx + R * Math.cos(a), y: cy + R * Math.sin(a) }
  })

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: 'block' }}>
      {placed.map((n) => (
        <line
          key={'e' + n.id}
          x1={cx} y1={cy} x2={n.x} y2={n.y}
          stroke="var(--border, #2a2a33)"
          strokeWidth={0.5 + 3 * ((n.weight || 0) / maxW)}
          opacity={0.5}
        />
      ))}

      {placed.map((n) => (
        <g key={n.id} style={{ cursor: 'pointer' }} onClick={() => nav(`/entities/${n.id}`)}>
          <foreignObject x={n.x - 16} y={n.y - 16} width={32} height={32}>
            <FaceAvatar url={n.face} name={n.name} size={32} />
          </foreignObject>
          <text x={n.x} y={n.y + 28} fontSize={9} fill="var(--muted, #8a8a99)" textAnchor="middle">
            {(n.name || n.id.slice(0, 6)).slice(0, 16)}
          </text>
        </g>
      ))}

      <g>
        <circle cx={cx} cy={cy} r={26} fill="none" stroke="var(--accent, #863bff)" strokeWidth={2} />
        <foreignObject x={cx - 24} y={cy - 24} width={48} height={48}>
          <FaceAvatar url={data.center.face} name={data.center.name} size={48} />
        </foreignObject>
        <text x={cx} y={cy + 42} fontSize={11} fontWeight={600} fill="var(--fg, #e8e8ea)" textAnchor="middle">
          {(data.center.name || 'center').slice(0, 18)}
        </text>
      </g>
    </svg>
  )
}
