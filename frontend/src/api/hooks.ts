// Data hooks: each screen asks for data through these. TanStack Query caches the
// results, shows loading/error states, and re-fetches when told something changed.
import { useQuery } from '@tanstack/react-query'
import { api, type Health } from './client'

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => api<Health>('/health'),
    refetchInterval: 15_000, // keep the status dot in the sidebar up to date
    retry: false,
  })
}
