"""Seed applicable_obligation_registry for the (national, community) scope.

Ships the R13 follow-on to R10 (migration 043). R10 landed the schema empty
by design so the dashboard could degrade honestly ("Ingestion completeness")
on any bank whose scope wasn't yet authored. This script authors the first
scope — the OCC-supervised community-bank obligation set — so the widget
on testbank (and every other national-charter community-tier pilot) flips
from fallback to registry mode.

Scope resolved by :func:`_bank_scope` in ``src/api/dashboard_api.py``:

    primary_regulator=OCC  → bank_charter=national
    total_assets_usd<10B    → asset_tier=community

Testbank's ``bank_profile`` row (seeded by
``scripts/seed_testbank_dashboard.py``) sets primary_regulator=OCC but
leaves ``total_assets_usd`` NULL — the tier can't be derived, so the
widget stays in fallback. This script backfills ``total_assets_usd=$2B``
on the testbank row (well inside the community band) so the scope
resolves. Any other bank whose ``total_assets_usd`` is already set is
left untouched.

The obligation list is a curated core set — BSA/AML, OFAC, consumer
protection (Reg B/CC/DD/E/Z, RESPA, HMDA, FCRA), CRA, safety/soundness,
FDIC deposit-insurance advertising, call reporting. It is deliberately
*not* exhaustive: the audit's guidance is that a defensible denominator
is one that a compliance officer can explain, not one that reproduces
every eCFR section. Ops can add or retire rows without a schema change
because the registry is bank_charter × asset_tier scoped and
citation-keyed.

Idempotency: ``INSERT ... ON CONFLICT ON CONSTRAINT
uq_registry_scope_citation_key DO NOTHING``. Re-runs are safe. To
supersede an entry, set ``effective_to`` on the existing row via a
separate migration or one-off UPDATE — this script never deletes.

Usage:

    # dry-run — prints the planned rowset + a diff against what's
    # already in the registry, no writes
    DRY_RUN=1 python -m scripts.seed_applicable_obligation_registry

    # apply
    DRY_RUN=0 python -m scripts.seed_applicable_obligation_registry

Env vars follow the same conventions as the other seed scripts
(AURORA_HOST, AURORA_PORT, DB_NAME, DB_USER, DB_PASSWORD).

Follow-ons queued in ``docs/examination_readiness_resolutions.md``:
  * R14 — bulk-seed control ↔ obligation mappings from ControlRuleMapping.
  * R15 — evidence-authoring UI (or CSV upload) for governance_evidence.
"""
from __future__ import annotations

import asyncio
import os
from typing import List, Tuple

import asyncpg


BANK_CHARTER = "national"
ASSET_TIER = "community"

# Testbank community-tier default — $2B, comfortably inside the <$10B
# community band. Only written if the existing row has NULL there.
TESTBANK_ID = "testbank"
TESTBANK_COMMUNITY_ASSETS_USD = 2_000_000_000

# Curated obligation set. Each tuple:
#   (cfr_citation, regulator, obligation_key, description, source)
#
# `source` is the human-readable provenance so the audit trail shows *why*
# a row exists. `is_required` defaults TRUE via the migration server_default;
# `effective_from`/`effective_to` left NULL (unbounded) — this is the
# steady-state set as of 2026-07-06. Sunset entries by UPDATE-setting
# effective_to on a specific row rather than deleting.
OBLIGATIONS: List[Tuple[str, str, str, str, str]] = [
    # ── BSA / AML — 31 CFR Chapter X ────────────────────────────────
    ("31 CFR 1020.210", "FinCEN", "bsa_aml_program",
     "AML program: internal controls, independent testing, BSA officer, training, CDD.",
     "31 CFR Chapter X — FinCEN AML program rule"),
    ("31 CFR 1020.220", "FinCEN", "customer_identification_program",
     "Customer Identification Program (CIP): identity verification, recordkeeping, watchlist check.",
     "31 CFR § 1020.220 — CIP rule"),
    ("31 CFR 1010.311", "FinCEN", "ctr_filing",
     "Currency Transaction Report — file within 15 days on cash transactions >$10,000.",
     "31 CFR § 1010.311"),
    ("31 CFR 1020.320", "FinCEN", "sar_filing",
     "Suspicious Activity Report — file within 30 days of initial detection.",
     "31 CFR § 1020.320"),
    ("31 CFR 1010.410", "FinCEN", "funds_transfer_recordkeeping",
     "Records for funds transfers >=$3,000 (travel rule + originator/beneficiary).",
     "31 CFR § 1010.410"),
    ("31 CFR 1010.430", "FinCEN", "bsa_record_retention",
     "Retain BSA records (CTR, SAR support, CIP, funds-transfer) for 5 years.",
     "31 CFR § 1010.430"),
    ("31 CFR 1010.520", "FinCEN", "section_314a_response",
     "Section 314(a): search records against FinCEN law-enforcement requests within 14 days.",
     "31 CFR § 1010.520 — USA PATRIOT Act §314(a)"),
    ("31 CFR 1010.605", "FinCEN", "section_314b_info_sharing",
     "Section 314(b): voluntary information-sharing between financial institutions.",
     "31 CFR § 1010.605 — USA PATRIOT Act §314(b)"),
    ("31 CFR 1010.230", "FinCEN", "beneficial_ownership",
     "Beneficial ownership identification for legal-entity customers at account opening.",
     "31 CFR § 1010.230 (CDD rule)"),

    # ── OFAC — 31 CFR Chapter V ─────────────────────────────────────
    ("31 CFR 501.603", "OFAC", "ofac_rejected_transactions",
     "Report rejected transactions within 10 business days.",
     "31 CFR § 501.603"),
    ("31 CFR 501.604", "OFAC", "ofac_blocked_property_annual_report",
     "Annual report of blocked property, due by September 30.",
     "31 CFR § 501.604"),
    ("31 CFR 501.605", "OFAC", "ofac_blocked_property_initial_report",
     "Initial blocked-property report within 10 business days.",
     "31 CFR § 501.605"),

    # ── Reg B (ECOA) — 12 CFR Part 1002 ────────────────────────────
    ("12 CFR 1002.9",  "CFPB", "ecoa_adverse_action_notice",
     "Adverse-action notice within 30 days of application decision.",
     "12 CFR § 1002.9"),
    ("12 CFR 1002.14", "CFPB", "ecoa_appraisal_disclosure",
     "Copy of appraisal / valuation to applicant on first-lien mortgage.",
     "12 CFR § 1002.14"),

    # ── Reg CC (Funds Availability) — 12 CFR Part 229 ──────────────
    ("12 CFR 229.10", "FRB", "reg_cc_next_day_availability",
     "Next-day availability for cash, government checks, on-us checks, first $225.",
     "12 CFR § 229.10"),
    ("12 CFR 229.13", "FRB", "reg_cc_exception_holds",
     "Notice + reason for exception holds (new account, large deposit, redeposited, etc.).",
     "12 CFR § 229.13"),

    # ── Reg DD (Truth in Savings) — 12 CFR Part 1030 ───────────────
    ("12 CFR 1030.4", "CFPB", "reg_dd_account_disclosures",
     "Account disclosures at account opening and on request.",
     "12 CFR § 1030.4"),

    # ── Reg E (EFT) — 12 CFR Part 1005 ─────────────────────────────
    ("12 CFR 1005.7",  "CFPB", "reg_e_initial_disclosures",
     "Initial EFT disclosures at consumer-account opening.",
     "12 CFR § 1005.7"),
    ("12 CFR 1005.11", "CFPB", "reg_e_error_resolution",
     "Error-resolution: investigate within 10 business days, provisional credit if not resolved.",
     "12 CFR § 1005.11"),

    # ── Reg Z (TILA) — 12 CFR Part 1026 ────────────────────────────
    ("12 CFR 1026.17", "CFPB", "reg_z_general_disclosures",
     "TILA general-disclosure form + timing requirements.",
     "12 CFR § 1026.17"),
    ("12 CFR 1026.18", "CFPB", "reg_z_closed_end_credit",
     "Closed-end credit disclosures (APR, finance charge, amount financed, total of payments).",
     "12 CFR § 1026.18"),
    ("12 CFR 1026.19", "CFPB", "reg_z_trid_disclosures",
     "TRID: Loan Estimate within 3 business days; Closing Disclosure 3 business days pre-consummation.",
     "12 CFR § 1026.19"),
    ("12 CFR 1026.43", "CFPB", "reg_z_ability_to_repay",
     "Ability-to-Repay / Qualified Mortgage rule for consumer mortgage originators.",
     "12 CFR § 1026.43"),

    # ── RESPA — 12 CFR Part 1024 ───────────────────────────────────
    ("12 CFR 1024.35", "CFPB", "respa_error_resolution",
     "RESPA error-resolution procedures on servicing.",
     "12 CFR § 1024.35"),
    ("12 CFR 1024.41", "CFPB", "respa_loss_mitigation",
     "Loss-mitigation procedures on delinquent mortgages.",
     "12 CFR § 1024.41"),

    # ── HMDA — 12 CFR Part 1003 ────────────────────────────────────
    ("12 CFR 1003.4", "CFPB", "hmda_data_collection",
     "HMDA data collection + LAR reporting for covered institutions.",
     "12 CFR § 1003.4"),

    # ── CRA — 12 CFR Part 25 (OCC national banks) ──────────────────
    ("12 CFR 25.22", "OCC", "cra_lending_test",
     "CRA lending test — small-bank streamlined performance evaluation.",
     "12 CFR § 25.22"),
    ("12 CFR 25.42", "OCC", "cra_data_reporting",
     "CRA data collection + reporting.",
     "12 CFR § 25.42"),

    # ── FCRA / Reg V — 12 CFR Part 1022 ────────────────────────────
    ("12 CFR 1022.42", "CFPB", "fcra_furnisher_accuracy",
     "Furnisher accuracy + integrity procedures for consumer-report data.",
     "12 CFR § 1022.42"),
    ("12 CFR 1022.90", "CFPB", "fcra_red_flags",
     "Identity-theft red flags program (FACT Act §114).",
     "12 CFR § 1022.90"),

    # ── OCC safety & soundness — 12 CFR Part 30 ────────────────────
    ("12 CFR 30",           "OCC", "safety_soundness_standards",
     "OCC interagency safety-and-soundness standards (Appendix A) + third-party risk (App B).",
     "12 CFR Part 30"),

    # ── FDIC deposit insurance advertising — 12 CFR Part 328 ───────
    ("12 CFR 328.3", "FDIC", "fdic_insurance_signage",
     "FDIC official advertising statement + digital-channel display requirements.",
     "12 CFR § 328.3"),

    # ── FFIEC Call Report — 12 CFR § 304.3 ─────────────────────────
    ("12 CFR 304.3", "FDIC", "call_report_filing",
     "Consolidated Reports of Condition and Income (Call Report) — quarterly filing.",
     "12 CFR § 304.3"),

    # ── Regulation W (affiliate transactions) — 12 CFR Part 223 ────
    ("12 CFR 223.11", "FRB", "reg_w_affiliate_limits",
     "Section 23A limits on covered transactions with affiliates (10%/20% caps).",
     "12 CFR § 223.11"),

    # ── Fair lending / UDAAP — 12 USC 5531 (dual codified) ─────────
    ("12 USC 5531", "CFPB", "udaap_prohibition",
     "Prohibition on unfair, deceptive, or abusive acts or practices in consumer finance.",
     "12 USC § 5531 (Dodd-Frank §1031)"),
]


# ── SQL ────────────────────────────────────────────────────────────


PLANNED_KEYS_QUERY = """
    SELECT cfr_citation, obligation_key
      FROM applicable_obligation_registry
     WHERE bank_charter = $1 AND asset_tier = $2
"""

INSERT_SQL = """
    INSERT INTO applicable_obligation_registry
        (bank_charter, asset_tier, cfr_citation, regulator,
         obligation_key, description, is_required, source)
    VALUES ($1, $2, $3, $4, $5, $6, TRUE, $7)
    ON CONFLICT ON CONSTRAINT uq_registry_scope_citation_key DO NOTHING
"""

BANK_PROFILE_BACKFILL_SQL = """
    UPDATE bank_profile
       SET total_assets_usd = $2,
           updated_at = NOW()
     WHERE bank_id = $1
       AND total_assets_usd IS NULL
"""


# ── main ───────────────────────────────────────────────────────────


async def main() -> int:
    dry_run = os.environ.get("DRY_RUN", "1") != "0"

    conn = await asyncpg.connect(
        host=os.environ["AURORA_HOST"],
        port=int(os.environ.get("AURORA_PORT", "5432")),
        database=os.environ["DB_NAME"],
        user=os.environ.get("DB_USER", "fortress_testbank"),
        password=os.environ["DB_PASSWORD"],
        ssl="require",
    )

    try:
        existing = await conn.fetch(
            PLANNED_KEYS_QUERY, BANK_CHARTER, ASSET_TIER)
        existing_pairs = {(r["cfr_citation"], r["obligation_key"])
                          for r in existing}
        planned_pairs = {(o[0], o[2]) for o in OBLIGATIONS}
        new_pairs = planned_pairs - existing_pairs

        print(
            f"seed_applicable_obligation_registry: scope=({BANK_CHARTER},{ASSET_TIER})"
        )
        print(
            f"  existing rows: {len(existing_pairs):>3}  |  "
            f"planned rows: {len(planned_pairs):>3}  |  "
            f"new rows to insert: {len(new_pairs):>3}"
        )
        for cfr, key in sorted(new_pairs):
            print(f"    + {cfr:<20} {key}")

        # Testbank asset backfill preview
        tb_row = await conn.fetchrow(
            "SELECT total_assets_usd FROM bank_profile WHERE bank_id=$1",
            TESTBANK_ID,
        )
        tb_needs_backfill = (
            tb_row is not None and tb_row["total_assets_usd"] is None
        )
        if tb_needs_backfill:
            print(
                f"  testbank.bank_profile.total_assets_usd is NULL — will set "
                f"to ${TESTBANK_COMMUNITY_ASSETS_USD:,} (community tier)"
            )
        elif tb_row is None:
            print(
                "  testbank.bank_profile row missing — run "
                "scripts.seed_testbank_dashboard first"
            )
        else:
            print(
                f"  testbank.bank_profile.total_assets_usd already set "
                f"(${int(tb_row['total_assets_usd']):,}); leaving untouched"
            )

        if dry_run:
            print("DRY_RUN=1 — no writes. Set DRY_RUN=0 to apply.")
            return 0

        async with conn.transaction():
            inserted = 0
            for cfr, regulator, key, description, source in OBLIGATIONS:
                result = await conn.execute(
                    INSERT_SQL, BANK_CHARTER, ASSET_TIER, cfr, regulator,
                    key, description, source,
                )
                # asyncpg returns "INSERT 0 1" on insert, "INSERT 0 0" on skip
                if result.endswith(" 1"):
                    inserted += 1

            backfilled = 0
            if tb_needs_backfill:
                result = await conn.execute(
                    BANK_PROFILE_BACKFILL_SQL,
                    TESTBANK_ID, TESTBANK_COMMUNITY_ASSETS_USD,
                )
                if result.endswith(" 1"):
                    backfilled = 1

        print(
            f"seed_applicable_obligation_registry: inserted={inserted} "
            f"skipped={len(OBLIGATIONS) - inserted} "
            f"bank_profile_backfilled={backfilled}"
        )
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
