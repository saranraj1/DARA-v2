import { useState, useEffect } from 'react'
import { api } from '../api.js'
import { CheckCircle, XCircle, ExternalLink } from 'lucide-react'

const OUTCOME_COLOR = {
  accepted: 'green', auto_accepted: 'green',
  rejected: 'red',  pending: 'yellow',
}

export default function FixesPage() {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)
  const [outcome, setOutcome] = useState('')
  const [selected,setSelected]= useState(null)
  const [validation,setVal]   = useState(null)
  const [action, setAction]   = useState(null) // { type, fixId }
  const [notes, setNotes]     = useState('')

  useEffect(() => {
    let alive = true
    async function load() {
      setLoading(true)
      try {
        const params = outcome ? { outcome } : {}
        const d = await api.fixes(params)
        if (alive) { setData(d); setLoading(false) }
      } catch (e) {
        if (alive) { setError(e.message); setLoading(false) }
      }
    }
    load()
  }, [outcome])

  async function loadValidation(fixId) {
    setSelected(fixId); setVal(null)
    try {
      const v = await api.fixValidation(fixId)
      setVal(v)
    } catch {}
  }

  async function submitAction() {
    if (!action) return
    try {
      if (action.type === 'approve') {
        await api.approveFix(action.fixId, notes)
      } else {
        await api.rejectFix(action.fixId, notes)
      }
      setAction(null); setNotes('')
      // Reload
      const params = outcome ? { outcome } : {}
      const d = await api.fixes(params)
      setData(d)
    } catch (e) {
      alert(`Failed: ${e.message}`)
    }
  }

  const rows = data?.items || []

  return (
    <>
      <div className="page-header">
        <h2>Fix Queue</h2>
        <p>{data?.total ?? '…'} total fixes · HITL approval interface</p>
      </div>

      {/* Filter */}
      <div className="card" style={{ marginBottom: 20, padding: '12px 20px', display: 'flex', gap: 12, alignItems: 'center' }}>
        <select
          id="filter-outcome"
          value={outcome}
          onChange={e => setOutcome(e.target.value)}
          style={{
            background: 'var(--bg-card)', color: 'var(--text-secondary)',
            border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
            padding: '6px 10px', fontSize: 12,
          }}
        >
          {['','pending','accepted','auto_accepted','rejected'].map(o => (
            <option key={o} value={o}>{o || 'All outcomes'}</option>
          ))}
        </select>
      </div>

      {/* HITL Action Modal */}
      {action && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 999,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <div className="card" style={{ width: 420, boxShadow: 'var(--shadow-lg)' }}>
            <div style={{ fontWeight: 700, marginBottom: 14, fontSize: 15 }}>
              {action.type === 'approve' ? '✅ Approve Fix' : '❌ Reject Fix'}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
              Fix ID: <span className="mono" style={{ color: 'var(--text-code)' }}>{action.fixId}</span>
            </div>
            <textarea
              id="action-notes"
              placeholder={action.type === 'approve' ? 'Reviewer notes (optional)…' : 'Reason for rejection…'}
              value={notes}
              onChange={e => setNotes(e.target.value)}
              rows={4}
              style={{
                width: '100%', background: 'var(--bg-surface)', border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)', padding: 12, color: 'var(--text-primary)',
                fontSize: 12, resize: 'vertical', outline: 'none', marginBottom: 16,
              }}
            />
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button className="btn btn-ghost" onClick={() => { setAction(null); setNotes('') }}>Cancel</button>
              <button
                id="btn-confirm-action"
                className="btn btn-primary"
                style={{ background: action.type === 'approve' ? 'var(--green)' : 'var(--red)' }}
                onClick={submitAction}
              >
                {action.type === 'approve' ? 'Approve & Create PR' : 'Reject'}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className={selected ? 'two-col' : ''} style={{ alignItems: 'start' }}>
        <div className="card" style={{ padding: 0 }}>
          {loading ? (
            <div style={{ padding: 24 }}>{Array.from({length:5},(_,i) => <div key={i} className="skeleton" style={{height:34,marginBottom:8}} />)}</div>
          ) : error ? (
            <div className="empty-state"><p style={{ color: 'var(--red)' }}>{error}</p></div>
          ) : rows.length === 0 ? (
            <div className="empty-state"><p>No fixes found</p></div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Fix ID</th><th>Strategy</th><th>Confidence</th>
                    <th>Validation</th><th>Outcome</th><th>PR</th><th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(fix => (
                    <tr
                      key={fix.id}
                      id={`fix-row-${fix.id}`}
                      onClick={() => loadValidation(fix.id)}
                      style={{ cursor:'pointer', background: selected===fix.id ? 'var(--accent-dim)' : '' }}
                    >
                      <td><span className="mono" style={{ fontSize: 11, color: 'var(--text-code)' }}>{fix.id?.slice(0,8)}…</span></td>
                      <td><span className="mono" style={{ fontSize: 11 }}>{fix.strategy}</span></td>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <div style={{
                            width: `${Math.round((fix.confidence||0)*60)}px`,
                            height: 5,
                            background: fix.confidence > 0.85 ? 'var(--green)' : fix.confidence > 0.6 ? 'var(--yellow)' : 'var(--red)',
                            borderRadius: 3,
                          }} />
                          <span style={{ fontSize: 11 }}>{fix.confidence ? `${(fix.confidence*100).toFixed(0)}%` : '—'}</span>
                        </div>
                      </td>
                      <td>
                        {fix.validation_pass === true  ? <span style={{ color: 'var(--green)'  }}>✓ Pass</span> :
                         fix.validation_pass === false ? <span style={{ color: 'var(--red)'    }}>✗ Fail</span> :
                         <span style={{ color: 'var(--text-muted)' }}>—</span>}
                      </td>
                      <td>
                        <span className={`badge badge-${OUTCOME_COLOR[fix.outcome]||'yellow'}`}>{fix.outcome||'pending'}</span>
                      </td>
                      <td>
                        {fix.pr_url
                          ? <a href={fix.pr_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()} style={{ color: 'var(--accent)', fontSize: 12 }}>
                              PR <ExternalLink size={10} />
                            </a>
                          : <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>—</span>
                        }
                      </td>
                      <td onClick={e => e.stopPropagation()}>
                        {(!fix.outcome || fix.outcome === 'pending') && (
                          <div style={{ display: 'flex', gap: 6 }}>
                            <button
                              id={`btn-approve-${fix.id?.slice(0,8)}`}
                              className="btn btn-ghost"
                              style={{ padding: '4px 10px', fontSize: 11, color: 'var(--green)', borderColor: 'var(--green-dim)' }}
                              onClick={() => setAction({ type: 'approve', fixId: fix.id })}
                            ><CheckCircle size={11} /> Approve</button>
                            <button
                              id={`btn-reject-${fix.id?.slice(0,8)}`}
                              className="btn btn-ghost"
                              style={{ padding: '4px 10px', fontSize: 11, color: 'var(--red)', borderColor: 'var(--red-dim)' }}
                              onClick={() => setAction({ type: 'reject', fixId: fix.id })}
                            ><XCircle size={11} /> Reject</button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Validation detail panel */}
        {selected && (
          <div className="card fade-in" style={{ fontSize: 12 }}>
            <div style={{ fontWeight: 600, marginBottom: 12, display: 'flex', justifyContent: 'space-between' }}>
              Validation Report
              <button className="btn btn-ghost" style={{ padding:'2px 8px',fontSize:11 }} onClick={() => setSelected(null)}>✕</button>
            </div>
            {!validation
              ? <div className="skeleton" style={{ height: 140 }} />
              : <>
                  <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:12 }}>
                    {validation.validation_pass
                      ? <span className="badge badge-green">✓ Passed</span>
                      : <span className="badge badge-red">✗ Failed</span>
                    }
                  </div>
                  <div style={{ color:'var(--text-muted)', marginBottom:4 }}>Lines changed: {validation.lines_changed ?? '—'}</div>
                  {validation.test_results && (
                    <div style={{ marginTop:10 }}>
                      <div style={{ fontWeight:600, marginBottom:6 }}>Test Results</div>
                      <pre style={{
                        background:'var(--bg-surface)', borderRadius:6, padding:10,
                        fontSize:11, color:'var(--text-code)', overflow:'auto', maxHeight:200
                      }}>
                        {JSON.stringify(validation.test_results, null, 2)}
                      </pre>
                    </div>
                  )}
                  {validation.semgrep_results && Object.keys(validation.semgrep_results).length > 0 && (
                    <div style={{ marginTop:10 }}>
                      <div style={{ fontWeight:600, marginBottom:6 }}>Semgrep Findings</div>
                      <pre style={{
                        background:'var(--bg-surface)', borderRadius:6, padding:10,
                        fontSize:11, color:'var(--red)', overflow:'auto', maxHeight:160
                      }}>
                        {JSON.stringify(validation.semgrep_results, null, 2)}
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
