import { Link, useParams, useSearchParams } from 'react-router'
import type { Brand } from '../api/client'
import { useAssets, useBrand } from '../api/hooks'
import LookAndFeel from '../components/brand/LookAndFeel'
import { ErrorBox, Loading } from '../components/common'

const TABS = [['overview', 'Overview'], ['look', 'Look & feel'], ['photos', 'Photos']] as const

/** A brand: what the agents read (Overview), how its images look (Look & feel) and its photos. */
export default function BrandDetail() {
  const { brandId = '' } = useParams()
  const brand = useBrand(brandId)
  const assets = useAssets(brandId)
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') ?? 'overview'

  if (brand.isLoading) return <Loading what="Loading brand" />
  if (brand.error) return <ErrorBox error={brand.error} />
  if (!brand.data) return null
  const p = brand.data.profile

  return (
    <section className="page">
      <Link to="/brands" className="back-link">← Brands</Link>
      <div className="detail-header">
        <h1>{p.name}</h1>
        <span className="muted">{p.industry} · {p.market}</span>
      </div>
      <div className="action-buttons" style={{ margin: '0.75rem 0 1rem' }}>
        <Link className="btn btn-primary" to={`/new?brand=${brand.data.id}`}>New post for {p.name}</Link>
        <Link className="btn" to={`/reviews?status=&brand=${brand.data.id}`}>All {p.name} posts</Link>
        <Link className="btn" to={`/brands/${brand.data.id}/edit`}>Edit brand</Link>
      </div>

      <div className="chips tabs" role="tablist">
        {TABS.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} className={`chip ${tab === key ? 'active' : ''}`}
            onClick={() => setParams(key === 'overview' ? {} : { tab: key }, { replace: true })}>
            {label}{key === 'photos' && assets.data ? ` · ${assets.data.length}` : ''}
          </button>
        ))}
      </div>

      {tab === 'look' && <LookAndFeel key={brand.data.updated_at} brand={brand.data} />}
      {tab === 'photos' && <p className="muted">The photo library and style board come in the next step.</p>}
      {tab === 'overview' && <Overview brand={brand.data} />}
    </section>
  )
}

function Overview({ brand }: { brand: Brand }) {
  const p = brand.profile
  return (
    <>

      <div className="card small">
        <p><strong>What they do:</strong> {p.description}</p>
        <p><strong>Audience:</strong> {p.audience}</p>
        <p><strong>Voice:</strong> {p.voice}</p>
        <p><strong>Language:</strong> {p.language}</p>
      </div>

      <div className="detail-grid">
        <div>
          <ListBlock title="Do" items={p.dos ?? []} />
          <ListBlock title="Don't" items={p.donts ?? []} className="donts" />
        </div>
        <div>
          <ListBlock title="Content pillars" items={p.content_pillars ?? []} />
        </div>
      </div>

      <h2>Products <span className="muted small">(the only facts the agents may state about the brand's own products)</span></h2>
      {(p.products ?? []).length === 0 ? (
        <p className="muted small">None listed: posts can't make claims about specific products.</p>
      ) : (
        <div className="card-list">
          {(p.products ?? []).map((prod) => (
            <div key={prod.name} className="card small">
              <strong>{prod.name}</strong>
              {prod.description && <p>{prod.description}</p>}
              {(prod.key_ingredients ?? []).length > 0 && (
                <p><strong>Key ingredients:</strong> {(prod.key_ingredients ?? []).join(', ')}</p>
              )}
              {(prod.approved_claims ?? []).length > 0 && (
                <p><strong>Approved claims:</strong> {(prod.approved_claims ?? []).map((c) => `“${c}”`).join(' · ')}</p>
              )}
            </div>
          ))}
        </div>
      )}

      {(p.example_posts ?? []).length > 0 && (
        <>
          <h2>Example posts <span className="muted small">(style reference only)</span></h2>
          {(p.example_posts ?? []).map((ex, i) => (
            <blockquote key={i} className="card small example">{ex}</blockquote>
          ))}
        </>
      )}
    </>
  )
}

function ListBlock({ title, items, className = '' }: { title: string; items: string[]; className?: string }) {
  if (items.length === 0) return null
  return (
    <>
      <h2>{title}</h2>
      <ul className={`small ${className}`}>{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
    </>
  )
}
