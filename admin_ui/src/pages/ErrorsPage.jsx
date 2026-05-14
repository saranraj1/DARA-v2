import { useState, useEffect } from 'react'
import { api } from '../api.js'
import { Search, Filter } from 'lucide-react'

const STATUS_COLOR = {
  fixed: 'green', escalated: 'red', pending: 'yellow',
  analyzing: 'blue', failed: 'red', template_hit: 'purple',
}
const SEV_COLOR = { P0: 'red', P1: 'red', P2: 'yellow', P3: 'blue', P4: 'blue' }

export default function ErrorsPage() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [status, setStatus]   = useState('')
  const [service, setService] = useState('')
  const [severity, setSeverity] = useState('')
  const [selected, setSelected] = useState(null)
  const [pipeline, setPipeline] = useState(null)

  useEffect(() => {
    let alive = true
    async function load() {
      setLoading(true)
      try {
        const params = {}
        if (status)   params.status = status
        if (service)  params.service = service
        if (severity) params.severity = severity
        const d = await api.errors(params)
        if (alive) { setData(d); setLoading(false) }
      } catch (e) {
        if (alive) { setError(e.message); setLoading(false) }
      }
    }
    load()
    return () => { alive = false }
  }, [status, service, severity])

  async function loadPipeline(id) {
    setSelected(id); setPipeline(null)
    try {
      const p = await api.pipeline(id)
      setPipeline(p)
    } catch {}
  }

  const rows = data?.items || []

  return (
    <>
      <div className="page-header">
        <h2>Error Queue</h2>
        <p>{data?.total ?? '…'} total errors · {data?.items?.length ?? 0} shown</p>
      </div>

      {/* Filters */}
      <div className="card" style={{ marginBottom: 20, display: 'flex', gap: 12, flexWrap: 'wrap', padding: '14px 20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Filter size={13} color="var(--text-muted)" />
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Filters:</span>
        </div>
        {[
          { label: 'Status',   val: status,   set: setStatus,   opts: ['','pending','analyzing','fixed','escalated','failed'] },
          { label: 'Severity', val: severity, set: setSeverity, opts: ['','P0','P1','P2','P3','P4'] },
        ].map(({ label, val, set, opts }) => (
          <select
            key={label}
            id={`filter-${label.toLowerCase()}`}
            value={val}
            onChange={e => set(e.target.value)}
            style={{
              background: 'var(--bg-card)', color: 'var(--text-secondary)',
              border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
              padding: '6px 10px', fontSize: 12, outline: 'none', cursor: 'pointer',
            }}
          >
            {opts.map(o => <option key={o} value={o}>{o || `All ${label}s`}</option>)}
          </select>
        ))}
        <input
          id="filter-service"
          placeholder="Service name…"
          value={service}
          onChange={e => setService(e.target.value)}
          style={{
            background: 'var(--bg-card)', color: 'var(--text-primary)',
            border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
            padding: '6px 12px', fontSize: 12, outline: 'none', flex: 1, minWidth: 120,
          }}
        />
      </div>

      <div className={selected ? 'two-col' : ''} style={{ alignItems: 'start' }}>
        {/* Table */}
        <div className="card" style={{ padding: 0 }}>
          {loading ? (
            <div style={{ padding: 24 }}>
              {Array.from({ length: 6 }, (_, i) => (
                <div key={i} className="skeleton" style={{ height: 32, marginBottom: 8 }} />
              ))}
            </div>
          ) : error ? (
            <div className="empty-state"><p style={{ color: 'var(--red)' }}>{error}</p></div>
          ) : rows.length === 0 ? (
            <div className="empty-state"><p>No errors match the current filters</p></div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Class</th><th>Service</th><th>Severity</th>
                    <th>Status</th><th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(row => (
                    <tr
                      key={row.id}
                      id={`error-row-${row.id}`}
                      onClick={() => loadPipeline(row.id)}
                      style={{ cursor: 'pointer', background: selected === row.id ? 'var(--accent-dim)' : '' }}
                    >
                      <td><span className="mono" style={{ color: 'var(--text-code)', fontSize: 12 }}>{row.error_class}</span></td>
                      <td>{row.service || '—'}</td>
                      <td>
                        <span className={`badge badge-${SEV_COLOR[row.severity] || 'blue'}`}>{row.severity}</span>
                      </td>
                      <td>
                        <span className={`badge badge-${STATUS_COLOR[row.status] || 'blue'}`}>{row.status}</span>
                      </td>
                      <td style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        {new Date(row.created_at).toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Pipeline detail panel */}
        {selected && (
          <div className="card fade-in" style={{ fontSize: 12 }}>
            <div style={{ fontWeight: 600, marginBottom: 12, display: 'flex', justifyContent: 'space-between' }}>
              Pipeline State
              <button
                className="btn btn-ghost"
                style={{ padding: '2px 8px', fontSize: 11 }}
                onClick={() => setSelected(null)}
              >✕</button>
            </div>
            {!pipeline
              ? <div className="skeleton" style={{ height: 120 }} />
              : <>
                  <div style={{ marginBottom: 10 }}>
                    <span className="mono" style={{ color: 'var(--text-code)', fontSize: 11, wordBreak: 'break-all' }}>
                      {pipeline.error?.id}
                    </span>
                  </div>
                  {Object.entries(pipeline.error || {}).map(([k, v]) => (
                    <div key={k} style={{ display: 'flex', gap: 8, marginBottom: 5 }}>
                      <span style={{ color: 'var(--text-muted)', minWidth: 90 }}>{k}:</span>
                      <span style={{ color: 'var(--text-primary)', wordBreak: 'break-all' }}>
                        {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                      </span>
                    </div>
                  ))}
                  {pipeline.redis_state && Object.keys(pipeline.redis_state).length > 0 && (
                    <div style={{ marginTop: 12 }}>
                      <div style={{ color: 'var(--text-muted)', marginBottom: 6, fontWeight: 600 }}>Redis Pipeline State</div>
                      <pre style={{
                        background: 'var(--bg-surface)', borderRadius: 6, padding: 10,
                        overflow: 'auto', fontSize: 11, color: 'var(--text-code)', maxHeight: 200,
                      }}>
                        {JSON.stringify(pipeline.redis_state, null, 2)}
                      </pre>
                    </div>
                  )}
                </>
            }
          </div>
        )}
      </div>
    </>
  )
}
