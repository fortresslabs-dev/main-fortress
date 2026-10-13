"""scripts.demo.seed — idempotent demo seed for the testbank dashboard.

Builds on `scripts.seed_demo_p2` (which seeds compliance_rules, changes,
deadlines, baseline workflows) and adds the pieces the demo-readiness
runbook §4 requires:

  1. Bank registration row in `client_credentials` for testbank-demo with a
     known API key hash (canonical SHA256(`a`*64) — matches the integration
     test conftest so contract tests + dashboard hit the same credential).
  2. sync_versions(id=1) seeded so `/sync/postgres` never returns NULL.
  3. sync_metadata row for testbank-demo so `/sync/initial` re-pages cleanly.
  4. 20 OFAC SDN entries with mixed entity types (individual / alias /
     entity / vessel) and matching `ofac_sdn_vectors` rows (1 per name
     variant). Prefixed `DEMO-` so they cannot collide with real OFAC scrape
     output.
  5. semantic_diff JSON populated on every regulatory_change_record so the
     dashboard /changes panel passes its commit-27c3bae regression guard.
  6. Workflow status mix per runbook §4:
        - 2 in `waiting_approval` (no checkpoint approval row yet)
        - 1 `completed` today with an approved checkpoint_approval
        - 1 `failed` today with a rejected checkpoint_approval
        - 1 `running` (in-progress, no approval row)

Idempotent against an existing populated DB — every section uses ON CONFLICT
upserts or DELETE-on-deterministic-key + INSERT. Safe to run repeatedly.

Run:
    python -m scripts.demo.seed                 # local docker-compose Postgres
    python -m scripts.demo.seed --no-base       # skip the underlying seed_demo_p2

The "--no-base" flag is for re-runs that only need the new sections (e.g. a
quick refresh of SDN + change semantic_diff after a partial wipe).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import create_async_engine

from scripts.seed_demo_p2 import seed as seed_base
from src.utils.config import settings


# ── Canonical demo credentials — same as tests/integration/api/conftest.py ──
TESTBANK_CUSTOMER_ID = "testbank-demo"
TESTBANK_BANK_NAME = "Demo Test Bank"
TESTBANK_API_KEY = "a" * 64                        # raw key, never persisted
TESTBANK_API_KEY_HASH = hashlib.sha256(TESTBANK_API_KEY.encode()).hexdigest()


# ── SDN demo set ───────────────────────────────────────────────────────────
# DEMO- prefixed so production OFAC scrapes can never collide. Mix of entity
# types covers the screening-class breakdown the calibration runbook uses.

SDN_ENTRIES = [
    # (sdn_number, entity_name, entity_type, programs, aliases, country, remarks)
    ("DEMO-0001", "Ivan Petrovich Volkov",       "individual",
     ["UKRAINE-EO13660"], ["I.P. Volkov", "Ivan Volkoff"], "Russia",
     "DOB 1968-03-12. Banking sector — Sovcombank affiliate."),
    ("DEMO-0002", "Mehdi Reza Karami",           "individual",
     ["IRAN", "SDGT"],    ["M. Karami", "Mehdi Karami"], "Iran",
     "IRGC-QF financial facilitator."),
    ("DEMO-0003", "Aleksandr Sergeyevich Orlov", "individual",
     ["UKRAINE-EO13662"], ["Alex Orlov", "A.S. Orlov"], "Russia",
     "State Duma deputy. Sanctioned 2022-03."),
    ("DEMO-0004", "Hassan Abdullah Al-Qassem",   "individual",
     ["SDGT"],            ["H. Al-Qassem", "Hassan Qassem"], "Lebanon",
     "Hezbollah-linked financier."),
    ("DEMO-0005", "Dimitri Volkov",              "individual",
     ["UKRAINE-EO13660"], ["D. Volkov"], "Russia",
     "Common-name false-positive bait — distinct DOB 1972-09-04."),
    ("DEMO-0006", "Wei Jiang Liu",               "individual",
     ["NORTH-KOREA"],     ["W.J. Liu", "Wei Liu"], "China",
     "DPRK proliferation finance — Dandong-based broker."),
    ("DEMO-0007", "Carlos Mendoza Reyes",        "individual",
     ["SDNTK"],           ["C. Mendoza", "Carlos Reyes"], "Mexico",
     "Cartel del Golfo financial cell."),
    ("DEMO-0008", "Zaynab Faridun Hosseini",     "individual",
     ["IRAN"],            ["Z. Hosseini"], "Iran",
     "Front-company beneficial owner."),
    ("DEMO-0009", "Pavel Mikhailov",             "individual",
     ["UKRAINE-EO13662"], ["P. Mikhailov", "Pavlo Mykhailov"], "Russia",
     "Cyber-sector sanctioned individual."),
    ("DEMO-0010", "Saeed Tehrani",               "individual",
     ["IRAN", "SDGT"],    ["S. Tehrani"], "Iran",
     "Crypto-mixer operator."),

    ("DEMO-0101", "Northstar Maritime Holdings LLC", "entity",
     ["RUSSIA-EO14024"], ["Northstar Maritime", "NMH LLC"], "Cyprus",
     "Shadow-fleet shipping front."),
    ("DEMO-0102", "Bahar Pars Trading Co.",          "entity",
     ["IRAN"], ["Bahar Pars", "BPT Co"], "Iran",
     "Petrochemical export front."),
    ("DEMO-0103", "Mercury Capital Partners FZE",     "entity",
     ["RUSSIA-EO14024"], ["Mercury Capital", "MCP FZE"], "United Arab Emirates",
     "Sanctioned-Russian-affiliate fund."),
    ("DEMO-0104", "Crimson River Logistics SA",       "entity",
     ["VENEZUELA"], ["Crimson River", "CRL SA"], "Panama",
     "Oil-shipping front."),
    ("DEMO-0105", "Gulf Star Petrochemicals",         "entity",
     ["IRAN"], ["Gulf Star Petro", "GSP"], "Iran",
     "NIOC subsidiary."),

    ("DEMO-0201", "M/V SEA ORACLE",                  "vessel",
     ["RUSSIA-EO14024"], ["SEA ORACLE"], "Panama",
     "Shadow-fleet tanker. IMO 9876543."),
    ("DEMO-0202", "M/V BLUE TRADER",                 "vessel",
     ["IRAN"], ["BLUE TRADER"], "Iran",
     "Sanctioned IRISL vessel. IMO 9123456."),
    ("DEMO-0203", "M/V NORTHERN STAR",               "vessel",
     ["NORTH-KOREA"], ["NORTHERN STAR"], "Mongolia",
     "DPRK coal export tanker. IMO 9555111."),

    ("DEMO-0301", "Aircraft EP-IRH",                 "aircraft",
     ["IRAN"], ["EP-IRH"], "Iran",
     "Mahan Air aircraft — sanctioned IRGC airlift."),
    ("DEMO-0302", "Aircraft RA-67222",               "aircraft",
     ["RUSSIA-EO14024"], ["RA-67222"], "Russia",
     "FSB-linked aircraft."),
]


def _normalize_name(raw: str) -> str:
    """Match the canonical OFAC normalize_name (validators.py:_normalise_name).

    Memory `feedback_ofac_normalize_pair` — this MUST stay in sync with
    `src/services/ofac_sdn_loader.py:normalize_name` so the screener's
    lookup matches the loader's index keys.
    """
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", raw)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper()
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ── Workflow + approval mix (overrides scripts.seed_demo_p2.WORKFLOWS) ────
# Runbook §4 distribution: 2 awaiting_approval, 1 approved today, 1 rejected,
# 1 in-progress.

DEMO_WORKFLOWS = [
    # (jira_id, jira_key, status, started_offset_hours, completed_offset_hours,
    #  artifacts_dict, checkpoint: None | ("approved"|"rejected", role, note))
    ("90001", "DEMO-901", "waiting_approval", 4, None,
     {"rule_ref": "BSA-CTR-10K"}, None),
    ("90002", "DEMO-902", "waiting_approval", 3, None,
     {"rule_ref": "OFAC-SDN-SCREEN"}, None),
    ("90003", "DEMO-903", "completed", 6, 5,
     {"rule_ref": "OCC-WIRE-DUAL",
      "pr_url": "https://github.com/testbank/compliance-tests/pull/903"},
     ("approved", "CCO", "Looks good — control covered by existing wire test.")),
    ("90004", "DEMO-904", "failed", 8, 7,
     {"rule_ref": "CFPB-1071-SBL",
      "error": "Rejected at framework selection — needs Tier-2 data set."},
     ("rejected", "CRO", "Out of scope for this quarter.")),
    ("90005", "DEMO-905", "running", 1, None,
     {"rule_ref": "BSA-SAR-5K"}, None),
]


async def seed_demo(engine, run_base: bool = True) -> None:
    if run_base:
        await seed_base(engine)

    async with engine.begin() as conn:
        # 1. Bank registration ------------------------------------------------
        await conn.execute(text("""
            INSERT INTO client_credentials
              (id, customer_id, bank_name, api_key_hash, is_active, tier, created_at)
            VALUES
              (:id, :cid, :name, :hash, TRUE, 'standard', NOW())
            ON CONFLICT (customer_id) DO UPDATE
              SET bank_name = EXCLUDED.bank_name,
                  api_key_hash = EXCLUDED.api_key_hash,
                  is_active = TRUE
        """), {
            "id": str(uuid.uuid4()),
            "cid": TESTBANK_CUSTOMER_ID,
            "name": TESTBANK_BANK_NAME,
            "hash": TESTBANK_API_KEY_HASH,
        })

        # 2. sync_versions seed -----------------------------------------------
        await conn.execute(text("""
            INSERT INTO sync_versions (id, current_version, updated_at)
            VALUES (1, 1, NOW())
            ON CONFLICT (id) DO UPDATE
              SET current_version = GREATEST(sync_versions.current_version, 1),
                  updated_at = NOW()
        """))

        # 3. sync_metadata for testbank-demo ---------------------------------
        await conn.execute(text("""
            INSERT INTO sync_metadata
              (customer_id, last_sync_version, last_sync_timestamp, sync_count)
            VALUES (:cid, 1, NOW(), 1)
            ON CONFLICT (customer_id) DO UPDATE
              SET last_sync_version = EXCLUDED.last_sync_version,
                  last_sync_timestamp = EXCLUDED.last_sync_timestamp,
                  updated_at = NOW()
        """), {"cid": TESTBANK_CUSTOMER_ID})

        # 4. OFAC SDN entries + vectors --------------------------------------
        sdn_numbers = [e[0] for e in SDN_ENTRIES]
        await conn.execute(
            text("DELETE FROM ofac_sdn_vectors WHERE sdn_number IN :n")
                .bindparams(bindparam("n", expanding=True)),
            {"n": sdn_numbers},
        )
        await conn.execute(
            text("DELETE FROM ofac_sdn_entries WHERE sdn_number IN :n")
                .bindparams(bindparam("n", expanding=True)),
            {"n": sdn_numbers},
        )

        for (sdn_no, name, etype, programs, aliases, country, remarks) in SDN_ENTRIES:
            await conn.execute(text("""
                INSERT INTO ofac_sdn_entries
                  (sdn_number, entity_name, entity_type,
                   programs, aliases, country, remarks,
                   is_active, source_updated_at)
                VALUES
                  (:sdn, :name, :etype,
                   CAST(:prog AS JSON), CAST(:alias AS JSON),
                   :country, :remarks, TRUE, NOW())
            """), {
                "sdn": sdn_no, "name": name, "etype": etype,
                "prog": json.dumps(programs),
                "alias": json.dumps(aliases),
                "country": country, "remarks": remarks,
            })

            # ofac_sdn_vectors — one row for primary name + one per alias.
            variants = [(name, True)] + [(a, False) for a in aliases]
            for (variant, is_primary) in variants:
                await conn.execute(text("""
                    INSERT INTO ofac_sdn_vectors
                      (sdn_number, name_variant, name_normalized, is_primary,
                       entity_type, programs, country, is_active,
                       missed_pulls, last_seen_at, source_updated_at, created_at)
                    VALUES
                      (:sdn, :raw, :norm, :prim,
                       :etype, CAST(:prog AS JSON), :country, TRUE,
                       0, NOW(), NOW(), NOW())
                    ON CONFLICT (sdn_number, name_normalized) DO NOTHING
                """), {
                    "sdn": sdn_no,
                    "raw": variant,
                    "norm": _normalize_name(variant),
                    "prim": is_primary,
                    "etype": etype,
                    "prog": json.dumps(programs),
                    "country": country,
                })

        # 5. semantic_diff on every change record ----------------------------
        # Used by the dashboard /changes panel (commit 27c3bae regression).
        await conn.execute(text("""
            UPDATE regulatory_change_records
            SET semantic_diff = COALESCE(NULLIF(semantic_diff, ''),
                                         '{"added":[],"removed":[],"modified":[]}')
            WHERE change_ref LIKE 'CHG-2026-%'
        """))
        # Specialise one row so the demo storyline has something concrete on screen.
        await conn.execute(text("""
            UPDATE regulatory_change_records
            SET semantic_diff = :diff
            WHERE change_ref = 'CHG-2026-0412'
        """), {
            "diff": json.dumps({
                "added": [],
                "removed": [],
                "modified": [
                    {"clause": "beneficial_ownership_threshold_pct",
                     "before": 25, "after": 20,
                     "effective": "2026-07-11"},
                ],
            }),
        })

        # 6. Workflow approval mix -------------------------------------------
        # The base seed inserts a different set; we layer our DEMO-9xx rows
        # on top so the runbook's status distribution is visible regardless.
        demo_jiras = [w[0] for w in DEMO_WORKFLOWS]
        await conn.execute(
            text("""DELETE FROM checkpoint_approvals
                    WHERE workflow_id IN (
                      SELECT id FROM workflow_executions
                      WHERE jira_story_id IN :ids
                    )""")
                .bindparams(bindparam("ids", expanding=True)),
            {"ids": demo_jiras},
        )
        await conn.execute(
            text("DELETE FROM workflow_executions WHERE jira_story_id IN :ids")
                .bindparams(bindparam("ids", expanding=True)),
            {"ids": demo_jiras},
        )

        # workflow_executions.started_at is TIMESTAMP WITHOUT TIME ZONE
        # (model uses naive datetime); pass naive UTC to avoid asyncpg
        # "can't subtract offset-naive and offset-aware datetimes".
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        for (jid, jkey, status, off_h, comp_h, artifacts, approval) in DEMO_WORKFLOWS:
            started = now_naive - timedelta(hours=off_h)
            completed = now_naive - timedelta(hours=comp_h) if comp_h else None
            wf_id = uuid.uuid4()
            await conn.execute(text("""
                INSERT INTO workflow_executions
                  (id, jira_story_id, jira_story_key, status,
                   started_at, completed_at, artifacts, context_data)
                VALUES
                  (:id, :jid, :jkey, :status, :started, :completed,
                   CAST(:art AS JSON), CAST('{}' AS JSON))
            """), {
                "id": wf_id, "jid": jid, "jkey": jkey, "status": status,
                "started": started, "completed": completed,
                "art": json.dumps(artifacts),
            })

            if approval is not None:
                decision, role, note = approval
                await conn.execute(text("""
                    INSERT INTO checkpoint_approvals
                      (id, workflow_id, checkpoint_name, status,
                       approved_by, approved_at, feedback)
                    VALUES
                      (gen_random_uuid(), :wid, 'pre_generation', :dec,
                       :email, NOW() - INTERVAL '1 hour',
                       CAST(:fb AS JSON))
                """), {
                    "wid": wf_id, "dec": decision,
                    "email": f"{role.lower()}@testbank.demo",
                    "fb": json.dumps({"role": role, "note": note}),
                })

    # Summary ---------------------------------------------------------------
    async with engine.connect() as conn:
        print("Demo seed summary:")
        for tbl in (
            "client_credentials",
            "sync_metadata",
            "compliance_rules",
            "regulatory_change_records",
            "compliance_deadlines",
            "ofac_sdn_entries",
            "ofac_sdn_vectors",
            "workflow_executions",
            "checkpoint_approvals",
        ):
            n = await conn.scalar(text(f"SELECT COUNT(*) FROM {tbl}"))
            print(f"  {tbl}: {n}")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--no-base", action="store_true",
                   help="Skip scripts.seed_demo_p2 base seed; only run the demo "
                        "extensions (bank reg, SDN, semantic_diff, approvals).")
    args = p.parse_args()

    url = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url, echo=False)
    try:
        print(f"Seeding demo state into {url.split('@')[-1]} ...")
        await seed_demo(engine, run_base=not args.no_base)
        print("Done.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
