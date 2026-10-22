import { useEffect, useState } from 'react'
import { Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { ApiError, get, post } from '../api/client'
import type { components } from '../api/schema'
import Page from './Page'
import { routes } from '../api/routes'

type User = components['schemas']['User']

const pages = [
  ['/', 'Fix my code', '✳'], ['/dashboard', 'Dashboard', '◫'], ['/studio', 'Code Studio', '⌘'],
  ['/tests', 'Test Generator', '✓'], ['/bugs', 'Bug Reports', '⚑'], ['/report', 'Run Report', '▧'],
  ['/history', 'Run History', '◷'], ['/settings', 'Settings', '⚙'], ['/account', 'Account', '◉'],
]

export default function App() {
  const [user, setUser] = useState<User|null>(null)
  const [apiState, setApiState] = useState<'checking'|'online'|'offline'>('checking')
  const [authRequired, setAuthRequired] = useState(false)
  const location = useLocation()
  useEffect(() => {
    let alive = true
    get<User|null>(routes.account.me).then(setUser).catch(() => setUser(null))
    get(routes.settings.read).catch((e:unknown)=>{if(e instanceof ApiError&&e.status===401)setAuthRequired(true)})
    const check = () => get(routes.health).then(() => { if (alive) setApiState('online') }).catch(() => { if (alive) setApiState('offline') })
    void check()
    const timer = window.setInterval(check, 5000)
    return () => { alive = false; window.clearInterval(timer) }
  }, [])
  const current = pages.find(([path]) => path === location.pathname) ?? pages[0]
  async function signOut() { await post(routes.account.logout).catch(() => undefined); setUser(null) }
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-icon">T</span><span>TestPilot <b>AI</b><small>QUALITY ENGINEERING</small></span></div>
      <div className="workspace"><span className="workspace-dot"/> Default project <span className="chevron">⌄</span></div>
      <div className="nav-label">WORKSPACE</div>
      <nav>{pages.map(([to,label,icon])=><NavLink key={to} to={to} end={to==='/'} className={({isActive})=>`nav-link ${isActive?'active':''}`}><span className="nav-icon">{icon}</span>{label}{label==='Code Studio'&&<span className="nav-badge">NEW</span>}</NavLink>)}</nav>
      <div className="sidebar-bottom"><div className="engine"><span className={`status-dot ${apiState==='online'?'green':apiState==='offline'?'red':''}`}/><span>API {apiState}</span><span className="engine-right">v1.0</span></div><div className="user-card"><div className="avatar">{user?.username?.[0]?.toUpperCase()||'G'}</div><div className="user-meta"><strong>{user?.username||'Guest workspace'}</strong><small>{user?.role||'Local session'}</small></div></div></div>
    </aside>
    <main className="main"><header className="topbar"><div className="crumb">WORKSPACE <span>/</span> <strong>{current[1].toUpperCase()}</strong></div><div className="top-actions"><span className="status-pill"><i className={`status-dot ${apiState==='online'?'green':''}`}/>{apiState==='online'?'Systems operational':'Connecting to API'}</span>{user&&<button className="icon-button" aria-label="Sign out" title="Sign out" onClick={signOut}>↗</button>}<button className="icon-button" aria-label="Notifications">♧</button><div className="avatar small">{user?.username?.[0]?.toUpperCase()||'G'}</div></div></header>
      <Routes>{pages.map(([path,title])=><Route key={path} path={path} element={authRequired&&!user&&path!=='/account'?<Navigate to="/account" replace/>:<Page title={title} user={user} onUser={setUser}/>}/>)}</Routes>
    </main>
  </div>
}
