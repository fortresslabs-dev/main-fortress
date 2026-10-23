import axios from 'axios'
import { fetchAuthSession } from 'aws-amplify/auth'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8002',
  timeout: 30000,
})

// Fresh Cognito idToken per request. The old one-shot default in App.jsx
// went stale on token refresh (Amplify silently rotates every ~1h) and
// broke the whole dashboard with 401s. Amplify caches the session, so this
// is O(1) after the first call. E2E specs pre-set the default header, so
// skip when Authorization is already present on the merged config.
api.interceptors.request.use(async (config) => {
  if (config.headers?.Authorization) return config
  try {
    const session = await fetchAuthSession()
    const token = session.tokens?.idToken?.toString()
    if (token) config.headers.Authorization = `Bearer ${token}`
  } catch {
    // No session — let it 401 and surface auth failure in the UI.
  }
  return config
})

export const dashboardApi = {
  getOverview:     ()       => api.get('/api/v1/dashboard/overview'),
  getRules:        (params) => api.get('/api/v1/dashboard/rules', { params }),
  getChanges:      (params) => api.get('/api/v1/dashboard/changes', { params }),
  getChangeDetail: (ref)    => api.get(`/api/v1/dashboard/changes/${ref}`),
  getDeadlines:    ()       => api.get('/api/v1/dashboard/deadlines'),
  getRisk:         ()       => api.get('/api/v1/dashboard/risk'),
  getCoverage:     ()       => api.get('/api/v1/dashboard/coverage'),
  getWorkflows:    ()       => api.get('/api/v1/dashboard/workflows'),
  getOfacHealth:   ()       => api.get('/api/v1/dashboard/ofac-health'),
  getEnforcement:  (params) => api.get('/api/v1/dashboard/enforcement', { params }),
  getObligations:  (params) => api.get('/api/v1/dashboard/obligations', { params }),
  updateRule:      (id, b)  => api.patch(`/api/v1/dashboard/rules/${id}/status`, b),
  signOff:         (ref, b) => api.post(`/api/v1/dashboard/changes/${ref}/signoff`, b),
  signOffBulk:     (b)      => api.post('/api/v1/dashboard/changes/signoff/bulk', b),
}

export const testGenApi = {
  generate:   (id, b) => api.post(`/api/v1/rules/${id}/generate-test`, b),
  bulkGen:    (b)     => api.post('/api/v1/rules/bulk-generate', b),
  getStatus:  (id)    => api.get(`/api/v1/rules/${id}/test-status`),
}

export const readinessApi = {
  getReadiness: (lens) => api.get('/api/v1/dashboard/readiness', { params:{ lens } }),
  getRiskMatrix:()      => api.get('/api/v1/dashboard/risk-matrix'),
  getCro:       ()      => api.get('/api/v1/dashboard/cro'),
  getCio:       ()      => api.get('/api/v1/dashboard/cio'),
  getFindings:  ()      => api.get('/api/v1/dashboard/findings'),
  getDataFlow:  ()      => api.get('/api/v1/architecture/data-flow'),
}

export const exportApi = {
  // Legacy path — still here for the stateless-token export flow.
  create:   (body) => api.post('/api/v1/dashboard/exports', body),
  download: (id)   => api.get(`/api/v1/dashboard/exports/${id}/download`, { responseType:'blob' }),
}

// New WORM-backed Exports API (design doc PR #7).
export const exportsApi = {
  create:   (body) => api.post('/api/v1/exports', body),
  list:     (params) => api.get('/api/v1/exports', { params }),
  get:      (id)   => api.get(`/api/v1/exports/${id}`),
  sign:     (id, body) => api.post(`/api/v1/exports/${id}/sign`, body),
  download: (id)   => api.get(`/api/v1/exports/${id}/download`, { responseType:'blob' }),
}

export const screeningApi = {
  search:           (body)          => api.post('/api/v1/dashboard/screening/search', body),
  listCases:        (params)        => api.get('/api/v1/dashboard/screening/cases', { params }),
  getCase:          (id)            => api.get(`/api/v1/dashboard/screening/cases/${id}`),
  postDecision:     (id, body)      => api.post(`/api/v1/dashboard/screening/cases/${id}/decision`, body),
  getRulesPerf:     (params)        => api.get('/api/v1/dashboard/screening/rules-perf', { params }),
  patchScreeningRule:(id, body)     => api.patch(`/api/v1/dashboard/screening/rules/${id}`, body),
  getVectorIntel:   ()              => api.get('/api/v1/dashboard/screening/vector-intel'),
  getExecutiveToday:()              => api.get('/api/v1/dashboard/screening/executive-today'),
}

export default api
