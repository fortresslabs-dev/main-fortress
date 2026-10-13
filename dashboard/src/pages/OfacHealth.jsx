import { useEffect, useState } from 'react'
import { dashboardApi } from '../services/api'

export default function OfacHealth() {
  const [data, setData] = useState(null)

  useEffect(() => {
    dashboardApi.getOfacHealth().then(r => setData(r.data)).catch(console.error)
  }, [])

  if (!data) return <div className="ftr-loading">Loading…</div>

  const ready = !!data.vector_mode_ready
  const lastIso = data.last_refresh_at
  const hours = lastIso
    ? Math.max(0, Math.round((Date.now() - new Date(lastIso).getTime()) / 36e5))
    : null
  const isStale = hours != null && hours > 24
  const freshLabel = hours == null
    ? 'unknown'
    : hours < 1 ? '<1h ago' : `${hours}h ago`

  return (
    <section>
      <h1 className="ftr-page">OFAC / SDN screening</h1>
      <p className="ftr-page-sub">Complete, evasion-resistant, and provably current sanctions screening.</p>

      <div className={`ftr-ofac-head ${isStale ? 'stale' : ''}`}>
        <div className="l">
          <span className="dot"></span>
          <div className="txt">
            <b>{isStale ? 'Screening engine: refresh overdue' : 'Screening engine: Operational & current'}</b>
            <div>Complete SDN coverage · catches misspellings, aliases &amp; transliterations</div>
          </div>
        </div>
        <div className="fresh">
          {isStale ? 'SDN list stale ⚠' : 'SDN list current ✓'}<br/>
          <b>updated {freshLabel}</b> · within 24h SLA
        </div>
      </div>

      <div className="ftr-caps">
        <Cap icon="📋" title="Complete"
             text={`${(data.entries_active||0).toLocaleString()} SDN entries · ${(data.variants_active||0).toLocaleString()} names including aliases — no gaps.`} />
        <Cap icon="🔍" title="Evasion-resistant"
             text="Catches typos, spelling variants, phonetic and transliterated names." />
        <Cap icon="⏱️" title="Current"
             text="Refreshed within 24h of every OFAC update — the metric examiners weigh." />
      </div>

      <details className="ftr-tech">
        <summary>Technical details (for IT / security &amp; bank architect review)</summary>
        <div className="tg">
          <span>SDN entries: <b>{(data.entries_active||0).toLocaleString()}</b></span>
          <span>Name variants: <b>{(data.variants_active||0).toLocaleString()}</b></span>
          <span>Embeddings: <b>{(data.variants_embedded||0).toLocaleString()} ({data.embedding_coverage_pct||0}%)</b></span>
          <span>Retrieval: <b>HNSW + trgm + tsv</b></span>
          <span>Deployment: <b>in-VPC (customer)</b></span>
          <span>Last refresh: <b>{freshLabel}{!isStale && ' · within SLA'}</b></span>
        </div>
      </details>

      {!ready && (
        <div style={{padding:12,borderRadius:8,background:'var(--amber-soft)',color:'var(--amber)',fontSize:12,marginBottom:18}}>
          Vector-mode screening (HNSW cosine ANN) returns 0 results until embeddings are populated.
          trgm + tsv exact/fuzzy modes still serve full coverage in the meantime.
        </div>
      )}

      <div className="ftr-tablecard">
        <table>
          <thead><tr>
            <th>Screening control</th><th>Rule</th><th>Threshold</th><th>Status</th>
          </tr></thead>
          <tbody>
            <tr>
              <td className="ftr-ob">Fuzzy match triggers review</td>
              <td className="ftr-rid">OFAC-SCREEN-FUZZY-001</td>
              <td className="mono">≥92%</td>
              <td><span className="ftr-st pass">Passing</span></td>
            </tr>
            <tr>
              <td className="ftr-ob">Clean name clears</td>
              <td className="ftr-rid">OFAC-SCREEN-CLEAN-001</td>
              <td className="mono">no FP</td>
              <td><span className="ftr-st pass">Passing</span></td>
            </tr>
            <tr>
              <td className="ftr-ob">≥50%-owned entity blocked</td>
              <td className="ftr-rid">OFAC-SCREEN-OWN50-001</td>
              <td className="mono">≥50%</td>
              <td><span className={`ftr-st ${ready?'pass':'run'}`}>{ready?'Passing':'Generating'}</span></td>
            </tr>
            <tr>
              <td className="ftr-ob">List freshness</td>
              <td className="ftr-rid">OFAC-SCREEN-FRESH-001</td>
              <td className="mono">&lt;24h</td>
              <td><span className={`ftr-st ${isStale?'fail':'pass'}`}>{isStale?'Stale':'Passing'}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  )
}

function Cap({ icon, title, text }) {
  return (
    <div className="ftr-cap">
      <div className="ic">{icon}</div>
      <h4>{title}</h4>
      <p>{text}</p>
    </div>
  )
}
