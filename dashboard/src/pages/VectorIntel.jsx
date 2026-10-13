import { useEffect, useState } from 'react'
import { screeningApi } from '../services/api'
import { useAuth } from '../App'

/**
 * Admin-only model governance view. This is the ONE page where raw
 * subscores (cosine floats, embedding counts, similarity buckets) are
 * surfaced — Phase 6 guards keep them off every Analyst-facing page.
 */
export default function VectorIntel() {
  const { canGovernModel, canViewVectorIntel } = useAuth()
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    if (!canViewVectorIntel) return
    screeningApi.getVectorIntel()
      .then(r => setData(r.data))
      .catch(e => setErr(e?.response?.data?.detail || e.message))
  }, [canViewVectorIntel])

  if (!canViewVectorIntel) return <div className="ftr-loading">Vector Intelligence — Admin / CRO only.</div>
  if (err) return <div className="ftr-error">{err}</div>
  if (!data) return <div className="ftr-loading">Loading…</div>

  const cov = data.embedding_coverage || {}
  const pct = cov.active_variants ? Math.round((cov.embedded / cov.active_variants) * 100) : 0

  return (
    <section>
      <h1 className="ftr-page">Vector Intelligence</h1>
      <p className="ftr-page-sub">
        Model governance view — embedding coverage, similarity distribution,
        and engine-mode mix. {canGovernModel ? 'Admin write actions enabled.' : 'Read-only.'}
      </p>

      <div className="ftr-caps">
        <Cap title="Active SDN variants" value={cov.active_variants?.toLocaleString() || 0} />
        <Cap title="Embedded variants" value={`${(cov.embedded || 0).toLocaleString()} · ${pct}%`} />
      </div>

      <div className="ftr-tablecard">
        <h3>Final score distribution — last 30 days (matches only)</h3>
        <table>
          <thead><tr><th>Bucket (0–1)</th><th>Range</th><th>Hits</th></tr></thead>
          <tbody>
            {(data.similarity_buckets || []).map(b => (
              <tr key={b.bucket}>
                <td>{b.bucket}</td>
                <td>{((b.bucket - 1) / 10).toFixed(1)} – {(b.bucket / 10).toFixed(1)}</td>
                <td>{b.count.toLocaleString()}</td>
              </tr>
            ))}
            {(data.similarity_buckets || []).length === 0 && (
              <tr><td colSpan={3} className="ftr-empty">No matches recorded in the last 30 days.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="ftr-tablecard">
        <h3>Top screened names with matches — last 30 days</h3>
        <table>
          <thead><tr><th>Party</th><th>Hits</th><th>Avg final</th></tr></thead>
          <tbody>
            {(data.top_variants || []).map(v => (
              <tr key={v.name}>
                <td>{v.name}</td>
                <td>{v.hits}</td>
                <td className="mono">{v.avg_score}</td>
              </tr>
            ))}
            {(data.top_variants || []).length === 0 && (
              <tr><td colSpan={3} className="ftr-empty">No data yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="ftr-tablecard">
        <h3>Engine mode — last 7 days</h3>
        <table>
          <thead><tr><th>Mode</th><th>Calls</th></tr></thead>
          <tbody>
            {(data.engine_mode_split || []).map(m => (
              <tr key={m.mode}><td className="mono">{m.mode}</td><td>{m.count}</td></tr>
            ))}
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
      <p style={{ fontSize: 24, fontWeight: 700 }}>{value}</p>
    </div>
  )
}
