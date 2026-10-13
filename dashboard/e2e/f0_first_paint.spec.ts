/**
 * F0 — Dashboard first-paint timing.
 *
 * Acceptance (demo-readiness §6.2): first-contentful-paint <2s on the
 * steady-state (warmed) dashboard. We warm with one navigation, then
 * reload and measure FCP on the second hit so a cold Vite compile
 * doesn't fail the budget.
 */
import { test, expect, assertCleanJourney } from './fixtures'

const FCP_BUDGET_MS = Number(process.env.DEMO_FCP_BUDGET_MS || 2000)

test.use({ e2eRole: 'CCO' })

test('F0 — dashboard first-contentful-paint under budget', async ({
  loggedInPage: page, consoleErrors, badResponses,
}) => {
  await page.goto('/dashboard', { waitUntil: 'load' })
  await page.reload({ waitUntil: 'load' })

  // chrome-headless-shell doesn't always have the paint entry ready at
  // load-event time, so subscribe via PerformanceObserver with buffered:true
  // and fall back to -1 only after a hard wait.
  const fcpMs = await page.evaluate(() => new Promise<number>((resolve) => {
    const seen = performance.getEntriesByName('first-contentful-paint')[0]
    if (seen) { resolve(Math.round(seen.startTime)); return }
    const obs = new PerformanceObserver((list) => {
      const entry = list.getEntries().find((e) => e.name === 'first-contentful-paint')
      if (entry) { obs.disconnect(); resolve(Math.round(entry.startTime)) }
    })
    obs.observe({ type: 'paint', buffered: true })
    setTimeout(() => { obs.disconnect(); resolve(-1) }, 3000)
  }))

  expect(
    fcpMs,
    `first-contentful-paint was ${fcpMs}ms (budget ${FCP_BUDGET_MS}ms)`
  ).toBeGreaterThan(0)
  expect(fcpMs).toBeLessThan(FCP_BUDGET_MS)

  await assertCleanJourney({ consoleErrors, badResponses })
})
