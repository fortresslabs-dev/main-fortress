# Dashboard E2E — Playwright journey specs

Five Chrome-only specs covering the F1–F5 demo flows. See
`docs/runbooks/demo-readiness.md` §3 for acceptance criteria.

## Prereqs

### One-time

```bash
cd dashboard
npm install
npm run e2e:install      # downloads Chromium for Playwright
```

If `e2e:install` can't reach the Playwright CDN (sandboxed CI runner, etc.)
run it locally and check the result into a self-hosted CDN, or use the
official `mcr.microsoft.com/playwright` Docker image.

### Per-run — local stack

```bash
# 1. Backend up (docker-compose Postgres + Redis)
make up
make migrate

# 2. P1 + P2 servers (separate shells)
DEV_MODE=true ENVIRONMENT=test JIRA_WEBHOOK_SECRET=test-webhook-secret \
  PIPELINE_MODE=pipeline1 python -m uvicorn src.main_pipeline1:app --port 8001

DEV_MODE=true ENVIRONMENT=test JIRA_WEBHOOK_SECRET=test-webhook-secret \
  PIPELINE_MODE=pipeline2 python -m uvicorn src.main_pipeline2:app --port 8002

# 3. Dashboard + tests
cd dashboard
npm run e2e              # webServer launches `npm run dev` with VITE_E2E_AUTH_BYPASS=1
```

### Per-run — staging

```bash
E2E_BASE_URL=https://dashboard.staging.fortress.com \
E2E_API_BASE_URL=https://api.staging.fortress.com \
JIRA_WEBHOOK_SECRET=$STAGING_JIRA_SECRET \
  npm run e2e
```

In staging, the auth bypass flag is **not** baked into the build — specs
expect real Cognito users (see Phase 4 demo-seed for canonical demo
accounts). Currently this means F1/F4/F5 work without changes but F3
needs a JWT minted ahead of the run.

## How the auth bypass works

- `main.jsx` checks `VITE_E2E_AUTH_BYPASS=1` at build time AND
  `window.location.hostname === 'localhost'` at runtime. Both gates must
  be true for the bypass to engage.
- When engaged, `<Authenticator>` is skipped and `App` is rendered
  directly with a fake Cognito user (`e2e@test.local`).
- `App.jsx` reads `__e2e_role` / `__e2e_token` from `localStorage` and
  sets `Authorization: Bearer <token>` on the axios instance.
- Specs set role per-test via `test.use({ e2eRole: 'CCO' })` — see
  `fixtures.ts`.

A prod build with `VITE_E2E_AUTH_BYPASS=1` is inert because the runtime
hostname check fails. Still: do not ship the flag set.

## Adding a new journey

1. Drop a spec into `dashboard/e2e/F*_<name>.spec.ts`.
2. Import from `./fixtures` (not `@playwright/test` directly) — that gives
   you `loggedInPage`, `consoleErrors`, `badResponses`.
3. End with `await assertCleanJourney(...)` so the journey enforces the
   demo-readiness acceptance criteria.
