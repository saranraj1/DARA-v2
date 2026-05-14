import { useState, useEffect } from 'react'
import { api } from '../api.js'
import { GitBranch, Circle } from 'lucide-react'

function ServiceNode({ name, calls = 0, errors = 0, onClick, selected }) {
  const hasErrors = errors > 0
  return (
    <div
      id={`node-${name}`}
      onClick={() => onClick(name)}
      style={{
        display: 'inline-flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 6,
        padding: '12px 18px',
        borderRadius: 'var(--radius-md)',
        border: `2px solid ${selected ? 'var(--accent)' : hasErrors ? 'var(--red-dim)' : 'var(--border)'}`,
        background: selected ? 'var(--accent-dim)' : hasErrors ? 'var(--red-dim)' : 'var(--bg-card)',
        cursor: 'pointer',
        transition: 'all 0.15s',
        minWidth: 110,
        boxShadow: selected ? 'var(--shadow-glow)' : 'none',
      }}
    >
      <Circle size={10} fill={hasErrors ? 'var(--red)' : 'var(--green)'} color="transparent" />
      <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{name}</span>
      <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
        {calls} calls · {errors} err
      </div>
    </div>
  )
}

export default function TopologyPage() {
  const [topo, setTopo]         = useState(null)
  const [traces, setTraces]     = useState(null)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    let alive = true
    async function load() {
      try {
        const [t, tr] = await Promise.all([api.topology(), api.traces({ limit: 20 })])
        if (alive) { setTopo(t); setTraces(tr); setLoading(false) }
      } catch (e) {
        if (alive) { setError(e.message); setLoading(false) }
      }
    }
    load()
    return () => { alive = false }
  }, [])

  const edges = topo?.edges || []
  const nodes = topo?.nodes || []

  // Build service stats from edges
  const stats = {}
  edges.forEach(e => {
    stats[e.caller]  = stats[e.caller]  || { calls: 0, errors: 0 }
    stats[e.callee]  = stats[e.callee]  || { calls: 0, errors: 0 }
    stats[e.caller].calls  += e.call_count || 0
    stats[e.callee].calls  += e.call_count || 0
    stats[e.caller].errors += e.error_count || 0
  })

  const serviceNames = [...new Set([...nodes.map(n => n.name || n), ...edges.flatMap(e => [e.caller, e.callee])])]

  // Selected service: its edges
  const selectedEdges = selected
    ? edges.filter(e => e.caller === selected || e.callee === selected)
    : []

  const recentTraces = traces?.items || []

  return (
    <>
      <div className="page-header">
        <h2>Service Topology</h2>
        <p>{serviceNames.length} services · {edges.length} call edges discovered</p>
      </div>

      {error && (
        <div className="card" style={{ borderColor: 'var(--red)', marginBottom: 16 }}>
          <span style={{ color: 'var(--red)' }}>⚠ {error}</span>
        </div>
      )}

      {/* Topology canvas */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 16 }}>
          <GitBranch size={14} style={{ marginRight: 6 }} />
          Live Service Graph
          <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 10 }}>Click a service to see its connections</span>
        </div>
        {loading ? (
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="skeleton" style={{ width: 110, height: 80, borderRadius: 'var(--radius-md)' }} />
            ))}
          </div>
        ) : serviceNames.length === 0 ? (
          <div className="empty-state">
            <GitBranch size={32} />
            <p>No topology data yet — traces will build the service graph automatically</p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
            {serviceNames.map(name => (
              <ServiceNode
                key={name}
                name={name}
                calls={stats[name]?.calls || 0}
                errors={stats[name]?.errors || 0}
                selected={selected === name}
                onClick={n => setSelected(s => s === n ? null : n)}
              />
            ))}
          </div>
        )}

        {/* Edge table for selected service */}
        {selected && selectedEdges.length > 0 && (
          <div style={{ marginTop: 20 }} className="fade-in">
            <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 10, color: 'var(--accent)' }}>
              Connections for <span className="mono">{selected}</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Caller</th><th>→</th><th>Callee</th>
                    <th>Calls</th><th>Errors</th><th>Latency p99</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedEdges.map((e, i) => (
                    <tr key={i} id={`edge-row-${i}`}>
                      <td><span className="mono" style={{ fontSize: 12 }}>{e.caller}</span></td>
                      <td style={{ color: 'var(--text-muted)' }}>→</td>
                      <td><span className="mono" style={{ fontSize: 12 }}>{e.callee}</span></td>
                      <td>{e.call_count ?? '—'}</td>
                      <td style={{ color: (e.error_count || 0) > 0 ? 'var(--red)' : 'var(--green)' }}>
                        {e.error_count ?? 0}
                      </td>
                      <td style={{ fontSize: 12 }}>
                        {e.latency_p99_ms ? `${e.latency_p99_ms}ms` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* Recent traces */}
      <div className="card" style={{ padding: 0 }}>
        <div style={{ padding: '16px 20px', fontWeight: 600, fontSize: 13, borderBottom: '1px solid var(--border)' }}>
          Recent Traces
        </div>
        {loading ? (
          <div style={{ padding: 20 }}>
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} className="skeleton" style={{ height: 30, marginBottom: 7 }} />
            ))}
          </div>
        ) : recentTraces.length === 0 ? (
          <div className="empty-state"><p>No traces ingested yet</p></div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Trace ID</th><th>Root Service</th><th>Spans</th>
                  <th>Duration</th><th>Status</th><th>Started</th>
                </tr>
              </thead>
              <tbody>
                {recentTraces.map(tr => (
                  <tr key={tr.trace_id} id={`trace-row-${tr.trace_id}`}>
                    <td>
                      <span className="mono" style={{ fontSize: 11, color: 'var(--text-code)' }}>
                        {tr.trace_id?.slice(0, 16)}…
                      </span>
                    </td>
                    <td style={{ fontSize: 12 }}>{tr.root_service || '—'}</td>
                    <td style={{ textAlign: 'center' }}>{tr.span_count ?? '—'}</td>
                    <td style={{ fontSize: 12 }}>
                      {tr.duration_ms ? `${tr.duration_ms}ms` : '—'}
                    </td>
                    <td>
                      <span className={`badge badge-${tr.has_error ? 'red' : 'green'}`}>
                        {tr.has_error ? 'error' : 'ok'}
                      </span>
                    </td>
                    <td style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      {tr.started_at ? new Date(tr.started_at).toLocaleTimeString() : '—'}
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
