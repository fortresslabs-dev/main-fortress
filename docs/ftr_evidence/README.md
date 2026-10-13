# FTR Evidence — workpapers for AWS Partner Network submission

Staging area for artifacts uploaded to the AWS Partner Central FTR self-assessment form. The markdown FTR doc (`docs/aws_ftr_marketplace_p1_p2.md`) and the execution tracker (`docs/ftr_tracker.md`) are *internal* workpapers; the contents of this directory are what AWS actually reviews.

## Layout

| Subdir | Contents | Owner |
|---|---|---|
| `01_wafr/` | WAFR self-assessment export + (when scheduled) partner-led review report | Agent 1 / CTO |
| `02_arch_diagram/` | PNG / PDF architecture diagrams — lifted from WAFR §2.1 (P1) and §2.2 (P2). Must be legible at print size. | Agent 1 |
| `03_security_tools/` | Screenshots / JSON exports proving controls are live: GuardDuty findings page, Security Hub standards summary, Inspector enabled, Access Analyzer, WAF rule sets, KMS key policies | Agent 1 / Agent 3 |
| `04_self_assessment/` | AWS Partner Central FTR self-assessment checklist export (CSV or PDF) once filled | CTO |

## How to refresh

Evidence rots fast (a screenshot from May is suspect by August). Refresh cadence:

- Architecture diagrams — every time `infra/cloudformation/*.yml` topology changes
- Security tool screenshots — quarterly, or after any GuardDuty / Security Hub configuration change
- WAFR — annually, or after any pillar-level architecture change
- Self-assessment checklist — re-export every time §13 of the FTR doc changes

Do NOT commit binary artifacts that contain customer data (account IDs, bank IPs, internal email addresses). Redact before commit. Use `git lfs` for large binaries if any single file exceeds 10 MB.

## Submission packaging

When opening the FTR ticket via AWS Partner Central:

1. Zip `02_arch_diagram/` + `03_security_tools/` (the reviewer wants visual proof, not markdown).
2. Attach the WAFR completion record from `01_wafr/`.
3. Paste the FTR doc §13 status table into the Partner Central form as evidence per-control.
4. Link this repo's `docs/aws_ftr_marketplace_p1_p2.md` only if the reviewer asks (it's a workpaper, not a deliverable).
