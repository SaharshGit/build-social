// Add or edit a brand: /brands/new and /brands/:brandId/edit.
// The form keeps everything as plain strings while you type and turns it into a BrandProfile
// (the same shape the agents read) only when you save. The API checks it again.
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import type { BrandProfile } from '../api/client'
import { useBrand, useSaveBrand } from '../api/hooks'
import { ErrorBox, Loading } from '../components/common'

type ProductFields = { name: string; description: string; ingredients: string; claims: string }
type Fields = {
  name: string; industry: string; market: string; language: string
  description: string; audience: string; voice: string
  pillars: string; dos: string; donts: string // one item per line
  examples: string[]
  products: ProductFields[]
}

const EMPTY: Fields = {
  name: '', industry: '', market: '', language: 'English', description: '', audience: '', voice: '',
  pillars: '', dos: '', donts: '', examples: [], products: [],
}

const lines = (text: string) => text.split('\n').map((x) => x.trim()).filter(Boolean)

function toFields(p: BrandProfile): Fields {
  return {
    name: p.name, industry: p.industry, market: p.market, language: p.language ?? 'English',
    description: p.description, audience: p.audience, voice: p.voice,
    pillars: (p.content_pillars ?? []).join('\n'), dos: (p.dos ?? []).join('\n'), donts: (p.donts ?? []).join('\n'),
    examples: p.example_posts ?? [],
    products: (p.products ?? []).map((x) => ({
      name: x.name, description: x.description ?? '',
      ingredients: (x.key_ingredients ?? []).join(', '), claims: (x.approved_claims ?? []).join('\n'),
    })),
  }
}

function toProfile(f: Fields): BrandProfile {
  return {
    name: f.name.trim(), industry: f.industry.trim(), market: f.market.trim(), language: f.language.trim() || 'English',
    description: f.description.trim(), audience: f.audience.trim(), voice: f.voice.trim(),
    content_pillars: lines(f.pillars), dos: lines(f.dos), donts: lines(f.donts),
    example_posts: f.examples.map((x) => x.trim()).filter(Boolean),
    products: f.products.filter((x) => x.name.trim()).map((x) => ({
      name: x.name.trim(),
      description: x.description.trim() || null,
      key_ingredients: x.ingredients.split(',').map((i) => i.trim()).filter(Boolean),
      approved_claims: lines(x.claims),
    })),
  }
}

export default function BrandForm() {
  const { brandId } = useParams() // undefined = a new brand
  const navigate = useNavigate()
  const existing = useBrand(brandId ?? '')
  const save = useSaveBrand()
  const [f, setF] = useState<Fields>(EMPTY)

  // Editing: fill the form once the brand has loaded.
  useEffect(() => {
    if (existing.data) setF(toFields(existing.data.profile))
  }, [existing.data])

  if (brandId && existing.isLoading) return <Loading what="Loading brand" />
  if (brandId && existing.error) return <ErrorBox error={existing.error} />

  const set = (key: keyof Fields) => (e: { target: { value: string } }) => setF({ ...f, [key]: e.target.value })
  const setProduct = (i: number, key: keyof ProductFields, value: string) =>
    setF({ ...f, products: f.products.map((p, j) => (j === i ? { ...p, [key]: value } : p)) })

  return (
    <section className="page">
      <Link to={brandId ? `/brands/${brandId}` : '/brands'} className="back-link">← {brandId ? 'Brand' : 'Brands'}</Link>
      <h1>{brandId ? `Edit ${existing.data?.profile.name ?? 'brand'}` : 'New brand'}</h1>
      <p className="muted small">
        Everything here is what the agents read before writing for this brand. Fields marked * are required.
      </p>

      <form
        className="form brand-form"
        onSubmit={(e) => {
          e.preventDefault()
          // Keep profile parts this form doesn't edit yet (e.g. the design kit), or saving would delete them.
          save.mutate({ brandId, profile: { ...existing.data?.profile, ...toProfile(f) } }, { onSuccess: (b) => navigate(`/brands/${b.id}`) })
        }}
      >
        <h2>Basics</h2>
        <label>Name *<input required value={f.name} onChange={set('name')} placeholder="e.g. Brew House" /></label>
        <div className="form-row">
          <label>Industry *
            <input required value={f.industry} onChange={set('industry')} placeholder="e.g. specialty coffee cafe" />
          </label>
          <label>Market *<input required value={f.market} onChange={set('market')} placeholder="e.g. Bengaluru" /></label>
          <label>Language<input value={f.language} onChange={set('language')} placeholder="English, Hinglish…" /></label>
        </div>
        <p className="muted small hint">
          Brands with the same industry and market share one weekly market research report, so use the same
          wording for similar brands.
        </p>
        <label>What the business does *
          <textarea required rows={3} value={f.description} onChange={set('description')} />
        </label>
        <label>Audience *
          <textarea required rows={2} value={f.audience} onChange={set('audience')} placeholder="Who the posts talk to" />
        </label>
        <label>Voice *
          <textarea required rows={2} value={f.voice} onChange={set('voice')} placeholder="e.g. warm, witty, never salesy" />
        </label>

        <h2>Rules and themes <span className="muted small">(one per line)</span></h2>
        <label>Content pillars<textarea rows={3} value={f.pillars} onChange={set('pillars')} /></label>
        <div className="form-row">
          <label>Do<textarea rows={4} value={f.dos} onChange={set('dos')} /></label>
          <label>Don't<textarea rows={4} value={f.donts} onChange={set('donts')} /></label>
        </div>

        <h2>Products</h2>
        <p className="muted small hint">
          Approved claims are the <strong>only</strong> facts the agents may state about the brand's own products, so
          write them exactly as the brand allows.
        </p>
        {f.products.map((p, i) => (
          <fieldset key={i} className="card product-fields">
            <div className="form-row">
              <label>Product name<input value={p.name} onChange={(e) => setProduct(i, 'name', e.target.value)} /></label>
              <label>Key ingredients (comma separated)
                <input value={p.ingredients} onChange={(e) => setProduct(i, 'ingredients', e.target.value)} />
              </label>
            </div>
            <label>Description
              <textarea rows={2} value={p.description} onChange={(e) => setProduct(i, 'description', e.target.value)} />
            </label>
            <label>Approved claims (one per line)
              <textarea rows={3} value={p.claims} onChange={(e) => setProduct(i, 'claims', e.target.value)} />
            </label>
            <button type="button" className="btn btn-danger"
                    onClick={() => setF({ ...f, products: f.products.filter((_, j) => j !== i) })}>
              Remove product
            </button>
          </fieldset>
        ))}
        <button type="button" className="btn"
                onClick={() => setF({ ...f, products: [...f.products, { name: '', description: '', ingredients: '', claims: '' }] })}>
          + Add product
        </button>

        <h2>Example posts <span className="muted small">(style reference; never copied)</span></h2>
        {f.examples.map((x, i) => (
          <div key={i} className="example-field">
            <textarea rows={4} value={x}
                      onChange={(e) => setF({ ...f, examples: f.examples.map((y, j) => (j === i ? e.target.value : y)) })} />
            <button type="button" className="icon-btn" title="Remove"
                    onClick={() => setF({ ...f, examples: f.examples.filter((_, j) => j !== i) })}>✕</button>
          </div>
        ))}
        <button type="button" className="btn" onClick={() => setF({ ...f, examples: [...f.examples, ''] })}>
          + Add example post
        </button>

        {save.error && <ErrorBox error={save.error} />}
        <div className="action-buttons">
          <button className="btn btn-primary" disabled={save.isPending}>
            {save.isPending ? 'Saving…' : brandId ? 'Save changes' : 'Create brand'}
          </button>
          <Link className="btn" to={brandId ? `/brands/${brandId}` : '/brands'}>Cancel</Link>
        </div>
      </form>
    </section>
  )
}
