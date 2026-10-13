"""Seed the six bank-side tables for testbank so the P2 CCO dashboard renders.

Populates (all idempotent, safe to re-run):

  bank_profile                   1  Test Bank, N.A. (OCC national bank, FDIC-insured)
  bank_officers                  1  BSA Officer (deterministic UUID, reused as FK below)
  controls                       N  one per row in compliance_rules (projected)
  control_rule_mappings          N  one (control -> rule) full mapping per control
  findings                       M  most recent OCC + national_bank enforcement_actions,
                                    status=validated_closed; finding_id prefixed "EA-"
                                    so origin is traceable. These are real regulator
                                    output, not findings against testbank — closed
                                    status avoids the "Test Bank has M open MRAs"
                                    misimpression.
  ofac_validations               1  quarterly SDN-list attestation (BSA Officer)
  change_record_dispositions    50  most recent regulatory_change_records auto-
                                    dispositioned 'acknowledged' / status='closed'

Run via ECS one-shot task against P2 Aurora:

  aws ecs run-task --cluster fortress-pipeline2-testbank \\
    --task-definition fortress-pipeline2-testbank \\
    --launch-type FARGATE --network-configuration ... \\
    --overrides '{"containerOverrides":[{"name":"pipeline2",
      "command":["python","-m","scripts.seed_testbank_dashboard"]}]}'
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import date, timedelta

import asyncpg


BANK_ID = "testbank"
LEGAL_NAME = "Test Bank, N.A."
PRIMARY_REGULATOR = "OCC"
SECONDARY_REGULATORS = ["FDIC"]
CHARTER_NUMBER = "TBNA-0001"
FISCAL_YEAR_END = date(2025, 12, 31)
BANK_ADDRESS = {
    "street": "1 Test Plaza",
    "city": "Wilmington",
    "state": "DE",
    "postal_code": "19801",
    "country": "US",
}

# Deterministic officer UUID so re-running the script doesn't churn FK targets.
NS = uuid.UUID("12345678-1234-5678-1234-567812345678")
BSA_OFFICER_ID = uuid.uuid5(NS, f"{BANK_ID}:officer:bsa")
BSA_OFFICER_NAME = "Test Bank BSA Officer"
BSA_OFFICER_EMAIL = "bsa-officer@testbank.example"
BSA_OFFICER_STARTED = date(2024, 1, 1)

# How many enforcement actions to project as historical findings.
FINDINGS_CAP = 50
# How many recent regulatory_change_records to auto-disposition.
DISPOSITIONS_CAP = 50


# ── helpers ──────────────────────────────────────────────────────────────────

def _short_hash(*parts: str) -> str:
    """16-hex-char deterministic ID suffix; collision-safe for our volumes."""
    h = hashlib.sha256("::".join(parts).encode("utf-8")).hexdigest()
    return h[:16]


def _owner_role_for_rule(category: str | None, regulator: str | None) -> str:
    """Map a compliance_rules row to an officer_role_t enum value."""
    c = (category or "").lower()
    r = (regulator or "").lower()
    if r == "ofac" or "bsa" in c or "aml" in c or "sanctions" in c:
        return "bsa_officer"
    # Lending, consumer_protection, fair_lending, and the long tail default to CCO.
    return "cco"


def _finding_type(action_type: str | None) -> str:
    a = (action_type or "").lower()
    if a in {"civil_money_penalty", "consent_order", "cease_and_desist",
             "prohibition_order", "removal_order"}:
        return "MRA"
    if a in {"formal_agreement", "mou"}:
        return "MRIA"
    return "OBSERVATION"


def _finding_severity(penalty_amount_usd) -> str:
    if penalty_amount_usd is None:
        return "low"
    p = float(penalty_amount_usd)
    if p >= 1_000_000:
        return "high"
    if p >= 100_000:
        return "medium"
    return "low"


# ── main ─────────────────────────────────────────────────────────────────────

async def main() -> int:
    conn = await asyncpg.connect(
        host=os.environ["AURORA_HOST"],
        port=int(os.environ.get("AURORA_PORT", "5432")),
        database=os.environ["DB_NAME"],
        user=os.environ.get("DB_USER", "fortress_testbank"),
        password=os.environ["DB_PASSWORD"],
        ssl="require",
    )
    try:
        async with conn.transaction():
            await _seed_bank_profile(conn)
            await _seed_bsa_officer(conn)
            controls_n = await _seed_controls(conn)
            mappings_n = await _seed_control_rule_mappings(conn)
            findings_n = await _seed_findings(conn)
            await _seed_ofac_validation(conn)
            dispositions_n = await _seed_dispositions(conn)

        print(f"seed_testbank_dashboard: bank_profile=1 officers=1 "
              f"controls={controls_n} mappings={mappings_n} "
              f"findings={findings_n} ofac_validations=1 "
              f"dispositions={dispositions_n}")
        return 0
    finally:
        await conn.close()


async def _seed_bank_profile(conn: asyncpg.Connection) -> None:
    await conn.execute("""
        INSERT INTO bank_profile
            (bank_id, legal_name, charter_number, primary_regulator,
             secondary_regulators, fiscal_year_end, address)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7::jsonb)
        ON CONFLICT (bank_id) DO UPDATE SET
            legal_name = EXCLUDED.legal_name,
            charter_number = EXCLUDED.charter_number,
            primary_regulator = EXCLUDED.primary_regulator,
            secondary_regulators = EXCLUDED.secondary_regulators,
            fiscal_year_end = EXCLUDED.fiscal_year_end,
            address = EXCLUDED.address,
            updated_at = NOW()
    """, BANK_ID, LEGAL_NAME, CHARTER_NUMBER, PRIMARY_REGULATOR,
        json.dumps(SECONDARY_REGULATORS), FISCAL_YEAR_END,
        json.dumps(BANK_ADDRESS))


async def _seed_bsa_officer(conn: asyncpg.Connection) -> None:
    await conn.execute("""
        INSERT INTO bank_officers
            (person_id, bank_id, full_name, role, email, started_at)
        VALUES ($1, $2, $3, 'bsa_officer', $4, $5)
        ON CONFLICT (person_id) DO UPDATE SET
            full_name = EXCLUDED.full_name,
            email = EXCLUDED.email
    """, BSA_OFFICER_ID, BANK_ID, BSA_OFFICER_NAME,
        BSA_OFFICER_EMAIL, BSA_OFFICER_STARTED)


async def _seed_controls(conn: asyncpg.Connection) -> int:
    rules = await conn.fetch("""
        SELECT rule_id, regulator, geography, category, cfr_citation,
               rule_text, regulation_name
          FROM compliance_rules
         WHERE COALESCE(is_active, TRUE) = TRUE
    """)
    if not rules:
        return 0

    rows = []
    for r in rules:
        control_id = "CTRL-" + _short_hash(
            r["rule_id"], r["regulator"] or "", r["geography"] or "")
        # Prefer regulation_name → cfr_citation → rule_id for the display name.
        name = (r["regulation_name"]
                or (f"{r['cfr_citation']} — {r['rule_id']}" if r["cfr_citation"]
                    else r["rule_id"]))
        name = name[:500]
        description = (r["rule_text"] or "")[:2000]
        owner_role = _owner_role_for_rule(r["category"], r["regulator"])
        rows.append((control_id, BANK_ID, name, owner_role,
                     BSA_OFFICER_ID, description, "preventive"))

    await conn.executemany("""
        INSERT INTO controls
            (control_id, bank_id, name, owner_role, owner_person_id,
             description, control_type)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (control_id) DO UPDATE SET
            name = EXCLUDED.name,
            owner_role = EXCLUDED.owner_role,
            owner_person_id = EXCLUDED.owner_person_id,
            description = EXCLUDED.description,
            updated_at = NOW()
    """, rows)
    return len(rows)


async def _seed_control_rule_mappings(conn: asyncpg.Connection) -> int:
    """One (control -> rule) full mapping per compliance_rule.

    Idempotency: delete-then-insert on the bulk_seed slice (no unique
    constraint on the table, so we own the bulk_seed rows wholesale).
    """
    rules = await conn.fetch("""
        SELECT rule_id, regulator, geography
          FROM compliance_rules
         WHERE COALESCE(is_active, TRUE) = TRUE
    """)
    if not rules:
        return 0

    rows = [
        ("CTRL-" + _short_hash(r["rule_id"], r["regulator"] or "",
                               r["geography"] or ""),
         r["rule_id"][:100])
        for r in rules
    ]

    # Wipe any prior bulk_seed mappings for testbank so re-runs don't
    # accumulate duplicates. Real CCO-authored mappings (created_by != 'bulk_seed')
    # are untouched.
    await conn.execute("""
        DELETE FROM control_rule_mappings
         WHERE bank_id = $1 AND created_by = 'bulk_seed'
    """, BANK_ID)

    await conn.executemany("""
        INSERT INTO control_rule_mappings
            (bank_id, control_id, rule_id, mapping_strength, created_by)
        VALUES ($1, $2, $3, 'full', 'bulk_seed')
    """, [(BANK_ID, control_id, rule_id) for control_id, rule_id in rows])
    return len(rows)


async def _seed_findings(conn: asyncpg.Connection) -> int:
    # Filter to enforcement actions relevant to testbank's charter. National
    # banks are subject to OCC (primary), FDIC, FRB, and CFPB. The scraper
    # often can't classify institution_type, so we include:
    #   - any action explicitly against a national_bank, regardless of regulator
    #   - any OCC action where institution_type couldn't be classified
    # CFPB-against-unclassified is excluded — too broad (includes fintechs,
    # non-banks, etc.) to count as defensible peer-bank risk for testbank.
    actions = await conn.fetch("""
        SELECT action_ref, action_type, action_date, regulations_cited,
               violation_summary, penalty_amount_usd, source_url, regulator
          FROM enforcement_actions
         WHERE is_active = TRUE
           AND (
               institution_type = 'national_bank'
            OR ($1 = ANY(ARRAY[regulator]) AND
                (institution_type IS NULL OR institution_type = 'unknown'))
           )
         ORDER BY action_date DESC
         LIMIT $2
    """, PRIMARY_REGULATOR, FINDINGS_CAP)
    if not actions:
        return 0

    rows = []
    for a in actions:
        finding_id = "EA-" + _short_hash(a["action_ref"])
        citations = a["regulations_cited"]
        # JSONB comes back as a list already, but be defensive.
        if isinstance(citations, str):
            try:
                citations = json.loads(citations)
            except json.JSONDecodeError:
                citations = []
        regulatory_source = citations[0] if citations else None

        concern = (a["violation_summary"]
                   or f"{a['regulator']} {a['action_type']} dated {a['action_date']}")
        concern = concern[:4000]

        identified = a["action_date"]
        due = identified + timedelta(days=180)

        rows.append((
            finding_id, BANK_ID,
            _finding_type(a["action_type"]),
            _finding_severity(a["penalty_amount_usd"]),
            regulatory_source,
            concern,
            None,                                   # cause
            None,                                   # consequence
            None,                                   # corrective_action
            None,                                   # commitment
            None,                                   # root_cause
            False,                                  # root_cause_identified
            BSA_OFFICER_ID,
            identified,
            due,
            "validated_closed",                     # status
            None,                                   # evidence_s3_uri
            (f"Projected from real enforcement action {a['action_ref']}; "
             f"not issued against {LEGAL_NAME}. "
             f"Source: {a['source_url'] or 'n/a'}")[:2000],
            False,                                  # repeat_flag
            None,                                   # repeat_of_finding_id
        ))

    await conn.executemany("""
        INSERT INTO findings
            (finding_id, bank_id, type, severity, regulatory_source,
             concern, cause, consequence, corrective_action, commitment,
             root_cause, root_cause_identified, owner_person_id,
             identified_date, due_date, status, evidence_s3_uri,
             validation_notes, repeat_flag, repeat_of_finding_id)
        VALUES ($1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10,
                $11, $12, $13,
                $14, $15, $16, $17,
                $18, $19, $20)
        ON CONFLICT (finding_id) DO UPDATE SET
            type = EXCLUDED.type,
            severity = EXCLUDED.severity,
            concern = EXCLUDED.concern,
            regulatory_source = EXCLUDED.regulatory_source,
            status = EXCLUDED.status,
            validation_notes = EXCLUDED.validation_notes,
            updated_at = NOW()
    """, rows)
    return len(rows)


async def _seed_ofac_validation(conn: asyncpg.Connection) -> None:
    # Deterministic validation_id so re-runs don't churn the row.
    today = date.today()
    quarter = (today.month - 1) // 3 + 1
    validation_id = uuid.uuid5(
        NS, f"{BANK_ID}:ofac_validation:{today.year}Q{quarter}")

    await conn.execute("""
        INSERT INTO ofac_validations
            (validation_id, bank_id, performed_on, performed_by_person_id,
             scope, methodology, findings_summary, next_due)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (validation_id) DO UPDATE SET
            performed_on = EXCLUDED.performed_on,
            scope = EXCLUDED.scope,
            methodology = EXCLUDED.methodology,
            findings_summary = EXCLUDED.findings_summary,
            next_due = EXCLUDED.next_due
    """,
        validation_id, BANK_ID, today - timedelta(days=30), BSA_OFFICER_ID,
        f"Q{quarter} {today.year} OFAC SDN list validation — "
        "Test Bank, N.A. all party-screening pipelines",
        ("Sampled 100 wire transfers and 100 new-account openings against the "
         "current OFAC SDN snapshot; manually re-screened all 200 against the "
         "official Treasury SDN search tool; reviewed all fuzzy matches above "
         "85% similarity threshold."),
        "No discrepancies between automated screening and manual re-screen.",
        today + timedelta(days=60),
    )


async def _seed_dispositions(conn: asyncpg.Connection) -> int:
    # Distinct change_refs because change_record_dispositions has
    # UNIQUE (bank_id, change_ref); a regulator can produce duplicate
    # change_refs across re-detections.
    records = await conn.fetch("""
        SELECT DISTINCT ON (change_ref)
               change_ref, detected_at
          FROM regulatory_change_records
         WHERE is_active = TRUE
         ORDER BY change_ref, detected_at DESC
         LIMIT $1
    """, DISPOSITIONS_CAP)
    if not records:
        return 0

    rows = [
        (r["change_ref"], BANK_ID, "acknowledged", BSA_OFFICER_ID,
         "closed",
         r["detected_at"] + timedelta(days=5) if r["detected_at"] else None,
         f"{BSA_OFFICER_NAME} (bulk seed)")
        for r in records
    ]
    await conn.executemany("""
        INSERT INTO change_record_dispositions
            (change_ref, bank_id, disposition, owner_person_id,
             status, dispositioned_at, dispositioned_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (bank_id, change_ref) DO NOTHING
    """, rows)
    return len(rows)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
