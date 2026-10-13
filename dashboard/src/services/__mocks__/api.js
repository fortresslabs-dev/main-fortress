/**
 * Default mocks for src/services/api.js — used by all Vitest smoke tests.
 *
 * Each function returns a promise resolving to `{ data: ... }` so pages can
 * call `screeningApi.getX().then(r => setData(r.data))` without code changes.
 * Tests can override any individual function via `vi.mocked(...).mockResolvedValueOnce`.
 */
import { vi } from 'vitest'

const ok = (data) => () => Promise.resolve({ data })

export const dashboardApi = {
  getOverview:    vi.fn(ok({ total_rules: 0, active_rules: 0 })),
  getRules:       vi.fn(ok({ rules: [], total: 0 })),
  getChanges:     vi.fn(ok({ changes: [] })),
  getDeadlines:   vi.fn(ok({ deadlines: [] })),
  getRisk:        vi.fn(ok({ heatmap: [] })),
  getCoverage:    vi.fn(ok({ coverage: [] })),
  getWorkflows:   vi.fn(ok({ workflows: [] })),
  getOfacHealth:  vi.fn(ok({
    entries_active: 0, variants_total: 0, variants_active: 0,
    variants_embedded: 0, embedding_coverage_pct: 0,
    vector_mode_ready: false, last_list_update: null,
  })),
  getEnforcement: vi.fn(ok({ actions: [], total: 0 })),
  getObligations: vi.fn(ok({ total: 0, covered: 0, breakdown: [] })),
  updateRule:     vi.fn(ok({ ok: true })),
  signOff:        vi.fn(ok({ ok: true })),
}

export const testGenApi = {
  generate:  vi.fn(ok({ workflow_id: 'wf-test' })),
  bulkGen:   vi.fn(ok({ workflow_ids: [] })),
  getStatus: vi.fn(ok({ status: 'idle' })),
}

export const readinessApi = {
  getReadiness:  vi.fn(ok({
    lens: 'cco', score: 0, band: 'green', biggest_drag: 'none',
    dimensions: [], updated_at: '2026-06-08T00:00:00Z',
  })),
  getRiskMatrix: vi.fn(ok({ grid: [] })),
  getCro:        vi.fn(ok({ panels: [] })),
  getCio:        vi.fn(ok({ panels: [] })),
  getFindings:   vi.fn(ok({ findings: [] })),
  getDataFlow:   vi.fn(ok({ nodes: [], edges: [] })),
}

export const exportApi = {
  create:   vi.fn(ok({ export_id: 'exp-test', version_hash: 'abc123' })),
  download: vi.fn(() => Promise.resolve({
    data: new Blob(['test'], { type: 'text/csv' }),
    headers: { 'content-type': 'text/csv' },
  })),
}

export const screeningApi = {
  search:           vi.fn(ok({ matches: [], decision: 'PASS', final_score: 0 })),
  listCases:        vi.fn(ok({ cases: [], total: 0 })),
  getCase:          vi.fn(ok({
    case: {
      case_id: 'CASE-001', priority: 'low', decision_at_open: 'PASS',
      status: 'open', external_customer_id: 'CUST-1',
    },
    entity: { name: 'Test Entity', country: 'US' },
    events: [],
  })),
  postDecision:     vi.fn(ok({ ok: true })),
  patchScreeningRule: vi.fn(ok({
    rule_id: 'OFAC-SCREEN-FUZZY-001', weight: 1.0, version: 2,
    is_active: true, description: 'mock', updated_by: 'mock@local',
  })),
  getRulesPerf:     vi.fn(ok({
    rules: [],
    totals: {
      alerts: 0, true_positives: 0, false_positives: 0,
      open: 0, precision: null,
    },
    by_decision: [],
    window_days: 30,
    total_rules: 0,
  })),
  getVectorIntel:   vi.fn(ok({
    embedded: 0, total: 0, coverage_pct: 0, samples: [],
  })),
  getExecutiveToday: vi.fn(ok({
    tiles: [
      { key: 'screened', label: 'Screened today', value: 0 },
      { key: 'holds',    label: 'Holds',          value: 0 },
    ],
    last_list_update: '2026-06-08T00:00:00Z',
  })),
}

export default {
  defaults: { headers: { common: {} } },
}
