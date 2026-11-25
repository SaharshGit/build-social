import type { Run } from '../api/client'
import { stepsFor } from '../lib/steps'

/** The horizontal CI-style chain: one dot per step, joined by lines. */
export default function StepChain({ run }: { run: Run }) {
  return (
    <div className="chain" aria-label="Steps">
      {stepsFor(run).map((s) => (
        <div key={s.key} className={`chain-step s-${s.state}`} title={`${s.long}: ${s.state}`}>
          <span className="chain-dot" />
          <span className="chain-label">{s.label}</span>
        </div>
      ))}
    </div>
  )
}
