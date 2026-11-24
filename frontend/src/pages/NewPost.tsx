import { useState } from 'react'
import { useNavigate } from 'react-router'
import type { Brief } from '../api/client'
import { useBrands, useStartRun } from '../api/hooks'
import { ErrorBox, Loading } from '../components/common'

const GOALS = ['engagement', 'awareness', 'sales', 'community'] as const

/** Start a pipeline run: the API answers at once, then the run page shows live progress. */
export default function NewPost() {
  const brands = useBrands()
  const start = useStartRun()
  const navigate = useNavigate()
  // "Controlled inputs": each field's value lives in React state and the input shows it.
  const [brandId, setBrandId] = useState('')
  const [topic, setTopic] = useState('')
  const [platform, setPlatform] = useState<Brief['platform']>('instagram')
  const [goal, setGoal] = useState<NonNullable<Brief['goal']>>('engagement')
  const [notes, setNotes] = useState('')

  if (brands.isLoading) return <Loading what="Loading brands" />
  if (brands.error) return <ErrorBox error={brands.error} />

  const canSubmit = brandId && topic.trim().length >= 3 && !start.isPending

  return (
    <section>
      <h1>New post</h1>
      <p className="muted">Research, plan, write and review a post. Takes about 15–60 seconds.</p>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault()
          const brief: Brief = { topic: topic.trim(), platform, format: 'text', goal, notes: notes.trim() || null }
          start.mutate({ brandId, brief }, { onSuccess: (run) => navigate(`/runs/${run.id}`) })
        }}
      >
        <label>
          Brand
          <select value={brandId} onChange={(e) => setBrandId(e.target.value)} required>
            <option value="">Choose a brand…</option>
            {(brands.data ?? []).map((b) => (
              <option key={b.id} value={b.id}>{b.name} ({b.market})</option>
            ))}
          </select>
        </label>
        <label>
          Topic
          <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="e.g. onion hair oil for winter hair care" required />
        </label>
        <div className="form-row">
          <label>
            Platform
            <select value={platform} onChange={(e) => setPlatform(e.target.value as Brief['platform'])}>
              <option value="instagram">Instagram</option>
              <option value="linkedin">LinkedIn</option>
            </select>
          </label>
          <label>
            Goal
            <select value={goal} onChange={(e) => setGoal(e.target.value as typeof goal)}>
              {GOALS.map((g) => <option key={g} value={g}>{g}</option>)}
            </select>
          </label>
        </div>
        <label>
          Notes (optional)
          <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Anything the post must mention or avoid" />
        </label>
        <button className="btn btn-primary" disabled={!canSubmit}>
          {start.isPending ? 'Starting…' : 'Create post'}
        </button>
        {start.error && <ErrorBox error={start.error} />}
      </form>
    </section>
  )
}
