import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// App.jsx imports `aws-amplify/auth` at top level for fetchAuthSession; this
// pulls in the full Amplify bundle which is unhappy in jsdom. Pages reach
// AuthContext via `import { useAuth } from '../App'`, so this import is
// unavoidable on every page render. Stub it globally.
vi.mock('aws-amplify/auth', () => ({
  fetchAuthSession: vi.fn(() => Promise.resolve({ tokens: {} })),
  signOut: vi.fn(() => Promise.resolve()),
  signIn: vi.fn(() => Promise.resolve()),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// jsdom doesn't implement matchMedia or ResizeObserver — recharts pokes both.
if (!window.matchMedia) {
  window.matchMedia = () => ({
    matches: false,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })
}
if (!window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}
