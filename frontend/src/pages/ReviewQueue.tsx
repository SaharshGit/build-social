import { Link, useSearchParams } from 'react-router'
import type { PostSummary } from '../api/client'
import { useBrandNames, useBrands, usePosts } from '../api/hooks'
import { ErrorBox, Loading, StatusBadge } from '../components/common'
import { PLATFORM_LABELS, timeAgo } from '../lib/format'

const STATUSES = [
  { value: 'pending', label: 'Waiting' },
  { value: 'approved', label: 'Approved' },
  { value: 'rejected', label: 'Rejected' },
  { value: '', label: 'All' },
]

export default function ReviewQueue() {
  // Filters live in the URL (?status=…&brand=…), so a refresh or a shared link keeps them.
  const [params, setParams] = useSearchParams()
  const status = params.get('status') ?? 'pending'
  const brandId = params.get('brand') ?? ''
  const posts = usePosts({ status: status || undefined, brandId: brandId || undefined })
  const brands = useBrands()
  const brandNames = useBrandNames()

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params)
    next.set(key, value)
    setParams(next)
  }

  return (
    <section className="page">
      <h1>Reviews</h1>
      <p className="subtitle">Posts waiting for a person. Approve, ask for changes, or reject.</p>
      <div className="chips">
        {STATUSES.map((s) => (
          <button key={s.value} className={`chip${status === s.value ? ' active' : ''}`}
                  onClick={() => setFilter('status', s.value)}>
            {s.label}
          </button>
        ))}
        <select value={brandId} onChange={(e) => setFilter('brand', e.target.value)} aria-label="Brand">
          <option value="">All brands</option>
          {(brands.data ?? []).map((b) => (
            <option key={b.id} value={b.id}>{b.name}</option>
          ))}
        </select>
      </div>

      {posts.isLoading && <Loading what="Loading posts" />}
      {posts.error && <ErrorBox error={posts.error} />}
      {posts.data?.length === 0 && (
        <p className="muted">
          No {status || ''} posts{brandId ? ' for this brand' : ''}. <Link to="/new">Start a new post</Link>.
        </p>
      )}
      <div className="card-list">
        {posts.data?.map((p) => <PostCard key={p.id} post={p} brand={brandNames[p.brand_id]} />)}
      </div>
    </section>
  )
}

function PostCard({ post, brand }: { post: PostSummary; brand?: string }) {
  return (
    <Link to={`/posts/${post.id}`} className="card post-card">
      <div className="card-meta">
        <strong>{brand ?? 'Brand'}</strong>
        <span>·</span>
        <span>{PLATFORM_LABELS[post.platform] ?? post.platform}</span>
        <span>·</span>
        <span className="muted">{timeAgo(post.created_at)}</span>
        <span className="spacer" />
        {post.open_issues > 0 && (
          <span className="badge badge-bad" title="Reviewer issues still open">
            {post.open_issues} open issue{post.open_issues === 1 ? '' : 's'}
          </span>
        )}
        {post.revisions > 0 && <span className="badge badge-muted">revised ×{post.revisions}</span>}
        <StatusBadge status={post.status} />
      </div>
      <div className="card-hook">{post.hook}</div>
      <div className="muted small">{post.topic}</div>
    </Link>
  )
}
