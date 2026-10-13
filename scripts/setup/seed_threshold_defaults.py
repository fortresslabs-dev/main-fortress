"""One-shot: seed compliance_rules.threshold_default from resolver fallbacks.

Migration 039 added three columns: threshold_default (JSONB),
threshold_rationale (TEXT), threshold_source_citation (TEXT). Until
those columns are populated, ComplianceRuleResolver Tier 4 keeps
returning hardcoded Python constants — meaning the rule resolver still
isn't auditable as a system-of-record.

This script copies every key from
``src/services/compliance_rule_resolver.py:_FALLBACK_THRESHOLDS`` into a
matching compliance_rules row keyed by ``domain``. If no row exists for
the domain (greenfield Aurora), one is created with category='fallback'
so downstream code can still resolve it.

Idempotent: re-running is safe — only writes the columns if they're NULL
or differ. Add ``--dry-run`` to report counts without writing.

Usage:
    PIPELINE_MODE=pipeline1 python -m scripts.setup.seed_threshold_defaults
    PIPELINE_MODE=pipeline1 python -m scripts.setup.seed_threshold_defaults --dry-run

After this runs you can safely point the resolver at the DB columns
(follow-up — out of scope here per Q6).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import structlog
from sqlalchemy import text

from src.services.compliance_rule_resolver import _FALLBACK_THRESHOLDS
from src.services.database import close_db, get_db, init_db

logger = structlog.get_logger(__name__)


# Per-domain rationale + source citation. Lives here (not in the resolver)
# because it's metadata that belongs with the seed step, not with the
# runtime lookup. The CCO can override either text via UPDATE without
# changing code.
_DOMAIN_METADATA: dict[str, dict[str, str]] = {
    "wire_transfer": {
        "rationale": (
            "Wire transfer thresholds are mandated by the BSA Currency "
            "Transaction Reporting rule (31 CFR 1010.311) and the FinCEN "
            "Travel Rule (31 CFR 1010.410(f)). The dual-approval level "
            "mirrors OCC operational-risk guidance on segregation of "
            "duties for large outbound wires."
        ),
        "citation": (
            "31 CFR 1010.311 (CTR); 31 CFR 1010.410(f) (Travel Rule); "
            "OCC Bulletin 2017-7 §III(B) (operational-risk segregation)."
        ),
    },
    "sar": {
        "rationale": (
            "SAR thresholds derive from BSA 31 CFR 1020.320 — $5,000 for "
            "general suspicious activity, $0 (any amount) for insider abuse."
        ),
        "citation": "31 CFR 1020.320(a)(2); FFIEC BSA/AML Manual SAR §V.",
    },
    "ctr": {
        "rationale": (
            "Currency Transaction Report required for cash transactions "
            ">= $10,000 in a single business day under BSA."
        ),
        "citation": "31 CFR 1010.311; FinCEN FIN-2012-G005.",
    },
    "ofac": {
        "rationale": (
            "Fuzzy match threshold of 85% was calibrated against the OFAC "
            "synthetic gold set (F1>=0.90, recall>=0.95) per the per-class "
            "calibration runbook. Blocked-transaction reporting window of "
            "10 business days is mandated by 31 CFR 501.604."
        ),
        "citation": (
            "31 CFR 501.604 (reporting); OFAC 2019 Framework for OFAC "
            "Compliance Commitments §A.4; internal calibration "
            "ofac_calibration_gold.csv."
        ),
    },
    "reg_e": {
        "rationale": (
            "Reg E error-resolution windows are statutory under 12 CFR "
            "1005.11. The 60/10/45/90 day values are the consumer reporting "
            "window, provisional credit window, standard investigation "
            "window, and extended new-account window respectively."
        ),
        "citation": "12 CFR 1005.11(b)(1), (c)(2)(i)(A), (c)(3)(ii).",
    },
    "kyc_cdd": {
        "rationale": (
            "FinCEN CDD beneficial-ownership threshold amended from 25% "
            "to 20% by CHG-2026-0412 (effective 2026-04-11) following the "
            "Corporate Transparency Act alignment rulemaking. Resolver "
            "constant tracks the current effective value."
        ),
        "citation": (
            "31 CFR 1010.230(d)(1), as amended by FinCEN CHG-2026-0412; "
            "Corporate Transparency Act §6403."
        ),
    },
}


async def seed(dry_run: bool) -> dict[str, int]:
    updated = inserted = skipped = 0
    async with get_db() as session:
        for domain, sub_map in _FALLBACK_THRESHOLDS.items():
            default = {k: v["threshold"] for k, v in sub_map.items()}
            meta = _DOMAIN_METADATA.get(domain, {
                "rationale": "Resolver fallback (no detailed rationale on file).",
                "citation": "src/services/compliance_rule_resolver.py:_FALLBACK_THRESHOLDS",
            })

            # Find any compliance_rules row keyed by this domain. We prefer
            # an existing rule if one exists so we don't introduce phantom
            # rules; create a placeholder only if no match.
            existing = (await session.execute(text("""
                SELECT rule_id, threshold_default, threshold_rationale, threshold_source_citation
                FROM compliance_rules
                WHERE domain = :domain
                ORDER BY (threshold_default IS NULL), version DESC NULLS LAST
                LIMIT 1
            """), {"domain": domain})).first()

            payload = {
                "default": json.dumps(default),
                "rationale": meta["rationale"],
                "citation": meta["citation"],
            }

            if existing is None:
                if dry_run:
                    inserted += 1
                    logger.info("would_insert_fallback_rule", domain=domain)
                    continue
                await session.execute(text("""
                    INSERT INTO compliance_rules (
                        rule_id, regulator, category, geography, domain,
                        rule_type, rule_text,
                        threshold_default, threshold_rationale,
                        threshold_source_citation,
                        is_active, created_at, updated_at
                    ) VALUES (
                        :rule_id, 'MULTI', 'fallback', 'US', :domain,
                        'threshold_default', :rule_text,
                        CAST(:default AS jsonb), :rationale, :citation,
                        TRUE, now(), now()
                    )
                """), {
                    "rule_id": f"FALLBACK-THRESHOLD-{domain.upper()}",
                    "domain": domain,
                    "rule_text": (
                        f"Resolver fallback thresholds for domain {domain}. "
                        f"See threshold_rationale for documentation."
                    ),
                    **payload,
                })
                inserted += 1
                continue

            already_matches = (
                existing.threshold_default == default
                and existing.threshold_rationale == meta["rationale"]
                and existing.threshold_source_citation == meta["citation"]
            )
            if already_matches:
                skipped += 1
                continue

            if dry_run:
                updated += 1
                logger.info("would_update_rule", rule_id=existing.rule_id, domain=domain)
                continue

            await session.execute(text("""
                UPDATE compliance_rules
                SET threshold_default          = CAST(:default AS jsonb),
                    threshold_rationale        = :rationale,
                    threshold_source_citation  = :citation,
                    updated_at                 = now()
                WHERE rule_id = :rule_id
            """), {"rule_id": existing.rule_id, **payload})
            updated += 1

        if not dry_run:
            await session.commit()

    return {"updated": updated, "inserted": inserted, "skipped": skipped}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report counts without writing")
    args = parser.parse_args()

    await init_db()
    try:
        stats = await seed(dry_run=args.dry_run)
    finally:
        await close_db()

    logger.info("threshold_seed_done", dry_run=args.dry_run, **stats)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
