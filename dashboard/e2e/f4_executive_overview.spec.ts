/**
 * F4 — Executive overview navigation.
 *
 * User story: CCO arrives on the readiness landing, drills into
 * "Executive Today", then "Rules performance", all under the 3s spinner
 * budget per page transition.
 *
 * Acceptance (demo-readiness §6):
 *   - No spinner persists beyond 3s on any of the three pages.
 *   - No console.error / 4xx-5xx.
 */
import { test, expect, assertCleanJourney } from './fixtures'

test.use({ e2eRole: 'CCO' })

const STOPS = [
  { path: '/readiness',            heading: /Examination Readiness/i },
  { path: '/dashboard/executive',  heading: /Executive — Today/i },
  { path: '/dashboard/rules-perf', heading: /Rules performance/i },
]

test('F4 — CCO walks ExecutiveToday → Readiness → RulesPerf', async ({
  loggedInPage: page, consoleErrors, badResponses,
}) => {
  for (const { path, heading } of STOPS) {
    const t0 = Date.now()
    await page.goto(path)
    await expect(page.getByRole('heading', { name: heading })).toBeVisible({ timeout: 5_000 })
    const elapsed = Date.now() - t0
    expect(elapsed, `${path} must render in <3s, took ${elapsed}ms`).toBeLessThan(3_000)
  }

  await assertCleanJourney({ consoleErrors, badResponses })
})
