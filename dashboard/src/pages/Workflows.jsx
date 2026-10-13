import { useEffect, useState } from 'react'
import { dashboardApi } from '../services/api'

const WF_LABEL = {
  pending:'Pending', running:'Running',
  waiting_approval:'Awaiting', completed:'Completed', failed:'Failed',
}
const WF_KIND = {
  pending:'wait', running:'run', waiting_approval:'wait',
  completed:'pass', failed:'fail',
}

export default function Workflows() {
  const [wfs, setWfs] = useState([])
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    dashboardApi.getWorkflows()
      .then(r => setWfs(r.data.workflows || []))
      .finally(() => setLoading(false))
  }, [])

  const recent = wfs.filter(w => {
    const t = w.started_at && new Date(w.started_at).getTime()
    return t && t > Date.now() - 7 * 24 * 3600 * 1000
  })
  const successRate = recent.length === 0
    ? null
    : Math.round(100 * recent.filter(w => w.status === 'completed').length / recent.length)
  const avgDuration = (() => {
    const ds = recent.filter(w => w.duration_sec).map(w => w.duration_sec)
    if (!ds.length) return null
    return Math.round(ds.reduce((a,b) => a+b, 0) / ds.length)
  })()
  const openFailures = wfs.filter(w => w.status === 'failed').length

  const fmt = iso => { if (!iso || iso === 'None') return '—'; try { return new Date(iso).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}) } catch { return iso } }
  const dur = wf => { if (!wf.duration_sec) return '—'; const s = Math.round(wf.duration_sec); return s<60?`${s}s`:`${Math.floor(s/60)}m ${s%60}s` }

  return (
    <section>
      <h1 className="ftr-page">
        JIRA workflow runs <span className="count">· {wfs.length}</span>
      </h1>
      <p className="ftr-page-sub">
        Each run carries duration and a failure reason — the engine that produces your evidence.
      </p>

      <div className="ftr-kpis three">
        <div className="ftr-kpi">
          <div className="lab">Success rate (7d)</div>
          <div className={`val ${successRate==null?'':successRate>=70?'green':successRate>=40?'amber':'red'}`}>
            {successRate == null ? '—' : `${successRate}%`}
          </div>
          <div className="cap">{recent.length} runs in window</div>
        </div>
        <div className="ftr-kpi">
          <div className="lab">Avg duration</div>
          <div className="val">{avgDuration ? `${Math.floor(avgDuration/60)}m ${avgDuration%60}s` : '—'}</div>
          <div className="cap">intake → PR opened</div>
        </div>
        <div className="ftr-kpi">
          <div className="lab">Open failures</div>
          <div className={`val ${openFailures>0?'amber':'green'}`}>{openFailures}</div>
          <div className="cap">{openFailures > 0 ? 'diagnosed · retry available' : 'all clear'}</div>
        </div>
      </div>

      {loading
        ? <div className="ftr-loading">Loading…</div>
        : wfs.length === 0
          ? <div className="ftr-empty">
              <div>No workflow runs yet</div>
              <div style={{fontSize:12,marginTop:6}}>Go to Rules → Generate to start the pipeline.</div>
            </div>
          : (
            <div className="ftr-tablecard">
              <table>
                <thead><tr>
                  <th>JIRA story</th><th>Status</th><th>Started</th>
                  <th>Duration</th><th>Reason / Result</th>
                </tr></thead>
                <tbody>
                  {wfs.map(wf => {
                    const arts = typeof wf.artifacts === 'string'
                      ? safeJson(wf.artifacts) || {}
                      : wf.artifacts || {}
                    const kind = WF_KIND[wf.status] || 'wait'
                    return (
                      <tr key={wf.id}>
                        <td className="ftr-rid">{wf.jira_story_key || wf.jira_story_id}</td>
                        <td><span className={`ftr-st ${kind}`}>{WF_LABEL[wf.status] || wf.status}</span></td>
                        <td className="mono" style={{color:'var(--muted)'}}>{fmt(wf.started_at)}</td>
                        <td className="mono" style={{color:'var(--muted)'}}>{dur(wf)}</td>
                        <td>
                          {arts.github_pr_url
                            ? <a className="ftr-link" href={arts.github_pr_url} target="_blank" rel="noreferrer">View PR ↗</a>
                            : wf.failure_reason
                              ? <span style={{color:'var(--muted)'}}>{wf.failure_reason}</span>
                              : wf.status === 'running'
                                ? <span style={{color:'var(--navy)'}}>Pipeline running…</span>
                                : <span style={{color:'var(--faint)'}}>—</span>
                          }
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )
      }
    </section>
  )
}

function safeJson(s) { try { return JSON.parse(s) } catch { return null } }
