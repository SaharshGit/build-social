import { useCallback, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import type { Run } from '../api/client'
import { useRefreshAfterRun } from '../api/hooks'
import RunProgress from '../components/RunProgress'

/** Live progress of one run. Its own URL, so a refresh keeps watching the same run. */
export default function RunPage() {
  const { runId = '' } = useParams()
  const navigate = useNavigate()
  const refresh = useRefreshAfterRun()
  const [finished, setFinished] = useState<Run | null>(null)

  const onFinished = useCallback(
    (run: Run) => {
      setFinished(run)
      refresh(run.post_id)
      if (run.status === 'done' && run.post_id) navigate(`/posts/${run.post_id}`) // straight to review
    },
    [navigate, refresh],
  )

  return (
    <section>
      <h1>Creating your post</h1>
      <RunProgress runId={runId} onFinished={onFinished} />
      {finished?.status === 'failed' && (
        <p>
          The run failed (details above). <Link to="/new">Try again</Link>
        </p>
      )}
    </section>
  )
}
