import { useEffect, useRef } from 'react'
import type { Run } from '../api/client'
import { isActive, useRun } from '../api/hooks'
import { ErrorBox } from './common'

type Step = { agent: string; model: string; attempts: number; seconds: number; cost: number; currency: string }

// Internal step names -> what a person would call them.
function stepLabel(agent: string): string {
  const fixed: Record<string, string> = {
    topic_research: 'Researching the topic',
    market_research: 'Researching the market',
    strategy: 'Planning the post',
    content: 'Writing the post',
    reviewer: 'Reviewing it',
  }
  if (fixed[agent]) return fixed[agent]
  if (agent.startsWith('feedback_check')) return 'Checking your requests were done'
  if (agent.startsWith('feedback_revision')) return 'Applying your feedback'
  if (agent.startsWith('feedback_review')) return 'Reviewing the revision'
  if (agent.startsWith('revision')) return 'Fixing what the reviewer found'
  if (agent.startsWith('review')) return 'Reviewing again'
  return agent
}

export function formatCost(cost: Record<string, number>): string {
  const symbols: Record<string, string> = { INR: '₹', USD: '$' }
  const parts = Object.entries(cost).map(([cur, amount]) => `${symbols[cur] ?? cur + ' '}${amount.toFixed(3)}`)
  return parts.length ? parts.join(' + ') : '₹0'
}

/**
 * Live progress of a run (polled every second by useRun). Calls onFinished once, when the
 * run is done or failed, so the page can refresh data or move on.
 */
export default function RunProgress({ runId, onFinished }: { runId: string; onFinished?: (run: Run) => void }) {
  const run = useRun(runId)
  const reported = useRef(false)

  useEffect(() => {
    const data = run.data
    if (data && !isActive(data.status) && !reported.current) {
      reported.current = true
      onFinished?.(data)
    }
  }, [run.data, onFinished])

  if (run.error) return <ErrorBox error={run.error} />
  if (!run.data) return <p className="muted">Starting…</p>
  const r = run.data
  const steps = r.steps as Step[]

  return (
    <div className="card run-progress">
      <div className="run-status">
        {r.status === 'queued' && <span className="muted">Waiting for the worker… (is `buildsocial worker` running?)</span>}
        {r.status === 'running' && <span>Working on it…</span>}
        {r.status === 'done' && <span className="ok-text">✓ Done</span>}
        {r.status === 'failed' && <span className="bad-text">✗ Failed</span>}
        <span className="spacer" />
        <span className="muted small">cost so far: {formatCost(r.cost)}</span>
      </div>
      <ol className="steps">
        {steps.map((s, i) => (
          <li key={i} className="step-done">
            <span>✓ {stepLabel(s.agent)}</span>
            <span className="muted small">
              {s.seconds.toFixed(1)}s · {s.model}{s.attempts > 1 ? ` · ${s.attempts} attempts` : ''}
            </span>
          </li>
        ))}
        {r.status === 'running' && r.current_step && (
          <li className="step-current">
            <span className="spinner" /> {stepLabel(r.current_step)}…
          </li>
        )}
      </ol>
      {r.error && <div className="error-box small">{r.error}</div>}
    </div>
  )
}
