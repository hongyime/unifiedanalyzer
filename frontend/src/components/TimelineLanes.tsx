/**
 * Multi-platform timeline swimlanes (SVG, zero deps). One lane per platform,
 * a dot per event, year gridlines, and red alert markers — turns thousands of
 * events into a legible "when was this person active where" view.
 */
type Lane = { source: string; events: { t: number; type: string | null }[] }
type Data = {
  lanes: Lane[]
  alerts: { type: string; t: number }[]
  min_t: number | null
  max_t: number | null
  total: number
}

const PLATFORM_COLOR: Record<string, string> = {
  instagram: '#e1306c', tiktok: '#69c9d0', x: '#1d9bf0', twitter: '#1d9bf0',
  facebook: '#1877f2', telegram: '#2aabee', whatsapp: '#25d366', youtube: '#ff0000',
  strava: '#fc4c02', threads: '#a78bfa', lemon8: '#ffd400', github: '#9aa0a6',
  beeper: '#6f42c1', website: '#10b981', search: '#f59e0b',
}
const colorFor = (s: string) => PLATFORM_COLOR[s.toLowerCase()] || '#7cc4a3'

export function TimelineLanes({ data }: { data: Data }) {
  const { lanes, alerts, min_t, max_t } = data
  if (!min_t || !max_t || lanes.length === 0) {
    return <div className="text-sm text-muted">No timeline events.</div>
  }
  const W = 1000, rowH = 26, padL = 90, padR = 24, padT = 22, padB = 6
  const H = padT + lanes.length * rowH + padB
  const span = Math.max(1, max_t - min_t)
  const x = (t: number) => padL + ((t - min_t) / span) * (W - padL - padR)

  const startY = new Date(min_t * 1000).getFullYear()
  const endY = new Date(max_t * 1000).getFullYear()
  const yearTicks: { y: number; x: number }[] = []
  for (let y = startY; y <= endY; y++) {
    const t = new Date(y, 0, 1).getTime() / 1000
    if (t >= min_t && t <= max_t) yearTicks.push({ y, x: x(t) })
  }

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: 'block' }}>
      {yearTicks.map((t) => (
        <g key={t.y}>
          <line x1={t.x} y1={padT - 4} x2={t.x} y2={H} stroke="var(--color-border, #2a2a33)" strokeWidth={1} />
          <text x={t.x + 3} y={padT - 8} fontSize={9} fill="var(--color-muted, #8a8a99)">{t.y}</text>
        </g>
      ))}
      {alerts.map((a, i) => (
        <line key={i} x1={x(a.t)} y1={padT - 2} x2={x(a.t)} y2={H} stroke="#e0564a" strokeWidth={1} opacity={0.45}>
          <title>{a.type}</title>
        </line>
      ))}
      {lanes.map((lane, li) => {
        const y = padT + li * rowH + rowH / 2
        const col = colorFor(lane.source)
        return (
          <g key={lane.source}>
            <text x={0} y={y + 3} fontSize={10} fill="var(--color-fg, #e8e8ea)">{lane.source}</text>
            <line x1={padL} y1={y} x2={W - padR} y2={y} stroke="var(--color-border, #222)" strokeWidth={0.5} />
            {lane.events.map((e, ei) => (
              <circle key={ei} cx={x(e.t)} cy={y} r={2.2} fill={col} opacity={0.8} />
            ))}
            <text x={W - padR + 2} y={y + 3} fontSize={8} fill="var(--color-muted, #8a8a99)" textAnchor="start">
              {lane.events.length}
            </text>
          </g>
        )
      })}
    </svg>
  )
}
