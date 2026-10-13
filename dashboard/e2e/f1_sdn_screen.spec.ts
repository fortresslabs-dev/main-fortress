/**
 * F1 — SDN screen journey.
 *
 * User story: an analyst pastes a name into the SDN search form, screens it,
 * sees a decision (PASS / REVIEW / HOLD) within a few seconds.
 *
 * Acceptance (demo-readiness §6):
 *   - Total journey <60s.
 *   - No console.error during the journey.
 *   - No 4xx/5xx on same-origin requests.
 *
 * Backend prereq: P2 reachable, screening endpoint configured. The bypass-
 * token Bearer header is set via App.jsx E2E mode (see fixtures.ts).
 */
import { test, expect, assertCleanJourney } from './fixtures'

test.use({ e2eRole: 'CCO' })

test('F1 — analyst screens a party against SDN and sees a decision', async ({
  loggedInPage: page, consoleErrors, badResponses,
}) => {
  await page.goto('/dashboard/search')
  await expect(page.getByRole('heading', { name: /SDN Screening/i })).toBeVisible()

  await page.getByLabel(/^Name/).fill('Jane Doe')
  await page.getByLabel(/Country/).fill('US')
  await page.getByRole('button', { name: /^Screen$/ }).click()

  // Decision badge shows PASS, REVIEW, or HOLD. Empty test DB returns PASS.
  const decision = page.locator('.ftr-decision')
  await expect(decision).toBeVisible({ timeout: 10_000 })
  await expect(decision).toContainText(/PASS|REVIEW|HOLD/)

  await assertCleanJourney({ consoleErrors, badResponses })
})
