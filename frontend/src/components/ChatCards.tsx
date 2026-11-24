// Cards shown under an assistant reply. The backend sends them as message "attachments":
//   {type: "run", id}    a post being created or revised -> live progress, then the post
//   {type: "post", id}   the post the reply is about     -> the post with its buttons
//   {type: "data", ...}  a data question's query+result  -> the numbers / list it was answered from
//   {type: "error", ...} the assistant failed            -> a short technical note
import { useCallback } from 'react'
import { Link } from 'react-router'
import type { Run } from '../api/client'
import { useBrandNames, usePost, useRefreshAfterRun, useRun } from '../api/hooks'
import { PLATFORM_LABELS } from '../lib/format'
import { ErrorBox, Loading, StatusBadge } from './common'
import Highlighted from './Highlighted'
import PostActions from './PostActions'
import RunProgress, { formatCost } from './RunProgress'

export type Attachment = { type: string; id?: string; [key: string]: unknown }

export default function ChatCard({ attachment }: { attachment: Attachment }) {
  switch (attachment.type) {
    case 'run':
      return <RunCard runId={attachment.id!} />
    case 'post':
      return <PostCard postId={attachment.id!} />
    case 'data':
      return <DataCard query={attachment.query as Query} result={attachment.result as Result} />
    case 'error':
      return <div className="muted small">Details: {String(attachment.message ?? '')}</div>
    default:
      return null
  }
}

/** Live progress while the worker runs; once done, the post it produced (or revised). */
function RunCard({ runId }: { runId: string }) {
  const run = useRun(runId)
  const refresh = useRefreshAfterRun()
  const onFinished = useCallback((r: Run) => refresh(r.post_id), [refresh])

  if (run.data?.status === 'done' && run.data.post_id) {
    return (
      <div className="chat-card-stack">
        <div className="muted small">
          ✓ {run.data.kind === 'revision' ? 'Revised' : 'Created'} for {formatCost(run.data.cost)}
        </div>
        <PostCard postId={run.data.post_id} />
      </div>
    )
  }
  return <RunProgress runId={runId} onFinished={onFinished} />
}

/** The post, with the same Approve / Revise / Reject buttons as the post page. */
function PostCard({ postId }: { postId: string }) {
  const post = usePost(postId)
  const brandNames = useBrandNames()
  if (post.isLoading) return <Loading what="Loading post" />
  if (post.error) return <ErrorBox error={post.error} />
  if (!post.data) return null
  const p = post.data
  const issues = p.review.issues

  return (
    <div className="card chat-post">
      <div className="card-meta">
        <strong>{brandNames[p.brand_id] ?? 'Post'}</strong>
        <span className="muted">{PLATFORM_LABELS[p.platform] ?? p.platform}</span>
        <StatusBadge status={p.status} />
        <span className="spacer" />
        <Link to={`/posts/${p.id}`} className="small">Open full post →</Link>
      </div>
      <Highlighted text={p.full_text} quotes={issues.map((i) => i.quote)} />
      {issues.length > 0 && (
        <p className="small muted">
          {issues.length} open review issue{issues.length > 1 ? 's' : ''} (highlighted). Ask me about them, or open
          the full post.
        </p>
      )}
      <PostActions post={p} />
    </div>
  )
}

type Query = { tool: string; [key: string]: unknown }
type ListedPost = { id: string; brand: string; platform: string; status: string; topic: string; created: string }
type Result = { posts?: ListedPost[]; error?: string; [key: string]: unknown }

const TOOL_LABELS: Record<string, string> = {
  count_posts: 'Counted posts',
  list_posts: 'Listed posts',
  run_stats: 'Checked runs',
  spend: 'Added up spend',
  brand_info: 'Read the brand profile',
}

/** What a data answer was based on: posts as links, other results as a small table. */
function DataCard({ query, result }: { query: Query; result: Result }) {
  const filters = Object.entries(query)
    .filter(([k]) => k !== 'tool')
    .map(([k, v]) => `${k}: ${v}`)
    .join(', ')

  return (
    <div className="card data-card small">
      <div className="muted">
        {TOOL_LABELS[query.tool] ?? query.tool}
        {filters ? ` (${filters})` : ''}, from the database
      </div>
      {result.posts ? (
        result.posts.length === 0 ? (
          <p className="muted">No posts.</p>
        ) : (
          <ul className="data-posts">
            {result.posts.map((p) => (
              <li key={p.id}>
                <Link to={`/posts/${p.id}`}>{p.topic}</Link>{' '}
                <span className="muted">· {p.brand} · {PLATFORM_LABELS[p.platform] ?? p.platform} · {p.created}</span>{' '}
                <StatusBadge status={p.status} />
              </li>
            ))}
          </ul>
        )
      ) : (
        <details>
          <summary>Show the data</summary>
          <table className="data-table">
            <tbody>
              {Object.entries(result).map(([k, v]) => (
                <tr key={k}>
                  <th>{k.replaceAll('_', ' ')}</th>
                  <td>{formatValue(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  )
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (Array.isArray(v)) return v.map((x) => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ') || '—'
  if (typeof v === 'object') {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => `${k}: ${typeof x === 'object' ? JSON.stringify(x) : x}`)
      .join(', ') || '—'
  }
  return String(v)
}
