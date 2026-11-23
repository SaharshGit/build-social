import { NavLink, Route, Routes } from 'react-router'
import { useHealth } from './api/hooks'
import Brands from './pages/Brands'
import NewPost from './pages/NewPost'
import NotFound from './pages/NotFound'
import PostDetail from './pages/PostDetail'
import ReviewQueue from './pages/ReviewQueue'

export default function App() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="logo">BuildSocial</div>
        <nav>
          <NavLink to="/" end>Review queue</NavLink>
          <NavLink to="/new">New post</NavLink>
          <NavLink to="/brands">Brands</NavLink>
        </nav>
        <ApiStatus />
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<ReviewQueue />} />
          <Route path="/posts/:postId" element={<PostDetail />} />
          <Route path="/new" element={<NewPost />} />
          <Route path="/brands" element={<Brands />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  )
}

/** A dot showing whether the API (and its database) can be reached. */
function ApiStatus() {
  const health = useHealth()
  const ok = health.data?.status === 'ok'
  const label = health.isLoading ? 'Connecting…' : ok ? 'API connected' : 'API unreachable'
  return (
    <div className="api-status" title="GET /health, checked every 15 seconds">
      <span className={`dot ${health.isLoading ? 'dot-wait' : ok ? 'dot-ok' : 'dot-bad'}`} />
      {label}
    </div>
  )
}
