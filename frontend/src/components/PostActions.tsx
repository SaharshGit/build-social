import { useCallback, useState } from 'react'
import type { Post, Run } from '../api/client'
import { useDecide, useRefreshAfterRun, useRevise } from '../api/hooks'
import { timeAgo } from '../lib/format'
import { ErrorBox } from './common'
import RunProgress from './RunProgress'

type Mode = 'idle' | 'rejecting' | 'revising'

/** Approve / reject / revise a pending post. A revision runs in the background with live progress. */
export default function PostActions({ post }: { post: Post }) {
  const [mode, setMode] = useState<Mode>('idle')
  const [reason, setReason] = useState('')
  const [feedback, setFeedback] = useState('')
  const [revisionRunId, setRevisionRunId] = useState<string | null>(null)
  const [revisionFailed, setRevisionFailed] = useState(false)
  const decide = useDecide(post.id)
  const revise = useRevise(post.id)
  const refresh = useRefreshAfterRun()

  // useCallback: the same function between renders, so RunProgress's effect doesn't re-run needlessly.
  const onRevisionFinished = useCallback(
    (run: Run) => {
      refresh(post.id)
      if (run.status === 'done') {
        setRevisionRunId(null)
        setFeedback('')
        setMode('idle')
      } else {
        setRevisionFailed(true)
      }
    },
    [post.id, refresh],
  )

  if (post.status !== 'pending') {
    return (
      <div className={`decision decision-${post.status}`}>
        {post.status === 'approved' ? '✓ Approved' : '✗ Rejected'} {timeAgo(post.decided_at)}
      </div>
    )
  }

  if (revisionRunId) {
    return (
      <div className="actions">
        <h2>Revising with your feedback</h2>
        <p className="muted small">“{feedback}”</p>
        <RunProgress runId={revisionRunId} onFinished={onRevisionFinished} />
        {revisionFailed && (
          <button className="btn" onClick={() => { setRevisionRunId(null); setRevisionFailed(false) }}>
            Back to the post
          </button>
        )}
      </div>
    )
  }

  const busy = decide.isPending || revise.isPending
  const error = decide.error ?? revise.error

  return (
    <div className="actions">
      <div className="action-buttons">
        <button className="btn btn-primary" disabled={busy} onClick={() => decide.mutate({ decision: 'approve' })}>
          Approve
        </button>
        <button className="btn" disabled={busy} onClick={() => setMode(mode === 'revising' ? 'idle' : 'revising')}>
          Revise…
        </button>
        <button className="btn btn-danger" disabled={busy} onClick={() => setMode(mode === 'rejecting' ? 'idle' : 'rejecting')}>
          Reject…
        </button>
      </div>

      {mode === 'revising' && (
        <form
          className="action-form"
          onSubmit={(e) => {
            e.preventDefault() // stop the browser's own form submit (it would reload the page)
            revise.mutate(feedback.trim(), { onSuccess: (run) => setRevisionRunId(run.id) })
          }}
        >
          <label>
            What should change?
            <textarea
              rows={3}
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              placeholder="e.g. Make the hook a question and keep it shorter."
              autoFocus
            />
          </label>
          <button className="btn btn-primary" disabled={busy || feedback.trim().length < 3}>
            {revise.isPending ? 'Sending…' : 'Revise the post'}
          </button>
        </form>
      )}

      {mode === 'rejecting' && (
        <form
          className="action-form"
          onSubmit={(e) => {
            e.preventDefault()
            decide.mutate({ decision: 'reject', reason: reason.trim() || undefined })
          }}
        >
          <label>
            Reason (optional, kept with the post)
            <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Off-brand tone" autoFocus />
          </label>
          <button className="btn btn-danger" disabled={busy}>Reject the post</button>
        </form>
      )}

      {error && <ErrorBox error={error} />}
    </div>
  )
}
