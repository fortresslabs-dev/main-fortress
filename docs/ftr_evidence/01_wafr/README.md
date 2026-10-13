# 01 — AWS Well-Architected Review

## What goes here

- `wafr_self_assessment_{YYYY-MM-DD}.pdf` — export from `docs/aws_well_architected_review_p1_p2.md`, rendered to PDF for the reviewer.
- `wafr_partner_led_report_{YYYY-MM-DD}.pdf` — when an AWS Solutions Architect runs the formal partner-led review (S12 in WAFR; FTR §12.1 ⚠️). **Not yet scheduled.**
- `wafr_remediation_plan.md` — short version of the WAFR 30/60/90-day plan, extracted from the source doc for the reviewer.

## Status (2026-06-26)

- Self-assessment: ✅ complete — source is `docs/aws_well_architected_review_p1_p2.md`
- Partner-led review: ⚠️ pending — required for FTR pass (not just self-assessment)

## How AWS reads this

The FTR reviewer expects to see WAFR was *performed* and a remediation plan exists. They don't expect every HRI closed at FTR time — they expect closure dates with owners. This is why `docs/ftr_tracker.md` exists.
