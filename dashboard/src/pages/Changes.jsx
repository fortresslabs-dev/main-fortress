import { useEffect, useState } from 'react'
import { dashboardApi } from '../services/api'
import { useAuth } from '../App'

const SEV_CLS = { high:'high', medium:'med', low:'low' }

const PAGE_SIZE = 20

export default function Changes() {
  const { canApprove } = useAuth()
  const [changes, setChanges] = useState([])
  const [total, setTotal] = useState(0)
  const [openTotal, setOpenTotal] = useState(0)
  const [aging, setAging] = useState({ fresh:0, aging:0, overdue:0 })
  const [bucket, setBucket] = useState(null)  // null | 'fresh' | 'aging' | 'overdue'
  const [page, setPage] = useState(1)
  const [signOff, setSignOff] = useState(null)
  const [decision, setDecision] = useState('acknowledged')
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(true)
  const [toast, setToast] = useState(null)   // { kind: 'ok'|'err', text }
  const [detailRef, setDetailRef] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState(null)
  const [selected, setSelected] = useState(() => new Set())
  const [bulk, setBulk] = useState(false)     // bulk-signoff modal open

  const load = () => {
    setLoading(true)
    const params = { page, page_size: PAGE_SIZE }
    if (bucket) params.bucket = bucket
    dashboardApi.getChanges(params)
      .then(r => {
        const arr = r.data.changes || []
        setChanges(arr)
        // Backwards-compatible with the pre-pagination backend that returns
        // only `changes`. Fall back to counts derived from the page payload
        // so the header doesn't regress to "0 unsigned".
        const apiTotal = r.data.total
        const apiOpen  = r.data.open_total
        setTotal(typeof apiTotal === 'number' ? apiTotal : arr.length)
        setOpenTotal(typeof apiOpen === 'number'
          ? apiOpen
          : arr.filter(c => c.status === 'detected').length)
        if (r.data.aging) setAging(r.data.aging)
      })
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [page, bucket])
  // Bucket change resets pagination — otherwise a filter that shrinks
  // the result set can land the user on a page that no longer exists.
  useEffect(() => { setPage(1); setSelected(new Set()) }, [bucket])

  useEffect(() => {
    if (!detailRef) { setDetail(null); setDetailError(null); return }
    setDetailLoading(true)
    setDetail(null)
    setDetailError(null)
    dashboardApi.getChangeDetail(detailRef)
      .then(r => setDetail(r.data))
      .catch(e => setDetailError(
        e.response?.data?.detail || e.message || 'unable to load detail'
      ))
      .finally(() => setDetailLoading(false))
  }, [detailRef])

  const handleSignOff = async () => {
    setSaving(true)
    const ref = signOff.change_ref
    try {
      await dashboardApi.signOff(ref, { decision, note })
      setSignOff(null)
      setToast({ kind:'ok', text:`Signed off ${ref} (${decision.replace(/_/g,' ')})` })
      load()
      if (detailRef === ref) setDetailRef(null)
    } catch (e) {
      const detail = e.response?.data?.detail
                  || e.response?.statusText
                  || e.message
                  || 'unknown error'
      const status = e.response?.status ? ` [HTTP ${e.response.status}]` : ''
      setToast({ kind:'err', text:`Sign-off failed for ${ref}${status}: ${detail}` })
    } finally {
      setSaving(false)
    }
  }

  const handleBulkSignOff = async () => {
    setSaving(true)
    const refs = Array.from(selected)
    try {
      const r = await dashboardApi.signOffBulk({ refs, decision, note })
      const s = r.data || {}
      const parts = []
      if (s.signed?.length)         parts.push(`${s.signed.length} signed`)
      if (s.already_signed?.length) parts.push(`${s.already_signed.length} already signed`)
      if (s.not_found?.length)      parts.push(`${s.not_found.length} not found`)
      setBulk(false)
      setSelected(new Set())
      setToast({ kind:'ok', text:`Bulk sign-off · ${parts.join(', ') || 'no changes'}` })
      load()
    } catch (e) {
      const detail = e.response?.data?.detail
                  || e.response?.statusText
                  || e.message
                  || 'unknown error'
      const status = e.response?.status ? ` [HTTP ${e.response.status}]` : ''
      setToast({ kind:'err', text:`Bulk sign-off failed${status}: ${detail}` })
    } finally {
      setSaving(false)
    }
  }

  const toggleRef = (ref) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(ref)) next.delete(ref)
      else               next.add(ref)
      return next
    })
  }
  // "Select all unsigned on this page" — only detected rows are selectable
  // because signed rows have no signoff action to bulk.
  const unsignedRefs = changes.filter(c => c.status === 'detected').map(c => c.change_ref)
  const allSelectedOnPage = unsignedRefs.length > 0
                         && unsignedRefs.every(r => selected.has(r))
  const toggleAllOnPage = () => {
    setSelected(prev => {
      const next = new Set(prev)
      if (allSelectedOnPage) unsignedRefs.forEach(r => next.delete(r))
      else                   unsignedRefs.forEach(r => next.add(r))
      return next
    })
  }

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), toast.kind === 'ok' ? 4000 : 8000)
    return () => clearTimeout(t)
  }, [toast])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <section>
      <h1 className="ftr-page">
        Regulatory change inbox <span className="count">· {openTotal} unsigned</span>
      </h1>
      <p className="ftr-page-sub">
        Deduplicated and translated to obligation impact — the change-management exam pillar.
        {total > 0 && ` · showing ${changes.length} of ${total.toLocaleString()} (page ${page}/${totalPages})`}
      </p>

      {toast && (
        <div role="alert" onClick={() => setToast(null)}
             style={{
               margin:'8px 0 12px', padding:'10px 14px', borderRadius:8, fontSize:13,
               cursor:'pointer',
               background: toast.kind === 'ok' ? '#e6f4ec' : '#fbe7e5',
               color:      toast.kind === 'ok' ? '#1f8f63' : '#a8322c',
               border: `1px solid ${toast.kind === 'ok' ? '#bfe2cf' : '#f1c2bd'}`,
             }}>
          {toast.kind === 'ok' ? '✓ ' : '✗ '}{toast.text}
          <span style={{float:'right',opacity:0.6,fontSize:11}}>dismiss</span>
        </div>
      )}

      {canApprove && (
        <div className="ftr-change-toolbar">
          <div className="l">
            <label style={{display:'flex',alignItems:'center',gap:6,fontSize:12,cursor:'pointer'}}>
              <input type="checkbox"
                checked={allSelectedOnPage}
                onChange={toggleAllOnPage}
                disabled={unsignedRefs.length === 0} />
              Select unsigned on page ({unsignedRefs.length})
            </label>
            <div className="buckets" role="tablist" aria-label="Aging bucket">
              <button className={bucket === null ? 'on' : ''}
                      onClick={() => setBucket(null)}>All</button>
              <button className={bucket === 'fresh' ? 'on' : ''}
                      onClick={() => setBucket('fresh')}>Fresh<span className="cnt">{aging.fresh}</span></button>
              <button className={bucket === 'aging' ? 'on' : ''}
                      onClick={() => setBucket('aging')}>Aging<span className="cnt">{aging.aging}</span></button>
              <button className={bucket === 'overdue' ? 'on' : ''}
                      onClick={() => setBucket('overdue')}>Overdue<span className="cnt">{aging.overdue}</span></button>
            </div>
          </div>
          <div className="r">
            {selected.size > 0 && (
              <button className="ftr-btn sm"
                      onClick={() => setSelected(new Set())}
                      title="Clear selection">Clear ({selected.size})</button>
            )}
            <button className="ftr-btn"
                    disabled={selected.size === 0}
                    onClick={() => { setBulk(true); setDecision('acknowledged'); setNote('') }}>
              Sign off ({selected.size})
            </button>
          </div>
        </div>
      )}

      {loading
        ? <div className="ftr-loading">Loading changes…</div>
        : changes.length === 0
          ? <div className="ftr-empty">No regulatory changes detected.</div>
          : changes.map(c => {
              const sev = (c.impact_rating || 'medium').toLowerCase()
              const sevCls = SEV_CLS[sev] || 'med'
              const isDetected = c.status === 'detected'
              const isSelected = selected.has(c.change_ref)
              const age = c.aging_bucket   // null on signed rows
              return (
                <div key={c.change_ref}
                     className={`ftr-change ${sev==='high'?'high':sev==='low'?'low':''} ${isSelected?'selected':''}`}>
                  {canApprove && isDetected && (
                    <input type="checkbox" className="sel"
                           checked={isSelected}
                           onChange={() => toggleRef(c.change_ref)}
                           onClick={e => e.stopPropagation()}
                           aria-label={`Select ${c.change_ref}`} />
                  )}
                  <div style={{flex:1, cursor:'pointer'}}
                       onClick={() => setDetailRef(c.change_ref)}
                       role="button"
                       title="View full diff">
                    <div className="meta">
                      <span className={`ftr-sev ${sevCls}`}>{sev}</span>
                      <span className="ftr-badge">{c.regulator}</span>
                      {c.rule_id && <span className="ftr-badge">{c.rule_id}</span>}
                      <span className="ftr-badge">{(c.status || 'detected').replace(/_/g,' ')}</span>
                      {age && (
                        <span className={`ftr-age ${age}`}
                              title={c.age_days != null ? `${c.age_days.toFixed(1)} days since detected` : ''}>
                          {age}{c.age_days != null && ` · ${Math.round(c.age_days)}d`}
                        </span>
                      )}
                    </div>
                    <div className="title">{(c.change_summary || '').slice(0, 120)}</div>
                    <div className="desc">{(c.semantic_diff || '').slice(0, 240) || '—'}</div>
                    <div className="foot">{c.change_ref} · {fmtDate(c.created_at)} · <span style={{textDecoration:'underline'}}>view diff</span></div>
                  </div>
                  {canApprove && isDetected && (
                    <button className="ftr-btn"
                      onClick={() => { setSignOff(c); setDecision('acknowledged'); setNote('') }}>
                      Sign off
                    </button>
                  )}
                </div>
              )
            })
      }

      {!loading && totalPages > 1 && (
        <div style={{display:'flex',gap:10,alignItems:'center',justifyContent:'center',
                     padding:'12px 0',fontSize:12}}>
          <button className="ftr-btn sm"
                  disabled={page <= 1}
                  onClick={() => setPage(p => Math.max(1, p - 1))}>← Prev</button>
          <span style={{color:'var(--muted)'}}>Page {page} of {totalPages}</span>
          <button className="ftr-btn sm"
                  disabled={page >= totalPages}
                  onClick={() => setPage(p => Math.min(totalPages, p + 1))}>Next →</button>
        </div>
      )}

      {detailRef && (
        <ChangeDetailDrawer
          changeRef={detailRef}
          detail={detail}
          loading={detailLoading}
          error={detailError}
          canApprove={canApprove}
          onClose={() => setDetailRef(null)}
          onSignOff={() => {
            const c = changes.find(x => x.change_ref === detailRef) || detail
            if (c) { setSignOff(c); setDecision('acknowledged'); setNote('') }
          }}
        />
      )}

      {signOff && (
        <div className="ftr-modal" onClick={() => setSignOff(null)}>
          <div className="ftr-modal-body" onClick={e => e.stopPropagation()}>
            <h3>Sign off — {signOff.change_ref}</h3>
            <p style={{fontSize:12,color:'var(--muted)',marginBottom:10}}>{signOff.change_summary}</p>
            <select value={decision} onChange={e => setDecision(e.target.value)}
              style={{marginBottom:10}}>
              <option value="acknowledged">Acknowledged — no action required</option>
              <option value="requires_action">Requires action — create JIRA epic</option>
              <option value="not_applicable">Not applicable to this bank</option>
            </select>
            <textarea rows={3} value={note} onChange={e => setNote(e.target.value)}
              placeholder="Optional note for audit trail" />
            <div className="ftr-modal-actions">
              <button className="cancel" onClick={() => setSignOff(null)}>Cancel</button>
              <button className="confirm" onClick={handleSignOff} disabled={saving}>
                {saving ? 'Saving…' : 'Submit sign-off'}
              </button>
            </div>
          </div>
        </div>
      )}

      {bulk && (
        <div className="ftr-modal" onClick={() => !saving && setBulk(false)}>
          <div className="ftr-modal-body" onClick={e => e.stopPropagation()}>
            <h3>Bulk sign-off — {selected.size} record{selected.size === 1 ? '' : 's'}</h3>
            <p style={{fontSize:12,color:'var(--muted)',marginBottom:10}}>
              One decision applied to every selected record. Refs already
              signed off will be reported back but not double-counted.
            </p>
            <div style={{maxHeight:120,overflow:'auto',fontFamily:'IBM Plex Mono,monospace',
                         fontSize:11,color:'var(--faint)',background:'var(--paper-2)',
                         border:'1px solid var(--line)',borderRadius:6,padding:'6px 8px',
                         marginBottom:10}}>
              {Array.from(selected).slice(0, 40).join(', ')}
              {selected.size > 40 && ` … +${selected.size - 40} more`}
            </div>
            <select value={decision} onChange={e => setDecision(e.target.value)}
              style={{marginBottom:10}}>
              <option value="acknowledged">Acknowledged — no action required</option>
              <option value="requires_action">Requires action — create JIRA epic</option>
              <option value="not_applicable">Not applicable to this bank</option>
            </select>
            <textarea rows={3} value={note} onChange={e => setNote(e.target.value)}
              placeholder="Optional note (applied to every ref in this batch)" />
            <div className="ftr-modal-actions">
              <button className="cancel" onClick={() => setBulk(false)} disabled={saving}>Cancel</button>
              <button className="confirm" onClick={handleBulkSignOff} disabled={saving}>
                {saving ? 'Signing…' : `Sign off ${selected.size} record${selected.size === 1 ? '' : 's'}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

function ChangeDetailDrawer({ changeRef, detail, loading, error, canApprove, onClose, onSignOff }) {
  const rule = detail?.rule || {}
  // Per-record delta lives on detail.change_delta (migration 037). Legacy
  // rows synced before that column existed leave it NULL — surface that
  // honestly instead of falling back to compliance_rules.change_delta,
  // which always reflects the rule's *latest* mutation, not this record's.
  const delta = detail?.change_delta || null
  const added = Array.isArray(delta?.added) ? delta.added : []
  const removed = Array.isArray(delta?.removed) ? delta.removed : []
  const deltaMissing = delta === null
  const showSignOff = canApprove && detail?.status === 'detected'

  return (
    <div className="ftr-modal" onClick={onClose}>
      <div className="ftr-modal-body"
           onClick={e => e.stopPropagation()}
           style={{maxWidth: 880, width: '92vw', maxHeight: '88vh', overflow: 'auto'}}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'baseline',marginBottom:8}}>
          <h3 style={{margin:0}}>{changeRef}</h3>
          <button className="ftr-btn sm" onClick={onClose}>Close</button>
        </div>

        {loading && <div className="ftr-loading">Loading detail…</div>}
        {error && <div className="ftr-empty" style={{color:'#a8322c'}}>Error: {error}</div>}

        {detail && (
          <>
            <div className="meta" style={{marginBottom:10}}>
              <span className={`ftr-sev ${SEV_CLS[(detail.impact_rating || 'medium').toLowerCase()] || 'med'}`}>
                {(detail.impact_rating || 'medium').toLowerCase()}
              </span>
              <span className="ftr-badge">{detail.regulator}</span>
              {detail.rule_id && <span className="ftr-badge">{detail.rule_id}</span>}
              <span className="ftr-badge">{(detail.change_type || '—')}</span>
              <span className="ftr-badge">{(detail.status || 'detected').replace(/_/g,' ')}</span>
            </div>

            <p style={{fontSize:13, margin:'6px 0 14px'}}>{detail.change_summary || '—'}</p>

            {deltaMissing ? (
              <DetailSection title="Diff">
                <div style={{fontSize:12, color:'var(--muted)',
                             background:'#fbf6e7', border:'1px solid #f0e2a8',
                             padding:'8px 10px', borderRadius:4}}>
                  Per-record diff not preserved for this change (detected
                  before migration 037). The summary above and the
                  current/previous rule text below are still accurate.
                </div>
                {detail.semantic_diff && (
                  <pre style={{whiteSpace:'pre-wrap', fontSize:12, margin:'8px 0 0'}}>
                    {detail.semantic_diff}
                  </pre>
                )}
              </DetailSection>
            ) : (
              <>
                <DetailSection title={`Added sentences (${added.length})`}>
                  {added.length === 0 ? <em style={{color:'var(--muted)'}}>none</em> :
                    <ul style={{margin:0,paddingLeft:18}}>
                      {added.map((s, i) => (
                        <li key={i} style={{background:'#e6f4ec', padding:'4px 8px',
                             borderRadius:4, margin:'3px 0', fontSize:13}}>
                          {String(s)}
                        </li>
                      ))}
                    </ul>}
                </DetailSection>

                <DetailSection title={`Removed sentences (${removed.length})`}>
                  {removed.length === 0 ? <em style={{color:'var(--muted)'}}>none</em> :
                    <ul style={{margin:0,paddingLeft:18}}>
                      {removed.map((s, i) => (
                        <li key={i} style={{background:'#fbe7e5', padding:'4px 8px',
                             borderRadius:4, margin:'3px 0', fontSize:13,
                             textDecoration:'line-through', opacity:0.85}}>
                          {String(s)}
                        </li>
                      ))}
                    </ul>}
                </DetailSection>

                {detail.semantic_diff && (added.length === 0 && removed.length === 0) && (
                  <DetailSection title="Semantic diff">
                    <pre style={{whiteSpace:'pre-wrap', fontSize:12, margin:0}}>
                      {detail.semantic_diff}
                    </pre>
                  </DetailSection>
                )}
              </>
            )}

            <DetailSection title="Current rule text">
              {rule.rule_text
                ? <pre style={{whiteSpace:'pre-wrap',fontSize:12,margin:0,
                              maxHeight:240,overflow:'auto'}}>{rule.rule_text}</pre>
                : <em style={{color:'var(--muted)'}}>rule no longer active or not found</em>}
            </DetailSection>

            <DetailSection title="Previous rule text">
              {rule.previous_content
                ? <pre style={{whiteSpace:'pre-wrap',fontSize:12,margin:0,
                              maxHeight:240,overflow:'auto',
                              background:'#f7f7f7',padding:6,borderRadius:4}}>
                    {rule.previous_content}
                  </pre>
                : <em style={{color:'var(--muted)'}}>no prior version recorded</em>}
            </DetailSection>

            <div style={{fontSize:11,color:'var(--muted)',marginTop:14,
                         display:'flex',gap:14,flexWrap:'wrap'}}>
              {rule.cfr_citation && (() => {
                const ecfr = ecfrUrl(rule.cfr_citation)
                return ecfr
                  ? <span>{rule.cfr_citation} · <a className="ftr-link" href={ecfr} target="_blank" rel="noreferrer">eCFR ↗</a></span>
                  : <span>{rule.cfr_citation}</span>
              })()}
              {rule.source_url && !/ffiec\.gov/i.test(rule.source_url) && (
                <a href={rule.source_url} target="_blank" rel="noreferrer">
                  source ↗
                </a>
              )}
              {detail.signed_off_at && <span>signed off {fmtDate(detail.signed_off_at)} by {detail.reviewer_name || '—'}</span>}
            </div>

            {showSignOff && (
              <div className="ftr-modal-actions" style={{marginTop:16}}>
                <button className="cancel" onClick={onClose}>Close</button>
                <button className="confirm" onClick={onSignOff}>Sign off…</button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function DetailSection({ title, children }) {
  return (
    <div style={{margin:'10px 0'}}>
      <div style={{fontSize:11,textTransform:'uppercase',letterSpacing:0.4,
                   color:'var(--muted)',marginBottom:4}}>{title}</div>
      {children}
    </div>
  )
}

function fmtDate(iso) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleDateString() } catch { return iso }
}

// Build a stable eCFR URL from a CFR citation like
//   "31 C.F.R. § 1020.220"  →  https://www.ecfr.gov/current/title-31/section-1020.220
//   "12 CFR 1010.100"       →  https://www.ecfr.gov/current/title-12/section-1010.100
// eCFR is the authoritative version of the regulation and never bot-gates,
// so we prefer it over `source_url` whenever the citation parses. Returns
// null when the citation doesn't match the expected shape — we leave the
// raw citation visible in that case so nothing silently disappears.
export function ecfrUrl(citation) {
  if (!citation) return null
  const m = /(\d+)\s*C\.?\s*F\.?\s*R\.?\s*(?:§|Sec\.?|Section)?\s*(\d+(?:\.\d+)?)/i.exec(citation)
  if (!m) return null
  return `https://www.ecfr.gov/current/title-${m[1]}/section-${m[2]}`
}
