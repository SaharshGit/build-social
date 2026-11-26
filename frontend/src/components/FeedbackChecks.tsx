// "Your requests": after a revision, each thing the person asked for, done ✓ or not ✗.
// Measurable requests (length, hashtags, emojis, questions, phrases) were checked by code.

export type FeedbackCheck = { request: string; done: boolean; detail?: string; measured?: boolean }
export type Note = { kind: string; text: string; at?: string; checks?: FeedbackCheck[] }

/** The checks from the most recent revision note, if it has any. */
export function latestChecks(notes: unknown[]): { feedback: string; checks: FeedbackCheck[] } | null {
  const revise = [...(notes as Note[])].reverse().find((n) => n.kind === 'revise')
  return revise?.checks?.length ? { feedback: revise.text, checks: revise.checks } : null
}

export default function FeedbackChecks({ checks, compact = false }: { checks: FeedbackCheck[]; compact?: boolean }) {
  const missed = checks.filter((c) => !c.done).length
  return (
    <div className={`feedback-checks${compact ? ' compact' : ''}`}>
      <div className="feedback-checks-title">
        Your requests{missed ? <span className="bad-text"> · {missed} not done</span> : <span> · all done</span>}
      </div>
      <ul>
        {checks.map((c, i) => (
          <li key={i} className={c.done ? 'done' : 'missed'}>
            <span className="mark">{c.done ? '✓' : '✗'}</span>
            <span>
              {c.request}
              {c.detail && (!c.done || c.measured) && <span className="muted"> ({c.detail})</span>}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
