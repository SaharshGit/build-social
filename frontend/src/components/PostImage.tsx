// A single-image post's designed image: the picture, whether it passed its checks, Download, "Try another
// layout" (a background run with live progress), and what was checked. Falls back to the words-only preview
// while there is no image (still being made, or making it failed).
import { useCallback, useState } from 'react'
import { apiUrl, type Post, type PostImage as PostImageData, type Run } from '../api/client'
import { useRedesignImage, useRefreshAfterRun } from '../api/hooks'
import { ErrorBox } from './common'
import ImageTextPreview from './ImageTextPreview'
import RunProgress from './RunProgress'

const LAYOUTS: Record<string, string> = {
  photo: 'Photo with headline', split: 'Split', framed: 'Framed photo', type_led: 'Type-led',
}
const MEASURED: Record<string, string> = {
  fits: 'Text fits', inside_safe_box: 'Inside the safe area', text_sizes: 'Text sizes', contrast: 'Contrast',
  calm_behind: 'Calm behind the text', logo_size: 'Logo size',
}
const VISUAL: Record<string, string> = {
  words_exact: 'Words match', text_readable: 'Easy to read', one_focus: 'One clear focus',
  brand_colours_fonts: 'Brand colours and fonts', style_match: 'Matches the brand’s style',
  product_true: 'Product shown truthfully', not_generic: 'Not generic', no_other_brands: 'No other brands',
  logo_ok: 'Logo',
}

type MeasuredCheck = { name: string; passed: boolean; detail: string }
type VisualCheck = { name: string; passed: boolean; reason: string }
type Attempt = { layout?: string; problems?: string[]; problem?: string }

export default function PostImage({ post, quotes = [], compact = false }: { post: Post; quotes?: string[]; compact?: boolean }) {
  const img = post.image as PostImageData | null | undefined
  const redesign = useRedesignImage(post.id)
  const refresh = useRefreshAfterRun()
  const [runId, setRunId] = useState<string | null>(null)
  const onFinished = useCallback(() => {
    refresh(post.id)
    setRunId(null) // the new image (or the error) is on the post now
  }, [post.id, refresh])

  if (!post.draft.image_text) return null // a text post
  const hasFile = !!img?.url
  const canChange = post.status === 'pending' && !compact

  if (!img && !runId) return <ImageTextPreview post={post} quotes={quotes} />

  const start = () => redesign.mutate(undefined, { onSuccess: (run: Run) => setRunId(run.id) })
  const visual = (img?.visual?.checks ?? []) as VisualCheck[]
  const measured = (img?.checks ?? []) as MeasuredCheck[]
  const attempts = (img?.attempts ?? []) as Attempt[]
  const read = (img?.visual as { read?: { headline?: string } } | null | undefined)?.read
  const needsLook = img?.status === 'needs_attention'

  return (
    <div className={`post-image${compact ? ' compact' : ''}`}>
      {hasFile && (
        <img className="post-image-pic" src={apiUrl(img!.url)} width={img!.width ?? 1080} height={img!.height ?? 1350}
             alt={read?.headline ?? post.draft.image_text.headline} />
      )}

      {img && (
        <div className="post-image-bar">
          {hasFile && (
            <span className={`image-status ${needsLook ? 'warn' : 'ok'}`}>
              {needsLook ? 'Needs a look' : '✓ Checked'}
            </span>
          )}
          {img.layout && <span className="muted small">{LAYOUTS[img.layout] ?? img.layout}</span>}
          <span className="spacer" />
          {hasFile && (
            <a className="btn btn-small" href={apiUrl(img.url)} download={`post-${post.id.slice(0, 8)}.png`}>Download</a>
          )}
          {canChange && !runId && (
            <button className="btn btn-small" onClick={start} disabled={redesign.isPending}>
              {img.error ? 'Try again' : 'Try another layout'}
            </button>
          )}
        </div>
      )}

      {img?.error && (
        <p className="warn small">
          {hasFile ? 'The last attempt to change the image failed: ' : 'The image couldn’t be made: '}{img.error}
        </p>
      )}
      {needsLook && !compact && (
        <p className="small warn">
          A check still fails (see below). The image is kept so you can judge it; try another layout, or approve it if it looks right to you.
        </p>
      )}

      {runId && <RunProgress runId={runId} onFinished={onFinished} />}
      {redesign.error && <ErrorBox error={redesign.error} />}

      {img && !compact && (measured.length > 0 || visual.length > 0) && (
        <details className="image-checks">
          <summary>What was checked</summary>
          <ul className="small">
            {measured.map((c) => (
              <li key={c.name} className={c.passed ? '' : 'warn'}>
                {c.passed ? '✓' : '✗'} {MEASURED[c.name] ?? c.name}
                <span className="muted"> · {c.detail}</span>
              </li>
            ))}
            {visual.map((c) => (
              <li key={c.name} className={c.passed ? '' : 'warn'}>
                {c.passed ? '✓' : '✗'} {VISUAL[c.name] ?? c.name}
                <span className="muted"> · {c.reason}</span>
              </li>
            ))}
            {(img.visual as { error?: string } | null)?.error && (
              <li className="muted">The model check couldn’t run, so only the measured checks apply.</li>
            )}
          </ul>
          {attempts.length > 1 && (
            <p className="muted tiny">
              Designs tried: {attempts.filter((a) => a.layout).map((a) => LAYOUTS[a.layout!] ?? a.layout).join(' → ')}.
              The first ones failed: {attempts.flatMap((a) => a.problems ?? []).slice(0, 3).join('; ') || 'see the checks'}.
            </p>
          )}
        </details>
      )}
    </div>
  )
}
