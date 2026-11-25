import { Link } from 'react-router'
import { useBrands } from '../api/hooks'
import { ErrorBox, Loading } from '../components/common'
import { timeAgo } from '../lib/format'

export default function Brands() {
  const brands = useBrands()
  return (
    <section className="page">
      <div className="detail-header">
        <h1>Brands</h1>
        <span className="spacer" />
        <Link className="btn btn-primary" to="/brands/new">+ New brand</Link>
      </div>
      <p className="subtitle">What the agents know about each brand: voice, rules and approved product claims.</p>

      {brands.isLoading && <Loading what="Loading brands" />}
      {brands.error && <ErrorBox error={brands.error} />}
      <div className="brand-grid">
        {brands.data?.map((b) => (
          <div key={b.id} className="card brand-card">
            <Link to={`/brands/${b.id}`} style={{ textDecoration: 'none' }}><h3>{b.name}</h3></Link>
            <div className="muted small">{b.industry} · {b.market}</div>
            <div className="small" style={{ margin: '0.4rem 0 0.9rem' }}>
              {b.products} product{b.products === 1 ? '' : 's'} · updated {timeAgo(b.updated_at)}
            </div>
            <div className="action-buttons">
              <Link className="btn btn-primary" to={`/new?brand=${b.id}`}>New post</Link>
              <Link className="btn" to={`/brands/${b.id}/edit`}>Edit</Link>
            </div>
          </div>
        ))}
        <Link to="/brands/new" className="card add-card">
          <h3 style={{ margin: '0 0 0.3rem' }}>+ Add a brand</h3>
          <div className="muted small">
            Fill in the form. Later you'll be able to paste the brand's website and let BuildSocial draft it for you to check.
          </div>
        </Link>
      </div>
    </section>
  )
}
