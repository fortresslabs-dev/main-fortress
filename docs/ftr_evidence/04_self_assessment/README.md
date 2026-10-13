# 04 — AWS Partner Central FTR self-assessment

## What goes here

- `ftr_self_assessment_{YYYY-MM-DD}.pdf` — export of the filled-in AWS Partner Central FTR checklist (the **actual** AWS-format submission artifact).
- `ftr_self_assessment_responses.csv` — machine-readable copy of the responses for diff over time.
- `ftr_reviewer_qa_{YYYY-MM-DD}.md` — anticipated reviewer questions + our answers (start from FTR §8.4).

## Where the form lives

AWS Partner Central → Partner Hub → APN Customer Engagements → **Foundational Technical Review**. Requires Partner Central account login (CTO role). The form is a structured online questionnaire, NOT a freeform PDF.

## Mapping

Every control in the Partner Central form maps to a section of `docs/aws_ftr_marketplace_p1_p2.md`. When filling the form, paste the section reference (e.g. "see §4.3 KMS key management") + attach the relevant artifact from `03_security_tools/`.

## Status (2026-06-26)

⬜ Not started. Do NOT open the Partner Central form until all hard blockers (FTR §13.1 B1–B10) are closed or have an explicit AWS-approved exception. Opening early and abandoning is a yellow flag with the reviewer.

## Submission gate

Per `docs/ftr_tracker.md` working notes: this file gets populated only after Phase 0–4 of the tracker is complete AND the six out-of-scope §13.1 blockers (B2, B5, B6, B7-full, B8, B10) are closed or waived.
