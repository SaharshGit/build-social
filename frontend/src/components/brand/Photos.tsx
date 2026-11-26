// Brand → Photos: the style board (what the brand's own pictures look like) and the photo library.
// Every upload is read once by an image-reading model (about INR 0.09): what is in it, where text
// fits, and problems like another brand's logo. The reading never becomes a product claim.
import { useState } from 'react'
import { assetUrl, type Asset, type AssetKind, type Brand, type Style } from '../../api/client'
import {
  useAssets, useDeleteAsset, useReadAsset, useReadStyle, useStyle, useUpdateAsset, useUploadAsset,
} from '../../api/hooks'
import { formatCost } from '../RunProgress'
import { ErrorBox, Loading } from '../common'

const KINDS: [AssetKind, string][] = [
  ['product', 'Product'], ['lifestyle', 'Lifestyle'], ['place', 'Place'], ['reference', 'Reference'],
  ['logo', 'Logo'], ['other', 'Other'],
]
const KIND_LABEL = Object.fromEntries(KINDS) as Record<AssetKind, string>
const MIN_REFERENCES = 3

type Filter = AssetKind | 'all' | 'one-post'

export default function Photos({ brand }: { brand: Brand }) {
  const assets = useAssets(brand.id)
  const [filter, setFilter] = useState<Filter>('all')

  if (assets.isLoading) return <Loading what="Loading photos" />
  if (assets.error) return <ErrorBox error={assets.error} />
  const all = assets.data ?? []
  const shown = all.filter((a) =>
    filter === 'all' ? true : filter === 'one-post' ? !a.in_library : a.in_library && a.kind === filter)
  const count = (f: Filter) => all.filter((a) => (f === 'one-post' ? !a.in_library : a.in_library && a.kind === f)).length

  return (
    <div className="photos">
      <Uploader brand={brand} />
      <StyleBoard brand={brand} />

      <h2 className="library-title">Library</h2>
      <div className="chips">
        <button className={`chip ${filter === 'all' ? 'active' : ''}`} onClick={() => setFilter('all')}>All · {all.length}</button>
        {KINDS.filter(([k]) => count(k) > 0).map(([k, label]) => (
          <button key={k} className={`chip ${filter === k ? 'active' : ''}`} onClick={() => setFilter(k)}>{label} · {count(k)}</button>
        ))}
        {count('one-post') > 0 && (
          <button className={`chip ${filter === 'one-post' ? 'active' : ''}`} onClick={() => setFilter('one-post')}>
            For one post · {count('one-post')}
          </button>
        )}
      </div>
      {all.length === 0 && (
        <p className="muted">No photos yet. Upload the brand's real product photos first: posts are built around them.</p>
      )}
      <div className="photo-grid">
        {shown.map((a) => <PhotoCard key={a.id} brand={brand} asset={a} />)}
      </div>
    </div>
  )
}

function Uploader({ brand }: { brand: Brand }) {
  const upload = useUploadAsset(brand.id)
  const [kind, setKind] = useState<AssetKind>('product')
  const [product, setProduct] = useState('')
  const [progress, setProgress] = useState<string | null>(null)
  const products = brand.profile.products ?? []

  // One at a time: each upload is read by a model before the server answers.
  const onFiles = async (files: FileList | null) => {
    if (!files) return
    const list = Array.from(files)
    for (const [i, file] of list.entries()) {
      setProgress(`Uploading and reading ${i + 1} of ${list.length}…`)
      try {
        await upload.mutateAsync({ file, kind, product: product || undefined })
      } catch {
        break // the error is shown below
      }
    }
    setProgress(null)
  }

  return (
    <div className="uploader card">
      <div className="uploader-fields">
        <label>Kind
          <select value={kind} onChange={(e) => setKind(e.target.value as AssetKind)}>
            {KINDS.filter(([k]) => k !== 'logo').map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
        </label>
        <label>Shows product
          <select value={product} onChange={(e) => setProduct(e.target.value)}>
            <option value="">None / not sure</option>
            {products.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
          </select>
        </label>
        <label className="btn btn-primary file-btn">
          {progress ?? '+ Upload photos'}
          <input type="file" accept="image/png,image/jpeg,image/webp" multiple hidden disabled={!!progress}
            onChange={(e) => { onFiles(e.target.files); e.target.value = '' }} />
        </label>
      </div>
      <p className="muted small">
        PNG, JPG or WebP, up to 10 MB. Each photo is read once (about ₹0.09) to see what is in it and where text fits.
        Use <strong>Reference</strong> for the brand's best past posts: they teach the style board.
      </p>
      {upload.error && <ErrorBox error={upload.error} />}
    </div>
  )
}

function StyleBoard({ brand }: { brand: Brand }) {
  const style = useStyle(brand.id)
  const read = useReadStyle(brand.id)
  if (style.isLoading || !style.data) return null
  const s: Style = style.data
  const r = s.reading
  const canRead = s.references_usable >= MIN_REFERENCES

  const onRead = () => {
    if (s.edited && !confirm('You edited the style board. Reading again replaces your edits. Continue?')) return
    read.mutate(s.edited)
  }

  return (
    <div className="card style-board">
      <div className="style-head">
        <h2>Style board</h2>
        {r && s.at && (
          <span className="muted small">
            Read from {s.asset_ids.length} reference images · {formatCost({ [s.currency ?? 'USD']: s.model_cost ?? 0 })} · {new Date(s.at).toLocaleString()}
            {s.edited && ' · edited by you'}
          </span>
        )}
        <span className="spacer" />
        {r && s.stale && <span className="stale-chip">References changed</span>}
        <button className="btn btn-primary btn-small" onClick={onRead} disabled={!canRead || read.isPending}>
          {read.isPending ? 'Reading…' : r ? 'Read again' : 'Read style'}
        </button>
      </div>

      {!r && (
        <p className="muted small">
          {canRead
            ? `${s.references_usable} reference images ready. Reading them costs about ₹0.2 and takes a few seconds.`
            : `Upload at least ${MIN_REFERENCES} reference images (the brand's best past posts or mood photos) to read its style. ${s.references_usable} usable so far.`}
        </p>
      )}

      {r && (
        <div className="style-body">
          <div>
            <div className="palette">
              {r.palette.map((hex) => <span key={hex} className="palette-swatch" style={{ background: hex }} title={hex} />)}
            </div>
            <p className="muted tiny">Colours seen in the photos</p>
            {s.kit_mismatch.length > 0 && (
              <p className="warn tiny">Not in the kit: {s.kit_mismatch.join(', ')}. Worth a look.</p>
            )}
          </div>
          <dl className="style-facts small">
            <dt>Lighting</dt><dd>{r.lighting}</dd>
            <dt>Composition</dt><dd>{r.composition}</dd>
            <dt>Props</dt><dd>{r.textures_props.join(', ') || '–'}</dd>
            <dt>Text on images</dt><dd>{r.text_on_image}</dd>
            <dt>Mood</dt><dd>{r.mood.join(' · ')}</dd>
            <dt>Avoid</dt><dd>{r.avoid.join(', ') || '–'}</dd>
          </dl>
        </div>
      )}

      {s.references_excluded.length > 0 && (
        <p className="muted tiny">
          Left out: {s.references_excluded.length} reference{s.references_excluded.length > 1 ? 's' : ''} (
          {s.references_excluded.map((e) => String(e.reason)).join('; ')}).
        </p>
      )}
      {read.error && <ErrorBox error={read.error} />}
    </div>
  )
}

function PhotoCard({ brand, asset: a }: { brand: Brand; asset: Asset }) {
  const update = useUpdateAsset(brand.id)
  const reread = useReadAsset(brand.id)
  const remove = useDeleteAsset(brand.id)
  const analysis = (a.analysis ?? {}) as Record<string, unknown>
  const otherBrands = (analysis.other_brands as string[] | undefined) ?? []
  const issues = (analysis.issues as string[] | undefined) ?? []
  const textSpace = (analysis.text_space as string[] | undefined) ?? []
  const checked = 'other_brands' in analysis
  const flagged = otherBrands.length > 0 || issues.length > 0

  return (
    <div className={`photo-card ${otherBrands.length ? 'blocked' : ''} ${a.in_library ? '' : 'one-post'}`}>
      <div className="photo-thumb">
        <img src={assetUrl(a.id)} alt={a.description ?? a.original_name ?? 'Brand photo'} loading="lazy" />
        <span className="kind-chip">{a.in_library ? KIND_LABEL[a.kind] : 'For one post'}</span>
        <button className="photo-delete" title="Delete photo"
          onClick={() => confirm('Delete this photo?') && remove.mutate(a.id)}>✕</button>
      </div>
      <div className="photo-info">
        <strong className="small">{a.product ?? a.original_name ?? KIND_LABEL[a.kind]}</strong>
        {a.description && <p className="muted tiny clamp">{a.description}</p>}

        {!checked && (
          <p className="tiny warn">
            {analysis.error ? 'Couldn’t read this photo.' : 'Not checked yet.'}{' '}
            <button className="link-btn" onClick={() => reread.mutate(a.id)} disabled={reread.isPending}>
              {reread.isPending ? 'Reading…' : 'Read it'}
            </button>
          </p>
        )}
        {otherBrands.length > 0 && (
          <p className="tiny warn">Shows another brand ({otherBrands.join(', ')}). Not used for style.</p>
        )}
        {issues.length > 0 && <p className="tiny warn">{issues.join(' · ')}</p>}
        {checked && !flagged && (
          <p className="tiny">{textSpace.length ? `✓ Text fits at the ${textSpace.join(', ')}` : '✓ No issues; busy photo, text goes beside it'}</p>
        )}

        <div className="photo-edit">
          <select value={a.kind} onChange={(e) => update.mutate({ assetId: a.id, change: { kind: e.target.value as AssetKind } })}>
            {KINDS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
          <select value={a.product ?? ''} onChange={(e) => update.mutate({ assetId: a.id, change: { product: e.target.value || null } })}>
            <option value="">No product</option>
            {(brand.profile.products ?? []).map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
          </select>
        </div>
        {!a.in_library && (
          <button className="link-btn tiny" onClick={() => update.mutate({ assetId: a.id, change: { in_library: true } })}>
            Save to library
          </button>
        )}
        {(update.error || reread.error || remove.error) && <ErrorBox error={(update.error || reread.error || remove.error)!} />}
      </div>
    </div>
  )
}
