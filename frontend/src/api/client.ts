// The only place that talks HTTP. Pages use the hooks in hooks.ts, which use this.
// Types come from schema.ts, generated from the backend (npm run gen:api).
import type { components } from './schema'

export type Brand = components['schemas']['BrandOut']
export type BrandSummary = components['schemas']['BrandSummary']
export type BrandProfile = components['schemas']['BrandProfile']
export type Run = components['schemas']['RunOut']
export type PostSummary = components['schemas']['PostSummary']
export type Post = components['schemas']['PostOut']
export type Brief = components['schemas']['PostBrief']
export type Health = components['schemas']['Health']
export type ConversationSummary = components['schemas']['ConversationSummary']
export type Conversation = components['schemas']['ConversationOut']
export type Message = components['schemas']['MessageOut']
export type ChatTurn = components['schemas']['ChatTurnOut']
export type BrandDesign = components['schemas']['BrandDesign']
export type BrandColor = components['schemas']['BrandColor']
export type Asset = components['schemas']['AssetOut']
export type AssetKind = Asset['kind']
export type Style = components['schemas']['StyleOut']

/** An error the API returned, with its status code and FastAPI's "detail" message. */
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const BASE = '/api' // the Vite dev server forwards this to FastAPI (see vite.config.ts)

export async function api<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const response = await fetch(BASE + path, {
    method: init?.method ?? 'GET',
    headers: init?.body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    throw new ApiError(response.status, describeError(data) ?? `${response.status} ${response.statusText}`)
  }
  return data as T
}

/** Upload a file as multipart form data (other fields are sent as form fields). */
export async function upload<T>(path: string, file: File, fields: Record<string, string> = {}): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  for (const [k, v] of Object.entries(fields)) form.append(k, v)
  const response = await fetch(BASE + path, { method: 'POST', body: form })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    throw new ApiError(response.status, describeError(data) ?? `${response.status} ${response.statusText}`)
  }
  return data as T
}

/** The URL an uploaded image is served from (AssetOut.url is relative to the API). */
export const assetUrl = (assetId: string) => `${BASE}/assets/${assetId}/file`

// FastAPI errors look like {"detail": "brand not found"} or, for invalid input,
// {"detail": [{"loc": ["body", "topic"], "msg": "Field required"}, ...]}.
function describeError(data: unknown): string | null {
  const detail = (data as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((e: { loc?: unknown[]; msg?: string }) => `${(e.loc ?? []).slice(1).join('.')}: ${e.msg}`)
      .join('; ')
  }
  return null
}
