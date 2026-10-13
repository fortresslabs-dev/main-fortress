import { useEffect, useState } from 'react'
import { dashboardApi } from '../services/api'

const REGS = ['All','OCC','CFPB','FinCEN','OFAC','FDIC']
const PENALTY_SANITY_MAX = 10_000_000_000

const fmtMoney = v => v == null
  ? '—'
  : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:0 }).format(Number(v))
const fmtType = s => (s || '').replace(/_/g,' ').replace(/\b\w/g, c => c.toUpperCase())

export default function Enforcement() {
  const [actions, setActions] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [reg, setReg] = useState('All')
  const pageSize = 25

  useEffect(() => {
    const params = { page, page_size: pageSize }
    if (reg !== 'All') params.regulator = reg
    dashboardApi.getEnforcement(params).then(r => {
      setActions(r.data.actions || []); setTotal(r.data.total || 0)
    }).catch(console.error)
  }, [page, reg])

  const lastPage = Math.max(1, Math.ceil(total / pageSize))

  return (
    <section>
      <h1 className="ftr-page">
        Enforcement actions <span className="count">· {total.toLocaleString()} records</span>
      </h1>
      <p className="ftr-page-sub">
        Parsed peer enforcement — real dates, named institutions, validated penalties (CRO&apos;s leading indicator).
      </p>
      <div className="ftr-note-strip">
        Penalty parser sanity-bounded: figures above $10B held for review.
      </div>

      <div className="ftr-filters">
        <div className="grp">
          {REGS.map(r => (
            <button key={r} className={`ftr-chip ${reg===r?'on':''}`}
              onClick={() => { setReg(r); setPage(1) }}>{r}</button>
          ))}
        </div>
      </div>

      <div className="ftr-tablecard">
        <table>
          <thead><tr>
            <th>Date</th><th>Regulator</th><th>Action</th>
            <th>Institution</th><th>Summary</th><th style={{textAlign:'right'}}>Penalty</th>
          </tr></thead>
          <tbody>
            {actions.length === 0
              ? <tr><td colSpan={6} style={{padding:30,textAlign:'center',color:'var(--faint)'}}>No enforcement actions.</td></tr>
              : actions.map(a => {
                  const sane = a.penalty_amount_usd != null && Number(a.penalty_amount_usd) > PENALTY_SANITY_MAX
                  return (
                    <tr key={a.action_ref}>
                      <td className="mono" style={{whiteSpace:'nowrap'}}>{a.action_date}</td>
                      <td><span className="ftr-reg">{a.regulator}</span></td>
                      <td style={{color:'var(--ink-2)'}}>{fmtType(a.action_type)}</td>
                      <td style={{color:'var(--muted)'}}>{a.institution_name || a.institution_type || '—'}</td>
                      <td className="ftr-ob" style={{maxWidth:380,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
                        {a.source_url
                          ? <a className="ftr-link" href={a.source_url} target="_blank" rel="noreferrer">{a.violation_summary || a.action_ref}</a>
                          : (a.violation_summary || a.action_ref)}
                      </td>
                      <td className="ftr-pen" style={{textAlign:'right'}}>
                        {sane ? <>{fmtMoney(a.penalty_amount_usd)} <span className="ftr-flag">held for review</span></>
                              : fmtMoney(a.penalty_amount_usd)}
                      </td>
                    </tr>
                  )
                })}
          </tbody>
        </table>
      </div>

      {lastPage > 1 && (
        <div style={{display:'flex',justifyContent:'flex-end',alignItems:'center',gap:8,marginTop:10,fontSize:11,color:'var(--muted)'}}>
          <button className="ftr-btn sm" disabled={page<=1} onClick={() => setPage(page-1)}>‹ Prev</button>
          <span>Page {page} of {lastPage}</span>
          <button className="ftr-btn sm" disabled={page>=lastPage} onClick={() => setPage(page+1)}>Next ›</button>
        </div>
      )}
    </section>
  )
}
