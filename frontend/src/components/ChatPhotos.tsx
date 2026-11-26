// Photos in the chat: chips for photos attached to the message being written, and a "Your photo" card
// under a sent message (what the image-reading model saw, and a button to keep it in the brand library).
import { assetUrl, type Asset } from '../api/client'
import { useAsset, useUpdateAsset } from '../api/hooks'

type Reading = { other_brands?: string[]; issues?: string[]; text_space?: string[]; suggested_product?: string | null }

const readingOf = (a: Asset) => (a.analysis ?? {}) as Reading & Record<string, unknown>

/** A photo attached to the message being written. */
export function PhotoChip({ asset, onRemove }: { asset: Asset; onRemove: () => void }) {
  const checked = 'other_brands' in readingOf(asset)
  return (
    <span className="photo-chip">
      <img src={assetUrl(asset.id)} alt="" />
      <span className="photo-chip-text">
        <span className="photo-chip-name">{asset.original_name ?? 'photo'}</span>
        <span className="tiny muted">{checked ? 'read ✓' : 'couldn’t read it'}</span>
      </span>
      <button type="button" className="icon-btn" title="Remove photo" onClick={onRemove}>✕</button>
    </span>
  )
}

/** Under a sent message: what the photo shows, and whether it's kept for future posts. */
export function PhotoNote({ assetId }: { assetId: string }) {
  const asset = useAsset(assetId)
  const update = useUpdateAsset(asset.data?.brand_id ?? '')
  if (!asset.data) return null
  const a = asset.data
  const r = readingOf(a)
  const product = a.product ?? r.suggested_product
  const flags = [...(r.other_brands ?? []).map((b) => `shows another brand (${b})`), ...(r.issues ?? [])]

  return (
    <div className="card photo-note">
      <img src={assetUrl(a.id)} alt={a.description ?? 'Attached photo'} />
      <div>
        <strong className="small">Your photo</strong>
        {a.description && <p className="muted tiny">{a.description}</p>}
        <p className="tiny">
          {product && <>Looks like {product}{a.product ? '' : ' (confirm)'}</>}
          {product && (r.text_space ?? []).length > 0 && '  ·  '}
          {(r.text_space ?? []).length > 0 && `text fits at the ${r.text_space!.join(', ')}`}
        </p>
        {flags.length > 0 && <p className="tiny warn">{flags.join(' · ')}</p>}
        {a.in_library ? (
          <p className="tiny muted">Saved in the brand library.</p>
        ) : (
          <p className="tiny muted">
            Used for this post only.{' '}
            <button className="link-btn tiny" disabled={update.isPending}
              onClick={() => update.mutate({ assetId: a.id, change: { in_library: true, ...(product && !a.product ? { product } : {}) } },
                { onSuccess: () => asset.refetch() })}>
              Save to the brand library
            </button>
          </p>
        )}
      </div>
    </div>
  )
}
