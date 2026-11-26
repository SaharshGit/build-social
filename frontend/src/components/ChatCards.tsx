// Cards shown under an assistant reply. The backend sends them as message "attachments":
//   {type: "run", id}    a post being created or revised -> live progress, then the post
//   {type: "post", id}   the post the reply is about     -> the post with its buttons
//   {type: "data", ...}  a data question's query+result  -> the numbers / list it was answered from
//   {type: "error", ...} the assistant failed            -> a short technical note
import { useEffect, useRef } from 'react'
import { Link } from 'react-router'
import { isActive, useBrandNames, usePost, useRefreshAfterRun, useRun } from '../api/hooks'
import { PLATFORM_LABELS } from '../lib/format'
import { ErrorBox, Loading, StatusBadge } from './common'
import FeedbackChecks, { latestChecks } from './FeedbackChecks'
import Highlighted from './Highlighted'
import PostActions from './PostActions'
import StepChain from './StepChain'
import { formatCost } from './RunProgress'

export type Attachment = { type: string; id?: string; [key: string]: unknown }

// onSend: send a chat message (e.g. "Show me post 2"); undefined while a message is being sent.
export default function ChatCard({ attachment, onSend }: { attachment: Attachment; onSend?: (text: string) => void }) {
  switch (attachment.type) {
    case 'run':
      return <RunCard runId={attachment.id!} />
    case 'post':
      return <PostCard postId={attachment.id!} />
    case 'data':
      return <DataCard query={attachment.query as Query} result={attachment.result as Result} onSend={onSend} />
    case 'error':
      return <div className="muted small">Details: {String(attachment.message ?? '')}</div>
    default:
      return null
  }
}

/** A build card with the CI-style step chain; once done, the post it produced (or revised). */
function RunCard({ runId }: { runId: string }) {
  const run = useRun(runId)
  const refresh = useRefreshAfterRun()
  const brandNames = useBrandNames()
  const refreshed = useRef(false)

  useEffect(() => {
    const r = run.data
    if (r && !isActive(r.status) && !refreshed.current) {
      refreshed.current = true
      refresh(r.post_id)
    }
  }, [run.data, refresh])

  if (run.error) return <ErrorBox error={run.error} />
  if (!run.data) return <p className="muted">Starting…</p>
  const r = run.data
  const what = r.kind === 'revision' ? 'Revision' : 'Post'
  const title = r.status === 'done' ? `${what} built` : r.status === 'failed' ? `${what} failed` : `Building ${what.toLowerCase()}`
  const platform = PLATFORM_LABELS[String(r.brief.platform)] ?? String(r.brief.platform ?? '')

  return (
    <div className="chat-card-stack">
      <Link to={`/pipelines/${r.id}`} className="card" style={{ textDecoration: 'none' }}>
        <div className="card-meta" style={{ marginBottom: '0.7rem' }}>
          <strong>{title} · {brandNames[r.brand_id] ?? 'Brand'} · {platform}</strong>
          <span className="spacer" />
          <span>{formatCost(r.cost)}{isActive(r.status) ? ' so far' : ''}</span>
        </div>
        <StepChain run={r} />
        {r.status === 'queued' && <div className="muted small" style={{ marginTop: '0.5rem' }}>Waiting for the worker… (is <code>buildsocial worker</code> running?)</div>}
        {r.error && <div className="bad-text small" style={{ marginTop: '0.5rem' }}>{r.error.slice(0, 200)}</div>}
      </Link>
      {r.status === 'done' && r.post_id && <PostCard postId={r.post_id} />}
    </div>
  )
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
      {(() => {
        const last = latestChecks(p.feedback)
        return last && <FeedbackChecks checks={last.checks} compact />
      })()}
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
function DataCard({ query, result, onSend }: { query: Query; result: Result; onSend?: (text: string) => void }) {
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
            {result.posts.map((p, i) => (
              <li key={p.id}>
                <Link to={`/posts/${p.id}`}>{p.topic}</Link>{' '}
                <span className="muted">· {p.brand} · {PLATFORM_LABELS[p.platform] ?? p.platform} · {p.created}</span>{' '}
                <StatusBadge status={p.status} />{' '}
                <button className="link-btn" disabled={!onSend} title="Show this post in the chat"
                        onClick={() => onSend?.(`Show me post ${i + 1} (${p.topic})`)}>
                  open here
                </button>
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
