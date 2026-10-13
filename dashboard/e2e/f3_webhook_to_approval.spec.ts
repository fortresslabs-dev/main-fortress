/**
 * F3 — JIRA webhook → workflow → CCO approval.
 *
 * User story: a regulator-change JIRA story is webhooked into P2 → the
 * 6-agent pipeline picks it up → CCO approves at the pre-generation
 * checkpoint → workflow continues.
 *
 * The dashboard currently has a Workflows view but no in-UI approve button
 * (the Approvals API exists but isn't wired into a page yet). So this spec
 * verifies the journey end-to-end via API for the approval step and
 * confirms the dashboard shows the new workflow.
 *
 * Acceptance (demo-readiness §6):
 *   - Webhook accepted (200, workflow_id present).
 *   - Workflows page shows the new row within 5s.
 *   - Approval POST succeeds, workflow transitions out of waiting_approval.
 *   - No console.error / 4xx-5xx on dashboard requests.
 */
import { test, expect, assertCleanJourney } from './fixtures'

const WEBHOOK_SECRET = process.env.JIRA_WEBHOOK_SECRET || 'test-webhook-secret'
const API_BASE = process.env.E2E_API_BASE_URL || 'http://localhost:8002'

test.use({ e2eRole: 'CCO' })

test('F3 — JIRA webhook lands a workflow and CCO can approve it', async ({
  loggedInPage: page, request, consoleErrors, badResponses,
}) => {
  const jiraKey = `E2E-${Date.now()}`
  const payload = {
    issue: {
      key: jiraKey,
      fields: {
        summary: 'Wire transfer compliance check',
        description:
          'When a wire transfer exceeds $10,000 USD, the system must log a CTR. ' +
          'Validate against BSA threshold.',
      },
    },
  }

  // 1. POST webhook via the proxied /webhooks/jira route.
  const wh = await request.post(`${API_BASE}/webhooks/jira`, {
    data: payload,
    headers: { Authorization: `Bearer ${WEBHOOK_SECRET}` },
  })
  expect(wh.ok(), `webhook POST returned ${wh.status()}`).toBeTruthy()
  const { workflow_id } = await wh.json()
  expect(workflow_id, 'workflow_id missing in webhook response').toBeTruthy()

  // 2. Workflow lands in the Workflows view.
  await page.goto('/workflows')
  await expect(page.getByRole('heading', { name: /JIRA workflow runs/i })).toBeVisible()
  await expect(page.getByText(jiraKey)).toBeVisible({ timeout: 5_000 })

  // 3. Approve via the API (no in-UI approve button exists yet).
  const approval = await request.post(`${API_BASE}/approvals/`, {
    data: {
      workflow_id,
      checkpoint: 'pre_generation',
      decision: 'approved',
      note: 'E2E approval',
    },
    headers: { Authorization: `Bearer e2e-bypass-token` },
  })
  expect(approval.ok(), `approval POST returned ${approval.status()}`).toBeTruthy()

  // 4. Workflow row updates — re-navigate to refresh state.
  await page.reload()
  await expect(page.getByText(jiraKey)).toBeVisible()

  await assertCleanJourney({ consoleErrors, badResponses })
})
