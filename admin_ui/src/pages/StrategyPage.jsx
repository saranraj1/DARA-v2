import { useState, useEffect } from 'react'
import { Zap, AlertTriangle, CheckCircle, TrendingUp } from 'lucide-react'
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip,
  CartesianGrid, Cell
} from 'recharts'

// Mock Phase 3 strategy health data (real data from /api/v1/admin/strategy-health once built)
function useMockStrategyData() {
  const classes = ['null_reference','type_mismatch','network_timeout','database_error','authentication','resource_leak','concurrency','logic_error']
  return {
    variants: classes.map(c => ({
      error_class: c,
      active_strategy: `${c}_v2`,
      fix_rate: 0.65 + Math.random() * 0.30,
      rejection_rate: Math.random() * 0.30,
      testing_variants: Math.random() > 0.7 ? 1 : 0,
      ab_tests_run: Math.floor(Math.random() * 12),
      last_promoted: Math.random() > 0.5 ? new Date(Date.now() - Math.random() * 7*24*3600*1000).toISOString() : null,
    })),
    ab_summary: { evaluated: 24, promoted: 7, retired: 4 },
    anomaly_summary: { alerts_24h: 3, services_flagged: 2 },
  }
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div className="glass" style={{ padding:'10px 14px', fontSize:12 }}>
      <div style={{ color:'var(--text-muted)', marginBottom:4 }}>{label}</div>
      {payload.map(p => (
        <div key={p.name} style={{ color: p.color || 'var(--accent)' }}>
          {p.name}: <strong>{typeof p.value === 'number' ? `${(p.value*100).toFixed(1)}%` : p.value}</strong>
        </div>
      ))}
    </div>
  )
}

export default function StrategyPage() {
  const { variants, ab_summary, anomaly_summary } = useMockStrategyData()
  const [selected, setSelected] = useState(null)

  const radarData = variants.slice(0, 6).map(v => ({
    subject: v.error_class.replace('_',' ').slice(0,12),
    fixRate: +(v.fix_rate * 100).toFixed(1),
    fullMark: 100,
  }))

  const barData = variants.map(v => ({
    name: v.error_class.replace('_',' '),
    fixRate: v.fix_rate,
    rejRate: v.rejection_rate,
  }))

  const sel = selected ? variants.find(v => v.error_class === selected) : null

  return (
    <>
      <div className="page-header">
        <h2>Strategy Health</h2>
        <p>Phase 3 reflexive memory — A/B test results and strategy performance per error class</p>
      </div>

      {/* A/B + Anomaly summary */}
      <div className="stats-grid" style={{ marginBottom: 24 }}>
        <div className="stat-card">
          <div className="stat-label">A/B Tests Run</div>
          <div className="stat-value">{ab_summary.evaluated}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Strategies Promoted</div>
          <div className="stat-value" style={{
            background: 'linear-gradient(135deg,var(--text-primary),var(--green))',
            WebkitBackgroundClip:'text', WebkitTextFillColor:'transparent'
          }}>{ab_summary.promoted}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Strategies Retired</div>
          <div className="stat-value" style={{
            background: 'linear-gradient(135deg,var(--text-primary),var(--red))',
            WebkitBackgroundClip:'text', WebkitTextFillColor:'transparent'
          }}>{ab_summary.retired}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Anomaly Alerts (24h)</div>
          <div className="stat-value" style={{
            background: anomaly_summary.alerts_24h > 0
              ? 'linear-gradient(135deg,var(--text-primary),var(--yellow))'
              : 'linear-gradient(135deg,var(--text-primary),var(--green))',
            WebkitBackgroundClip:'text', WebkitTextFillColor:'transparent'
          }}>{anomaly_summary.alerts_24h}</div>
          <div style={{ fontSize:11, color:'var(--text-muted)', marginTop:4 }}>
            {anomaly_summary.services_flagged} services flagged
          </div>
        </div>
      </div>

      {/* Charts row */}
      <div className="two-col" style={{ marginBottom: 20 }}>
        <div className="card">
          <div style={{ fontWeight:600, fontSize:13, marginBottom:16 }}>Fix Rate by Error Class</div>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={barData} margin={{ top:4, right:0, bottom:40, left:-16 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(99,179,237,0.06)" />
              <XAxis dataKey="name" tick={{ fill:'var(--text-muted)', fontSize:9 }} angle={-35} textAnchor="end" />
              <YAxis tickFormatter={v=>`${(v*100).toFixed(0)}%`} tick={{ fill:'var(--text-muted)', fontSize:10 }} />
              <Tooltip content={<CustomTooltip />} />
              <Bar dataKey="fixRate" name="Fix Rate" radius={[4,4,0,0]}>
                {barData.map((entry, i) => (
                  <Cell
                    key={i}
                    fill={entry.fixRate >= 0.85 ? '#34d399' : entry.fixRate >= 0.65 ? '#38bdf8' : '#f87171'}
                    cursor="pointer"
                    onClick={() => setSelected(selected === variants[i].error_class ? null : variants[i].error_class)}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <div style={{ fontWeight:600, fontSize:13, marginBottom:16 }}>Strategy Coverage Radar</div>
          <ResponsiveContainer width="100%" height={220}>
            <RadarChart data={radarData}>
              <PolarGrid stroke="rgba(99,179,237,0.1)" />
              <PolarAngleAxis dataKey="subject" tick={{ fill:'var(--text-muted)', fontSize:10 }} />
              <PolarRadiusAxis domain={[0,100]} tick={{ fill:'var(--text-muted)', fontSize:9 }} />
              <Radar name="Fix Rate %" dataKey="fixRate" stroke="var(--accent)" fill="var(--accent)" fillOpacity={0.15} />
            </RadarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Strategy table */}
      <div className="card" style={{ padding:0 }}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Error Class</th>
                <th>Active Strategy</th>
                <th>Fix Rate</th>
                <th>Rejection Rate</th>
                <th>Testing Variants</th>
                <th>A/B Tests</th>
                <th>Last Promoted</th>
              </tr>
            </thead>
            <tbody>
              {variants.map(v => {
                const isGood = v.fix_rate >= 0.85
                const isBad  = v.fix_rate < 0.65
                return (
                  <tr
                    key={v.error_class}
                    id={`strategy-row-${v.error_class}`}
                    onClick={() => setSelected(s => s === v.error_class ? null : v.error_class)}
                    style={{
                      cursor: 'pointer',
                      background: selected === v.error_class ? 'var(--accent-dim)' : '',
                    }}
                  >
                    <td>
                      <span className="mono" style={{ color:'var(--text-code)', fontSize:12 }}>
                        {v.error_class}
                      </span>
                    </td>
                    <td>
                      <span className="mono" style={{ fontSize:11 }}>{v.active_strategy}</span>
                    </td>
                    <td>
                      <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                        <div style={{ flex:1, background:'var(--bg-surface)', borderRadius:3, height:5, minWidth:50 }}>
                          <div style={{
                            width:`${v.fix_rate*100}%`, height:'100%', borderRadius:3,
                            background: isGood ? 'var(--green)' : isBad ? 'var(--red)' : 'var(--yellow)',
                          }} />
                        </div>
                        <span style={{ fontSize:11, minWidth:36, color: isGood ? 'var(--green)' : isBad ? 'var(--red)' : 'var(--yellow)' }}>
                          {(v.fix_rate*100).toFixed(0)}%
                        </span>
                      </div>
                    </td>
                    <td>
                      <span style={{ fontSize:12, color: v.rejection_rate > 0.25 ? 'var(--red)' : 'var(--text-secondary)' }}>
                        {(v.rejection_rate*100).toFixed(0)}%
                      </span>
                    </td>
                    <td style={{ textAlign:'center' }}>
                      {v.testing_variants > 0
                        ? <span className="badge badge-blue">{v.testing_variants} testing</span>
                        : <span style={{ color:'var(--text-muted)', fontSize:12 }}>—</span>
                      }
                    </td>
                    <td style={{ textAlign:'center', fontSize:12 }}>{v.ab_tests_run}</td>
                    <td style={{ fontSize:11, color:'var(--text-muted)' }}>
                      {v.last_promoted ? new Date(v.last_promoted).toLocaleDateString() : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Selected class detail */}
      {sel && (
        <div className="card fade-in" style={{ marginTop:20 }}>
          <div style={{ fontWeight:700, fontSize:14, marginBottom:12 }}>
            Strategy Detail — <span className="mono" style={{ color:'var(--accent)' }}>{sel.error_class}</span>
          </div>
          <div className="three-col" style={{ marginBottom:0 }}>
            {[
              { label:'Active Strategy', value: sel.active_strategy, color:'var(--accent)' },
              { label:'Fix Rate',        value:`${(sel.fix_rate*100).toFixed(1)}%`, color: sel.fix_rate >= 0.85 ? 'var(--green)' : 'var(--yellow)' },
              { label:'A/B Tests Run',   value: sel.ab_tests_run,   color:'var(--purple)' },
            ].map(({ label, value, color }) => (
              <div key={label}>
                <div style={{ fontSize:11, color:'var(--text-muted)', marginBottom:4 }}>{label}</div>
                <div className="mono" style={{ fontSize:15, fontWeight:600, color }}>{value}</div>
              </div>
            ))}
          </div>
          <div style={{ marginTop:12, fontSize:12, color:'var(--text-muted)' }}>
            {sel.testing_variants > 0
              ? `⚗️ ${sel.testing_variants} variant(s) currently in A/B testing — will auto-promote if Fisher test p &lt; 0.05`
              : '✅ No variants in testing — strategy is stable'
            }
          </div>
        </div>
      )}
    </>
  )
}
