// The steps a run goes through, and where a given run is: done / running / waiting / failed / cached.
// Used by the Pipelines list (step chain) and the pipeline detail page (vertical steps).
import type { Run } from '../api/client'

export type StepState = 'done' | 'running' | 'waiting' | 'failed' | 'cached' | 'skipped'

export type StepDef = { key: string; label: string; long: string; optional?: boolean }

// Step labels match what the backend records (pipeline.py StepRunner labels).
const PIPELINE: StepDef[] = [
  { key: 'topic_research', label: 'Research', long: 'Research the topic' },
  { key: 'market_research', label: 'Market', long: 'Research the market' },
  { key: 'strategy', label: 'Plan', long: 'Plan the post' },
  { key: 'content', label: 'Write', long: 'Write the post' },
  { key: 'reviewer', label: 'Review', long: 'Review' },
  { key: 'revision', label: 'Fix', long: 'Fix review issues', optional: true },
]

const REVISION: StepDef[] = [
  { key: 'feedback_revision', label: 'Revise', long: 'Apply your feedback' },
  { key: 'feedback_review', label: 'Review', long: 'Review the revision' },
  { key: 'feedback_check', label: 'Check', long: 'Check your requests were done' },
]

export type RecordedStep = { agent: string; model: string; attempts: number; seconds: number; cost: number; currency: string }

export type StepView = StepDef & { state: StepState; records: RecordedStep[] }

const matches = (agent: string, key: string) =>
  agent === key || agent.startsWith(`${key}_`) || (key === 'revision' && /^(revision|review)_\d+$/.test(agent))

/** Every step of this run with its state, in order. */
export function stepsFor(run: Run): StepView[] {
  const defs = run.kind === 'revision' ? REVISION : PIPELINE
  const recorded = run.steps as RecordedStep[]
  const current = run.current_step ?? ''
  const views: StepView[] = defs.map((d) => ({
    ...d,
    records: recorded.filter((s) => matches(s.agent, d.key)),
    state: 'waiting',
  }))
  const lastTouched = Math.max(-1, ...views.map((v, i) => (v.records.length || (current && matches(current, v.key)) ? i : -1)))

  views.forEach((v, i) => {
    if (run.status === 'running' && current && matches(current, v.key)) v.state = 'running'
    else if (v.records.length) v.state = 'done'
    else if (i < lastTouched) v.state = v.key.endsWith('research') ? 'cached' : 'skipped'
    else if (run.status === 'done') v.state = 'skipped' // optional steps that weren't needed
  })
  if (run.status === 'failed') {
    const failedAt = views.find((v) => v.state === 'waiting' || v.state === 'running')
    if (failedAt) failedAt.state = 'failed'
  }
  // The optional "fix" step is only shown once it actually ran.
  return views.filter((v) => !v.optional || v.records.length || v.state === 'running')
}

export function stepSeconds(v: StepView) {
  return v.records.reduce((t, r) => t + r.seconds, 0)
}

export function stepCost(v: StepView) {
  return v.records.reduce((t, r) => t + r.cost, 0)
}

/** Where a run came from, in words: "from chat “Monsoon post…”", "from the New post form"… ('' for old runs). */
export function triggerText(run: Run, chatTitles: Record<string, string>): string {
  switch (run.trigger) {
    case 'chat': {
      const title = run.conversation_id ? chatTitles[run.conversation_id] : undefined
      return run.conversation_id ? `from chat “${title ?? '…'}”` : 'from a chat (deleted)'
    }
    case 'form':
      return run.kind === 'revision' ? 'from the post page' : 'from the New post form'
    case 'schedule':
      return 'from a schedule'
    default:
      return ''
  }
}
