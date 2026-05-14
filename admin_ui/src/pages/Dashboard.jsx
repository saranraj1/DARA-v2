import { useState, useEffect } from 'react'
import { api } from '../api.js'
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis,
  Tooltip, ResponsiveContainer, CartesianGrid
} from 'recharts'
import { AlertTriangle, Wrench, CheckCircle, Clock, Zap, TrendingUp } from 'lucide-react'

const CHART_COLORS = {
  fixed:     '#34d399',
  escalated: '#f87171',
  pending:   '#fbbf24',
  accepted:  '#38bdf8',
  rejected:  '#a78bfa',
}

function StatCard({ id, icon: Icon, label, value, delta, color = 'var(--accent)', loading }) {
  return (
    <div id={id} className="stat-card">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span className="stat-label">{label}</span>
        <Icon size={16} color={color} opacity={0.7} />
      </div>
      {loading
        ? <div className="skeleton" style={{ height: 36, marginTop: 10 }} />
        : <div className="stat-value" style={{ background: `linear-gradient(135deg, var(--text-primary), ${color})`, WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>{value ?? '—'}</div>
      }
      {delta && <div className="stat-delta" style={{ marginTop: 6 }}>{delta}</div>}
    </div>
  )
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div className="glass" style={{ padding: '10px 14px', fontSize: 12 }}>
      <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>{label}</div>
      {payload.map(p => (
        <div key={p.name} style={{ color: p.color }}>
          {p.name}: <strong>{p.value}</strong>
        </div>
      ))}
    </div>
  )
}

// Generate mock trend data until real time-series endpoint exists
function mockTrend(base = 12, variance = 5, n = 12) {
  const now = new Date()
  return Array.from({ length: n }, (_, i) => {
    const t = new Date(now - (n - 1 - i) * 5 * 60 * 1000)
    return {
      time: t.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
      fixed: Math.max(0, base + Math.round((Math.random() - 0.5) * variance * 2)),
      escalated: Math.max(0, 2 + Math.round((Math.random() - 0.5) * 3)),
    }
  })
}

export default function Dashboard() {
  const [stats, setStats] = useState(null)
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [trend] = useState(() => mockTrend())

  useEffect(() => {
    let alive = true
    async function load() {
      try {
        const [s, h] = await Promise.all([api.stats(), api.health()])
        if (alive) { setStats(s); setHealth(h); setLoading(false) }
      } catch (e) {
        if (alive) { setError(e.message); setLoading(false) }
      }
    }
    load()
    return () => { alive = false }
  }, [])

  const s = stats || {}
  const totalErrors = s.total_errors ?? 0
  const totalFixed  = s.fixed ?? 0
  const fixRate     = totalErrors ? Math.round((totalFixed / totalErrors) * 100) : 0
  const pending     = s.pending ?? 0
  const escalated   = s.escalated ?? 0
  const avgConf     = s.avg_confidence != null ? `${(s.avg_confidence * 100).toFixed(1)}%` : '—'

  // Class breakdown for bar chart
  const classData = s.error_class_breakdown
    ? Object.entries(s.error_class_breakdown).map(([k, v]) => ({ name: k.replace('_', ' '), count: v }))
    : []

  return (
    <>
      <div className="page-header">
        <h2>Overview</h2>
        <p>Real-time DARA pipeline metrics</p>
      </div>

      {error && (
        <div className="card" style={{ borderColor: 'var(--red)', marginBottom: 20 }}>
          <span style={{ color: 'var(--red)' }}>⚠ API error: {error} — is the DARA server running?</span>
        </div>
      )}

      {/* KPI stats */}
      <div className="stats-grid">
        <StatCard id="stat-total"     icon={AlertTriangle} label="Total Errors"   value={totalErrors} color="var(--yellow)" loading={loading} />
        <StatCard id="stat-fixed"     icon={CheckCircle}   label="Fixed"          value={totalFixed}  color="var(--green)"  loading={loading} delta={`${fixRate}% fix rate`} />
        <StatCard id="stat-pending"   icon={Clock}         label="Pending"        value={pending}     color="var(--yellow)" loading={loading} />
        <StatCard id="stat-escalated" icon={AlertTriangle} label="Escalated"      value={escalated}   color="var(--red)"    loading={loading} />
        <StatCard id="stat-confidence" icon={TrendingUp}   label="Avg Confidence" value={avgConf}     color="var(--accent)" loading={loading} />
        <StatCard id="stat-health"    icon={Zap}           label="API Health"
          value={health ? (health.status === 'ok' ? 'OK' : health.status) : error ? 'DOWN' : '…'}
          color={health?.status === 'ok' ? 'var(--green)' : 'var(--red)'}
          loading={false}
        />
      </div>

      {/* Charts */}
      <div className="two-col">
        <div className="card">
          <div style={{ marginBottom: 14, fontWeight: 600, fontSize: 13 }}>Pipeline Throughput (5m windows)</div>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={trend} margin={{ top: 4, right: 0, bottom: 0, left: -20 }}>
              <defs>
                <linearGradient id="gFixed" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#34d399" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#34d399" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="gEsc" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#f87171" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#f87171" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(99,179,237,0.06)" />
              <XAxis dataKey="time" tick={{ fill: 'var(--text-muted)', fontSize: 10 }} />
              <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 10 }} />
              <Tooltip content={<CustomTooltip />} />
              <Area type="monotone" dataKey="fixed"     stroke="#34d399" fill="url(#gFixed)" strokeWidth={2} name="Fixed" />
              <Area type="monotone" dataKey="escalated" stroke="#f87171" fill="url(#gEsc)"   strokeWidth={2} name="Escalated" />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <div style={{ marginBottom: 14, fontWeight: 600, fontSize: 13 }}>Errors by Class</div>
          {classData.length === 0 ? (
            <div className="empty-state"><p>No data yet — ingest some errors to see breakdown</p></div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={classData} margin={{ top: 4, right: 0, bottom: 0, left: -20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(99,179,237,0.06)" />
                <XAxis dataKey="name" tick={{ fill: 'var(--text-muted)', fontSize: 10 }} />
                <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 10 }} />
                <Tooltip content={<CustomTooltip />} />
                <Bar dataKey="count" fill="var(--accent)" radius={[4,4,0,0]} name="Errors" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Outcome distribution */}
      {s.outcome_breakdown && (
        <div className="card">
          <div style={{ marginBottom: 14, fontWeight: 600, fontSize: 13 }}>Fix Outcomes</div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            {Object.entries(s.outcome_breakdown).map(([k, v]) => (
              <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                <span className={`badge badge-${k === 'accepted' ? 'green' : k === 'rejected' ? 'red' : 'yellow'}`}>
                  {k}
                </span>
                <strong style={{ color: 'var(--text-primary)' }}>{v}</strong>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  )
}
