// One build, like opening a CI job: each step with what it used (model, time, cost) and what's left.
import { useEffect, useRef } from 'react'
import { Link, useParams } from 'react-router'
import { useBrandNames, useConversations, useRefreshAfterRun, useRun } from '../api/hooks'
import { ErrorBox, Loading } from '../components/common'
import { formatCost } from '../components/RunProgress'
import { PLATFORM_LABELS, timeAgo } from '../lib/format'
import { stepCost, stepSeconds, stepsFor, triggerText, type StepView } from '../lib/steps'

const STATUS_LABEL: Record<string, string> = { queued: 'Queued', running: 'Running', done: 'Done', failed: 'Failed' }

export default function PipelineDetail() {
  const { runId = '' } = useParams()
  const run = useRun(runId)
  const brandNames = useBrandNames()
  const chats = useConversations()
  const refresh = useRefreshAfterRun()
  const refreshed = useRef(false)

  // When the run finishes, refresh the post lists so Reviews shows the new post.
  useEffect(() => {
    const r = run.data
    if (r && (r.status === 'done' || r.status === 'failed') && !refreshed.current) {
      refreshed.current = true
      refresh(r.post_id)
    }
  }, [run.data, refresh])

  if (run.isLoading) return <Loading what="Loading pipeline" />
  if (run.error) return <ErrorBox error={run.error} />
  if (!run.data) return null
  const r = run.data
  const brand = brandNames[r.brand_id] ?? 'Brand'
  const topic = String(r.brief.topic ?? '')
  const steps = stepsFor(r)
  const done = steps.filter((s) => s.state === 'done' || s.state === 'cached').length

  return (
    <section className="page">
      <Link to="/pipelines" className="back-link">Pipelines / {brand} · {topic}</Link>
      <div className="detail-header">
        <h1>{topic || 'Post build'}</h1>
        <span className="spacer" />
        <span className={`badge${r.status === 'failed' ? ' badge-bad' : r.status === 'done' ? ' badge-ok' : ''}`}>
          {STATUS_LABEL[r.status] ?? r.status}
        </span>
      </div>
      <p className="subtitle">
        {brand} · {PLATFORM_LABELS[String(r.brief.platform)] ?? String(r.brief.platform)} ·{' '}
        {r.kind === 'revision' ? 'revision' : 'new post'} · started {timeAgo(r.started_at ?? r.created_at)}
      </p>

      <div className="detail-two">
        <ol className="vsteps">
          {steps.map((s) => <VStep key={s.key} step={s} />)}
        </ol>

        <aside className="card summary">
          <h2 style={{ marginTop: 0 }}>Summary</h2>
          <dl>
            <dt>Status</dt>
            <dd>{STATUS_LABEL[r.status]}{r.status === 'running' ? `, step ${done + 1} of ${steps.length}` : ''}</dd>
            {r.trigger && (
              <>
                <dt>Started</dt>
                <dd>
                  {r.trigger === 'chat' && r.conversation_id
                    ? <Link to={`/chat/${r.conversation_id}`}>{triggerText(r, Object.fromEntries((chats.data ?? []).map((c) => [c.id, c.title])))}</Link>
                    : triggerText(r, {})}
                </dd>
              </>
            )}
            <dt>Brief</dt>
            <dd>{topic} · {String(r.brief.goal ?? 'engagement')}</dd>
            <dt>Cost</dt>
            <dd>{formatCost(r.cost)}{r.status === 'running' ? ' so far' : ''}</dd>
            <dt>When</dt>
            <dd>{new Date(r.started_at ?? r.created_at).toLocaleString()}</dd>
          </dl>
          {r.error && <div className="error-box small" style={{ marginBottom: '1rem' }}>{r.error}</div>}
          <div className="action-buttons">
            {r.post_id && <Link className="btn btn-primary" to={`/posts/${r.post_id}`}>Open the post</Link>}
            {r.status === 'failed' && <Link className="btn" to={`/new?brand=${r.brand_id}`}>Try again</Link>}
          </div>
        </aside>
      </div>
    </section>
  )
}

function VStep({ step }: { step: StepView }) {
  return (
    <li className={`vstep s-${step.state}`}>
      <span className="chain-dot" />
      <div>
        <div className="vstep-name">{step.long}</div>
        <div className="vstep-detail">{detail(step)}</div>
      </div>
    </li>
  )
}

function detail(s: StepView): string {
  const models = [...new Set(s.records.map((r) => r.model))].join(', ')
  const attempts = s.records.reduce((t, r) => t + r.attempts, 0)
  switch (s.state) {
    case 'done':
      return [models, `${stepSeconds(s).toFixed(1)}s`,
              formatCost({ [s.records[0]?.currency ?? 'INR']: stepCost(s) }),
              attempts > s.records.length ? `${attempts} attempts` : ''].filter(Boolean).join(' · ')
    case 'running':
      return 'running now…'
    case 'cached':
      return 'used saved research, no cost'
    case 'failed':
      return 'failed here (see the error in the summary)'
    case 'skipped':
      return 'not needed'
    default:
      return 'waiting'
  }
}
