import { useEffect, useState } from 'react'
import { screeningApi } from '../services/api'
import { useAuth } from '../App'

export default function ExecutiveToday() {
  const { canEdit } = useAuth()                // CCO check
  const [data, setData] = useState(null)

  useEffect(() => {
    screeningApi.getExecutiveToday().then(r => setData(r.data)).catch(console.error)
  }, [])

  if (!canEdit) return <div className="ftr-loading">CCO access required.</div>
  if (!data) return <div className="ftr-loading">Loading…</div>

  const last = data.last_list_update
  const hours = last
    ? Math.max(0, Math.round((Date.now() - new Date(last).getTime()) / 36e5))
    : null

  return (
    <section>
      <h1 className="ftr-page">Executive — Today</h1>
      <p className="ftr-page-sub">Board-level view of OFAC screening for the current day.</p>

      <div className="ftr-caps">
        {data.tiles.map(t => (
          <div key={t.key} className="ftr-cap">
            <h4>{t.label}</h4>
            <p style={{ fontSize: 28, fontWeight: 700 }}>{t.value.toLocaleString()}</p>
          </div>
        ))}
      </div>

      <div className="ftr-ofac-head" style={{ marginTop: 24 }}>
        <div className="l">
          <span className="dot"></span>
          <div className="txt">
            <b>SDN list freshness</b>
            <div>OFAC SLA: within 24h of source publication.</div>
          </div>
        </div>
        <div className="fresh">
          {hours == null ? 'unknown' : hours < 1 ? '< 1h ago' : `${hours}h ago`}
        </div>
      </div>
    </section>
  )
}
