import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright config — F1–F5 journey specs for the demo-readiness gate.
 *
 * Modes:
 *   - Local dev:  `npm run e2e`           (uses webServer below + DEV_MODE backend)
 *   - Staging:    `E2E_BASE_URL=… npm run e2e`
 *   - CI nightly: scheduled, reports on Slack — see ci-cd.yml.
 *
 * Required env before running:
 *   - Backend: PIPELINE_MODE=pipeline2, DEV_MODE=true (or DEV_MODE=true
 *     ENVIRONMENT=test for the JIRA webhook bypass), JIRA_WEBHOOK_SECRET set.
 *   - Dashboard: VITE_E2E_AUTH_BYPASS=1 (build-time, see main.jsx + App.jsx).
 *
 * Chrome-only by intent — the presenter machine is Chrome (see
 * docs/runbooks/demo-readiness.md §7).
 */
const BASE_URL = process.env.E2E_BASE_URL || 'http://localhost:5173'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  timeout: 30_000,
  expect: { timeout: 5_000 },

  use: {
    baseURL: BASE_URL,
    actionTimeout: 5_000,
    navigationTimeout: 15_000,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: 'npm run dev',
        url: BASE_URL,
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
        env: { VITE_E2E_AUTH_BYPASS: '1' },
      },
})
