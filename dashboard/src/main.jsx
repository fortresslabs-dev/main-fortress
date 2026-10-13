import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { Amplify } from 'aws-amplify'
import { Authenticator } from '@aws-amplify/ui-react'
import '@aws-amplify/ui-react/styles.css'
import './styles/theme.css'
import App from './App'

Amplify.configure({
  Auth: {
    Cognito: {
      userPoolId:       import.meta.env.VITE_COGNITO_USER_POOL_ID,
      userPoolClientId: import.meta.env.VITE_COGNITO_CLIENT_ID,
      loginWith: {
        oauth: {
          domain:         import.meta.env.VITE_COGNITO_DOMAIN,
          scopes:         ['openid', 'email', 'profile'],
          redirectSignIn:  [`${window.location.origin}/auth/callback`],
          redirectSignOut: [`${window.location.origin}/auth/logout`],
          responseType: 'code',
        }
      }
    }
  }
})

// Playwright journey specs need to mount App without going through Cognito.
// Gated tight: VITE_E2E_AUTH_BYPASS=1 at build time AND hostname=localhost
// at runtime. The runtime check makes a stray prod build with the flag set
// inert because the hostname will be something else. Specs control role via
// localStorage (see App.jsx) so different journeys can exercise different
// RBAC paths.
const E2E_BYPASS = import.meta.env.VITE_E2E_AUTH_BYPASS === '1'
                && typeof window !== 'undefined'
                && window.location.hostname === 'localhost'

const Tree = E2E_BYPASS
  ? <App
      cognitoUser={{ signInDetails: { loginId: 'e2e@test.local' } }}
      onSignOut={() => {}}
    />
  : <Authenticator hideSignUp loginMechanisms={['email']}>
      {({ signOut, user }) => <App cognitoUser={user} onSignOut={signOut} />}
    </Authenticator>

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>{Tree}</BrowserRouter>
  </StrictMode>
)
