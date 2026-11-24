// Data hooks: each screen asks for data through these. TanStack Query caches the
// results, tracks loading/error states, and re-fetches when told something changed.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Brand, type BrandSummary, type Brief, type Health, type Post, type PostSummary, type Run } from './client'

export type HealthState = 'ok' | 'database-down' | 'api-down'

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    // /health answers 503 with {"database": "unreachable"} when the API is up but Postgres isn't,
    // so read the body ourselves instead of treating every non-200 as "API down".
    queryFn: async (): Promise<HealthState> => {
      try {
        const response = await fetch('/api/health')
        const body = (await response.json()) as Health
        if (response.ok && body.status === 'ok') return 'ok'
        return body.database === 'unreachable' ? 'database-down' : 'api-down'
      } catch {
        return 'api-down' // nothing answered at all
      }
    },
    refetchInterval: 15_000, // keep the status dot in the sidebar up to date
  })
}

export type PostFilters = { status?: string; brandId?: string }

export function usePosts(filters: PostFilters) {
  const params = new URLSearchParams()
  if (filters.status) params.set('status', filters.status)
  if (filters.brandId) params.set('brand_id', filters.brandId)
  const query = params.toString()
  return useQuery({
    queryKey: ['posts', filters], // a different filter = a different cache entry
    queryFn: () => api<PostSummary[]>(`/posts${query ? `?${query}` : ''}`),
  })
}

export function usePost(postId: string) {
  return useQuery({ queryKey: ['post', postId], queryFn: () => api<Post>(`/posts/${postId}`) })
}

export function useBrands() {
  return useQuery({
    queryKey: ['brands'],
    queryFn: () => api<BrandSummary[]>('/brands'),
    staleTime: 60_000, // brands rarely change
  })
}

/** id -> brand name, for showing names next to posts. */
export function useBrandNames(): Record<string, string> {
  const brands = useBrands()
  return Object.fromEntries((brands.data ?? []).map((b) => [b.id, b.name]))
}

// ----------------------------------------------------------------------
// Runs: polled every second until they finish
// ----------------------------------------------------------------------

export const isActive = (status?: string) => status === 'queued' || status === 'running'

export function useRun(runId: string | null | undefined) {
  return useQuery({
    queryKey: ['run', runId],
    queryFn: () => api<Run>(`/runs/${runId}`),
    enabled: !!runId,
    // Poll while the worker is on it; stop automatically once the run is done or failed.
    refetchInterval: (query) => (isActive(query.state.data?.status) ? 1000 : false),
  })
}

// ----------------------------------------------------------------------
// Mutations: things that change data. Afterwards, related cached data is marked
// stale ("invalidated") so lists and pages refresh by themselves.
// ----------------------------------------------------------------------

export function useStartRun() {
  return useMutation({
    mutationFn: ({ brandId, brief }: { brandId: string; brief: Brief }) =>
      api<Run>(`/brands/${brandId}/runs`, { method: 'POST', body: brief }),
  })
}

export function useDecide(postId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ decision, reason }: { decision: 'approve' | 'reject'; reason?: string }) =>
      api<Post>(`/posts/${postId}/${decision}`, { method: 'POST', body: reason ? { reason } : {} }),
    onSuccess: (post) => {
      queryClient.setQueryData(['post', postId], post) // show the new status immediately
      queryClient.invalidateQueries({ queryKey: ['posts'] })
    },
  })
}

export function useRevise(postId: string) {
  return useMutation({
    mutationFn: (feedback: string) => api<Run>(`/posts/${postId}/revise`, { method: 'POST', body: { feedback } }),
  })
}

/** Call when a run finishes: refresh the post it produced or revised, and the queue. */
export function useRefreshAfterRun() {
  const queryClient = useQueryClient()
  return (postId?: string | null) => {
    if (postId) queryClient.invalidateQueries({ queryKey: ['post', postId] })
    queryClient.invalidateQueries({ queryKey: ['posts'] })
  }
}

export function useBrand(brandId: string) {
  return useQuery({ queryKey: ['brand', brandId], queryFn: () => api<Brand>(`/brands/${brandId}`) })
}
