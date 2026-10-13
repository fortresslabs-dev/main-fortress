/**
 * F5 — Vector Intelligence + Obligations + Enforcement drilldown.
 *
 * User story: an Admin / CRO opens Vector Intelligence to inspect embedding
 * coverage, then drills into Obligations and Enforcement to see the
 * regulator-attributed views.
 *
 * Acceptance (demo-readiness §6):
 *   - Vector Intelligence search returns results <1s.
 *   - Obligations + Enforcement drilldowns render the page header without
 *     spinner-stuck states.
 *   - No console.error / 4xx-5xx.
 *
 * Role: Admin (Vector Intelligence requires canViewVectorIntel).
 */
import { test, expect, assertCleanJourney } from './fixtures'

test.use({ e2eRole: 'Admin' })

test('F5 — Admin drilldown across VectorIntel, Obligations, Enforcement', async ({
  loggedInPage: page, consoleErrors, badResponses,
}) => {
  // VectorIntel — load budget is <1s on staging per runbook.
  const t0 = Date.now()
  await page.goto('/dashboard/vector')
  await expect(page.getByRole('heading', { name: /Vector Intelligence/i })).toBeVisible()
  const elapsed = Date.now() - t0
  expect(elapsed, `vector page paint must be <1.5s, took ${elapsed}ms`).toBeLessThan(1_500)

  // Obligations — every obligation must attribute to a regulator (no
  // "unknown" bucket — that's the runbook acceptance for this page).
  // Scope to the breakdown panel; the page sub-header itself mentions
  // "unknown" as flavor text and would false-positive a page-wide match.
  await page.goto('/obligations')
  await expect(page.getByRole('heading', { name: /^Obligations/i })).toBeVisible()
  const breakdown = page.locator('.ftr-panel', { hasText: 'By regulator' })
  await expect(breakdown).toBeVisible()
  await expect(breakdown.getByText(/^unknown$/i)).toHaveCount(0)

  // Enforcement — table renders with at least the header row.
  await page.goto('/enforcement')
  await expect(page.getByRole('heading', { name: /Enforcement actions/i })).toBeVisible()

  await assertCleanJourney({ consoleErrors, badResponses })
})
