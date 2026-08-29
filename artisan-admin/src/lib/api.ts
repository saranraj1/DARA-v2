// DARA Admin UI — API client
const ADMIN_TOKEN = (import.meta.env.VITE_ADMIN_TOKEN as string) || 'dara-admin-secret';
const BASE = '';  // proxied via vite config to http://localhost:8000

const headers = () => ({
  'Content-Type': 'application/json',
  'X-Admin-Token': ADMIN_TOKEN,
});

async function get(path: string) {
  const r = await fetch(BASE + path, { headers: headers() });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

async function post(path: string, body = {}) {
  const r = await fetch(BASE + path, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export const api = {
  // Health
  health: () => get('/health'),
  healthReady: () => get('/health/ready'),

  // Admin stats
  stats: () => get('/api/v1/admin/stats'),
  errors: (params: Record<string, string | number | boolean | undefined> = {}) => {
    const cleanParams: Record<string, string> = {};
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined) cleanParams[k] = String(v);
    });
    const q = new URLSearchParams(cleanParams).toString();
    return get(`/api/v1/admin/errors${q ? '?' + q : ''}`);
  },
  fixes: (params: Record<string, string | number | boolean | undefined> = {}) => {
    const cleanParams: Record<string, string> = {};
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined) cleanParams[k] = String(v);
    });
    const q = new URLSearchParams(cleanParams).toString();
    return get(`/api/v1/admin/fixes${q ? '?' + q : ''}`);
  },
  patterns: () => get('/api/v1/admin/patterns'),
  pipeline: (errorId: string) => get(`/api/v1/admin/pipeline/${errorId}`),
  audit: (params: Record<string, string | number | boolean | undefined> = {}) => {
    const cleanParams: Record<string, string> = {};
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined) cleanParams[k] = String(v);
    });
    const q = new URLSearchParams(cleanParams).toString();
    return get(`/api/v1/admin/audit${q ? '?' + q : ''}`);
  },
  reindex: () => post('/api/v1/admin/reindex'),
  throughput: () => get('/api/v1/admin/throughput'),
  errorsByClass: () => get('/api/v1/admin/errors-by-class'),
  strategies: () => get('/api/v1/admin/strategies'),

  // Fixes HITL
  approveFix: (fixId: string, notes = '') =>
    post(`/api/v1/fixes/${fixId}/approve`, { reviewer_notes: notes, create_pr: true }),
  rejectFix: (fixId: string, reason: string) =>
    post(`/api/v1/fixes/${fixId}/reject`, { reason }),
  fixValidation: (fixId: string) => get(`/api/v1/fixes/${fixId}/validation`),

  // Traces
  topology: () => get('/api/v1/topology'),
  traces: (params: Record<string, string | number | boolean | undefined> = {}) => {
    const cleanParams: Record<string, string> = {};
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined) cleanParams[k] = String(v);
    });
    const q = new URLSearchParams(cleanParams).toString();
    return get(`/api/v1/traces${q ? '?' + q : ''}`);
  },
};
