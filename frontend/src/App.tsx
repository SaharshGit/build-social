import { useState } from 'react'
import { Link, Navigate, NavLink, Route, Routes, useLocation } from 'react-router'
import { useConversations, useHealth } from './api/hooks'
import {
  ClockIcon, CollapseIcon, InboxIcon, LogoMark, PlusIcon, SlidersIcon, TagIcon,
} from './components/Icons'
import BrandDetail from './pages/BrandDetail'
import BrandForm from './pages/BrandForm'
import Brands from './pages/Brands'
import Chat from './pages/Chat'
import NewPost from './pages/NewPost'
import NotFound from './pages/NotFound'
import PipelineDetail from './pages/PipelineDetail'
import Pipelines from './pages/Pipelines'
import PostDetail from './pages/PostDetail'
import ReviewQueue from './pages/ReviewQueue'
import Scheduled from './pages/Scheduled'

const COLLAPSE_KEY = 'buildsocial.sidebar.collapsed'

export default function App() {
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(COLLAPSE_KEY) === '1' } catch { return false }
  })
  const toggle = () => {
    setCollapsed((c) => {
      try { localStorage.setItem(COLLAPSE_KEY, c ? '0' : '1') } catch { /* private mode: just don't remember */ }
      return !c
    })
  }

  return (
    <div className="layout">
      <aside className={`sidebar${collapsed ? ' collapsed' : ''}`}>
        <div className="sidebar-top">
          <Link to="/" className="logo-mark" title="BuildSocial"><LogoMark /></Link>
          {!collapsed && <span className="logo-word">BuildSocial</span>}
          <button className="collapse-btn" onClick={toggle} title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
            <CollapseIcon />
          </button>
        </div>
        <NewChatLink />
        <NavItem to="/pipelines" icon={<SlidersIcon />} label="Pipelines" />
        <NavItem to="/scheduled" icon={<ClockIcon />} label="Scheduled" />
        <NavItem to="/reviews" icon={<InboxIcon />} label="Reviews" />
        <NavItem to="/brands" icon={<TagIcon />} label="Brands" />
        <Recents />
        <ApiStatus />
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<Chat />} />
          <Route path="/chat/:conversationId" element={<Chat />} />
          <Route path="/pipelines" element={<Pipelines />} />
          <Route path="/pipelines/:runId" element={<PipelineDetail />} />
          <Route path="/runs/:runId" element={<PipelineDetail />} />
          <Route path="/scheduled" element={<Scheduled />} />
          <Route path="/reviews" element={<ReviewQueue />} />
          <Route path="/queue" element={<Navigate to="/reviews" replace />} />
          <Route path="/posts/:postId" element={<PostDetail />} />
          <Route path="/new" element={<NewPost />} />
          <Route path="/brands" element={<Brands />} />
          <Route path="/brands/new" element={<BrandForm />} />
          <Route path="/brands/:brandId" element={<BrandDetail />} />
          <Route path="/brands/:brandId/edit" element={<BrandForm />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  )
}

function NavItem({ to, icon, label }: { to: string; icon: React.ReactNode; label: string }) {
  return (
    <NavLink to={to} className="nav-item" title={label}>
      {icon}
      <span className="nav-label">{label}</span>
    </NavLink>
  )
}

/** "New chat" is highlighted only on the empty new-chat page, not inside an existing chat. */
function NewChatLink() {
  const { pathname } = useLocation()
  return (
    <Link to="/" className={`nav-item${pathname === '/' ? ' active' : ''}`} title="New chat">
      <PlusIcon />
      <span className="nav-label">New chat</span>
    </Link>
  )
}

/** Recent chats, newest activity first. */
function Recents() {
  const conversations = useConversations()
  const list = conversations.data ?? []
  if (list.length === 0) return <div className="recents" />
  return (
    <div className="recents">
      <div className="recents-title">Recents</div>
      {list.map((c) => (
        <NavLink key={c.id} to={`/chat/${c.id}`} title={c.title}>{c.title}</NavLink>
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
      ? 'Connected'
      : state === 'database-down'
        ? 'Database unreachable'
        : 'API unreachable'
  const dot = health.isLoading ? 'dot-wait' : state === 'ok' ? 'dot-ok' : 'dot-bad'
  return (
    <div className="api-status" title={`${label} (checked every 15 seconds)`}>
      <span className={`dot ${dot}`} />
      <span>{label}</span>
    </div>
  )
}
