import { useEffect, useRef } from 'react'
import 'leaflet/dist/leaflet.css'
import L from 'leaflet'

/**
 * Geo map (Leaflet, imperative). Strava routes as polylines + start points,
 * Instagram tagged-place pins. Tiles load client-side from OpenStreetMap.
 * Populates as the collector fills strava_gps_streams / IG post geo.
 */
type Geo = {
  routes: {
    name: string | null
    type: string | null
    date: string | null
    source: string
    evidence_type?: string | null
    confidence?: number | null
    source_table?: string | null
    source_record_id?: string | null
    points: [number, number][]
  }[]
  points: {
    lat: number
    lng: number
    label: string | null
    source: string
    occurred_at: string | null
    evidence_type?: string | null
    confidence?: number | null
    source_table?: string | null
    source_record_id?: string | null
  }[]
  counts: { routes: number; points: number; evidence_types?: Record<string, number> }
}

export type GeoSelectedEvent = {
  kind: 'route' | 'point'
  label: string | null
  source: string
  occurred_at: string | null
  lat?: number
  lng?: number
  route_type?: string | null
  point_count?: number
  start?: [number, number] | null
  end?: [number, number] | null
  evidence_type?: string | null
  confidence?: number | null
  source_table?: string | null
  source_record_id?: string | null
}

function evidenceLabel(value?: string | null) {
  return value ? value.replace(/_/g, ' ') : 'location evidence'
}

function pointColor(point: Geo['points'][number]) {
  if (point.evidence_type === 'message_location') return '#22c55e'
  if (point.evidence_type === 'venue_geocode') return '#8b5cf6'
  if (point.source === 'strava') return '#fc4c02'
  if (point.source === 'telegram') return '#22c55e'
  return '#e1306c'
}

export function GeoMap({
  data,
  onEventSelect,
}: {
  data: Geo
  onEventSelect?: (event: GeoSelectedEvent) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)

  useEffect(() => {
    if (!ref.current) return
    if (!mapRef.current) {
      mapRef.current = L.map(ref.current, { scrollWheelZoom: false }).setView([1.35, 103.82], 11)
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '© OpenStreetMap',
      }).addTo(mapRef.current)
    }
    const map = mapRef.current
    const layer = L.layerGroup().addTo(map)
    const bounds: [number, number][] = []

    data.routes.forEach((r) => {
      const latlngs = r.points.map((p) => [p[0], p[1]] as [number, number])
      if (latlngs.length >= 2) {
        L.polyline(latlngs, { color: '#fc4c02', weight: 2, opacity: 0.7 })
          .bindPopup(`${r.name || 'activity'}${r.date ? ' · ' + r.date.slice(0, 10) : ''}`)
          .on('click', () => onEventSelect?.({
            kind: 'route',
            label: r.name,
            source: r.source,
            occurred_at: r.date,
            route_type: r.type,
            point_count: latlngs.length,
            start: latlngs[0] ?? null,
            end: latlngs[latlngs.length - 1] ?? null,
            evidence_type: r.evidence_type,
            confidence: r.confidence,
            source_table: r.source_table,
            source_record_id: r.source_record_id,
          }))
          .addTo(layer)
        latlngs.forEach((ll) => bounds.push(ll))
      }
    })
    data.points.forEach((p) => {
      L.circleMarker([p.lat, p.lng], {
        radius: 4,
        color: pointColor(p),
        fillOpacity: 0.6,
      })
        .bindPopup(`${p.label || p.source}${p.occurred_at ? ` · ${p.occurred_at.slice(0, 10)}` : ''} · ${evidenceLabel(p.evidence_type)}`)
        .on('click', () => onEventSelect?.({
          kind: 'point',
          label: p.label,
          source: p.source,
          occurred_at: p.occurred_at,
          lat: p.lat,
          lng: p.lng,
          evidence_type: p.evidence_type,
          confidence: p.confidence,
          source_table: p.source_table,
          source_record_id: p.source_record_id,
        }))
        .addTo(layer)
      bounds.push([p.lat, p.lng])
    })

    if (bounds.length) map.fitBounds(bounds, { padding: [24, 24], maxZoom: 14 })
    setTimeout(() => map.invalidateSize(), 50)
    return () => { layer.remove() }
  }, [data])

  return <div ref={ref} style={{ height: 420, borderRadius: 8, overflow: 'hidden', zIndex: 0 }} />
}
