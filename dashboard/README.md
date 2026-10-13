# fortress-dashboard
CCO / CRO / CTO compliance dashboard — React SPA served from S3 + CloudFront.

## Local dev
```bash
npm install
cp .env.example .env.local   # fill in from SSM
npm run dev                  # http://localhost:5173
```

## Deploy
```bash
./deploy.sh testbank production
# or via git tag: git tag dashboard-testbank-v1.0 && git push origin --tags
```

## Pages
- /overview  — health score, coverage chart, deadlines
- /rules     — 847 rules, filter by regulator, Generate Test, Mark N/A
- /changes   — regulatory change inbox, sign-off
- /workflows — JIRA pipeline runs, PR links
