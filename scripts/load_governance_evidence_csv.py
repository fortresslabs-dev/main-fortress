"""Load governance_evidence rows from a CSV round-trip.

Ships the R15 follow-on to R12 (migration 045). R12 landed the
``governance_evidence`` schema empty by design so the P7 widget could
degrade honestly to the "Risk-attribution proxy" label on any bank
whose ops hadn't yet uploaded artifacts. This script is the authoring
path — a CSV round-trip that ops (compliance analyst) can run per
bank, per rotation, to keep the widget on the real-registry read.

Why CSV (not a dashboard authoring UI):
  * Governance artifacts arrive as PDFs uploaded to S3 by the
    compliance team's existing workflow (annual board minutes,
    training records exported from the LMS, ERA signed off in the
    ERM tool). There is no dashboard-native upload step in the pilot
    scope — the artifacts already exist in S3, they just need to be
    catalogued.
  * A CSV round-trip is auditable end-to-end: the file that seeded
    a bank's evidence is itself the audit trail. Examiners can be
    handed the CSV alongside the S3 URIs.
  * Rebuilding a bank's evidence set after a data loss is one
    ``python -m scripts.load_governance_evidence_csv --apply`` away.
  * A dashboard UI is R16 — not gating this population fix.

### CSV shape

Required columns (comma-separated header, one artifact per row)::

    bank_id, evidence_type, artifact_ref, source, attested_by,
    effective_from, effective_to, notes

- ``evidence_type`` must be one of the eight canonical types
  (``_CANONICAL_TYPES`` below — matches the ``ck_ge_evidence_type``
  CHECK on the table and ``_CANONICAL_GOVERNANCE_TYPES`` in
  ``src/api/dashboard_api.py``). Non-canonical rows are rejected
  with a validation error, not silently loaded.
- ``artifact_ref`` optional but strongly encouraged. Must be either
  an ``s3://`` URI, an ``https://`` URL, or a bare doc-id. Bad
  values are rejected.
- ``effective_from`` / ``effective_to`` optional ISO dates
  (``YYYY-MM-DD``). NULL means unbounded (rare for governance
  evidence — most artifacts rotate on an annual cycle).
- ``source``, ``attested_by``, ``notes`` free text for audit
  trail. Empty strings are stored as NULL.

### Supersede semantics

The R12 schema carries a partial UNIQUE on
``(bank_id, evidence_type) WHERE superseded_at IS NULL`` — at most
one live row per (bank, type). Two upload modes:

- **default (append-only)**. If a live row already exists for the
  (bank, evidence_type) pair, the CSV row is *skipped* and reported
  as ``already_present``. Safe for re-runs; the count of "seed"
  operations is deterministic across runs.
- **``--supersede`` mode**. For every CSV row where a live row
  already exists, the loader first sets ``superseded_at=NOW()`` on
  the prior row and then inserts the CSV row. This is the annual-
  rotation path — a board acknowledgement replacing last year's,
  a policy manual v7 replacing v6.

Both modes run inside a single transaction so a validation failure
on row N doesn't leak a partial state from rows 1..N-1.

### Usage

    # dry-run — validates CSV shape, reports planned actions
    # (insert / skip / supersede-then-insert), no writes
    python -m scripts.load_governance_evidence_csv \\
        --csv scripts/setup/data/governance_evidence_testbank.csv

    # apply, append-only (skip if live row exists)
    python -m scripts.load_governance_evidence_csv \\
        --csv scripts/setup/data/governance_evidence_testbank.csv \\
        --apply

    # apply, supersede-and-rotate (for annual rotations)
    python -m scripts.load_governance_evidence_csv \\
        --csv scripts/setup/data/governance_evidence_testbank.csv \\
        --apply --supersede

DB connection follows the same env vars as other seed scripts
(AURORA_HOST, AURORA_PORT, DB_NAME, DB_USER, DB_PASSWORD).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import re
import sys
from datetime import date
from typing import List, Optional, Tuple

import asyncpg


# Keep in sync with:
#   alembic/versions/045_governance_evidence.py::_CANONICAL_TYPES
#   src/api/dashboard_api.py::_CANONICAL_GOVERNANCE_TYPES
_CANONICAL_TYPES = frozenset({
    "board_bsa_acknowledgement",
    "board_ofac_acknowledgement",
    "bsa_officer_designation",
    "bsa_committee_charter",
    "bsa_committee_minutes",
    "bsa_training_records",
    "bsa_policy_manual",
    "enterprise_risk_assessment",
})

_REQUIRED_COLUMNS = (
    "bank_id", "evidence_type", "artifact_ref", "source",
    "attested_by", "effective_from", "effective_to", "notes",
)

_ARTIFACT_REF_RE = re.compile(
    r"^(s3://[a-z0-9.\-]+/[^\s]+"     # S3 URI
    r"|https?://[^\s]+"                # HTTPS URL
    r"|[A-Za-z0-9._\-:]+)$"            # bare doc-id
)


# ── validation ────────────────────────────────────────────────────


def _parse_date(raw: str, column: str, row_no: int) -> Optional[date]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as e:
        raise SystemExit(
            f"row {row_no}: {column} {raw!r} is not a valid ISO date "
            f"(YYYY-MM-DD): {e}"
        )


def _clean(raw: Optional[str]) -> Optional[str]:
    """Empty strings and whitespace-only cells → NULL."""
    if raw is None:
        return None
    trimmed = raw.strip()
    return trimmed or None


def _validate_row(row: dict, row_no: int) -> dict:
    bank_id = _clean(row.get("bank_id"))
    if not bank_id:
        raise SystemExit(f"row {row_no}: bank_id is required")

    evidence_type = _clean(row.get("evidence_type"))
    if evidence_type not in _CANONICAL_TYPES:
        raise SystemExit(
            f"row {row_no}: evidence_type {evidence_type!r} is not one of "
            f"the eight canonical types. See "
            f"src/api/dashboard_api.py::_CANONICAL_GOVERNANCE_TYPES."
        )

    artifact_ref = _clean(row.get("artifact_ref"))
    if artifact_ref and not _ARTIFACT_REF_RE.match(artifact_ref):
        raise SystemExit(
            f"row {row_no}: artifact_ref {artifact_ref!r} is not a valid "
            f"s3://, https://, or doc-id form"
        )

    effective_from = _parse_date(
        row.get("effective_from") or "", "effective_from", row_no)
    effective_to = _parse_date(
        row.get("effective_to") or "", "effective_to", row_no)
    if effective_from and effective_to and effective_from > effective_to:
        raise SystemExit(
            f"row {row_no}: effective_from ({effective_from}) is after "
            f"effective_to ({effective_to})"
        )

    return {
        "bank_id":        bank_id,
        "evidence_type":  evidence_type,
        "artifact_ref":   artifact_ref,
        "source":         _clean(row.get("source")),
        "attested_by":    _clean(row.get("attested_by")),
        "notes":          _clean(row.get("notes")),
        "effective_from": effective_from,
        "effective_to":   effective_to,
    }


def _load_csv(path: str) -> List[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise SystemExit(f"{path}: no header row")
        missing = set(_REQUIRED_COLUMNS) - set(reader.fieldnames)
        if missing:
            raise SystemExit(
                f"{path}: header missing required columns: "
                f"{sorted(missing)}"
            )
        rows = []
        for i, raw in enumerate(reader, start=2):  # header is row 1
            rows.append(_validate_row(raw, i))
    return rows


# ── SQL ────────────────────────────────────────────────────────────


_LIVE_ROW_SQL = """
    SELECT id FROM governance_evidence
     WHERE bank_id = $1 AND evidence_type = $2 AND superseded_at IS NULL
"""

_SUPERSEDE_SQL = """
    UPDATE governance_evidence SET superseded_at = NOW()
     WHERE id = $1
"""

_INSERT_SQL = """
    INSERT INTO governance_evidence
        (bank_id, evidence_type, artifact_ref, source, attested_by,
         notes, effective_from, effective_to)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""


# ── main ───────────────────────────────────────────────────────────


async def _apply(
    conn: asyncpg.Connection, rows: List[dict], supersede: bool,
) -> Tuple[int, int, int]:
    """Return (inserted, skipped, superseded)."""
    inserted = skipped = superseded = 0
    async with conn.transaction():
        for r in rows:
            live_id = await conn.fetchval(
                _LIVE_ROW_SQL, r["bank_id"], r["evidence_type"])
            if live_id is not None:
                if not supersede:
                    skipped += 1
                    continue
                await conn.execute(_SUPERSEDE_SQL, live_id)
                superseded += 1
            await conn.execute(
                _INSERT_SQL,
                r["bank_id"], r["evidence_type"], r["artifact_ref"],
                r["source"], r["attested_by"], r["notes"],
                r["effective_from"], r["effective_to"],
            )
            inserted += 1
    return inserted, skipped, superseded


async def _dry_run(
    conn: asyncpg.Connection, rows: List[dict], supersede: bool,
) -> Tuple[int, int, int]:
    """Report planned actions without writing. Same tuple as _apply."""
    planned_insert = planned_skip = planned_supersede = 0
    for r in rows:
        live_id = await conn.fetchval(
            _LIVE_ROW_SQL, r["bank_id"], r["evidence_type"])
        if live_id is not None:
            if supersede:
                planned_supersede += 1
                planned_insert += 1
                action = "SUPERSEDE+INSERT"
            else:
                planned_skip += 1
                action = "SKIP (live row exists)"
        else:
            planned_insert += 1
            action = "INSERT"
        print(
            f"  {action:<24} bank={r['bank_id']:<20} "
            f"type={r['evidence_type']:<32} "
            f"ref={(r['artifact_ref'] or '(none)')[:60]}"
        )
    return planned_insert, planned_skip, planned_supersede


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load governance_evidence rows from a CSV.",
    )
    parser.add_argument("--csv", required=True, help="Path to CSV file")
    parser.add_argument("--apply", action="store_true",
                        help="Write to DB (default is dry-run).")
    parser.add_argument("--supersede", action="store_true",
                        help="Supersede live rows on conflict (annual rotation).")
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        raise SystemExit(f"CSV file not found: {args.csv}")

    rows = _load_csv(args.csv)
    print(
        f"load_governance_evidence_csv: parsed {len(rows)} rows from "
        f"{args.csv} (mode={'apply' if args.apply else 'dry-run'}, "
        f"supersede={args.supersede})"
    )

    conn = await asyncpg.connect(
        host=os.environ["AURORA_HOST"],
        port=int(os.environ.get("AURORA_PORT", "5432")),
        database=os.environ["DB_NAME"],
        user=os.environ.get("DB_USER", "fortress_testbank"),
        password=os.environ["DB_PASSWORD"],
        ssl="require",
    )

    try:
        if args.apply:
            inserted, skipped, superseded = await _apply(
                conn, rows, args.supersede)
            print(
                f"load_governance_evidence_csv: inserted={inserted} "
                f"skipped={skipped} superseded={superseded}"
            )
        else:
            planned_i, planned_s, planned_sup = await _dry_run(
                conn, rows, args.supersede)
            print(
                f"load_governance_evidence_csv: planned_insert={planned_i} "
                f"planned_skip={planned_s} planned_supersede={planned_sup}"
            )
            print("Dry-run only. Re-run with --apply to write.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
