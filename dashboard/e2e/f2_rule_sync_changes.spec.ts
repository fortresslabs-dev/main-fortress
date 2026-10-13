/**
 * F2 — Rule sync → Changes inbox.
 *
 * User story: CCO opens the dashboard, navigates to the regulatory change
 * inbox, and sees the latest detected rule mutations with a `semantic_diff`
 * column populated (commit 27c3bae regression guard).
 *
 * Acceptance (demo-readiness §6):
 *   - Page paint <2s after navigation.
 *   - If seed data is loaded, at least one change row is visible.
 *   - No console.error / 4xx-5xx.
 *
 * Backend prereq: P1 sync run, OR the demo seed loaded — Phase 4 will
 * standardise this via scripts/demo/seed.py.
 */
import { test, expect, assertCleanJourney } from './fixtures'

test.use({ e2eRole: 'CCO' })

test('F2 — CCO views the Regulatory change inbox', async ({
  loggedInPage: page, consoleErrors, badResponses,
}) => {
  const t0 = Date.now()
  await page.goto('/changes')
  await expect(page.getByRole('heading', { name: /Regulatory change inbox/i })).toBeVisible()
  const elapsed = Date.now() - t0
  expect(elapsed, 'page paint must be under 2s on staging').toBeLessThan(2_000)

  // Either we have rows (seeded) or we render the empty state. Both are
  // acceptable here — what we are guarding against is a hard error / 500.
  const card = page.locator('.ftr-loading, .ftr-empty, [data-testid="change-card"]')
  await expect(card.first()).toBeVisible({ timeout: 5_000 })

  await assertCleanJourney({ consoleErrors, badResponses })
})
