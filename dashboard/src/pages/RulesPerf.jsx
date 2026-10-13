import { useEffect, useState } from 'react'
import { screeningApi } from '../services/api'
import { useAuth } from '../App'

export default function RulesPerf() {
  const { canApprove, canGovernModel } = useAuth()
  const [data, setData] = useState(null)
  const [engineMode, setEngineMode] = useState('')   // '' | 'legacy' | 'vector'
  const [editingRule, setEditingRule] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    setData(null)
    const params = engineMode ? { engine_mode: engineMode } : undefined
    screeningApi.getRulesPerf(params).then(r => setData(r.data)).catch(console.error)
  }, [engineMode, reloadKey])

  if (!canApprove) return <div className="ftr-loading">CCO / CRO access required.</div>
  if (!data) return <div className="ftr-loading">Loading…</div>

  const { rules, totals, by_decision } = data
  const fpRate = totals.alerts ? Math.round((totals.false_positives / totals.alerts) * 100) : 0
  const tpRate = totals.alerts ? Math.round((totals.true_positives  / totals.alerts) * 100) : 0

  return (
    <section>
      <h1 className="ftr-page">Rules performance</h1>
      <p className="ftr-page-sub">
        Alert volume, true / false positive rates, and per-rule status across the
        OFAC screening controls.
      </p>

      <div className="ftr-form" style={{ marginBottom: 12 }}>
        <label>
          Engine mode{' '}
          <select
            data-testid="engine-mode-filter"
            value={engineMode}
            onChange={e => setEngineMode(e.target.value)}
          >
            <option value="">All</option>
            <option value="legacy">Legacy (SequenceMatcher)</option>
            <option value="vector">Vector (pgvector + trgm)</option>
          </select>
        </label>
        {engineMode && (
          <span className="ftr-hint" style={{ marginLeft: 8 }}>
            Showing cases opened under <b>{engineMode}</b> only — useful during the
            legacy→vector A/B so engine-driven precision swings don't get blamed
            on a rule.
          </span>
        )}
      </div>

      <div className="ftr-caps">
        <Cap title="Alerts (lifetime)" value={totals.alerts} />
        <Cap title="True positives" value={`${totals.true_positives} · ${tpRate}%`} />
        <Cap title="False positives" value={`${totals.false_positives} · ${fpRate}%`} />
        <Cap title="Open" value={totals.open} />
        <Cap title="Precision" value={totals.precision == null ? '—' : totals.precision} />
      </div>

      <div className="ftr-tablecard">
        <h3>Rule inventory</h3>
        <p className="ftr-page-sub">
          <b>Policy weight</b> is display-only for FUZZY / OWN50 / CLEAN —
          scoring is governed by{' '}
          <code>OFAC_HOLD_THRESHOLD</code> and per-class SSM thresholds at{' '}
          <code>/fortress/p2/ofac/threshold/*</code>. The FRESH weight is
          engine-active: <code>&gt; 0</code> enables the staleness bump,{' '}
          <code>≤ 0</code> disables it.
        </p>
        <table>
          <thead><tr>
            <th>Rule</th>
            <th>Name</th>
            <th>Policy weight</th>
            <th>Description</th>
            <th>Status</th>
            {canGovernModel && <th>Action</th>}
          </tr></thead>
          <tbody>
            {rules.map(r => (
              <tr key={r.rule_id}>
                <td className="mono">{r.rule_id}</td>
                <td>{r.name}</td>
                <td>{r.weight}</td>
                <td className="ftr-cell-desc">{r.description || '—'}</td>
                <td>
                  <span className={`ftr-st ${r.is_active === false ? 'fail' : 'pass'}`}>
                    {r.is_active === false ? 'inactive' : 'active'}
                  </span>
                </td>
                {canGovernModel && (
                  <td>
                    <button
                      className="ftr-link"
                      data-testid={`edit-rule-${r.rule_id}`}
                      onClick={() => setEditingRule(r)}
                    >
                      Edit
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editingRule && (
        <EditRuleModal
          rule={editingRule}
          onClose={() => setEditingRule(null)}
          onSaved={() => {
            setEditingRule(null)
            setReloadKey(k => k + 1)
          }}
        />
      )}

      <div className="ftr-tablecard">
        <h3>Alert volume by opening decision</h3>
        <table>
          <thead><tr><th>Decision</th><th>Count</th></tr></thead>
          <tbody>
            {by_decision.map(r => (
              <tr key={r.decision}><td>{r.decision}</td><td>{r.n}</td></tr>
            ))}
            {by_decision.length === 0 && (
              <tr><td colSpan={2} className="ftr-empty">No cases yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function Cap({ title, value }) {
  return (
    <div className="ftr-cap">
      <h4>{title}</h4>
      <p style={{ fontSize: 22, fontWeight: 600 }}>{value}</p>
    </div>
  )
}

function EditRuleModal({ rule, onClose, onSaved }) {
  const [weight, setWeight] = useState(String(rule.weight ?? ''))
  const [isActive, setIsActive] = useState(rule.is_active !== false)
  const [description, setDescription] = useState(rule.description || '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const save = async () => {
    setSaving(true); setError(null)
    try {
      const body = {}
      const w = Number(weight)
      if (!Number.isNaN(w) && w !== Number(rule.weight)) body.weight = w
      if (isActive !== (rule.is_active !== false)) body.is_active = isActive
      if (description !== (rule.description || '')) body.description = description
      if (Object.keys(body).length === 0) {
        onClose()
        return
      }
      await screeningApi.patchScreeningRule(rule.rule_id, body)
      onSaved()
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="ftr-modal" role="dialog" aria-labelledby="edit-rule-title">
      <div className="ftr-modal-card" data-testid="edit-rule-modal">
        <h3 id="edit-rule-title">Edit rule — <span className="mono">{rule.rule_id}</span></h3>
        <p className="ftr-page-sub">
          Version <b>{rule.version}</b> · changes audited under your email and
          bump the version monotonically.
        </p>
        <div className="ftr-form">
          <label>
            Policy weight
            <input
              type="number" step="0.1" min="0" max="10"
              value={weight} onChange={e => setWeight(e.target.value)}
              data-testid="edit-rule-weight"
            />
          </label>
          <label>
            <input
              type="checkbox" checked={isActive}
              onChange={e => setIsActive(e.target.checked)}
              data-testid="edit-rule-active"
            />
            {' '}Active (uncheck to deactivate)
          </label>
          <label>
            Description
            <textarea
              rows={3} maxLength={1000}
              value={description} onChange={e => setDescription(e.target.value)}
              data-testid="edit-rule-description"
            />
          </label>
        </div>
        {error && <div className="ftr-error">{error}</div>}
        <div className="ftr-modal-actions">
          <button className="ftr-btn" onClick={onClose} disabled={saving}>Cancel</button>
          <button className="confirm" onClick={save} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
