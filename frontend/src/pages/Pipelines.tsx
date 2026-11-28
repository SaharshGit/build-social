// Pipelines: every post build, CI/CD style. Each row shows the run's step chain:
// done (filled), running (pulsing), waiting (empty), cached (ring), failed (red).
import { Link, useSearchParams } from 'react-router'
import type { Run } from '../api/client'
import { isActive, useBrandNames, useConversations, useRuns } from '../api/hooks'
import { ErrorBox, Loading } from '../components/common'
import { formatCost } from '../components/RunProgress'
import StepChain from '../components/StepChain'
import { PLATFORM_LABELS, timeAgo } from '../lib/format'
import { triggerText } from '../lib/steps'

const FILTERS = [
  { value: '', label: 'All' },
  { value: 'running', label: 'Running' },
  { value: 'queued', label: 'Queued' },
  { value: 'done', label: 'Done' },
  { value: 'failed', label: 'Failed' },
]

export default function Pipelines() {
  const [params, setParams] = useSearchParams()
  const filter = params.get('status') ?? ''
  const runs = useRuns()
  const brandNames = useBrandNames()
  const chats = useConversations()
  const chatTitles = Object.fromEntries((chats.data ?? []).map((c) => [c.id, c.title]))
  const all = runs.data ?? []
  const shown = filter ? all.filter((r) => r.status === filter) : all
  const count = (status: string) => all.filter((r) => r.status === status).length

  return (
    <section className="page">
      <h1>Pipelines</h1>
      <p className="subtitle">Every post build, from the chat, the form and (soon) schedules. Updates live.</p>

      <div className="chips">
        {FILTERS.map((f) => (
          <button key={f.value} className={`chip${filter === f.value ? ' active' : ''}`}
                  onClick={() => setParams(f.value ? { status: f.value } : {})}>
            {f.label}{f.value && count(f.value) ? ` · ${count(f.value)}` : ''}
          </button>
        ))}
      </div>

      {runs.isLoading && <Loading what="Loading pipelines" />}
      {runs.error && <ErrorBox error={runs.error} />}
      {runs.data && shown.length === 0 && (
        <p className="muted">No {filter || ''} builds yet. Ask for a post in the chat and it shows up here.</p>
      )}
      <div className="card-list">
        {shown.map((r) => <PipelineRow key={r.id} run={r} brand={brandNames[r.brand_id]} source={triggerText(r, chatTitles)} />)}
      </div>
    </section>
  )
}

function PipelineRow({ run, brand, source }: { run: Run; brand?: string; source: string }) {
  const topic = String(run.brief.topic ?? '')
  const platform = PLATFORM_LABELS[String(run.brief.platform)] ?? String(run.brief.platform ?? '')
  return (
    <Link to={`/pipelines/${run.id}`} className="card pipeline-row">
      <span className={`status-dot st-${run.status}`} title={run.status} />
      <div className="info">
        <div className="pipeline-title">{[brand ?? 'Brand', topic, platform].filter(Boolean).join(' · ')}</div>
        <div className={`pipeline-meta${run.status === 'failed' ? ' bad' : ''}`}>{metaLine(run, source)}</div>
      </div>
      <StepChain run={run} />
    </Link>
  )
}

function metaLine(run: Run, source: string): string {
  const kind = run.kind === 'revision' ? 'Revision' : run.kind === 'image' ? 'Image' : 'New post'
  const parts = [source ? `${kind} ${source}` : kind]
  if (run.status === 'queued') parts.push(`queued ${timeAgo(run.created_at)}, waiting for the worker`)
  else if (run.status === 'running') parts.push(`started ${timeAgo(run.started_at ?? run.created_at)}`)
  else if (run.status === 'done') parts.push(`done ${timeAgo(run.finished_at)}${duration(run)}`)
  else if (run.status === 'failed') parts.push(`failed${run.error ? `: ${run.error.slice(0, 80)}` : ''}`)
  if (Object.keys(run.cost).length) parts.push(isActive(run.status) ? `${formatCost(run.cost)} so far` : formatCost(run.cost))
  return parts.join(' · ')
}

function duration(run: Run): string {
  if (!run.started_at || !run.finished_at) return ''
  const s = (new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000
  return ` in ${Math.round(s)}s`
}
