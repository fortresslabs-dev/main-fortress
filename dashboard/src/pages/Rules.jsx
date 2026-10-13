import { useEffect, useState, useCallback, Fragment } from 'react'
import { useSearchParams } from 'react-router-dom'
import { dashboardApi, testGenApi } from '../services/api'
import { useAuth } from '../App'

const REGS = ['All','FinCEN','OCC','CFPB','FDIC','OFAC','FFIEC','CA-DFPI','TX-DOB','FL-OFR']

function riskClass(s) {
  if (s == null) return 'lo'
  if (s >= 0.8) return 'hi'
  if (s >= 0.6) return 'md'
  return 'lo'
}

export default function Rules() {
  const { canEdit } = useAuth()
  const [params] = useSearchParams()
  const idFilter = params.get('ids') || ''
  const [rules, setRules] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [reg, setReg] = useState('All')
  const [jur, setJur] = useState('All')          // All | Federal | State
  const [sev, setSev] = useState('All')          // All | Critical | High | Medium | Low
  const [scope, setScope] = useState('applicable')  // applicable | all
  const [allTotal, setAllTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [genModal, setGenModal] = useState(null)
  const [naModal, setNaModal] = useState(null)
  const [naReason, setNaReason] = useState('')
  const [genLoading, setGenLoading] = useState(false)
  const [genResult, setGenResult] = useState(null)
  const [override, setOverride] = useState('')
  const [openRow, setOpenRow] = useState(null)   // expanded rule_id (read full text)

  const PAGE_SIZE = 100

  const load = useCallback(() => {
    setLoading(true)
    dashboardApi.getRules({
      regulator: reg==='All'?undefined:reg,
      severity: sev==='All'?undefined:sev,
      scope,
      page, page_size: PAGE_SIZE,
    })
      .then(r => {
        setRules(r.data.rules||[])
        setTotal(r.data.total||0)
        setAllTotal(r.data.all_total||0)
      })
      .finally(() => setLoading(false))
  }, [reg, sev, scope, page])

  useEffect(() => { load() }, [load])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const idSet = idFilter
    ? new Set(idFilter.split(',').map(s => s.trim()).filter(Boolean))
    : null
  const visibleRules = rules.filter(r => {
    if (idSet && !idSet.has(r.rule_id)) return false
    if (jur === 'Federal' && /^(CA|TX|FL|NY|WA)-/.test(r.rule_id || '')) return false
    if (jur === 'State'   && !/^(CA|TX|FL|NY|WA)-/.test(r.rule_id || '')) return false
    return true
  })

  const handleGenerate = async () => {
    if (!genModal) return
    setGenLoading(true)
    try {
      const r = await testGenApi.generate(genModal.rule_id, { threshold_override: override||undefined })
      setGenResult(r.data)
    } catch (e) {
      setGenResult({ error: e.response?.data?.detail||e.message })
    } finally {
      setGenLoading(false)
    }
  }

  const handleNA = async () => {
    if (!naModal || !naReason) return
    await dashboardApi.updateRule(naModal.rule_id, { status:'not_applicable', na_reason:naReason })
    setNaModal(null); setNaReason(''); load()
  }

  return (
    <section>
      <h1 className="ftr-page">Rules <span className="count">· {total.toLocaleString()}</span></h1>
      <p className="ftr-page-sub">
        {scope === 'applicable'
          ? 'Applicable rules for this institution, ranked by severity then risk.'
          : 'Full corpus — every rule, including non-applicable.'}
        {idFilter && ` · filtered to ${idSet.size} from risk matrix`}
        {total > 0 && ` · showing ${visibleRules.length} of ${total.toLocaleString()}`}
        {scope === 'applicable' && allTotal > total && ` applicable (${allTotal.toLocaleString()} total)`}
        {total > 0 && ` · page ${page}/${totalPages}`}
      </p>

      <div className="ftr-filters">
        <span className="glab">Jurisdiction</span>
        <div className="grp">
          {['All','Federal','State'].map(j => (
            <button key={j} className={`ftr-chip ${jur===j?'on':''}`}
                    onClick={() => { setJur(j); setPage(1) }}>{j}</button>
          ))}
        </div>
        <span className="glab" style={{marginLeft:6}}>Regulator</span>
        <div className="grp">
          {REGS.map(r => (
            <button key={r} className={`ftr-chip ${reg===r?'on':''}`}
                    onClick={() => { setReg(r); setPage(1) }}>{r}</button>
          ))}
        </div>
        <span className="glab" style={{marginLeft:6}}>Severity</span>
        <div className="grp">
          {['All','Critical','High','Medium','Low'].map(s => (
            <button key={s} className={`ftr-chip ${sev===s?'on':''}`}
                    onClick={() => { setSev(s); setPage(1) }}>{s}</button>
          ))}
        </div>
        <span className="glab" style={{marginLeft:6}}>Scope</span>
        <div className="grp">
          <button className={`ftr-chip ${scope==='applicable'?'on':''}`}
                  onClick={() => { setScope('applicable'); setPage(1) }}>Applicable only</button>
          <button className={`ftr-chip ${scope==='all'?'on':''}`}
                  onClick={() => { setScope('all'); setPage(1) }}>
            Show all{allTotal ? ` (${allTotal.toLocaleString()})` : ''}
          </button>
        </div>
      </div>

      {loading
        ? <div className="ftr-loading">Loading rules…</div>
        : (
          <div className="ftr-tablecard">
            <table>
              <thead><tr>
                <th>Rule ID</th><th>Regulator</th><th>CFR</th>
                <th>Obligation</th><th>Severity</th><th>Risk</th><th>Status</th><th>Actions</th>
              </tr></thead>
              <tbody>
                {visibleRules.length === 0
                  ? <tr><td colSpan={8} style={{padding:30,textAlign:'center',color:'var(--faint)'}}>No rules match.</td></tr>
                  : visibleRules.map(rule => {
                    const tested  = rule.test_count > 0
                    const excluded = rule.bank_status === 'not_applicable'
                    return (
                      <Fragment key={rule.rule_id}>
                      <tr>
                        <td className="ftr-rid">{rule.rule_id}</td>
                        <td><span className="ftr-reg">{rule.regulator}</span></td>
                        <td className="ftr-cfr">{rule.cfr_citation || '—'}</td>
                        <td className="ftr-ob" title="Click to read full text"
                            style={{maxWidth:280,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',cursor:'pointer'}}
                            onClick={() => setOpenRow(openRow===rule.rule_id?null:rule.rule_id)}>
                          <span style={{color:'var(--muted)',marginRight:6}}>{openRow===rule.rule_id?'▾':'▸'}</span>
                          {(rule.rule_text || rule.regulation_name || '').slice(0,140)}
                        </td>
                        <td>
                          {rule.severity
                            ? <span style={{
                                padding:'2px 8px', borderRadius:10, fontSize:12, fontWeight:600,
                                color:'#fff', background:{
                                  Critical:'#c0392b', High:'#e67e22',
                                  Medium:'#b8860b', Low:'#7f8c8d',
                                }[rule.severity] || '#7f8c8d',
                              }}>{rule.severity}</span>
                            : <span style={{color:'var(--faint)'}}>—</span>}
                        </td>
                        <td>
                          <span className={`ftr-risk ${riskClass(rule.enforcement_risk_score)}`}>
                            {rule.enforcement_risk_score != null
                              ? Number(rule.enforcement_risk_score).toFixed(2)
                              : '—'}
                          </span>
                        </td>
                        <td>
                          {excluded
                            ? <span className="ftr-st wait">Excluded</span>
                            : tested
                              ? <span className="ftr-st pass">Tested</span>
                              : <span className="ftr-st wait">Not tested</span>}
                        </td>
                        <td>
                          {canEdit && !excluded && (
                            <>
                              <button className="ftr-btn sm"
                                onClick={() => { setGenModal(rule); setGenResult(null); setOverride('') }}>
                                {tested ? 'Regenerate' : 'Generate ↗'}
                              </button>
                              <button className="ftr-btn na sm"
                                onClick={() => { setNaModal(rule); setNaReason('') }}>
                                N/A
                              </button>
                            </>
                          )}
                        </td>
                      </tr>
                      {openRow===rule.rule_id && (
                        <tr>
                          <td colSpan={8} style={{padding:'4px 20px 16px', background:'var(--panel, #16181d)'}}>
                            <p style={{margin:'0 0 10px', lineHeight:1.55}}>
                              {rule.rule_text || rule.regulation_name || '—'}
                            </p>
                            <div style={{display:'flex', flexWrap:'wrap', gap:'6px 18px', fontSize:12, color:'var(--muted)'}}>
                              <span>Rule&nbsp;ID: <span className="mono">{rule.rule_id}</span></span>
                              <span>Citation: {rule.cfr_citation || '—'}</span>
                              <span>Severity: {rule.severity || '—'}</span>
                              <span>Risk: {rule.enforcement_risk_score != null ? Number(rule.enforcement_risk_score).toFixed(2) : '—'}</span>
                              <span>Status: {excluded ? 'Excluded' : tested ? 'Tested' : 'Not tested'}</span>
                              {rule.na_reason && <span>N/A reason: {rule.na_reason}</span>}
                            </div>
                          </td>
                        </tr>
                      )}
                      </Fragment>
                    )
                  })
                }
              </tbody>
            </table>
            {totalPages > 1 && (
              <div className="ftr-pager"
                   style={{display:'flex',gap:10,alignItems:'center',justifyContent:'center',
                           padding:'12px 0',fontSize:12}}>
                <button className="ftr-btn sm"
                        disabled={page <= 1 || loading}
                        onClick={() => setPage(p => Math.max(1, p - 1))}>← Prev</button>
                <span style={{color:'var(--muted)'}}>Page {page} of {totalPages}</span>
                <button className="ftr-btn sm"
                        disabled={page >= totalPages || loading}
                        onClick={() => setPage(p => Math.min(totalPages, p + 1))}>Next →</button>
              </div>
            )}
          </div>
        )
      }

      {genModal && !genResult && (
        <Modal onClose={() => setGenModal(null)}>
          <h3>Generate test — {genModal.rule_id}</h3>
          <div style={{background:'var(--paper-2)',borderRadius:8,padding:10,marginBottom:10,fontSize:11.5}}>
            <div><span style={{color:'var(--muted)'}}>Regulator:</span> {genModal.regulator}
              &nbsp;&nbsp;<span style={{color:'var(--muted)'}}>Risk:</span> {(genModal.enforcement_risk_score||0).toFixed(2)}</div>
            <div className="mono" style={{marginTop:4,fontSize:10.5}}>{genModal.cfr_citation}</div>
          </div>
          <input value={override} onChange={e => setOverride(e.target.value)}
                 placeholder="Bank threshold override (optional)" />
          <div style={{margin:'10px 0',padding:10,borderRadius:8,background:'var(--navy-soft)',color:'var(--navy)',fontSize:11.5}}>
            6-agent pipeline runs automatically. GitHub PR within ~5 min.
          </div>
          <div className="ftr-modal-actions">
            <button className="cancel" onClick={() => setGenModal(null)}>Cancel</button>
            <button className="confirm" onClick={handleGenerate} disabled={genLoading}>
              {genLoading ? 'Creating…' : 'Create JIRA story ↗'}
            </button>
          </div>
        </Modal>
      )}

      {genModal && genResult && (
        <Modal onClose={() => { setGenModal(null); setGenResult(null) }}>
          {genResult.error
            ? <>
                <h3 style={{color:'var(--red)'}}>Failed</h3>
                <p style={{fontSize:12,color:'var(--muted)'}}>{genResult.error}</p>
              </>
            : <>
                <h3 style={{color:'var(--emerald)'}}>Story created ✓</h3>
                <p style={{fontSize:12,marginBottom:4}}>JIRA:&nbsp;
                  <a className="ftr-link" href={genResult.jira_url} target="_blank" rel="noreferrer">{genResult.jira_key}</a>
                </p>
                <p style={{fontSize:11,color:'var(--muted)'}}>{genResult.message}</p>
              </>
          }
          <div className="ftr-modal-actions">
            <button className="cancel" onClick={() => { setGenModal(null); setGenResult(null) }}>Close</button>
          </div>
        </Modal>
      )}

      {naModal && (
        <Modal onClose={() => setNaModal(null)}>
          <h3>Mark N/A — {naModal.rule_id}</h3>
          <textarea rows={4} value={naReason} onChange={e => setNaReason(e.target.value)}
                    placeholder="Reason (required for audit trail)..." />
          <div className="ftr-modal-actions">
            <button className="cancel" onClick={() => setNaModal(null)}>Cancel</button>
            <button className="confirm" onClick={handleNA} disabled={!naReason}>Confirm N/A</button>
          </div>
        </Modal>
      )}
    </section>
  )
}

function Modal({ children, onClose }) {
  return (
    <div className="ftr-modal" onClick={onClose}>
      <div className="ftr-modal-body" onClick={e => e.stopPropagation()}>
        {children}
      </div>
    </div>
  )
}
