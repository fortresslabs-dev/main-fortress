import { Routes, Route, Navigate } from 'react-router-dom'
import { fetchAuthSession } from 'aws-amplify/auth'
import { createContext, useContext, useEffect, useState } from 'react'
import Readiness from './pages/Readiness'
import Reports from './pages/Reports'
import Rules from './pages/Rules'
import Changes from './pages/Changes'
import Workflows from './pages/Workflows'
import OfacHealth from './pages/OfacHealth'
import Enforcement from './pages/Enforcement'
import Obligations from './pages/Obligations'
import SdnSearch from './pages/SdnSearch'
import Cases from './pages/Cases'
import Investigation from './pages/Investigation'
import RulesPerf from './pages/RulesPerf'
import ExecutiveToday from './pages/ExecutiveToday'
import VectorIntel from './pages/VectorIntel'
import Navbar from './components/Navbar'
import api from './services/api'

export const AuthContext = createContext({
  role: 'viewer',
  canEdit: false,
  canApprove: false,
  canScreen: false,
  canViewVectorIntel: false,
  canGovernModel: false,
})
export const useAuth = () => useContext(AuthContext)

// Same gate as main.jsx — env flag + localhost. Playwright sets role +
// token in localStorage via addInitScript before each spec.
const E2E_BYPASS = import.meta.env.VITE_E2E_AUTH_BYPASS === '1'
                && typeof window !== 'undefined'
                && window.location.hostname === 'localhost'

export default function App({ cognitoUser, onSignOut }) {
  const [role, setRole] = useState('viewer')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (E2E_BYPASS) {
      const r = window.localStorage.getItem('__e2e_role') || 'CCO'
      const token = window.localStorage.getItem('__e2e_token') || 'e2e-bypass'
      setRole(r)
      api.defaults.headers.common['Authorization'] = `Bearer ${token}`
      // Backend dev-token path hardcodes CCO unless X-E2E-Role tells it
      // otherwise — required for F5 (Admin) and any future Analyst-only
      // journey specs.
      api.defaults.headers.common['X-E2E-Role'] = r
      setLoading(false)
      return
    }
    // Role derives from groups[0]. Token attachment is handled per-request
    // by the axios interceptor in services/api.js so refresh-rotated tokens
    // don't get stuck on the axios default headers.
    fetchAuthSession().then(s => {
      const groups = s.tokens?.idToken?.payload?.['cognito:groups'] || []
      setRole(groups[0] || 'viewer')
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [cognitoUser])

  if (loading) return <div className="ftr-loading">Loading…</div>

  const ctx = {
    role, onSignOut,
    user: cognitoUser,
    canEdit:            ['CCO','Admin','admin'].includes(role),
    canApprove:         ['CCO','CRO','Admin','admin'].includes(role),
    canScreen:          ['Analyst','CCO','CRO','Admin','admin'].includes(role),
    canViewVectorIntel: ['Admin','CRO','admin'].includes(role),
    canGovernModel:     ['Admin','admin'].includes(role),
    email: cognitoUser?.signInDetails?.loginId || '',
  }

  return (
    <AuthContext.Provider value={ctx}>
      <div className="ftr-app">
        <Navbar />
        <main className="ftr-main">
          <Routes>
            <Route path="/"          element={<Navigate to="/readiness" replace />} />
            <Route path="/overview"  element={<Navigate to="/readiness" replace />} />
            <Route path="/readiness" element={<Readiness />} />
            <Route path="/rules"     element={<Rules />} />
            <Route path="/changes"   element={<Changes />} />
            <Route path="/workflows" element={<Workflows />} />
            <Route path="/enforcement" element={<Enforcement />} />
            <Route path="/obligations" element={<Obligations />} />
            <Route path="/ofac"      element={<OfacHealth />} />
            <Route path="/reports"   element={<Reports />} />
            {/* SDN screening surface (sdn-dashboard-completion.md Phase 5) */}
            <Route path="/dashboard/search"      element={<SdnSearch />} />
            <Route path="/dashboard/cases"       element={<Cases />} />
            <Route path="/dashboard/cases/:caseId" element={<Investigation />} />
            <Route path="/dashboard/rules-perf"  element={<RulesPerf />} />
            <Route path="/dashboard/executive"   element={<ExecutiveToday />} />
            <Route path="/dashboard/vector"      element={<VectorIntel />} />
            <Route path="/auth/callback" element={<Navigate to="/readiness" replace />} />
          </Routes>
        </main>
      </div>
    </AuthContext.Provider>
  )
}
