import { useEffect, useState, useCallback } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { screeningApi } from '../services/api'
import { useAuth } from '../App'

const DECISION_TONE = { PASS: 'pass', REVIEW: 'run', HOLD: 'fail' }

// Keep in sync with PASS_HOLD_MIN_NOTES_CHARS in src/api/dashboard_api.py
const PASS_HOLD_MIN_NOTES_CHARS = 50

export default function Investigation() {
  const { caseId } = useParams()
  const { canScreen, canEdit, email } = useAuth()
  const nav = useNavigate()
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    screeningApi.getCase(caseId)
      .then(r => setData(r.data))
      .catch(e => setErr(e?.response?.data?.detail || e.message))
  }, [caseId])

  useEffect(() => { load() }, [load])

  const notesLen = notes.trim().length
  const isHoldPass = (action) =>
    action === 'pass' && data?.case?.decision_at_open === 'HOLD'
  const passBlockedByNotes =
    isHoldPass('pass') && notesLen < PASS_HOLD_MIN_NOTES_CHARS

  const decide = async (action) => {
    if (action === 'pass' && !canEdit) {
      alert('Pass-with-override requires CCO.')
      return
    }
    if (isHoldPass(action) && notesLen < PASS_HOLD_MIN_NOTES_CHARS) {
      alert(
        `Pass on a HOLD case requires reviewer notes ` +
        `(≥${PASS_HOLD_MIN_NOTES_CHARS} chars).`
      )
      return
    }
    setBusy(true)
    try {
      await screeningApi.postDecision(caseId, { action, notes })
      load()
    } catch (e) {
      alert(e?.response?.data?.detail || e.message)
    } finally {
      setBusy(false)
    }
  }

  if (err) return <div className="ftr-error">{err}</div>
  if (!data) return <div className="ftr-loading">Loading…</div>
  if (!canScreen) return <div className="ftr-loading">Screening access required.</div>

  const c = data.case
  const ent = data.entity
  const latest = data.events?.[data.events.length - 1]

  return (
    <section>
      <div className="ftr-crumbs">
        <Link to="/dashboard/cases">← All cases</Link>
      </div>

      <h1 className="ftr-page">Case {c.case_id}</h1>

      <div className="ftr-case-head">
        <span className={`ftr-st ${c.priority === 'high' ? 'fail' : 'run'}`}>{c.priority}</span>
        <span className={`ftr-st ${DECISION_TONE[c.decision_at_open] || ''}`}>{c.decision_at_open}</span>
        <span>Customer: <b>{c.external_customer_id || '—'}</b></span>
        <span>SDN: <b className="mono">{c.sdn_number || '—'}</b></span>
        <span>Events: <b>{c.event_count}</b></span>
        <span>Status: <b>{c.status}</b></span>
      </div>

      {latest?.top_match && (
        <div className="ftr-tablecard">
          <h3>Score breakdown — latest screening</h3>
          <table>
            <thead><tr>
              <th>Fuzzy Match</th><th>Alias Match</th>
              <th>Vector Similarity</th><th>Final Risk Score</th>
            </tr></thead>
            <tbody><tr>
              <td>{latest.top_match.display_scores?.['Fuzzy Match'] ?? '—'}</td>
              <td>{latest.top_match.display_scores?.['Alias Match'] ?? '—'}</td>
              <td>{latest.top_match.display_scores?.['Vector Similarity'] ?? '—'}</td>
              <td><b>{latest.top_match.display_scores?.['Final Risk Score'] ?? '—'}</b></td>
            </tr></tbody>
          </table>
        </div>
      )}

      {ent && (
        <div className="ftr-card">
          <h3>Matched entity</h3>
          <div className="kv">
            <span>Name</span><b>{ent.entity_name}</b>
            <span>Type</span><b>{ent.entity_type || '—'}</b>
            <span>Country</span><b>{ent.country || '—'}</b>
            <span>Nationality</span><b>{ent.nationality || '—'}</b>
            <span>Programs</span><b>{(ent.programs || []).join(', ') || '—'}</b>
            <span>Aliases</span><b>{(ent.aliases || []).slice(0, 5).join(' · ') || '—'}</b>
          </div>
          {ent.remarks && <p className="ftr-remarks">{ent.remarks}</p>}
        </div>
      )}

      <div className="ftr-tablecard">
        <h3>Decision history ({data.events?.length || 0})</h3>
        <table>
          <thead><tr>
            <th>When</th><th>Actor</th><th>Decision</th>
            <th>Score</th><th>Mode</th><th>Party</th>
          </tr></thead>
          <tbody>
            {(data.events || []).map(ev => (
              <tr key={ev.screening_id}>
                <td className="mono">{ev.created_at?.replace('T', ' ').slice(0, 19)}</td>
                <td>{ev.actor}</td>
                <td><span className={`ftr-st ${DECISION_TONE[ev.decision] || ''}`}>{ev.decision}</span></td>
                <td>{ev.final_score ?? 0}</td>
                <td className="mono">{ev.engine_mode}</td>
                <td>{ev.party_name}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {c.status !== 'cleared' && c.status !== 'closed' && (
        <div className="ftr-card">
          <h3>Record decision</h3>
          <textarea
            placeholder={
              c.decision_at_open === 'HOLD'
                ? `Reviewer notes — required (≥${PASS_HOLD_MIN_NOTES_CHARS} chars) to Pass a HOLD`
                : 'Notes — required for escalate / override'
            }
            value={notes}
            onChange={e => setNotes(e.target.value)}
            rows={4}
          />
          {c.decision_at_open === 'HOLD' && (
            <p className={`ftr-foot ${passBlockedByNotes ? 'warn' : ''}`}>
              HOLD→Pass requires a written rationale for the audit trail.
              {' '}{notesLen}/{PASS_HOLD_MIN_NOTES_CHARS} chars.
            </p>
          )}
          <div className="ftr-actions">
            <button
              className="ftr-btn"
              disabled={busy || !canEdit || passBlockedByNotes}
              title={
                !canEdit
                  ? 'CCO only'
                  : passBlockedByNotes
                    ? `Need ≥${PASS_HOLD_MIN_NOTES_CHARS} chars of notes to Pass a HOLD`
                    : ''
              }
              onClick={() => decide('pass')}
            >
              Pass (CCO override)
            </button>
            <button className="ftr-btn" disabled={busy} onClick={() => decide('review')}>
              Send to review
            </button>
            <button className="ftr-btn danger" disabled={busy} onClick={() => decide('escalate')}>
              Escalate
            </button>
          </div>
          <p className="ftr-foot">Signing as <b>{email}</b></p>
        </div>
      )}

      {(c.status === 'cleared' || c.status === 'closed') && (
        <div className="ftr-ok">
          Resolved by <b>{c.resolved_by_user_id}</b> on{' '}
          {c.resolved_at?.replace('T', ' ').slice(0, 19)}.
          {c.reviewer_notes && <em> — {c.reviewer_notes}</em>}
        </div>
      )}
    </section>
  )
}
