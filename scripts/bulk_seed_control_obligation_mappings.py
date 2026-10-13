"""Bulk-seed control_obligation_mappings from control_rule_mappings.

Ships the R14 follow-on to R11 (migration 044). R11 landed
``control_obligation_mappings`` empty by design so the P2 widget could
degrade honestly ("Obligation attribution") on any bank whose ops
hadn't yet authored obligation-level control mappings. This script
authors the first population — a deterministic derivation from the
rule-level mappings ops already maintain in ``control_rule_mappings``.

Why derive from ``control_rule_mappings`` (rule-level) instead of
asking ops to hand-author obligation-level rows: pilot banks typically
carry hundreds of controls × thousands of obligations, and hand-
authoring the cross-product on day one is a non-starter. The R14
derivation gives every bank a defensible starting position that ops
can then refine (upgrade strengths, add rationale, retire misfits) at
their own pace. R11's schema was written for authored rows; bulk-
seeded rows carry a clear provenance marker so examiners can see
which mappings were derived vs which were curated.

### Derivation

For each live ``control_rule_mappings`` row (``superseded_at IS NULL``)
we look up every active obligation of the rule
(``obligations.regulation_id = crm.rule_id AND is_active = TRUE``) and
insert one ``control_obligation_mappings`` row per (control, obligation).
Strength propagates with a **conservative demotion**:

    control_rule_mappings.strength   →   bulk-seeded row strength
    ────────────────────────────────────────────────────────────────
    'full'                           →   'full'
    'partial'                        →   'related'   (demoted)
    'related'                        →   'related'

Reasoning: a `full` rule-level mapping asserts the control satisfies
the entire rule end-to-end, so every obligation *of that rule* is
implicitly satisfied — propagating `full` is defensible. A `partial`
rule mapping means the control addresses *some* of the rule; without
obligation-level authorial intent, we cannot claim `partial` on each
obligation individually (which would inflate the widget). Demoting
`partial` → `related` keeps the audit trail honest: examiners see
that these are derivations, not assertions.

### Rationale text

Every bulk-seeded row carries a rationale of the form::

    Bulk-seed from control_rule_mappings (rule <rule_id>, strength
    <original_strength>, derived <derived_strength>). Ops should
    review and refine before examiner presentation.

The ``created_by`` column is set to ``r14_bulk_seed`` so a follow-up
audit query can distinguish derived rows from human-authored ones::

    SELECT COUNT(*) FROM control_obligation_mappings
     WHERE created_by = 'r14_bulk_seed' AND bank_id = :bid

### Idempotency

``INSERT ... ON CONFLICT ON CONSTRAINT uq_com_bank_control_obligation
DO NOTHING``. Re-runs are safe. The script never updates existing
rows — if ops has already refined a mapping (upgraded strength, added
rationale), the bulk-seed leaves it alone. If ops has explicitly
superseded a bulk-seeded row, this script does NOT resurrect it —
the unique constraint on ``(bank_id, control_id, obligation_id)``
covers superseded rows too, so `ON CONFLICT` catches them.

### Usage

    # dry-run — reports planned row count + top-10 controls by
    # obligation fan-out, no writes
    DRY_RUN=1 python -m scripts.bulk_seed_control_obligation_mappings

    # apply
    DRY_RUN=0 python -m scripts.bulk_seed_control_obligation_mappings

    # scope to one bank (default: all banks with rule mappings)
    BANK_ID=testbank DRY_RUN=0 \\
        python -m scripts.bulk_seed_control_obligation_mappings

Env vars follow the other seed scripts (AURORA_HOST, AURORA_PORT,
DB_NAME, DB_USER, DB_PASSWORD).
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

import asyncpg


CREATED_BY = "r14_bulk_seed"

# Demotion map — full stays full, partial demotes to related, related
# stays related. Keep in sync with the docstring above and R14 doc.
_STRENGTH_DEMOTION = {
    "full":    "full",
    "partial": "related",
    "related": "related",
}


# ── SQL ────────────────────────────────────────────────────────────

# Planned-rows query — one row per (bank, control, obligation) derivable
# from a live rule mapping, joined to active obligations. Aggregates
# strength so we know how to demote. WHERE clauses filter to live rule
# mappings + active obligations; the ANTI-JOIN excludes rows that
# already exist in control_obligation_mappings so DRY_RUN shows the
# actual delta.
_PLANNED_SQL = """
    SELECT crm.bank_id,
           crm.control_id,
           o.obligation_id,
           crm.rule_id,
           crm.mapping_strength AS source_strength
      FROM control_rule_mappings crm
      JOIN obligations o
        ON o.regulation_id = crm.rule_id
       AND o.is_active = TRUE
     WHERE crm.superseded_at IS NULL
       AND ($1::text IS NULL OR crm.bank_id = $1)
       AND NOT EXISTS (
             SELECT 1 FROM control_obligation_mappings com
              WHERE com.bank_id = crm.bank_id
                AND com.control_id = crm.control_id
                AND com.obligation_id = o.obligation_id
           )
"""

# Insert derived rows. Rationale + created_by carry the R14 provenance.
_INSERT_SQL = """
    INSERT INTO control_obligation_mappings
        (bank_id, control_id, obligation_id, mapping_strength,
         rationale, created_by, effective_from)
    VALUES ($1, $2, $3, $4, $5, $6, CURRENT_DATE)
    ON CONFLICT ON CONSTRAINT uq_com_bank_control_obligation DO NOTHING
"""

# Per-bank fan-out summary (dry-run readout).
_FANOUT_SQL = """
    SELECT crm.bank_id,
           COUNT(DISTINCT crm.control_id)         AS controls,
           COUNT(DISTINCT o.obligation_id)        AS obligations,
           COUNT(*)                                AS mappings_derivable
      FROM control_rule_mappings crm
      JOIN obligations o
        ON o.regulation_id = crm.rule_id
       AND o.is_active = TRUE
     WHERE crm.superseded_at IS NULL
       AND ($1::text IS NULL OR crm.bank_id = $1)
     GROUP BY crm.bank_id
     ORDER BY crm.bank_id
"""

# Top-10 controls by obligation fan-out — helps ops spot outliers
# where one control balloons into hundreds of obligation mappings.
_TOP_CONTROLS_SQL = """
    SELECT crm.bank_id,
           crm.control_id,
           COUNT(DISTINCT o.obligation_id) AS obligation_count,
           MIN(crm.mapping_strength) AS strength
      FROM control_rule_mappings crm
      JOIN obligations o
        ON o.regulation_id = crm.rule_id
       AND o.is_active = TRUE
     WHERE crm.superseded_at IS NULL
       AND ($1::text IS NULL OR crm.bank_id = $1)
     GROUP BY crm.bank_id, crm.control_id
     ORDER BY obligation_count DESC
     LIMIT 10
"""


def _rationale(rule_id: str, source: str, derived: str) -> str:
    return (
        f"Bulk-seed from control_rule_mappings (rule {rule_id}, "
        f"strength {source}, derived {derived}). Ops should review and "
        f"refine before examiner presentation."
    )


async def main() -> int:
    dry_run = os.environ.get("DRY_RUN", "1") != "0"
    bank_filter: Optional[str] = os.environ.get("BANK_ID") or None

    conn = await asyncpg.connect(
        host=os.environ["AURORA_HOST"],
        port=int(os.environ.get("AURORA_PORT", "5432")),
        database=os.environ["DB_NAME"],
        user=os.environ.get("DB_USER", "fortress_testbank"),
        password=os.environ["DB_PASSWORD"],
        ssl="require",
    )

    try:
        # Fan-out summary — same query the resolver walks minus the
        # anti-join. Gives ops a "what am I about to write" readout.
        fanout = await conn.fetch(_FANOUT_SQL, bank_filter)
        print(
            f"bulk_seed_control_obligation_mappings: bank_filter="
            f"{bank_filter or '(all)'}"
        )
        if not fanout:
            print("  no live rule mappings found; nothing to do")
            return 0

        print(f"  {'bank_id':<32} {'controls':>8} {'obligations':>12} "
              f"{'mappings':>10}")
        for r in fanout:
            print(
                f"  {r['bank_id']:<32} {r['controls']:>8} "
                f"{r['obligations']:>12} {r['mappings_derivable']:>10}"
            )

        # Delta — rows that would actually be inserted (anti-joined
        # against existing control_obligation_mappings).
        planned = await conn.fetch(_PLANNED_SQL, bank_filter)
        print(f"  new rows to insert (delta): {len(planned)}")

        top = await conn.fetch(_TOP_CONTROLS_SQL, bank_filter)
        if top:
            print("  top-10 controls by obligation fan-out:")
            for r in top:
                print(
                    f"    {r['bank_id']:<24} {r['control_id']:<30} "
                    f"{r['obligation_count']:>6} obligations "
                    f"(strength {r['strength']})"
                )

        if dry_run:
            print("DRY_RUN=1 — no writes. Set DRY_RUN=0 to apply.")
            return 0

        # Apply — single transaction. Iterate the planned set so we can
        # compute the demotion + rationale per row rather than pushing
        # it into pure SQL (keeps the logic auditable in one place).
        inserted = 0
        strength_hist = {"full": 0, "partial": 0, "related": 0}
        async with conn.transaction():
            for row in planned:
                source_strength = (row["source_strength"] or "full").lower()
                derived = _STRENGTH_DEMOTION.get(source_strength, "related")
                strength_hist[derived] = strength_hist.get(derived, 0) + 1
                result = await conn.execute(
                    _INSERT_SQL,
                    row["bank_id"],
                    row["control_id"],
                    row["obligation_id"],
                    derived,
                    _rationale(row["rule_id"], source_strength, derived),
                    CREATED_BY,
                )
                if result.endswith(" 1"):
                    inserted += 1

        print(
            f"bulk_seed_control_obligation_mappings: inserted={inserted} "
            f"strength_hist={strength_hist}"
        )
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
