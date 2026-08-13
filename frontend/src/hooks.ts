/**
 * React Query hooks wrapping the api client. New/migrated pages consume these
 * instead of calling api.* + useState/useEffect directly.
 *
 * TODO(frontend): migrate Entities/EntityDetail/Communities onto these hooks
 * too (they still use the imperative api client, which continues to work).
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, MediaBrowseParams } from './api'

export function useHealth() {
  return useQuery({ queryKey: ['health'], queryFn: api.getHealth, refetchInterval: 30_000 })
}

export function useRuns(page: number) {
  return useQuery({ queryKey: ['runs', page], queryFn: () => api.getRuns(page) })
}

export function useTriggerRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: api.triggerRun,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['runs'] }),
  })
}

export function useAlerts(page: number, unreadOnly: boolean) {
  return useQuery({ queryKey: ['alerts', page, unreadOnly], queryFn: () => api.getAlerts(page, unreadOnly) })
}

export function useMarkRead() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.markRead(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
  })
}

export function useMarkAllRead() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.markAllRead(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
  })
}

export function useCollectorHealth() {
  return useQuery({
    queryKey: ['collector-health'],
    queryFn: api.getCollectorHealth,
    refetchInterval: 30_000,
  })
}

export function useMediaStats() {
  return useQuery({ queryKey: ['media-stats'], queryFn: api.getMediaStats })
}

export function useMediaCoverage() {
  return useQuery({ queryKey: ['media-coverage'], queryFn: api.getMediaCoverage })
}

export function useMediaFilters() {
  return useQuery({ queryKey: ['media-filters'], queryFn: api.getMediaFilters })
}

export function useMediaBrowse(params: MediaBrowseParams) {
  return useQuery({
    queryKey: ['media-browse', params],
    queryFn: () => api.browseMedia(params),
    placeholderData: (prev) => prev,
  })
}

// ── Faces (facetracker engine, /api/face) ──
export function useFaceStats() {
  return useQuery({ queryKey: ['face-stats'], queryFn: api.getFaceStats, refetchInterval: 30_000 })
}

export function useFaceIdentities(page: number) {
  return useQuery({
    queryKey: ['face-identities', page],
    queryFn: () => api.getFaceIdentities(page),
    placeholderData: (prev) => prev,
  })
}
