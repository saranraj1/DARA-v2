import { useState, useEffect } from 'react'
import { api } from '../api.js'
import { BookOpen, TrendingUp, TrendingDown } from 'lucide-react'

export default function PatternsPage() {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)
  const [reindexing, setRI]   = useState(false)
  const [reindexMsg, setRIM]  = useState(null)

  useEffect(() => {
    let alive = true
    api.patterns()
      .then(d => { if (alive) { setData(d); setLoading(false) } })
      .catch(e => { if (alive) { setError(e.message); setLoading(false) } })
    return () => { alive = false }
  }, [])

  async function triggerReindex() {
    setRI(true); setRIM(null)
    try {
      const r = await api.reindex()
      setRIM(`✅ ${r.message || 'Re-index queued'}`)
    } catch (e) {
      setRIM(`❌ ${e.message}`)
    } finally {
      setRI(false)
    }
  }

  const patterns = data?.items || []
  const active   = patterns.filter(p => p.status === 'active').length
  const highConf = patterns.filter(p => (p.success_rate || 0) >= 0.85).length

  return (
    <>
      <div className="page-header">
        <h2>Pattern Library</h2>
        <p>Active fix templates and pattern statistics</p>
      </div>

      {/* Summary stats */}
      <div className="stats-grid" style={{ marginBottom: 24 }}>
        {[
          { id:'stat-total-patterns',  label:'Total Patterns', value: patterns.length, color:'var(--accent)' },
          { id:'stat-active-patterns', label:'Active',          value: active,          color:'var(--green)'  },
          { id:'stat-high-conf',       label:'≥85% Success Rate', value: highConf,       color:'var(--purple)' },
        ].map(({ id, label, value, color }) => (
          <div key={id} id={id} className="stat-card">
            <div className="stat-label">{label}</div>
            <div className="stat-value" style={{
              background: `linear-gradient(135deg, var(--text-primary), ${color})`,
              WebkitBackgroundClip:'text', WebkitTextFillColor:'transparent'
            }}>{loading ? '…' : value}</div>
          </div>
        ))}

        <div className="stat-card" style={{ display:'flex', flexDirection:'column', justifyContent:'space-between' }}>
          <div className="stat-label">Qdrant Index</div>
          <button
            id="btn-reindex"
            className={`btn ${reindexing ? 'btn-ghost' : 'btn-primary'}`}
            style={{ marginTop: 8 }}
            onClick={triggerReindex}
            disabled={reindexing}
          >
            {reindexing ? '⟳ Indexing…' : 'Re-index Now'}
          </button>
          {reindexMsg && <div style={{ fontSize: 11, marginTop: 6, color: 'var(--text-muted)' }}>{reindexMsg}</div>}
        </div>
      </div>

      {/* Patterns table */}
      <div className="card" style={{ padding: 0 }}>
        {loading ? (
          <div style={{ padding: 24 }}>
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="skeleton" style={{ height: 36, marginBottom: 8 }} />
            ))}
          </div>
        ) : error ? (
          <div className="empty-state"><p style={{ color: 'var(--red)' }}>{error}</p></div>
        ) : patterns.length === 0 ? (
          <div className="empty-state">
            <BookOpen size={32} />
            <p>No patterns yet — the memory graph builds patterns as fixes are accepted</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Error Class</th>
                  <th>Template Preview</th>
                  <th>Success Rate</th>
                  <th>Times Used</th>
                  <th>Status</th>
                  <th>Last Used</th>
                </tr>
              </thead>
              <tbody>
                {patterns.map((p, i) => {
                  const rate = p.success_rate ?? 0
                  const high = rate >= 0.85
                  return (
                    <tr key={p.id || i} id={`pattern-row-${i}`}>
                      <td>
                        <span className="mono" style={{ color: 'var(--text-code)', fontSize: 12 }}>
                          {p.error_class}
                        </span>
                      </td>
                      <td style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        <span style={{ fontSize: 11 }}>
                          {p.fix_template?.slice(0, 60) || p.template_id || '—'}
                          {p.fix_template?.length > 60 ? '…' : ''}
                        </span>
                      </td>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          {high
                            ? <TrendingUp size={12} color="var(--green)" />
                            : <TrendingDown size={12} color="var(--yellow)" />
                          }
                          <div style={{ flex: 1, background: 'var(--bg-surface)', borderRadius: 4, height: 6, minWidth: 60 }}>
                            <div style={{
                              width: `${rate * 100}%`, height: '100%', borderRadius: 4,
                              background: high ? 'var(--green)' : rate > 0.6 ? 'var(--yellow)' : 'var(--red)',
                            }} />
                          </div>
                          <span style={{ fontSize: 11, minWidth: 36 }}>{(rate * 100).toFixed(0)}%</span>
                        </div>
                      </td>
                      <td style={{ textAlign: 'center' }}>{p.use_count ?? p.acceptance_count ?? '—'}</td>
                      <td>
                        <span className={`badge badge-${p.status === 'active' ? 'green' : 'yellow'}`}>
                          {p.status || 'active'}
                        </span>
                      </td>
                      <td style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        {p.last_used ? new Date(p.last_used).toLocaleDateString() : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
