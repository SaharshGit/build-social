import { NavLink, Route, Routes } from 'react-router'
import { useHealth } from './api/hooks'
import Brands from './pages/Brands'
import NewPost from './pages/NewPost'
import NotFound from './pages/NotFound'
import PostDetail from './pages/PostDetail'
import ReviewQueue from './pages/ReviewQueue'
import RunPage from './pages/RunPage'

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
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="/brands" element={<Brands />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  )
}

/** A dot showing whether the API and its database can be reached. */
function ApiStatus() {
  const health = useHealth()
  const state = health.data
  const label = health.isLoading
    ? 'Connecting…'
    : state === 'ok'
      ? 'API connected'
      : state === 'database-down'
        ? 'Database unreachable'
        : 'API unreachable'
  const dot = health.isLoading ? 'dot-wait' : state === 'ok' ? 'dot-ok' : 'dot-bad'
  return (
    <div className="api-status" title="GET /health, checked every 15 seconds">
      <span className={`dot ${dot}`} />
      {label}
    </div>
  )
}
