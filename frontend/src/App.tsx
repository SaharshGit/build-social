import { Link, NavLink, Route, Routes } from 'react-router'
import { useConversations, useHealth } from './api/hooks'
import { timeAgo } from './lib/format'
import BrandDetail from './pages/BrandDetail'
import BrandForm from './pages/BrandForm'
import Chat from './pages/Chat'
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
        <Link to="/" className="btn btn-primary new-chat">+ New chat</Link>
        <nav>
          <NavLink to="/queue">Review queue</NavLink>
          <NavLink to="/new">New post (form)</NavLink>
          <NavLink to="/brands">Brands</NavLink>
        </nav>
        <ChatList />
        <ApiStatus />
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<Chat />} />
          <Route path="/chat/:conversationId" element={<Chat />} />
          <Route path="/queue" element={<ReviewQueue />} />
          <Route path="/posts/:postId" element={<PostDetail />} />
          <Route path="/new" element={<NewPost />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="/brands" element={<Brands />} />
          <Route path="/brands/:brandId" element={<BrandDetail />} />
          <Route path="/brands/new" element={<BrandForm />} />
          <Route path="/brands/:brandId/edit" element={<BrandForm />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  )
}

/** Recent chats, newest activity first. */
function ChatList() {
  const conversations = useConversations()
  const list = conversations.data ?? []
  if (list.length === 0) return null
  return (
    <div className="chat-list">
      <div className="chat-list-title">Chats</div>
      {list.map((c) => (
        <NavLink key={c.id} to={`/chat/${c.id}`} title={c.title}>
          <span className="chat-list-name">{c.title}</span>
          <span className="muted small">{timeAgo(c.updated_at)}</span>
        </NavLink>
      ))}
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
