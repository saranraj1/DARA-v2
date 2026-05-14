// DARA Admin UI — API client
const ADMIN_TOKEN = import.meta.env.VITE_ADMIN_TOKEN || 'dara-admin-secret'
const BASE = ''  // proxied via vite to localhost:8000

const headers = () => ({
  'Content-Type': 'application/json',
  'X-Admin-Token': ADMIN_TOKEN,
})

async function get(path) {
  const r = await fetch(BASE + path, { headers: headers() })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

async function post(path, body = {}) {
  const r = await fetch(BASE + path, { method: 'POST', headers: headers(), body: JSON.stringify(body) })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export const api = {
  // Health
  health: () => get('/health'),
  healthReady: () => get('/health/ready'),

  // Admin stats
  stats: () => get('/api/v1/admin/stats'),
  errors: (params = {}) => {
    const q = new URLSearchParams(params).toString()
    return get(`/api/v1/admin/errors${q ? '?' + q : ''}`)
  },
  fixes: (params = {}) => {
    const q = new URLSearchParams(params).toString()
    return get(`/api/v1/admin/fixes${q ? '?' + q : ''}`)
  },
  patterns: () => get('/api/v1/admin/patterns'),
  pipeline: (errorId) => get(`/api/v1/admin/pipeline/${errorId}`),
  audit: (params = {}) => {
    const q = new URLSearchParams(params).toString()
    return get(`/api/v1/admin/audit${q ? '?' + q : ''}`)
  },
  reindex: () => post('/api/v1/admin/reindex'),

  // Fixes HITL
  approveFix: (fixId, notes = '') => post(`/api/v1/fixes/${fixId}/approve`, { reviewer_notes: notes, create_pr: true }),
  rejectFix: (fixId, reason) => post(`/api/v1/fixes/${fixId}/reject`, { reason }),
  fixValidation: (fixId) => get(`/api/v1/fixes/${fixId}/validation`),

  // Traces
  topology: () => get('/api/v1/topology'),
  traces: (params = {}) => {
    const q = new URLSearchParams(params).toString()
    return get(`/api/v1/traces${q ? '?' + q : ''}`)
  },
}
