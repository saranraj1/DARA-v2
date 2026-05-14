import { useState, useEffect, useCallback } from 'react'
import Sidebar from './components/Sidebar.jsx'
import Topbar from './components/Topbar.jsx'
import Dashboard from './pages/Dashboard.jsx'
import ErrorsPage from './pages/ErrorsPage.jsx'
import FixesPage from './pages/FixesPage.jsx'
import PatternsPage from './pages/PatternsPage.jsx'
import AuditPage from './pages/AuditPage.jsx'
import TopologyPage from './pages/TopologyPage.jsx'
import StrategyPage from './pages/StrategyPage.jsx'

const PAGES = {
  dashboard:  { label: 'Dashboard',   component: Dashboard },
  errors:     { label: 'Errors',       component: ErrorsPage },
  fixes:      { label: 'Fixes',        component: FixesPage },
  patterns:   { label: 'Patterns',     component: PatternsPage },
  strategy:   { label: 'Strategy',     component: StrategyPage },
  topology:   { label: 'Topology',     component: TopologyPage },
  audit:      { label: 'Audit Log',    component: AuditPage },
}

export default function App() {
  const [page, setPage] = useState('dashboard')
  const [lastRefresh, setLastRefresh] = useState(new Date())

  const refresh = useCallback(() => setLastRefresh(new Date()), [])

  // Auto-refresh every 30s
  useEffect(() => {
    const id = setInterval(refresh, 30_000)
    return () => clearInterval(id)
  }, [refresh])

  const PageComponent = PAGES[page]?.component || Dashboard

  return (
    <div style={{ display: 'flex' }}>
      <Sidebar current={page} setPage={setPage} pages={PAGES} />
      <div className="main">
        <Topbar title={PAGES[page]?.label} lastRefresh={lastRefresh} onRefresh={refresh} />
        <div className="page fade-in">
          <PageComponent key={page + lastRefresh.getTime()} />
        </div>
      </div>
    </div>
  )
}
