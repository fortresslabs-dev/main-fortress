"""Seed P2 Aurora with demo data so the CCO dashboard has something to render.

Idempotent — truncates demo tables then re-inserts. Adds three columns the
dashboard_api.py queries that aren't on the ComplianceRule model yet
(regulation_name, is_obligation, sync_version) and widens the bank_policies
status CHECK so the SPA's not_applicable / override writes don't blow up.

Run via ECS RunTask:

  aws ecs run-task --cluster fortress-pipeline2-testbank \\
    --task-definition fortress-pipeline2-testbank \\
    --launch-type FARGATE --network-configuration ... \\
    --overrides '{"containerOverrides":[{"name":"app","command":["python","-m","scripts.seed_demo_p2"]}]}'
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.utils.config import settings


SCHEMA_PATCHES = [
    # Columns dashboard_api.py SELECTs that aren't on the model yet.
    "ALTER TABLE compliance_rules ADD COLUMN IF NOT EXISTS regulation_name TEXT",
    "ALTER TABLE compliance_rules ADD COLUMN IF NOT EXISTS is_obligation BOOLEAN DEFAULT FALSE",
    "ALTER TABLE compliance_rules ADD COLUMN IF NOT EXISTS sync_version BIGINT",
    # Relax bank_policies status CHECK to include dashboard's writes.
    "ALTER TABLE bank_policies DROP CONSTRAINT IF EXISTS bank_policies_status_check",
    """ALTER TABLE bank_policies ADD CONSTRAINT bank_policies_status_check
       CHECK (status IN ('active','under_review','retired','not_applicable','override'))""",
    # threshold_override + na_reason columns the SPA writes via PATCH.
    "ALTER TABLE bank_policies ADD COLUMN IF NOT EXISTS threshold_override TEXT",
    "ALTER TABLE bank_policies ADD COLUMN IF NOT EXISTS na_reason TEXT",
]


RULES = [
    # (rule_id, regulator, regulation_name, category, cfr_citation, rule_text,
    #  risk, is_obligation, effective_date)
    ("BSA-CTR-10K",      "fincen", "Currency Transaction Report (CTR) Filing",
     "bsa_aml", "31 CFR 1010.311",
     "File a CTR for any currency transaction exceeding $10,000.",
     9.4, True, date(2025, 1, 1)),
    ("BSA-SAR-5K",       "fincen", "Suspicious Activity Report (SAR)",
     "bsa_aml", "31 CFR 1020.320",
     "File a SAR within 30 days of detecting suspicious activity of $5,000+.",
     9.1, True, date(2025, 1, 1)),
    ("BSA-BENEFICIAL",   "fincen", "Beneficial Ownership Identification (20%)",
     "bsa_aml", "31 CFR 1010.230",
     "Identify and verify beneficial owners holding 20% or more equity.",
     8.9, True, date(2026, 4, 11)),
    ("BSA-TRAVEL-3K",    "fincen", "Travel Rule — Funds Transfer Recordkeeping",
     "bsa_aml", "31 CFR 1010.410(f)",
     "Pass on originator/beneficiary info for wire transfers of $3,000+.",
     7.8, True, date(2025, 1, 1)),
    ("OFAC-SDN-SCREEN",  "ofac",   "OFAC SDN Screening — Block on Match",
     "sanctions", "31 CFR 501",
     "Screen all parties against the OFAC SDN list; block on 85%+ similarity.",
     9.6, True, date(2025, 1, 1)),
    ("CFPB-REGE-EFT",    "cfpb",   "Regulation E — Electronic Fund Transfers",
     "consumer_protection", "12 CFR 1005",
     "Provide error-resolution rights for unauthorized EFTs within 60 days.",
     6.4, True, date(2025, 1, 1)),
    ("CFPB-REGZ-TILA",   "cfpb",   "Regulation Z — Truth in Lending",
     "consumer_protection", "12 CFR 1026",
     "Disclose APR and finance charges on consumer credit transactions.",
     7.1, True, date(2025, 1, 1)),
    ("CFPB-1071-SBL",    "cfpb",   "Section 1071 — Small Business Lending Data",
     "lending", "12 CFR 1002.107",
     "Collect and report demographic data on small business credit applications.",
     8.3, True, date(2026, 10, 1)),
    ("CFPB-REGB-ECOA",   "cfpb",   "Regulation B — Equal Credit Opportunity Act",
     "fair_lending", "12 CFR 1002",
     "Prohibit credit discrimination on race, sex, age, marital status.",
     7.7, True, date(2025, 1, 1)),
    ("OCC-WIRE-DUAL",    "occ",    "Wire Transfer Dual Approval ($10K)",
     "wire_transfer", "12 CFR 21",
     "Require dual approval for outgoing wires of $10,000+.",
     7.2, True, date(2025, 1, 1)),
    ("OCC-IRR-PRGM",     "occ",    "Interest Rate Risk Management",
     "risk_mgmt", "12 CFR 30 App A",
     "Maintain a board-approved IRR program with quarterly stress tests.",
     6.0, False, date(2025, 1, 1)),
    ("FRB-REGCC-AVAIL",  "frb",    "Regulation CC — Funds Availability",
     "deposits", "12 CFR 229",
     "Make deposited funds available per the Reg CC schedule.",
     5.8, True, date(2025, 1, 1)),
    ("FRB-REGDD-TISA",   "frb",    "Regulation DD — Truth in Savings",
     "deposits", "12 CFR 1030",
     "Disclose APY and fees on deposit accounts at account opening.",
     5.3, True, date(2025, 1, 1)),
    ("FFIEC-CYBER-RA",   "ffiec",  "FFIEC Cybersecurity Risk Assessment",
     "cybersecurity", "FFIEC IT Handbook",
     "Perform annual cybersecurity assessment using FFIEC CAT methodology.",
     8.0, False, date(2025, 1, 1)),
    ("FFIEC-BSA-IND-RA", "ffiec",  "BSA/AML Independent Risk Assessment",
     "bsa_aml", "FFIEC BSA/AML Manual",
     "Conduct independent BSA/AML risk assessment at least every 18 months.",
     7.4, False, date(2025, 1, 1)),
    ("NYDFS-23NYCRR500", "nydfs",  "23 NYCRR 500 — Cybersecurity Reg",
     "cybersecurity", "23 NYCRR 500",
     "Maintain a CISO, written cyber program, and 72-hour incident reporting.",
     8.6, True, date(2025, 11, 1)),
    ("SEC-REGSP-PRIV",   "sec",    "Regulation S-P — Customer Privacy",
     "privacy", "17 CFR 248",
     "Provide initial and annual privacy notices; safeguard customer NPI.",
     6.7, True, date(2025, 1, 1)),
    ("NCUA-MEMBER-BUS",  "ncua",   "Member Business Loan Limits",
     "lending", "12 CFR 723",
     "Cap aggregate MBLs at 1.75x net worth; document concentration risk.",
     5.5, True, date(2025, 1, 1)),
    ("FINCEN-CIP",       "fincen", "Customer Identification Program (CIP)",
     "bsa_aml", "31 CFR 1020.220",
     "Verify identity of new account holders within reasonable time.",
     8.2, True, date(2025, 1, 1)),
    ("CFPB-UDAAP",       "cfpb",   "UDAAP — Unfair, Deceptive, Abusive Acts",
     "consumer_protection", "12 USC 5531",
     "Prohibit unfair, deceptive, or abusive practices toward consumers.",
     8.5, True, date(2025, 1, 1)),
    ("OCC-3RDPARTY-RM",  "occ",    "Third-Party Risk Management",
     "vendor_mgmt", "OCC Bulletin 2023-17",
     "Maintain a third-party risk management framework across the lifecycle.",
     6.9, False, date(2025, 1, 1)),
    ("FRB-REGW-AFFTX",   "frb",    "Regulation W — Affiliate Transactions",
     "capital", "12 CFR 223",
     "Limit covered transactions with affiliates to 10% / 20% of capital.",
     7.0, True, date(2025, 1, 1)),
    ("FINCEN-512X",      "fincen", "FinCEN 314(a) Information Requests",
     "bsa_aml", "31 CFR 1010.520",
     "Search records for FinCEN 314(a) subjects within 14 days.",
     7.9, True, date(2025, 1, 1)),
    ("OFAC-RECORDKEEP",  "ofac",   "OFAC Recordkeeping — 5-Year Retention",
     "sanctions", "31 CFR 501.601",
     "Retain transaction records subject to OFAC programs for 5 years.",
     6.3, True, date(2025, 1, 1)),
    ("CFPB-REGV-FCRA",   "cfpb",   "Regulation V — Fair Credit Reporting",
     "consumer_protection", "12 CFR 1022",
     "Provide adverse-action notices when denying credit based on a CRA report.",
     6.8, True, date(2025, 1, 1)),
]


CHANGES = [
    # (change_ref, rule_id, regulator, summary, impact, change_type, status)
    # impact_rating CHECK: High|Medium|Low|None  (title case)
    # status      CHECK: detected|in_review|impact_assessed|remediation_planned|signed_off|closed
    ("CHG-2026-0412", "BSA-BENEFICIAL", "fincen",
     "Beneficial-ownership threshold lowered from 25% to 20% (FinCEN final rule).",
     "High",   "modified",   "detected"),
    ("CHG-2026-0517", "NYDFS-23NYCRR500", "nydfs",
     "23 NYCRR 500 amendment — 72h incident reporting now applies to MFA bypass events.",
     "High",   "clarified",  "detected"),
    ("CHG-2026-0428", "CFPB-1071-SBL", "cfpb",
     "Section 1071 compliance date pushed to Oct 1 2026 for Tier-2 lenders.",
     "Medium", "clarified",  "in_review"),
    ("CHG-2026-0501", "OCC-WIRE-DUAL", "occ",
     "OCC reaffirms $10K dual-approval guidance after recent enforcement actions.",
     "Medium", "clarified",  "detected"),
    ("CHG-2026-0322", "OFAC-SDN-SCREEN", "ofac",
     "OFAC added 142 new SDN entries (Russia/Iran). Re-run customer base screening.",
     "High",   "added",      "remediation_planned"),
    ("CHG-2026-0410", "FFIEC-CYBER-RA", "ffiec",
     "FFIEC CAT v2 published. Existing assessments remain valid until next cycle.",
     "Low",    "clarified",  "signed_off"),
]


DEADLINES = [
    # (key, rule_id, regulator, type, name, date, recurring)
    # ck_deadline_type: effective_date|filing_deadline|reporting_deadline|review_deadline
    ("CHG-2026-0412-IMPL",  "BSA-BENEFICIAL", "fincen", "effective_date",
     "Implement 20% beneficial-ownership threshold", date(2026, 7, 11), False),
    ("CTR-Q2-2026",         "BSA-CTR-10K",    "fincen", "filing_deadline",
     "CTR Q2 filing — aggregate report", date(2026, 7, 15), True),
    ("SBL-1071-TIER2",      "CFPB-1071-SBL",  "cfpb",   "effective_date",
     "Section 1071 Tier-2 reporting begins", date(2026, 10, 1), False),
    ("NYDFS-CERT-2026",     "NYDFS-23NYCRR500", "nydfs", "reporting_deadline",
     "23 NYCRR 500 annual certification", date(2026, 4, 15), True),
    ("BSA-IND-RA-DUE",      "FFIEC-BSA-IND-RA", "ffiec", "review_deadline",
     "BSA/AML independent risk assessment due", date(2026, 9, 30), True),
]


WORKFLOWS = [
    # (jira_id, jira_key, status, started_offset_hours, duration_min, artifacts)
    ("10401", "COMP-401", "completed",  72, 18,
     {"rule_ref": "BSA-CTR-10K",      "pr_url": "https://github.com/testbank/compliance-tests/pull/41"}),
    ("10402", "COMP-402", "completed",  68, 22,
     {"rule_ref": "OFAC-SDN-SCREEN",  "pr_url": "https://github.com/testbank/compliance-tests/pull/42"}),
    ("10403", "COMP-403", "completed",  44, 14,
     {"rule_ref": "OCC-WIRE-DUAL",    "pr_url": "https://github.com/testbank/compliance-tests/pull/43"}),
    ("10404", "COMP-404", "running",     2, None,
     {"rule_ref": "CFPB-1071-SBL"}),
    ("10405", "COMP-405", "failed",     12, 7,
     {"rule_ref": "BSA-SAR-5K", "error": "framework selection timeout"}),
]


async def seed(engine):
    async with engine.begin() as conn:
        for stmt in SCHEMA_PATCHES:
            await conn.execute(text(stmt))

        await conn.execute(text("DELETE FROM compliance_rules WHERE rule_id LIKE 'BSA-%' OR rule_id LIKE 'OFAC-%' OR rule_id LIKE 'CFPB-%' OR rule_id LIKE 'OCC-%' OR rule_id LIKE 'FRB-%' OR rule_id LIKE 'FFIEC-%' OR rule_id LIKE 'NYDFS-%' OR rule_id LIKE 'SEC-%' OR rule_id LIKE 'NCUA-%' OR rule_id LIKE 'FINCEN-%'"))
        for (rid, reg, name, cat, cfr, body, risk, oblig, eff) in RULES:
            reg = reg.upper()
            await conn.execute(text("""
                INSERT INTO compliance_rules
                  (rule_id, regulator, category, geography, rule_text,
                   cfr_citation, regulation_name, is_obligation, sync_version,
                   enforcement_risk_score, effective_date, is_active,
                   jurisdiction_level, obligation_count)
                VALUES
                  (:rid, :reg, :cat, 'usa', :body,
                   :cfr, :name, :oblig, 1,
                   :risk, :eff, TRUE,
                   :level, :obc)
            """), {
                "rid": rid, "reg": reg, "cat": cat, "body": body,
                "cfr": cfr, "name": name, "oblig": oblig,
                "risk": risk, "eff": eff,
                "level": "state" if reg == "NYDFS" else "federal",
                "obc": 1 if oblig else 0,
            })

        await conn.execute(text("DELETE FROM regulatory_change_records WHERE change_ref LIKE 'CHG-2026-%'"))
        for (ref, rid, reg, summary, impact, ctype, status) in CHANGES:
            reg = reg.upper()
            await conn.execute(text("""
                INSERT INTO regulatory_change_records
                  (change_ref, rule_id, regulator, change_summary,
                   impact_rating, change_type, status, is_active,
                   detected_at, created_at)
                VALUES
                  (:ref, :rid, :reg, :summary, :impact, :ctype, :status,
                   TRUE, NOW() - INTERVAL '7 days', NOW() - INTERVAL '7 days')
            """), {"ref": ref, "rid": rid, "reg": reg, "summary": summary,
                   "impact": impact, "ctype": ctype, "status": status})

        from sqlalchemy import bindparam
        await conn.execute(
            text("DELETE FROM compliance_deadlines WHERE deadline_key IN :keys")
                .bindparams(bindparam("keys", expanding=True)),
            {"keys": [d[0] for d in DEADLINES]},
        )
        for (key, rid, reg, dtype, name, dl_date, recurring) in DEADLINES:
            reg = reg.upper()
            days = (dl_date - date.today()).days
            await conn.execute(text("""
                INSERT INTO compliance_deadlines
                  (deadline_key, regulation_id, regulator, deadline_type,
                   deadline_name, deadline_date, days_remaining, is_recurring,
                   is_active)
                VALUES
                  (:key, :rid, :reg, :dtype, :name, :dl, :days, :rec, TRUE)
            """), {"key": key, "rid": rid, "reg": reg, "dtype": dtype,
                   "name": name, "dl": dl_date, "days": days, "rec": recurring})

        await conn.execute(
            text("DELETE FROM workflow_executions WHERE jira_story_id IN :ids")
                .bindparams(bindparam("ids", expanding=True)),
            {"ids": [w[0] for w in WORKFLOWS]},
        )
        import json
        for (jid, jkey, status, off_h, dur_min, artifacts) in WORKFLOWS:
            started = datetime.utcnow() - timedelta(hours=off_h)
            completed = (started + timedelta(minutes=dur_min)) if dur_min else None
            await conn.execute(text("""
                INSERT INTO workflow_executions
                  (jira_story_id, jira_story_key, status,
                   started_at, completed_at, artifacts, context_data)
                VALUES
                  (:jid, :jkey, :status, :started, :completed,
                   CAST(:artifacts AS JSON), CAST('{}' AS JSON))
            """), {"jid": jid, "jkey": jkey, "status": status,
                   "started": started, "completed": completed,
                   "artifacts": json.dumps(artifacts)})

    # Counts
    async with engine.connect() as conn:
        for tbl in ("compliance_rules", "regulatory_change_records",
                    "compliance_deadlines", "workflow_executions"):
            n = await conn.scalar(text(f"SELECT COUNT(*) FROM {tbl}"))
            print(f"  {tbl}: {n} rows")


async def main():
    url = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url, echo=False)
    try:
        print("Seeding P2 Aurora ...")
        await seed(engine)
        print("Done.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
