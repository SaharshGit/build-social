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
