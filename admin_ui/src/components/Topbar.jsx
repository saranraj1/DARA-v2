import { RefreshCw } from 'lucide-react'

export default function Topbar({ title, lastRefresh, onRefresh }) {
  const timeStr = lastRefresh.toLocaleTimeString('en-US', {
    hour: '2-digit', minute: '2-digit', second: '2-digit'
  })

  return (
    <div className="topbar">
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div className="live-dot" />
        <span className="topbar-title">{title}</span>
      </div>
      <div className="topbar-actions">
        <span className="refresh-indicator">Updated {timeStr}</span>
        <button
          id="btn-refresh"
          className="btn btn-ghost"
          onClick={onRefresh}
          style={{ padding: '6px 12px' }}
        >
          <RefreshCw size={13} />
          Refresh
        </button>
      </div>
    </div>
  )
}
