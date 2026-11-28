// Data hooks: each screen asks for data through these. TanStack Query caches the
// results, tracks loading/error states, and re-fetches when told something changed.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  api, upload, type Asset, type AssetKind, type Style, type Brand, type BrandProfile, type BrandSummary, type Brief, type ChatTurn, type Conversation, type ConversationSummary,
  type Health, type Post, type PostSummary, type Run,
} from './client'

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

/** Recent runs for the Pipelines page. Refreshes every 2 s while any run is still going. */
export function useRuns(status?: string) {
  return useQuery({
    queryKey: ['runs', status ?? 'all'],
    queryFn: () => api<Run[]>(`/runs?limit=100${status ? `&status=${status}` : ''}`),
    refetchInterval: (query) => ((query.state.data ?? []).some((r) => isActive(r.status)) ? 2000 : 15_000),
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

/** Design the post's image again with a different layout (also retries a failure). Returns the queued run. */
export function useRedesignImage(postId: string) {
  return useMutation({ mutationFn: () => api<Run>(`/posts/${postId}/image`, { method: 'POST' }) })
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
  return useQuery({ queryKey: ['brand', brandId], queryFn: () => api<Brand>(`/brands/${brandId}`), enabled: !!brandId })
}

// ----------------------------------------------------------------------
// Chat
// ----------------------------------------------------------------------

export function useConversations() {
  return useQuery({ queryKey: ['conversations'], queryFn: () => api<ConversationSummary[]>('/conversations') })
}

export function useConversation(conversationId: string | undefined) {
  return useQuery({
    queryKey: ['conversation', conversationId],
    queryFn: () => api<Conversation>(`/conversations/${conversationId}`),
    enabled: !!conversationId,
  })
}

export function useCreateConversation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (brandId: string | null) =>
      api<Conversation>('/conversations', { method: 'POST', body: brandId ? { brand_id: brandId } : {} }),
    onSuccess: (conv) => {
      queryClient.setQueryData(['conversation', conv.id], conv)
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })
}

/** Change a chat's brand and/or title: send only what changes. */
export function useUpdateConversation(conversationId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (changes: { brand_id?: string | null; title?: string }) =>
      api<Conversation>(`/conversations/${conversationId}`, { method: 'PATCH', body: changes }),
    onSuccess: (conv) => {
      queryClient.setQueryData(['conversation', conv.id], conv)
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })
}

/** Send a message; the reply (and any cards) arrive when the assistant has decided and acted. */
export function useSendMessage() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ conversationId, text, photoIds = [] }: { conversationId: string; text: string; photoIds?: string[] }) =>
      api<ChatTurn>(`/conversations/${conversationId}/messages`, { method: 'POST', body: { text, photo_ids: photoIds } }),
    onSuccess: (turn, { conversationId }) => {
      // Show both messages right away, before the refetch below comes back.
      queryClient.setQueryData<Conversation>(['conversation', conversationId], (old) =>
        old ? { ...old, messages: [...old.messages, turn.user, turn.assistant] } : old,
      )
      // The reply may have changed the title or brand, approved a post, etc.: refresh what it can touch.
      queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      queryClient.invalidateQueries({ queryKey: ['posts'] })
      queryClient.invalidateQueries({ queryKey: ['post'] })
    },
  })
}

export function useDeleteConversation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (conversationId: string) => api<null>(`/conversations/${conversationId}`, { method: 'DELETE' }),
    onSuccess: (_r, conversationId) => {
      queryClient.removeQueries({ queryKey: ['conversation', conversationId] })
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })
}

/** Create a brand (no brandId) or replace an existing one's profile. */
export function useSaveBrand() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ brandId, profile }: { brandId?: string; profile: BrandProfile }) =>
      brandId
        ? api<Brand>(`/brands/${brandId}`, { method: 'PUT', body: profile })
        : api<Brand>('/brands', { method: 'POST', body: profile }),
    onSuccess: (brand) => {
      queryClient.setQueryData(['brand', brand.id], brand)
      queryClient.invalidateQueries({ queryKey: ['brands'] })
    },
  })
}

// --- Brand images and style ---------------------------------------------------------------

export function useAssets(brandId: string) {
  return useQuery({
    queryKey: ['assets', brandId],
    queryFn: () => api<Asset[]>(`/brands/${brandId}/assets`),
    enabled: !!brandId,
  })
}

/** Upload an image. The server reads it with a model (a few seconds) before answering. */
export function useUploadAsset(brandId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ file, kind, product, inLibrary = true }: { file: File; kind: AssetKind; product?: string; inLibrary?: boolean }) =>
      upload<Asset>(`/brands/${brandId}/assets`, file, {
        kind, in_library: String(inLibrary), ...(product ? { product } : {}),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assets', brandId] })
      queryClient.invalidateQueries({ queryKey: ['style', brandId] })
    },
  })
}

export function useUpdateAsset(brandId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ assetId, change }: { assetId: string; change: Partial<Pick<Asset, 'kind' | 'product' | 'description' | 'in_library'>> }) =>
      api<Asset>(`/assets/${assetId}`, { method: 'PATCH', body: change }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assets', brandId] })
      queryClient.invalidateQueries({ queryKey: ['style', brandId] })
    },
  })
}

export function useDeleteAsset(brandId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (assetId: string) => api<null>(`/assets/${assetId}`, { method: 'DELETE' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assets', brandId] })
      queryClient.invalidateQueries({ queryKey: ['style', brandId] })
    },
  })
}

export function useStyle(brandId: string) {
  return useQuery({ queryKey: ['style', brandId], queryFn: () => api<Style>(`/brands/${brandId}/style`), enabled: !!brandId })
}

/** Read the style from the reference images (about INR 0.2-0.35). replace: overwrite a person's edit. */
export function useReadStyle(brandId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (replace: boolean = false) =>
      api<Style>(`/brands/${brandId}/style/read${replace ? '?replace=true' : ''}`, { method: 'POST' }),
    onSuccess: (style) => queryClient.setQueryData(['style', brandId], style),
  })
}

/** Read one photo again with the image-reading model (about INR 0.09). */
export function useReadAsset(brandId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (assetId: string) => api<Asset>(`/assets/${assetId}/read`, { method: 'POST' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assets', brandId] })
      queryClient.invalidateQueries({ queryKey: ['style', brandId] })
    },
  })
}

export function useAsset(assetId: string) {
  return useQuery({ queryKey: ['asset', assetId], queryFn: () => api<Asset>(`/assets/${assetId}`), enabled: !!assetId })
}
