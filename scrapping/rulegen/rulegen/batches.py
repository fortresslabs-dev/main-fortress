"""Citation batches, ordered so the riskiest work is validated first.

Batch by related citation, not by arbitrary chunk. Sections in a batch
cross-reference each other: 1020.210(a)(1) requires compliance with
1010.610 and 1010.620, and 1020.220 says the CIP must be part of the AML
program. Process them apart and those links have nothing to attach to.

Order is deliberate. Batch 1 is the sections already derived by hand, so
the first run can be scored against known answers before any budget is
spent on unknown ground.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Batch:
    key: str
    label: str
    citations: tuple[str, ...]
    why_this_order: str


BATCHES: tuple[Batch, ...] = (
    Batch("golden", "Hand-derived baseline",
          ("31 CFR 1020.210", "31 CFR 1020.220", "31 CFR 1020.320"),
          "Known answers exist. Score the prompt here before spending on "
          "sections nobody has checked."),

    Batch("funds_transfer", "Travel Rule and funds transfer recordkeeping",
          ("31 CFR 1010.410", "31 CFR 1010.415", "31 CFR 1020.410"),
          "Carries the (e)/(f) trap and the $3,000 threshold that has been "
          "wrong four times. High value, and failure is detectable. "
          "1010.430 sets the retention period these point at, but is owned "
          "by the recordkeeping batch — a citation belongs to exactly one "
          "batch or it gets extracted twice."),

    Batch("ctr", "Currency transaction reporting",
          ("31 CFR 1010.306", "31 CFR 1010.310", "31 CFR 1010.311",
           "31 CFR 1010.312", "31 CFR 1010.313", "31 CFR 1010.314",
           "31 CFR 1010.315", "31 CFR 1020.310", "31 CFR 1020.315"),
          "Dense with thresholds and exemptions — exercises the number "
          "extractor and the conditional-threshold few-shot."),

    Batch("cdd_bo", "Customer due diligence and beneficial ownership",
          ("31 CFR 1010.230",),
          "The 25 percent threshold, one of the four known-bad values."),

    Batch("info_sharing", "314(a) and 314(b)",
          ("31 CFR 1010.520", "31 CFR 1010.540", "31 CFR 1020.520",
           "31 CFR 1020.540"),
          "The delegation pattern: the CFR states the duty and hands the "
          "deadline to FinCEN. Tests whether GUIDANCE tier is caught."),

    Batch("foreign_accounts", "Correspondent, private banking, shell banks",
          ("31 CFR 1010.605", "31 CFR 1010.610", "31 CFR 1010.620",
           "31 CFR 1010.630", "31 CFR 1010.670", "31 CFR 1020.610",
           "31 CFR 1020.620", "31 CFR 1020.630", "31 CFR 1020.670"),
          "Cross-referenced by 1020.210(a)(1). Several 1020 sections are "
          "pointers to 1010 — watch for duplicate rules."),

    Batch("special_measures", "Section 311 special measures",
          tuple(f"31 CFR 1010.{n}" for n in
                (651, 653, 658, 659, 660, 661, 662, 663, 664)),
          "Structurally repetitive. If the prompt is stable it should "
          "produce near-identical rule shapes across all nine."),

    Batch("recordkeeping", "Records and retention",
          ("31 CFR 1010.400", "31 CFR 1010.420", "31 CFR 1010.430",
           "31 CFR 1010.440", "31 CFR 1020.400"),
          "Five-year retention appears throughout; tests consistency."),

    Batch("ncua", "NCUA security programme and reporting",
          ("12 CFR 748.0", "12 CFR 748.1", "12 CFR 748.2"),
          "Charter flips to credit-union-only. Tests the Title 12 few-shot."),

    Batch("agency_bsa", "Agency-level BSA compliance rules",
          ("12 CFR 21.21", "12 CFR 208.63", "12 CFR 326.8", "12 CFR 353.3"),
          "Where board approval actually lives — the duty the source "
          "workbook mis-cited to 31 CFR 1020.210."),
)

BY_KEY = {b.key: b for b in BATCHES}


def citations_through(batch_key: str) -> list[str]:
    """Everything up to and including a batch, in order."""
    out: list[str] = []
    for b in BATCHES:
        out.extend(b.citations)
        if b.key == batch_key:
            break
    return out


def total_citations() -> int:
    return sum(len(b.citations) for b in BATCHES)
