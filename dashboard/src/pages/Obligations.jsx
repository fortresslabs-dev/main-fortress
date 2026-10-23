import { useEffect, useState } from 'react'
import { dashboardApi } from '../services/api'

const PAGE_SIZE = 25

export default function Obligations() {
  const [data, setData] = useState(null)
  const [open, setOpen] = useState(null)   // expanded obligation_id
  const [regFilter, setRegFilter] = useState('')
  const [page, setPage] = useState(1)

  useEffect(() => {
    dashboardApi.getObligations({ page, page_size: PAGE_SIZE, regulator: regFilter || undefined })
      .then(r => setData(r.data))
      .catch(() => setData({ total:0, covered:0, breakdown:[], items:[], total_items:0, total_pages:1, page:1 }))
  }, [page, regFilter])

  // Switching the regulator filter resets to page 1.
  const setReg = (reg) => { setRegFilter(reg); setPage(1); setOpen(null) }

  if (!data) return <div className="ftr-loading">Loading…</div>
  const uncovered = (data.total || 0) - (data.covered || 0)
  const max = data.breakdown.reduce((m, r) => Math.max(m, r.total || 0), 1)

  const totalItems = data.total_items ?? (data.items || []).length
  const totalPages = data.total_pages ?? 1
  const start = totalItems === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const end = Math.min(page * PAGE_SIZE, totalItems)

  return (
    <section>
      <h1 className="ftr-page">
        Obligations <span className="count">· {(data.total || 0).toLocaleString()} extracted</span>
      </h1>
      <p className="ftr-page-sub">Every obligation attributed to a regulator — no “unknown” bucket.</p>

      <div className="ftr-kpis three">
        <Kpi label="Total obligations" value={data.total} sub="scored from source text" />
        <Kpi label="With test coverage" value={data.covered} sub={`${data.coverage_pct ?? 0}% · rising as pipeline runs`} tone="green" />
        <Kpi label="Awaiting tests"     value={uncovered} sub="queued for generation" tone="amber" />
      </div>

      <div className="ftr-panel">
        <h3>By regulator <span className="hint">attribution enforced at intake</span></h3>
        {data.breakdown.length === 0
          ? <div className="ftr-empty" style={{padding:20}}>No obligations extracted yet.</div>
          : data.breakdown.map(r => (
              <div className="ftr-chartrow" key={r.regulator}
                   style={{cursor:'pointer', opacity: regFilter && regFilter!==r.regulator ? 0.45 : 1}}
                   onClick={() => setReg(regFilter === r.regulator ? '' : r.regulator)}
                   title="Click to filter the list below">
                <span>{r.regulator}</span>
                <div className="ftr-track">
                  <i style={{width:`${Math.round((r.total/max)*100)}%`}} />
                </div>
                <span className="pc">{r.total}</span>
              </div>
            ))
        }
      </div>

      {/* Paginated obligation read-list */}
      <div className="ftr-panel">
        <h3>
          Obligations <span className="hint">click any row to read the full text</span>
          {regFilter && (
            <button className="ftr-btn sm" style={{marginLeft:10}}
                    onClick={() => setReg('')}>clear filter: {regFilter} ✕</button>
          )}
        </h3>

        {(data.items || []).length === 0
          ? <div className="ftr-empty" style={{padding:20}}>No obligations to display.</div>
          : (data.items || []).map(o => {
              const isOpen = open === o.obligation_id
              return (
                <div key={o.obligation_id}
                     style={{borderBottom:'1px solid var(--line, #2a2a2a)'}}>
                  <div onClick={() => setOpen(isOpen ? null : o.obligation_id)}
                       style={{display:'flex', alignItems:'center', gap:10, padding:'10px 4px', cursor:'pointer'}}>
                    <span style={{color:'var(--muted)', width:14}}>{isOpen ? '▾' : '▸'}</span>
                    <span className="ftr-reg">{o.regulator}</span>
                    <span className="ftr-cfr" style={{minWidth:160}}>{o.cfr_citation || '—'}</span>
                    <span style={{flex:1, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>
                      {o.source_text}
                    </span>
                    {o.has_test_coverage
                      ? <span className="ftr-st pass">Tested</span>
                      : <span className="ftr-st wait">No test</span>}
                  </div>
                  {isOpen && (
                    <div style={{padding:'4px 28px 16px', color:'var(--fg, #ddd)', lineHeight:1.5}}>
                      <p style={{margin:'0 0 10px'}}>{o.source_text}</p>
                      <div style={{display:'flex', flexWrap:'wrap', gap:'6px 18px', fontSize:12, color:'var(--muted)'}}>
                        <span>Obligation&nbsp;ID: <span className="mono">{o.obligation_id}</span></span>
                        <span>Rule: <span className="mono">{o.regulation_id || '—'}</span></span>
                        <span>Type: {o.obligation_type || '—'}</span>
                        <span>Category: {o.category || '—'}</span>
                        {o.section_reference && <span>§ {o.section_reference}</span>}
                      </div>
                    </div>
                  )}
                </div>
              )
            })
        }

        {/* Pager */}
        {totalItems > 0 && (
          <div style={{display:'flex', alignItems:'center', justifyContent:'space-between',
                       gap:12, padding:'12px 4px 2px', fontSize:13, color:'var(--muted)'}}>
            <span>Showing {start}–{end} of {totalItems.toLocaleString()}
              {regFilter ? ` · ${regFilter}` : ''}</span>
            <div style={{display:'flex', alignItems:'center', gap:8}}>
              <button className="ftr-btn sm" disabled={page <= 1}
                      onClick={() => setPage(p => Math.max(1, p - 1))}>‹ Prev</button>
              <span>Page {page} of {totalPages}</span>
              <button className="ftr-btn sm" disabled={page >= totalPages}
                      onClick={() => setPage(p => Math.min(totalPages, p + 1))}>Next ›</button>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}

function Kpi({ label, value, sub, tone }) {
  return (
    <div className="ftr-kpi">
      <div className="lab">{label}</div>
      <div className={`val ${tone || ''}`}>{(value ?? 0).toLocaleString()}</div>
      <div className="cap">{sub}</div>
    </div>
  )
}
