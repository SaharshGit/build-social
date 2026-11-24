// Data hooks: each screen asks for data through these. TanStack Query caches the
// results, tracks loading/error states, and re-fetches when told something changed.
import { useQuery } from '@tanstack/react-query'
import { api, type BrandSummary, type Health, type Post, type PostSummary } from './client'

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
