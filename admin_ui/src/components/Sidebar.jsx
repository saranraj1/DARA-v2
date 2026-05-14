import {
  LayoutDashboard, AlertTriangle, Wrench, BookOpen,
  GitBranch, ClipboardList, Zap, Activity
} from 'lucide-react'

const ICONS = {
  dashboard: LayoutDashboard,
  errors:    AlertTriangle,
  fixes:     Wrench,
  patterns:  BookOpen,
  strategy:  Zap,
  topology:  GitBranch,
  audit:     ClipboardList,
}

export default function Sidebar({ current, setPage, pages }) {
  return (
    <nav className="sidebar">
      <div className="sidebar-logo">
        <h1>DARA</h1>
        <span>Admin Dashboard</span>
      </div>

      {Object.entries(pages).map(([key, { label }]) => {
        const Icon = ICONS[key] || Activity
        return (
          <div
            key={key}
            id={`nav-${key}`}
            className={`nav-item ${current === key ? 'active' : ''}`}
            onClick={() => setPage(key)}
          >
            <Icon size={16} />
            {label}
          </div>
        )
      })}

      <div style={{ marginTop: 'auto', padding: '16px 24px', borderTop: '1px solid var(--border)' }}>
        <a
          href="http://localhost:8000/docs"
          target="_blank"
          rel="noreferrer"
          style={{ fontSize: 12, color: 'var(--text-muted)', textDecoration: 'none' }}
        >
          API Docs ↗
        </a>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6, opacity: 0.6 }}>
          v1.0.0 · Phase 3
        </div>
      </div>
    </nav>
  )
}
