import { useEffect, useState, useCallback } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { screeningApi } from '../services/api'
import { useAuth } from '../App'

const DECISION_TONE = { PASS: 'pass', REVIEW: 'run', HOLD: 'fail' }

export default function Cases() {
  const { canScreen } = useAuth()
  const [params, setParams] = useSearchParams()
  const [cases, setCases] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)

  const status   = params.get('status') || 'open'
  const priority = params.get('priority') || ''
  const q        = params.get('q') || ''

  const load = useCallback(() => {
    setLoading(true)
    const filters = { status }
    if (priority) filters.priority = priority
    if (q) filters.q = q
    screeningApi.listCases(filters)
      .then(r => { setCases(r.data.cases || []); setTotal(r.data.total || 0) })
      .finally(() => setLoading(false))
  }, [status, priority, q])

  useEffect(() => { load() }, [load])

  if (!canScreen) return <div className="ftr-loading">Screening access required.</div>

  const setFilter = (k, v) => {
    const p = new URLSearchParams(params)
    if (v) p.set(k, v); else p.delete(k)
    setParams(p)
  }

  return (
    <section>
      <h1 className="ftr-page">Screening cases</h1>
      <p className="ftr-page-sub">Open and historical SDN matches awaiting analyst review.</p>

      <div className="ftr-filters">
        <select value={status} onChange={e => setFilter('status', e.target.value)}>
          <option value="open">Open</option>
          <option value="closed">Closed</option>
          <option value="">All</option>
        </select>
        <select value={priority} onChange={e => setFilter('priority', e.target.value)}>
          <option value="">Any priority</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
        </select>
        <input
          placeholder="Search name / SDN / customer…"
          defaultValue={q}
          onKeyDown={e => e.key === 'Enter' && setFilter('q', e.currentTarget.value)}
        />
        <span className="ftr-foot">{total} total</span>
      </div>

      <div className="ftr-tablecard">
        {loading ? <div className="ftr-loading">Loading…</div> : (
          <table>
            <thead><tr>
              <th>Case</th><th>Priority</th><th>Decision</th>
              <th>Matched</th><th>SDN</th><th>Customer</th>
              <th>Events</th><th>Status</th><th>Opened</th>
            </tr></thead>
            <tbody>
              {cases.map(c => (
                <tr key={c.case_id}>
                  <td>
                    <Link to={`/dashboard/cases/${c.case_id}`} className="mono">
                      {c.case_id}
                    </Link>
                  </td>
                  <td><span className={`ftr-st ${c.priority === 'high' ? 'fail' : 'run'}`}>{c.priority}</span></td>
                  <td><span className={`ftr-st ${DECISION_TONE[c.decision_at_open] || ''}`}>{c.decision_at_open}</span></td>
                  <td>{c.matched_name || '—'}</td>
                  <td className="mono">{c.sdn_number || '—'}</td>
                  <td>{c.external_customer_id || '—'}</td>
                  <td>{c.event_count}</td>
                  <td>{c.status}</td>
                  <td className="mono">{c.created_at?.replace('T', ' ').slice(0, 16)}</td>
                </tr>
              ))}
              {cases.length === 0 && (
                <tr><td colSpan={9} className="ftr-empty">No cases match these filters.</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}
