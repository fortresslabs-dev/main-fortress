import { useEffect, useState } from 'react'
import { exportsApi } from '../services/api'
import { useAuth } from '../App'

// Tile list matches the 6 report types in src/exports/policy.yaml — when
// a new type is added there, add a tile here.
const TILES = [
  { type:'examiner_package', tag:'Required', tagCls:'req',
    title:'Examiner evidence package',
    desc:"PBC response: obligation inventory, mapped control, test + threshold + rationale, result, JIRA→PR trace.",
    formats:['pdf','csv'] },
  { type:'ofac_attestation', tag:'Required', tagCls:'req',
    title:'OFAC screening attestation',
    desc:'Dated proof of freshness within SLA, tested screening controls, completeness counts.',
    formats:['pdf','csv'] },
  { type:'board_report', tag:'Governance', tagCls:'opt',
    title:'Board / audit-committee report',
    desc:'Quarterly (fiscal): coverage trend, changes signed off, open findings, OFAC health.',
    formats:['pdf','csv'] },
  { type:'mra_tracker', tag:'Tracker', tagCls:'opt',
    title:'Findings / MRA remediation',
    desc:'Every finding with owner, deadline, status, evidence of closure.',
    formats:['pdf','csv'] },
  { type:'change_log', tag:'Change mgmt', tagCls:'opt',
    title:'Regulatory change log',
    desc:'Dated, signed-off record of changes and disposition.',
    formats:['pdf','csv'] },
  { type:'coverage', tag:'Self-assessment', tagCls:'opt',
    title:'Coverage report',
    desc:'Mapped rules vs tested rules — the mock-exam tool.',
    formats:['pdf','csv'] },
]

function bankIdFromHost() {
  // Convention: dashboard subdomain bank-{id}.fortresslabs.dev — fall back
  // to the env override for local dev. The backend cross-checks bank_id
  // against the JWT claim so a spoof can't read another bank's data.
  const m = (window.location.hostname || '').match(/^bank-([a-z0-9]+)\./)
  return m?.[1] || (import.meta.env.VITE_BANK_ID || 'testbank')
}

export default function Reports() {
  const { canApprove } = useAuth()
  const bankId = bankIdFromHost()
  const [asOf, setAsOf] = useState(new Date().toISOString().slice(0,10))
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [artifacts, setArtifacts] = useState([])
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    exportsApi.list({ bank_id: bankId }).then(r => setArtifacts(r.data.artifacts || []))
      .catch(() => setArtifacts([]))
  }, [bankId, reloadKey])

  const run = async (type, format) => {
    if (!canApprove) {
      setError('CCO or CRO role required to generate exports.')
      return
    }
    setError(null)
    setBusy(`${type}:${format}`)
    try {
      const { data } = await exportsApi.create({
        bank_id: bankId, type, format, as_of: asOf,
      })
      if (data.status === 'rendering') {
        // PDF rendering is async — exports-worker writes the artifact row
        // when done. Refresh the list so the user can download once ready.
        setTimeout(() => setReloadKey(k => k + 1), 2500)
      } else if (data.bucket) {
        // CSV path: the artifact is in S3 and the DB row exists. Refresh
        // the list so the new row appears alongside history.
        setReloadKey(k => k + 1)
      }
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Export failed')
    } finally {
      setBusy(null)
    }
  }

  const download = async (artifactId, format) => {
    try {
      const r = await exportsApi.download(artifactId)
      const fname = (r.headers['content-disposition'] || '')
        .match(/filename="?([^"]+)"?/)?.[1] || `fortress_${artifactId}.${format}`
      const url = URL.createObjectURL(r.data)
      const a = document.createElement('a')
      a.href = url; a.download = fname; a.click()
      setTimeout(() => URL.revokeObjectURL(url), 30000)
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Download failed')
    }
  }

  return (
    <section>
      <h1 className="ftr-page">
        Examination evidence &amp; reports <span className="count">· WORM-locked</span>
      </h1>
      <p className="ftr-page-sub">Frozen, dated, tamper-evident artifacts — what an exam actually runs on.</p>

      <div className="ftr-note-strip">
        Each artifact is hashed before render, written once with Object-Lock COMPLIANCE, and re-running
        the same as-of returns the existing object (idempotent). Bucket: <code>fortress-p2-exports-{bankId}-*</code>.
      </div>

      <div className="ftr-panel">
        <div className="ftr-exp-head">
          <div className="ftr-asof">
            <span>Generate “as of”:</span>
            <input type="date" value={asOf} onChange={e => setAsOf(e.target.value)} />
          </div>
          <div style={{fontSize:11.5,color:'var(--faint)'}}>
            Bank: <code>{bankId}</code> · Signing matrix enforced server-side.
          </div>
        </div>

        {!canApprove && (
          <div style={{margin:'10px 0',padding:'10px 12px',borderRadius:8,
            background:'var(--amber-soft)',color:'var(--amber)',fontSize:12}}>
            View-only role — exports require CCO or CRO permissions.
          </div>
        )}
        {error && (
          <div style={{margin:'10px 0',padding:'10px 12px',borderRadius:8,
            background:'var(--red-soft)',color:'var(--red)',fontSize:12}}>
            {error}
          </div>
        )}

        <div className="ftr-exports">
          {TILES.map(t => (
            <div key={t.type} className={`ftr-ex ${t.tagCls === 'req' ? 'req':''}`}>
              <span className={`tag ${t.tagCls}`}>{t.tag}</span>
              <h4>{t.title}</h4>
              <p>{t.desc}</p>
              <div className="acts">
                {t.formats.map(fmt => (
                  <button key={fmt}
                    className={fmt}
                    disabled={!canApprove || busy === `${t.type}:${fmt}`}
                    onClick={() => run(t.type, fmt)}>
                    {busy === `${t.type}:${fmt}` ? '…' : fmt.toUpperCase()}
                  </button>
                ))}
              </div>
              <div className="meta">type · {t.type}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="ftr-panel" style={{marginTop:24}}>
        <h2 style={{margin:'0 0 12px 0'}}>Recent artifacts</h2>
        {artifacts.length === 0 ? (
          <p style={{color:'var(--faint)',fontSize:12,margin:0}}>
            No artifacts yet. Generate one above.
          </p>
        ) : (
          <table className="ftr-table" style={{width:'100%'}}>
            <thead>
              <tr>
                <th>Type</th><th>As-of</th><th>Format</th><th>Version</th>
                <th>Generated</th><th>Retain until</th><th></th>
              </tr>
            </thead>
            <tbody>
            {artifacts.map(a => (
              <tr key={a.artifact_id}>
                <td>{a.type}</td>
                <td>{a.as_of}</td>
                <td>{a.format.toUpperCase()}</td>
                <td><code>v.{a.version_hash}</code></td>
                <td style={{fontSize:11.5,color:'var(--faint)'}}>{new Date(a.generated_at).toLocaleString()}</td>
                <td style={{fontSize:11.5,color:'var(--faint)'}}>{new Date(a.object_lock_retain_until).toLocaleDateString()}</td>
                <td>
                  <button className="csv" onClick={() => download(a.artifact_id, a.format)}>
                    Download
                  </button>
                </td>
              </tr>
            ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}
