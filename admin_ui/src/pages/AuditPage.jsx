import { useState, useEffect } from 'react'
import { api } from '../api.js'
import { ClipboardList, Filter } from 'lucide-react'

const ACTION_COLOR = {
  fix_approved:   'green',
  fix_rejected:   'red',
  fix_generated:  'blue',
  error_ingested: 'yellow',
  reindex:        'purple',
  strategy_promoted: 'green',
  strategy_retired:  'red',
}

export default function AuditPage() {
  const [data, setData]         = useState(null)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)
  const [action, setAction]     = useState('')
  const [limit, setLimit]       = useState(50)

  useEffect(() => {
    let alive = true
    setLoading(true)
    const params = { limit }
    if (action) params.action = action
    api.audit(params)
      .then(d => { if (alive) { setData(d); setLoading(false) } })
      .catch(e => { if (alive) { setError(e.message); setLoading(false) } })
    return () => { alive = false }
  }, [action, limit])

  const rows = data?.items || []
  const knownActions = [
    '', 'fix_approved', 'fix_rejected', 'fix_generated',
    'error_ingested', 'reindex', 'strategy_promoted', 'strategy_retired',
  ]

  return (
    <>
      <div className="page-header">
        <h2>Audit Log</h2>
        <p>{data?.count ?? '…'} recent entries · immutable record of all system actions</p>
      </div>

      {/* Filters */}
      <div className="card" style={{ marginBottom: 20, padding: '12px 20px', display: 'flex', gap: 12, alignItems: 'center' }}>
        <Filter size={13} color="var(--text-muted)" />
        <select
          id="filter-action-type"
          value={action}
          onChange={e => setAction(e.target.value)}
          style={{
            background: 'var(--bg-card)', color: 'var(--text-secondary)',
            border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
            padding: '6px 10px', fontSize: 12, cursor: 'pointer',
          }}
        >
          {knownActions.map(a => (
            <option key={a} value={a}>{a || 'All actions'}</option>
          ))}
        </select>
        <select
          id="filter-limit"
          value={limit}
          onChange={e => setLimit(Number(e.target.value))}
          style={{
            background: 'var(--bg-card)', color: 'var(--text-secondary)',
            border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
            padding: '6px 10px', fontSize: 12, cursor: 'pointer',
          }}
        >
          {[25, 50, 100, 200].map(n => <option key={n} value={n}>Last {n}</option>)}
        </select>
      </div>

      <div className="card" style={{ padding: 0 }}>
        {loading ? (
          <div style={{ padding: 24 }}>
            {Array.from({ length: 8 }, (_, i) => (
              <div key={i} className="skeleton" style={{ height: 32, marginBottom: 7 }} />
            ))}
          </div>
        ) : error ? (
          <div className="empty-state"><p style={{ color: 'var(--red)' }}>{error}</p></div>
        ) : rows.length === 0 ? (
          <div className="empty-state">
            <ClipboardList size={32} />
            <p>Audit log is empty — actions will appear here as the system operates</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Action</th>
                  <th>Actor</th>
                  <th>Resource Type</th>
                  <th>Resource ID</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(row => (
                  <tr key={row.id} id={`audit-row-${row.id}`}>
                    <td style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                      {new Date(row.created_at).toLocaleString()}
                    </td>
                    <td>
                      <span className={`badge badge-${ACTION_COLOR[row.action] || 'blue'}`}>
                        {row.action}
                      </span>
                    </td>
                    <td style={{ fontSize: 12 }}>{row.actor || '—'}</td>
                    <td style={{ fontSize: 12 }}>{row.resource_type || '—'}</td>
                    <td>
                      <span className="mono" style={{ fontSize: 11, color: 'var(--text-code)' }}>
                        {row.resource_id?.slice(0, 16) || '—'}
                        {row.resource_id?.length > 16 ? '…' : ''}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
