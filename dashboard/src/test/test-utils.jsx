/**
 * Test render helper — wraps a component in MemoryRouter + AuthContext with
 * all caps enabled (CCO + Admin). The 14 page-smoke tests use this to render
 * any page without hitting Cognito or the network. Pair with
 * `vi.mock('../../services/api')` in each test for the page's API surface.
 */
import { render } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { AuthContext } from '../App'

const FULL_ACCESS_CTX = {
  role: 'Admin',
  email: 'cco@test.local',
  user: { signInDetails: { loginId: 'cco@test.local' } },
  canEdit: true,
  canApprove: true,
  canScreen: true,
  canViewVectorIntel: true,
  canGovernModel: true,
  onSignOut: () => {},
}

export function renderWithRouter(ui, {
  route = '/',
  path = '/',
  auth = FULL_ACCESS_CTX,
} = {}) {
  return render(
    <AuthContext.Provider value={auth}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path={path} element={ui} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>
  )
}

export { FULL_ACCESS_CTX }
