import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { readinessApi } from '../services/api'
import { useAuth } from '../App'

// Map persona role → preferred lens, but allow user to switch.
const ROLE_LENS = { CCO:'cco', CRO:'cro', CIO:'cio' }
const LENS_META = {
  cco: { label:'compliance lens',     title:'CCO', sub:'compliance' },
  cro: { label:'risk lens',           title:'CRO', sub:'risk' },
  cio: { label:'infrastructure lens', title:'CIO', sub:'infrastructure' },
}
const BAND_COLOR = {
  failing:     '#c0453f',
  developing:  '#c98a18',
  operational: '#1f8f63',
  strong:      '#1f8f63',
}
const BAND_LABEL = {
  failing:'Failing', developing:'Developing',
  operational:'Operational', strong:'Strong',
}
// Risk×coverage cell visual class per spec.
function cellClass(risk, cov, count) {
  if (risk === 'high' && cov === 'untested')        return 'danger'
  if (risk === 'high' || cov === 'untested')        return 'warn'
  if (cov === 'tested')                              return 'ok'
  return 'neutral'
}
function dimColorClass(score) {
  if (score >= 70) return 'g'
  if (score >= 50) return 'a'
  return 'r'
}

export default function Readiness() {
  const { role } = useAuth()
  const navigate = useNavigate()
  const defaultLens = ROLE_LENS[role] || 'cco'
  const [lens, setLens] = useState(defaultLens)
  const [readiness, setReadiness] = useState(null)
  const [matrix, setMatrix] = useState(null)
  const [cro, setCro] = useState(null)
  const [cio, setCio] = useState(null)
  const [findings, setFindings] = useState({ findings: [] })

  useEffect(() => {
    setReadiness(null)
    readinessApi.getReadiness(lens)
      .then(r => setReadiness(r.data))
      .catch(() => setReadiness({ error:true }))
  }, [lens])

  useEffect(() => {
    Promise.all([
      readinessApi.getRiskMatrix(),
      readinessApi.getCro(),
      readinessApi.getCio(),
      readinessApi.getFindings().catch(() => ({ data:{ findings:[] } })),
    ]).then(([m, c1, c2, f]) => {
      setMatrix(m.data); setCro(c1.data); setCio(c2.data); setFindings(f.data)
    }).catch(console.error)
  }, [])

  if (!readiness) return <div className="ftr-loading">Loading readiness…</div>
  if (readiness.error) return <div className="ftr-empty">Could not load readiness score.</div>

  return (
    <section>
      <div className="ftr-topline">
        <div>
          <h1 className="ftr-page">Examination Readiness</h1>
          <p className="ftr-page-sub">One posture, three lenses · the executive landing for audit, risk &amp; compliance review</p>
        </div>
        <div className="ftr-lens">
          {['cco','cro','cio'].map(l => (
            <button key={l} className={l===lens?'on':''} onClick={() => setLens(l)}>
              {LENS_META[l].title}<small>{LENS_META[l].sub}</small>
            </button>
          ))}
        </div>
      </div>

      <div className="ftr-flow">
        <b>Flow:</b>
        <span>Readiness <span className="ar">→</span> see the gap</span>
        <span className="ar">→</span>
        <span>Rules / Obligations / OFAC <span className="ar">→</span> operate</span>
        <span className="ar">→</span>
        <span>Workflows <span className="ar">→</span> evidence produced</span>
        <span className="ar">→</span>
        <span>Reports <span className="ar">→</span> prove it to the examiner</span>
      </div>

      <Hero readiness={readiness} matrix={matrix} navigate={navigate} />

      <Dimensions dimensions={readiness.dimensions} />

      <Breakdown readiness={readiness} navigate={navigate} />

      {lens === 'cco' && <CcoLens readiness={readiness} findings={findings} />}
      {lens === 'cro' && <CroLens cro={cro} />}
      {lens === 'cio' && <CioLens cio={cio} />}
    </section>
  )
}

function Hero({ readiness, matrix, navigate }) {
  const color = BAND_COLOR[readiness.band] || '#26456b'
  const C = 2 * Math.PI * 77
  const offset = C * (1 - (readiness.score || 0) / 100)
  return (
    <div className="ftr-hero">
      <div className="ftr-panel ftr-gauge-wrap">
        <h3>Readiness score <span className="hint">{LENS_META[readiness.lens].label}</span></h3>
        <div className="ftr-gauge">
          <svg width="184" height="184" viewBox="0 0 184 184">
            <circle cx="92" cy="92" r="77" fill="none" stroke="#efeae1" strokeWidth="16"/>
            <circle cx="92" cy="92" r="77" fill="none" stroke={color}
                    strokeWidth="16" strokeLinecap="round"
                    strokeDasharray={C} strokeDashoffset={offset}/>
          </svg>
          <div className="center">
            <div className="num" style={{color}}>{readiness.score}</div>
            <div className="den">/ 100</div>
            <div className="band" style={{color}}>{BAND_LABEL[readiness.band] || readiness.band}</div>
          </div>
        </div>
        <div className="ftr-gauge-foot">
          Business read: <b style={{color:'#c0453f'}}>{readiness.biggest_drag}</b>
        </div>
      </div>

      <div className="ftr-panel">
        <h3>Risk × coverage matrix <span className="hint">where danger and gaps overlap</span></h3>
        {!matrix
          ? <div className="ftr-loading">Loading matrix…</div>
          : <MatrixGrid matrix={matrix} navigate={navigate} />
        }
        <div className="ftr-matrix-note">
          <span className="sw"></span>
          The red quadrant is the examiner&apos;s first finding — high-risk obligations with no tested control. Clearing these is the fastest readiness gain.
        </div>
      </div>
    </div>
  )
}

function MatrixGrid({ matrix, navigate }) {
  // Render in spec order: rows = high/med/low, cols = untested/partial/tested.
  const cellFor = (risk, cov) =>
    matrix.grid.find(c => c.risk === risk && c.coverage === cov)
      || { count:0, rule_ids:[] }
  const drill = ids => {
    if (!ids?.length) return
    navigate(`/rules?ids=${encodeURIComponent(ids.slice(0,20).join(','))}`)
  }
  const rowLabel = { high:'High', med:'Med', low:'Low' }
  return (
    <div className="ftr-matrix">
      <div></div>
      <div className="hd">Untested</div>
      <div className="hd">Partial</div>
      <div className="hd">Tested</div>
      {['high','med','low'].map(r => (
        <span key={r} style={{display:'contents'}}>
          <div className="yl">{rowLabel[r]}</div>
          {['untested','partial','tested'].map(c => {
            const cell = cellFor(r, c)
            const cls = cellClass(r, c, cell.count)
            const isDanger = r === 'high' && c === 'untested' && cell.count > 0
            return (
              <div key={c}
                className={`ftr-cell ${cls} ${cell.count>0?'clickable':''}`}
                onClick={() => cell.count>0 && drill(cell.rule_ids)}>
                {isDanger && <span className="tagq">⚠ danger</span>}
                <div className="c-n">{cell.count}</div>
                <div className="c-l">{r}·{c}</div>
              </div>
            )
          })}
        </span>
      ))}
    </div>
  )
}

function Dimensions({ dimensions }) {
  return (
    <div className="ftr-dims">
      {dimensions.map((d, i) => {
        const measured = d.score !== null && d.score !== undefined
        const cls = measured ? dimColorClass(d.score) : 'na'
        return (
          <div key={d.key} className={`ftr-dim ${cls}`}>
            <div className="dt">{i+1}·{d.label}</div>
            <div className="dbar"><i style={{width: measured ? `${d.score}%` : '0%'}} /></div>
            <div className="ds">{measured ? `${d.score}%` : 'N/A'}</div>
          </div>
        )
      })}
    </div>
  )
}

function Breakdown({ readiness, navigate }) {
  const [open, setOpen] = useState(false)
  // Rank by shortfall_impact desc — biggest recoverable ground first.
  // Unmeasured dims collapse to the bottom because they contribute
  // nothing to (and are excluded from) the composite until wired.
  const ranked = useMemo(() => {
    const list = (readiness.dimensions || []).map(d => ({
      ...d,
      _rank: d.shortfall_impact ?? -1,
    }))
    list.sort((a, b) => b._rank - a._rank)
    return list
  }, [readiness])

  const topGap = ranked.find(d => d.measured && d.shortfall_impact > 0)
  const topGapText = topGap
    ? `${topGap.label} · +${topGap.shortfall_impact.toFixed(1)} pts recoverable`
    : 'No shortfall — all measured dimensions at 100%'

  return (
    <div className="ftr-panel ftr-breakdown">
      <div
        className="ftr-breakdown-head"
        onClick={() => setOpen(o => !o)}
        role="button"
        tabIndex={0}
        onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && setOpen(o => !o)}
      >
        <div>
          <h3>Score breakdown <span className="hint">{LENS_META[readiness.lens].label} · biggest gap first</span></h3>
          <div className="ftr-breakdown-sub">Biggest recoverable ground: <b>{topGapText}</b></div>
        </div>
        <div className="ftr-breakdown-caret">{open ? '▾' : '▸'}</div>
      </div>
      {open && (
        <div className="ftr-breakdown-body">
          <div className="ftr-breakdown-row ftr-breakdown-h">
            <span>Dimension</span>
            <span>Weight</span>
            <span>Value</span>
            <span>Contribution</span>
            <span>Shortfall</span>
            <span>Action</span>
          </div>
          {ranked.map(d => {
            const cls = d.measured ? dimColorClass(d.score) : 'na'
            return (
              <div key={d.key} className={`ftr-breakdown-row ${cls}`}>
                <span className="l">{d.label}</span>
                <span className="w">×{d.weight?.toFixed(2)}</span>
                <span className="v">{d.measured ? `${d.score}%` : 'N/A'}</span>
                <span className="c">
                  {d.weighted_contribution === null || d.weighted_contribution === undefined
                    ? '—'
                    : `+${d.weighted_contribution.toFixed(2)}`}
                </span>
                <span className="s">
                  {d.shortfall_impact === null || d.shortfall_impact === undefined
                    ? '—'
                    : `+${d.shortfall_impact.toFixed(2)}`}
                </span>
                <span className="a">
                  {d.cta_href
                    ? <button className="ftr-breakdown-cta" onClick={() => navigate(d.cta_href)}>{d.cta_label}</button>
                    : '—'}
                </span>
              </div>
            )
          })}
          <div className="ftr-breakdown-note">
            Contribution and shortfall are in composite-score points ({LENS_META[readiness.lens].title} lens). Unmeasured dims (N/A) are excluded from the composite by renormalisation — no phantom baseline is baked in.
          </div>
        </div>
      )}
    </div>
  )
}

function CcoLens({ readiness, findings }) {
  const dims = useMemo(() =>
    Object.fromEntries((readiness.dimensions || []).map(d => [d.key, d.score])), [readiness])
  // Per-dim label map so the inventory row picks up the R10-driven
  // "Ingestion completeness" / "Applicable obligation coverage" label
  // straight from the /readiness response. Other rows keep their static
  // strings — this Row shim is view-only and hasn't earned a full
  // response-driven rebuild yet.
  const labels = useMemo(() =>
    Object.fromEntries((readiness.dimensions || []).map(d => [d.key, d.label])), [readiness])
  return (
    <div className="ftr-grid2">
      <div className="ftr-panel">
        <h3>Coverage &amp; evidence <span className="ftr-biz">avoids MRAs &amp; manual test cost</span></h3>
        <Row label={labels.inventory || "Ingestion completeness"} value={`${dims.inventory ?? 0}%`} tone={dims.inventory>=70?'good':dims.inventory>=50?'warn':'bad'} />
        <Row label="Obligation attribution"      value={`${dims.control_mapping ?? 0}%`}  tone={dims.control_mapping>=70?'good':dims.control_mapping>=50?'warn':'bad'} />
        <Row label="Tested obligations"          value={`${dims.testing ?? 0}%`}          tone={dims.testing>=70?'good':dims.testing>=30?'warn':'bad'} />
        <Row label="Change management"           value={`${dims.change_mgmt ?? 0}%`}      tone={dims.change_mgmt>=70?'good':'warn'} />
        <Row label="Governance evidence"         value={`${dims.governance ?? 0}%`}       tone={dims.governance>=70?'good':'warn'} />
      </div>
      <div className="ftr-panel">
        <h3>Open findings (MRAs) <span className="hint">most-scrutinized exam artifact</span></h3>
        {!findings.findings?.length
          ? <div style={{padding:'14px 0',fontSize:12,color:'var(--faint)'}}>
              No findings store wired up yet — the tracker will populate once the table is provisioned.
            </div>
          : findings.findings.slice(0, 6).map((f, i) => (
              <div key={i} className="ftr-row">
                <span>{f.finding_ref || f.title}</span>
                <span className={`ftr-mra ${f.status==='closed'?'closed':f.status==='in_progress'?'prog':'open'}`}>
                  {f.status}
                </span>
              </div>
            ))
        }
      </div>
    </div>
  )
}

function CroLens({ cro }) {
  if (!cro) return <div className="ftr-loading">Loading CRO lens…</div>
  return (
    <div className="ftr-grid2">
      <div className="ftr-panel">
        <h3>Residual risk vs. appetite <span className="ftr-biz">board &amp; capital governance</span></h3>
        {cro.residual_by_domain.map(d => {
          const breach = d.residual > d.appetite
          const tone = breach ? 'r' : d.residual >= d.appetite - 10 ? 'a' : ''
          return (
            <div key={d.domain} className="ftr-chartrow">
              <span>{prettyDomain(d.domain)}</span>
              <div className="ftr-track"><i className={tone} style={{width:`${d.residual}%`}} /></div>
              <span className="pc">{d.residual}/{d.appetite}</span>
            </div>
          )
        })}
      </div>
      <div className="ftr-panel">
        <h3>Key risk indicators <span className="hint">90-day trend</span></h3>
        {cro.kris.map((k, i) => (
          <Row key={i} label={k.name}
               value={`${k.value}${k.trend==='up'?' ▲':k.trend==='down'?' ▼':''}`}
               tone={k.bad?'bad':k.good?'good':k.warn?'warn':''} />
        ))}
        {cro.peer_signal?.length > 0 && (
          <div style={{marginTop:10,fontSize:11.5,color:'var(--muted)',borderTop:'1px dashed var(--line)',paddingTop:9}}>
            Peer signal: {cro.peer_signal.map(p => `${p.regulator} ${p.level}`).join(' · ')}
          </div>
        )}
      </div>
    </div>
  )
}

function CioLens({ cio }) {
  if (!cio) return <div className="ftr-loading">Loading CIO lens…</div>
  const e = cio.engine
  const a = cio.architecture
  const connOK = a.connectors.filter(c => c.ok).length
  return (
    <div className="ftr-grid2">
      <div className="ftr-panel">
        <h3>Evidence-engine integrity <span className="ftr-biz">vendor / third-party risk</span></h3>
        <Row label="Pipeline success rate (7d)"
             value={`${Math.round(e.pipeline_success_rate_7d*100)}%`}
             tone={e.pipeline_success_rate_7d>=0.8?'good':'warn'} />
        <Row label="Avg run duration"
             value={fmtDur(e.avg_duration_sec)} />
        <Row label="Index / embedding integrity"
             value={`${(e.index_integrity.embedded||0).toLocaleString()} / ${(e.index_integrity.total||0).toLocaleString()}${e.index_integrity.ok?' ✓':''}`}
             tone={e.index_integrity.ok?'good':'warn'} />
        <Row label="Uptime (30d)"
             value={`${(e.uptime_30d*100).toFixed(2)}%`} tone="good" />
        <Row label="Open failures" value={String(e.open_failures)}
             tone={e.open_failures>0?'warn':'good'} />
        {cio.certifications?.[0] && (
          <Row label="SOC 2 / certifications"
               value={cio.certifications[0].status.replace(/_/g,' ')}
               tone={cio.certifications[0].status==='active'?'good':'warn'} />
        )}
      </div>
      <div className="ftr-panel">
        <h3>Architecture &amp; data residency <span className="ftr-biz">for bank architect / TPRM</span></h3>
        <Row label="Deployment model" value="In-VPC (customer) ✓" tone="good" />
        <Row label="Customer transaction data"
             value={a.customer_data_leaves_vpc?'leaves VPC':'never leaves VPC'}
             tone={a.customer_data_leaves_vpc?'bad':'good'} />
        <Row label="Cross-boundary flow" value={a.cross_boundary_flow.replace(/_/g,' ')} tone="good" />
        <Row label={`Connectors (${a.connectors.map(c => c.name).join(' / ')})`}
             value={`${connOK} / ${a.connectors.length} healthy`}
             tone={connOK===a.connectors.length?'good':'warn'} />
        <Row label="RBAC + audit-trail immutability"
             value={a.rbac_enforced && a.audit_trail_immutable?'enforced':'partial'}
             tone={a.rbac_enforced && a.audit_trail_immutable?'good':'warn'} />
      </div>
    </div>
  )
}

function Row({ label, value, tone }) {
  return (
    <div className="ftr-row">
      <span>{label}</span>
      <span className={`v ${tone || ''}`}>{value}</span>
    </div>
  )
}

function fmtDur(sec) {
  if (!sec) return '—'
  if (sec < 60) return `${sec}s`
  return `${Math.floor(sec/60)}m ${sec%60}s`
}

function prettyDomain(k) {
  return ({
    sanctions:'Sanctions / OFAC',
    bsa_aml:'BSA / AML',
    consumer:'Consumer / fair lending',
    privacy:'Privacy / data',
    licensing:'State licensing',
  })[k] || k
}
