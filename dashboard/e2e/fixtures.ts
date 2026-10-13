import { test as base, expect, type Page, type ConsoleMessage, type Response } from '@playwright/test'

/**
 * Shared fixtures for F1–F5 journey specs.
 *
 * Provides:
 *   - `loggedInPage` — Page with localStorage pre-seeded for the E2E auth
 *     bypass (set role via test.use({ e2eRole }) or default CCO).
 *   - `consoleErrors` — array auto-populated with any console.error events
 *     during the test. Demo-readiness acceptance: array must be empty at
 *     end of journey.
 *   - `badResponses` — array auto-populated with any 4xx/5xx HTTP responses
 *     from same-origin requests during the test.
 *
 * Use `await assertCleanJourney({ consoleErrors, badResponses })` at the
 * end of each spec to enforce both acceptance criteria from
 * docs/runbooks/demo-readiness.md §3.
 */

type Fixtures = {
  e2eRole: string
  loggedInPage: Page
  consoleErrors: ConsoleMessage[]
  badResponses: Response[]
}

export const test = base.extend<Fixtures>({
  e2eRole: ['CCO', { option: true }],

  loggedInPage: async ({ page, e2eRole }, use) => {
    await page.addInitScript((role) => {
      window.localStorage.setItem('__e2e_role', role)
      window.localStorage.setItem('__e2e_token', 'e2e-bypass-token')
    }, e2eRole)
    await use(page)
  },

  consoleErrors: async ({ page }, use) => {
    const errors: ConsoleMessage[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg)
    })
    await use(errors)
  },

  badResponses: async ({ page }, use) => {
    const bad: Response[] = []
    page.on('response', (resp) => {
      const status = resp.status()
      const url = resp.url()
      // Skip cross-origin (Cognito 401s during the auth bypass etc.) — only
      // assert on our own API + dashboard surface.
      const isOurs = url.includes('localhost') || url.startsWith('/')
      if (isOurs && status >= 400) bad.push(resp)
    })
    await use(bad)
  },
})

export { expect }

export async function assertCleanJourney(
  { consoleErrors, badResponses }: { consoleErrors: ConsoleMessage[]; badResponses: Response[] }
) {
  if (consoleErrors.length) {
    const lines = consoleErrors.map((e) => `  - ${e.text()}`).join('\n')
    throw new Error(`Unexpected console.error events during journey:\n${lines}`)
  }
  if (badResponses.length) {
    const lines = badResponses
      .map((r) => `  - ${r.status()} ${r.request().method()} ${r.url()}`)
      .join('\n')
    throw new Error(`Unexpected 4xx/5xx responses during journey:\n${lines}`)
  }
}
