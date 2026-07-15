import { useEffect, useState } from 'react'

type Lane = { source: string; events: { t: number; type: string | null }[] }
type Data = {
  lanes: Lane[]
  alerts: { type: string; t: number }[]
  min_t: number | null
  max_t: number | null
  total: number
}
type Range = [number, number]

const PLATFORM_COLOR: Record<string, string> = {
  instagram: '#e1306c', tiktok: '#69c9d0', x: '#1d9bf0', twitter: '#1d9bf0',
  facebook: '#1877f2', telegram: '#2aabee', whatsapp: '#25d366', youtube: '#ff0000',
  strava: '#fc4c02', threads: '#a78bfa', lemon8: '#ffd400', github: '#9aa0a6',
  beeper: '#6f42c1', website: '#10b981', search: '#f59e0b',
}

const PRESET_WINDOWS = [
  { label: '1h', seconds: 60 * 60 },
  { label: '1d', seconds: 60 * 60 * 24 },
  { label: '1w', seconds: 60 * 60 * 24 * 7 },
  { label: '1m', seconds: 60 * 60 * 24 * 30 },
  { label: '1y', seconds: 60 * 60 * 24 * 365 },
]

const colorFor = (s: string) => PLATFORM_COLOR[s.toLowerCase()] || '#7cc4a3'

function clampRange(range: Range, min_t: number, max_t: number): Range {
  const start = Math.max(min_t, Math.min(range[0], max_t))
  const end = Math.max(min_t, Math.min(range[1], max_t))
  return start <= end ? [start, end] : [end, start]
}

function tickStep(span: number) {
  if (span <= 60 * 20) return 60
  if (span <= 60 * 60 * 6) return 60 * 5
  if (span <= 60 * 60 * 24 * 3) return 60 * 60
  if (span <= 60 * 60 * 24 * 31) return 60 * 60 * 24
  if (span <= 60 * 60 * 24 * 180) return 60 * 60 * 24 * 7
  if (span <= 60 * 60 * 24 * 365 * 3) return 60 * 60 * 24 * 30
  return 60 * 60 * 24 * 365
}

function formatTick(ts: number, span: number) {
  const date = new Date(ts * 1000)
  if (span <= 60 * 60 * 6) return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (span <= 60 * 60 * 24 * 31) return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
  if (span <= 60 * 60 * 24 * 365 * 3) return date.toLocaleDateString([], { year: 'numeric', month: 'short' })
  return String(date.getFullYear())
}

function buildTicks(start: number, end: number) {
  const span = Math.max(1, end - start)
  const step = tickStep(span)
  const first = Math.floor(start / step) * step
  const ticks: number[] = []
  for (let t = first; t <= end + step; t += step) {
    if (t >= start && t <= end) ticks.push(t)
  }
  return ticks
}

function bucketEvents(events: { t: number; type: string | null }[], start: number, end: number, width: number) {
  const inRange = events.filter((event) => event.t >= start && event.t <= end)
  if (inRange.length <= Math.max(160, width / 1.6)) {
    return inRange.map((event) => ({ t: event.t, count: 1, type: event.type }))
  }
  const bucketCount = Math.max(80, Math.floor(width / 3))
  const bucketSpan = Math.max(1, (end - start) / bucketCount)
  const buckets = new Map<number, { totalT: number; count: number; type: string | null }>()
  for (const event of inRange) {
    const idx = Math.min(bucketCount - 1, Math.floor((event.t - start) / bucketSpan))
    const current = buckets.get(idx)
    if (current) {
      current.totalT += event.t
      current.count += 1
      if (!current.type) current.type = event.type
    } else {
      buckets.set(idx, { totalT: event.t, count: 1, type: event.type })
    }
  }
  return Array.from(buckets.values())
    .map((bucket) => ({
      t: bucket.totalT / bucket.count,
      count: bucket.count,
      type: bucket.type,
    }))
    .sort((a, b) => a.t - b.t)
}

export function TimelineLanes({
  data,
  selectedRange,
  onRangeChange,
  highlightedTimes,
}: {
  data: Data
  selectedRange?: Range | null
  onRangeChange?: (range: Range | null) => void
  highlightedTimes?: number[]
}) {
  const { lanes, alerts, min_t, max_t } = data
  const [playing, setPlaying] = useState(false)
  // NOTE: hooks (useState above, useEffect below) must run on every render —
  // the "no timeline events" early-return lives AFTER all hooks (see below) so
  // the hook count stays stable when `data` changes between renders. Derive the
  // range with safe fallbacks so these values are defined even when empty.
  const safeMin = min_t ?? 0
  const safeMax = max_t ?? 0

  const W = 1000
  const rowH = 26
  const padL = 90
  const padR = 24
  const padT = 22
  const padB = 6
  const H = padT + Math.max(1, lanes.length) * rowH + padB
  const totalSpan = Math.max(1, safeMax - safeMin)
  const clampedRange: Range = selectedRange
    ? clampRange(selectedRange, safeMin, safeMax)
    : [safeMin, safeMax]
  const [rangeStart, rangeEnd] = clampedRange
  const span = Math.max(1, rangeEnd - rangeStart)
  const hasCustomRange = rangeStart > safeMin || rangeEnd < safeMax
  const ticks = buildTicks(rangeStart, rangeEnd)
  const x = (t: number) => padL + ((t - rangeStart) / span) * (W - padL - padR)

  const applyRange = (next: Range | null) => {
    if (!onRangeChange) return
    onRangeChange(next ? clampRange(next, safeMin, safeMax) : null)
  }

  const applyPreset = (seconds: number | null) => {
    if (!onRangeChange) return
    if (seconds == null || seconds >= totalSpan) {
      applyRange(null)
      return
    }
    const nextStart = Math.max(safeMin, rangeEnd - seconds)
    applyRange([nextStart, Math.min(safeMax, nextStart + seconds)])
  }

  const stepWindow = (direction: -1 | 1) => {
    if (!onRangeChange) return
    const width = hasCustomRange ? span : Math.min(totalSpan, 60 * 60 * 24 * 7)
    const step = Math.max(1, Math.round(width * 0.7))
    const baseStart = hasCustomRange ? rangeStart : safeMin
    const baseEnd = hasCustomRange ? rangeEnd : safeMin + width
    let nextStart = baseStart + direction * step
    let nextEnd = baseEnd + direction * step
    if (nextStart < safeMin) {
      nextStart = safeMin
      nextEnd = safeMin + width
    }
    if (nextEnd > safeMax) {
      nextEnd = safeMax
      nextStart = Math.max(safeMin, safeMax - width)
    }
    applyRange([nextStart, nextEnd])
  }

  useEffect(() => {
    if (!playing || !onRangeChange) return
    if (!hasCustomRange) {
      const seed = Math.min(totalSpan, 60 * 60 * 24 * 7)
      applyRange([safeMin, Math.min(safeMax, safeMin + seed)])
      return
    }
    const width = Math.max(1, span)
    const timer = window.setInterval(() => {
      const step = Math.max(1, Math.round(width * 0.18))
      let nextStart = rangeStart + step
      let nextEnd = rangeEnd + step
      if (nextEnd >= safeMax) {
        nextEnd = safeMax
        nextStart = Math.max(safeMin, safeMax - width)
        setPlaying(false)
      }
      applyRange([nextStart, nextEnd])
    }, 450)
    return () => window.clearInterval(timer)
  }, [playing, onRangeChange, hasCustomRange, rangeStart, rangeEnd, span, safeMin, safeMax, totalSpan])

  // Early-return AFTER all hooks so the hook order stays stable across renders.
  if (!min_t || !max_t || lanes.length === 0) {
    return <div className="text-sm text-muted">No timeline events.</div>
  }

  return (
    <div>
      {onRangeChange && (
        <div className="mb-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <button type="button" className="rounded border border-border px-2 py-1 hover:bg-hover" onClick={() => stepWindow(-1)}>
              Back
            </button>
            <button type="button" className="rounded border border-border px-2 py-1 hover:bg-hover" onClick={() => setPlaying((value) => !value)}>
              {playing ? 'Pause' : 'Play'}
            </button>
            <button type="button" className="rounded border border-border px-2 py-1 hover:bg-hover" onClick={() => stepWindow(1)}>
              Forward
            </button>
            {PRESET_WINDOWS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                className="rounded border border-border px-2 py-1 hover:bg-hover"
                onClick={() => applyPreset(preset.seconds)}
              >
                {preset.label}
              </button>
            ))}
            <button type="button" className="rounded border border-border px-2 py-1 hover:bg-hover" onClick={() => applyRange(null)}>
              All
            </button>
          </div>
          <div className="flex items-center justify-between text-xs text-text-muted">
            <span>{new Date(rangeStart * 1000).toLocaleString()}</span>
            <span>{Math.max(1, Math.round(span)).toLocaleString()} sec window</span>
            <span>{new Date(rangeEnd * 1000).toLocaleString()}</span>
          </div>
        </div>
      )}

      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: 'block' }}>
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={x(tick)} y1={padT - 4} x2={x(tick)} y2={H} stroke="var(--color-border, #2a2a33)" strokeWidth={1} />
            <text x={x(tick) + 3} y={padT - 8} fontSize={9} fill="var(--color-muted, #8a8a99)">
              {formatTick(tick, span)}
            </text>
          </g>
        ))}
        {hasCustomRange && (
          <rect
            x={padL}
            y={padT - 2}
            width={W - padL - padR}
            height={H - padT + 2}
            fill="rgba(59, 130, 246, 0.06)"
            stroke="rgba(59, 130, 246, 0.18)"
            strokeWidth={1}
          />
        )}
        {alerts
          .filter((alert) => alert.t >= rangeStart && alert.t <= rangeEnd)
          .map((alert, index) => (
            <line key={index} x1={x(alert.t)} y1={padT - 2} x2={x(alert.t)} y2={H} stroke="#e0564a" strokeWidth={1} opacity={0.45}>
              <title>{alert.type}</title>
            </line>
          ))}
        {(highlightedTimes || [])
          .filter((t) => t >= rangeStart && t <= rangeEnd)
          .map((t, index) => (
            <line
              key={`hl-${index}-${t}`}
              x1={x(t)}
              y1={padT - 2}
              x2={x(t)}
              y2={H}
              stroke="#22c55e"
              strokeWidth={2}
              opacity={0.65}
            />
          ))}
        {lanes.map((lane, laneIndex) => {
          const y = padT + laneIndex * rowH + rowH / 2
          const col = colorFor(lane.source)
          const visible = bucketEvents(lane.events, rangeStart, rangeEnd, W - padL - padR)
          return (
            <g key={lane.source}>
              <text x={0} y={y + 3} fontSize={10} fill="var(--color-fg, #e8e8ea)">{lane.source}</text>
              <line x1={padL} y1={y} x2={W - padR} y2={y} stroke="var(--color-border, #222)" strokeWidth={0.5} />
              {visible.map((event, eventIndex) => (
                <circle
                  key={eventIndex}
                  cx={x(event.t)}
                  cy={y}
                  r={Math.min(6, 2 + Math.log2(event.count + 1) * 0.7)}
                  fill={col}
                  opacity={Math.min(0.95, 0.38 + Math.log2(event.count + 1) * 0.12)}
                >
                  <title>{`${lane.source} ${event.type || 'event'}${event.count > 1 ? ` ×${event.count}` : ''}`}</title>
                </circle>
              ))}
              <text x={W - padR + 2} y={y + 3} fontSize={8} fill="var(--color-muted, #8a8a99)" textAnchor="start">
                {lane.events.length}
              </text>
            </g>
          )
        })}
      </svg>

      {onRangeChange && (
        <div className="mt-3 space-y-1">
          <input
            type="range"
            min={min_t}
            max={max_t}
            step={Math.max(1, Math.round(totalSpan / 800))}
            value={rangeStart}
            onChange={(e) => applyRange([Number(e.target.value), rangeEnd])}
            className="w-full"
          />
          <input
            type="range"
            min={min_t}
            max={max_t}
            step={Math.max(1, Math.round(totalSpan / 800))}
            value={rangeEnd}
            onChange={(e) => applyRange([rangeStart, Number(e.target.value)])}
            className="w-full"
          />
        </div>
      )}
    </div>
  )
}
