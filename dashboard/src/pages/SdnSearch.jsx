import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { screeningApi } from '../services/api'
import { useAuth } from '../App'

const DECISION_TONE = { PASS: 'pass', REVIEW: 'run', HOLD: 'fail' }

function fmtAge(hours) {
  if (hours == null) return '—'
  if (hours < 1) return `${Math.round(hours * 60)} min`
  if (hours < 48) return `${hours.toFixed(1)} h`
  return `${Math.round(hours / 24)} d`
}

function fmtOpened(iso) {
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export default function SdnSearch() {
  const { canScreen, canEdit } = useAuth()
  const nav = useNavigate()
  const [form, setForm] = useState({ name: '', country: '', dob: '', external_customer_id: '' })
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [deciding, setDeciding] = useState(null)
  const [decided, setDecided] = useState(null)

  if (!canScreen) {
    return <div className="ftr-loading">Screening access required.</div>
  }

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  const submit = async (e) => {
    e.preventDefault()
    if (!form.name.trim()) return
    setLoading(true); setError(null); setResult(null); setDecided(null)
    try {
      const body = Object.fromEntries(
        Object.entries(form).filter(([, v]) => v && String(v).trim())
      )
      const r = await screeningApi.search(body)
      setResult(r.data)
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Search failed')
    } finally {
      setLoading(false)
    }
  }

  const decide = async (action) => {
    if (!result?.matches?.length) return
    if (!result?.case_id) {
      nav('/dashboard/cases?status=open')
      return
    }
    setDeciding(action); setError(null)
    try {
      const r = await screeningApi.postDecision(result.case_id, { action })
      setDecided({ action, status: r.data.status, by: r.data.decided_by, at: r.data.decided_at })
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Decision failed')
    } finally {
      setDeciding(null)
    }
  }

  return (
    <section>
      <h1 className="ftr-page">SDN Screening</h1>
      <p className="ftr-page-sub">
        Search a party against the consolidated OFAC SDN list. Decisions are
        recorded for audit; matches open a case automatically.
      </p>

      <form className="ftr-form" onSubmit={submit}>
        <div className="row">
          <label>Name<input value={form.name} onChange={set('name')} required autoFocus /></label>
          <label>Country (ISO-2)<input value={form.country} onChange={set('country')} maxLength={2} /></label>
        </div>
        <div className="row">
          <label>Date of birth<input type="date" value={form.dob} onChange={set('dob')} /></label>
          <label>Customer ID<input value={form.external_customer_id} onChange={set('external_customer_id')} placeholder="optional" /></label>
        </div>
        <button className="ftr-btn primary" type="submit" disabled={loading}>
          {loading ? 'Screening…' : 'Screen'}
        </button>
      </form>

      {error && <div className="ftr-error">{error}</div>}

      {result && (
        <div className="ftr-result">
          <div className={`ftr-decision ${DECISION_TONE[result.decision] || ''}`}>
            <b>{result.decision}</b>
            <span>Final Risk Score: <b>{result.final_score ?? 0}</b></span>
            <span className="mono">id {result.screening_id?.slice(0, 8)}</span>
          </div>

          {result.sdn_data_age_hours != null && (
            <div className="ftr-hint" data-testid="freshness-line">
              SDN list refreshed{' '}
              <b>{fmtAge(result.sdn_data_age_hours)}</b> ago.{' '}
              {result.staleness_bumped
                ? <>Decision bumped one level because data is stale (REVIEW→HOLD).</>
                : <>Within freshness window — no staleness bump.</>}
            </div>
          )}

          {result.case_was_pre_existing && result.case_id && (
            <div className="ftr-hint" data-testid="attached-case-line">
              Attached to existing case{' '}
              <b className="mono">{result.case_id}</b>
              {result.case_opened_at && (
                <> (opened {fmtOpened(result.case_opened_at)})</>
              )}
              {' — '}any decision here applies to that case.
            </div>
          )}

          {result.matches?.length === 0 ? (
            <div className="ftr-ok">No matches — party clears.</div>
          ) : (
            <div className="ftr-tablecard">
              <table>
                <thead><tr>
                  <th>SDN</th><th>Matched name</th><th>Programs</th>
                  <th>Country</th><th>Fuzzy</th><th>Alias</th>
                  <th>Vector</th><th>Final</th>
                </tr></thead>
                <tbody>
                  {result.matches.map(m => (
                    <tr key={m.sdn_number}>
                      <td className="mono">{m.sdn_number}</td>
                      <td>{m.matched_name}{!m.is_primary && <em> (alias)</em>}</td>
                      <td>{(m.programs || []).join(', ')}</td>
                      <td>{m.country || '—'}</td>
                      <td>{m.display_scores?.['Fuzzy Match'] ?? '—'}</td>
                      <td>{m.display_scores?.['Alias Match'] ?? '—'}</td>
                      <td>{m.display_scores?.['Vector Similarity'] ?? '—'}</td>
                      <td><b>{m.display_scores?.['Final Risk Score'] ?? '—'}</b></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {result.matches?.length > 0 && !decided && (
            <div className="ftr-actions">
              <button
                className="ftr-btn"
                disabled={deciding || !canEdit || !result.case_id}
                title={!canEdit ? 'Pass requires CCO' : !result.case_id ? 'No case opened' : ''}
                onClick={() => decide('pass')}
              >
                Pass (CCO override)
              </button>
              <button
                className="ftr-btn"
                disabled={deciding || !result.case_id}
                onClick={() => decide('review')}
              >
                Send to review
              </button>
              <button
                className="ftr-btn danger"
                disabled={deciding || !result.case_id}
                onClick={() => decide('escalate')}
              >
                Escalate
              </button>
              {result.case_id && (
                <span className="ftr-hint mono">
                  case {result.case_id}
                  {' · '}
                  <a href={`/dashboard/cases/${result.case_id}`}>open</a>
                </span>
              )}
            </div>
          )}

          {decided && (
            <div className="ftr-ok">
              Decision recorded: <b>{decided.action.toUpperCase()}</b>
              {' '}(case now <b>{decided.status}</b>) by {decided.by}.
              {' '}<a href={`/dashboard/cases/${result.case_id}`}>View case</a>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
