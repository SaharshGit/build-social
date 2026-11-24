import { ApiError } from '../api/client'

export function Loading({ what = 'Loading' }: { what?: string }) {
  return <p className="muted">{what}…</p>
}

/** Shows an API error in plain words (e.g. "brand not found", or "API unreachable"). */
export function ErrorBox({ error }: { error: unknown }) {
  const message =
    error instanceof ApiError
      ? `${error.message} (HTTP ${error.status})`
      : 'Could not reach the API. Is `uv run buildsocial api` running?'
  return <div className="error-box">{message}</div>
}

const STATUS_CLASS: Record<string, string> = {
  pending: 'badge-warn',
  approved: 'badge-ok',
  rejected: 'badge-bad',
  queued: 'badge-muted',
  running: 'badge-warn',
  done: 'badge-ok',
  failed: 'badge-bad',
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${STATUS_CLASS[status] ?? 'badge-muted'}`}>{status}</span>
}
