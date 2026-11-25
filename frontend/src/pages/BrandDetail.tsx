import { Link, useParams } from 'react-router'
import { useBrand } from '../api/hooks'
import { ErrorBox, Loading } from '../components/common'

/** A brand's profile: everything the agents read before writing for it. */
export default function BrandDetail() {
  const { brandId = '' } = useParams()
  const brand = useBrand(brandId)

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
    </section>
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
