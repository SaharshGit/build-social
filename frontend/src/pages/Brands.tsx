import { Link } from 'react-router'
import { useBrands } from '../api/hooks'
import { ErrorBox, Loading } from '../components/common'
import { timeAgo } from '../lib/format'

export default function Brands() {
  const brands = useBrands()
  return (
    <section>
      <h1>Brands</h1>
      {brands.isLoading && <Loading what="Loading brands" />}
      {brands.error && <ErrorBox error={brands.error} />}
      {brands.data?.length === 0 && (
        <p className="muted">
          No brands yet. Import the examples with <code>uv run buildsocial import-brands examples/*.yaml</code>.
        </p>
      )}
      <div className="card-list">
        {brands.data?.map((b) => (
          <Link key={b.id} to={`/brands/${b.id}`} className="card post-card">
            <div className="card-meta">
              <strong>{b.name}</strong>
              <span className="spacer" />
              <span className="badge badge-muted">{b.products} product{b.products === 1 ? '' : 's'}</span>
            </div>
            <div className="muted small">{b.industry} · {b.market} · updated {timeAgo(b.updated_at)}</div>
          </Link>
        ))}
      </div>
    </section>
  )
}
