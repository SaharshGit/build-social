import { Link, useParams } from 'react-router'
import type { Post } from '../api/client'
import { useBrandNames, usePost } from '../api/hooks'
import { ErrorBox, Loading, StatusBadge } from '../components/common'
import Highlighted from '../components/Highlighted'
import ImageTextPreview from '../components/ImageTextPreview'
import FeedbackChecks, { latestChecks, type Note } from '../components/FeedbackChecks'
import PostActions from '../components/PostActions'
import { PLATFORM_LABELS, timeAgo } from '../lib/format'

// facts/history/feedback are stored as free-form JSON on the backend; these describe their shape.
type Source = { id: number; title?: string | null; url: string }
type Fact = { fact: string; scope?: string; sources?: Source[] }
type DraftLike = { hook: string; body: string; call_to_action: string; hashtags: string[] }
type HistoryEntry = { draft: DraftLike; feedback?: string }

const ISSUE_LABELS: Record<string, string> = {
  invented_fact: 'Invented fact',
  brand_rule: 'Breaks a brand rule',
  risky_claim: 'Risky claim',
  off_plan: 'Off plan',
  duplicate_cta: 'Extra call to action',
  copied_example: 'Copied from an example post',
  language: 'Language / voice',
  other: 'Other',
}

export default function PostDetail() {
  const { postId = '' } = useParams()
  const post = usePost(postId)
  const brandNames = useBrandNames()

  if (post.isLoading) return <Loading what="Loading post" />
  if (post.error) return <ErrorBox error={post.error} />
  if (!post.data) return null
  const p = post.data

  return (
    <section className="page">
      <Link to="/reviews" className="back-link">← Reviews</Link>
      <div className="detail-header">
        <h1>{brandNames[p.brand_id] ?? 'Post'}</h1>
        <span className="muted">{PLATFORM_LABELS[p.platform] ?? p.platform}</span>
        <StatusBadge status={p.status} />
        <span className="muted small">created {timeAgo(p.created_at)}</span>
      </div>
      <p className="muted">Topic: {p.topic}</p>
      <PostActions post={p} />
      {(() => {
        const last = latestChecks(p.feedback)
        return last && <FeedbackChecks checks={last.checks} />
      })()}

      <div className="detail-grid">
        <div>
          <h2>Post</h2>
          <ImageTextPreview post={p} quotes={p.review.issues.map((i) => i.quote)} />
          <div className="card">
            <Highlighted text={p.full_text} quotes={p.review.issues.map((i) => i.quote)} />
          </div>
          <p className="small"><strong>Image idea:</strong> {p.draft.image_idea}</p>
        </div>
        <div>
          <h2>Open review issues ({p.review.issues.length})</h2>
          <IssueList post={p} />
        </div>
      </div>

      <h2>Plan</h2>
      <div className="card small">
        <p><strong>Angle:</strong> {p.plan.angle}</p>
        <p><strong>Key message:</strong> {p.plan.key_message}</p>
        <p><strong>Call to action:</strong> {p.plan.cta_direction}</p>
        {p.plan.brief_concerns.length > 0 && (
          <div className="concerns">
            <strong>Concerns about the brief:</strong>
            <ul>{p.plan.brief_concerns.map((c, i) => <li key={i}>{c}</li>)}</ul>
          </div>
        )}
      </div>

      <h2>Facts this post may use</h2>
      <FactList facts={p.facts as Fact[]} />

      {p.history.length > 0 && (
        <>
          <h2>Earlier drafts ({p.history.length})</h2>
          {(p.history as HistoryEntry[]).map((h, i) => (
            <details key={i} className="card small">
              <summary>
                Draft {i + 1}
                {h.feedback ? ` → changed because: "${h.feedback}"` : ' → revised after automatic review'}
              </summary>
              <div className="post-text">
                {[h.draft.hook, h.draft.body, h.draft.call_to_action, h.draft.hashtags.join(' ')].join('\n\n')}
              </div>
            </details>
          ))}
        </>
      )}

      {p.feedback.length > 0 && (
        <>
          <h2>Notes</h2>
          <ul className="small">
            {(p.feedback as Note[]).map((n, i) => (
              <li key={i}>
                <strong>{n.kind}</strong>: {n.text} <span className="muted">{timeAgo(n.at)}</span>
                {n.checks && n.checks.length > 0 && <FeedbackChecks checks={n.checks} compact />}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

function IssueList({ post }: { post: Post }) {
  const suggestions = post.review.suggestions ?? []
  if (post.review.issues.length === 0) {
    return (
      <>
        <p className="ok-text">✓ The reviewer found no open issues.</p>
        <Suggestions items={suggestions} />
      </>
    )
  }
  return (
    <>
    {/* real problems first; optional ideas below them */}
    <ol className="issue-list">
      {post.review.issues.map((issue, i) => (
        <li key={i} className="card small">
          <div className="issue-type">{ISSUE_LABELS[issue.type] ?? issue.type}</div>
          <div><mark>“{issue.quote}”</mark></div>
          <div><strong>Problem:</strong> {issue.problem}</div>
          <div><strong>Suggested fix:</strong> {issue.fix}</div>
        </li>
      ))}
    </ol>
    <Suggestions items={suggestions} />
    </>
  )
}

/** The reviewer's optional, non-blocking ideas (style, a stronger hook...). They never block approval. */
function Suggestions({ items }: { items: string[] }) {
  if (items.length === 0) return null
  return (
    <div className="suggestions-box">
      <div className="muted small"><strong>Optional suggestions</strong> (not problems; approve as is if you like)</div>
      <ul className="small muted">{items.map((s, i) => <li key={i}>{s}</li>)}</ul>
    </div>
  )
}

function FactList({ facts }: { facts: Fact[] }) {
  if (facts.length === 0) return <p className="muted small">No researched facts were used (brand profile and brief only).</p>
  return (
    <ul className="fact-list">
      {facts.map((f, i) => (
        <li key={i} className="small">
          <span className="badge badge-muted">{f.scope ?? 'fact'}</span> {f.fact}
          <div className="sources">
            {(f.sources ?? []).map((s) => (
              <a key={s.id} href={s.url} target="_blank" rel="noopener noreferrer">{s.title ?? 'source'}</a>
            ))}
          </div>
        </li>
      ))}
    </ul>
  )
}
