# Pipeline 1 — Scraping Data Gaps
## What is missing, where, and exactly how to fix it

Audited against: `testmind_banking_v5`  
Date: April 25, 2026

---

## Summary

The v5 scraper infrastructure is solid. The `enrich_document()` helper exists and is partially adopted.  
The `ScraperScheduler` correctly calls `ObligationExtractor`, `EnforcementRiskScorer`, `DeadlineTracker`, and `ChangeManager` after each cycle.  

**However, six gaps mean the dashboard will receive incomplete or wrong data.**

---

## GAP-1 🔴 CRITICAL — `_build_regulations()` has no `rule_text` substance → Weaviate vectors are useless

**Files:** ALL connectors  
**Root cause:** `_build_regulations()` items contain only `regulation_name` (a short title like "Wire Transfer Dual Approval Requirement"). The scheduler resolves `rule_text` in priority order: `rule_text → requirement_text → regulation_name`. For regulation-category items, it always falls through to `regulation_name` because these items have no `rule_text` key. The vectorized `ComplianceRule` Weaviate documents contain 5-word titles — semantic search is meaningless.

**What the dashboard needs:** When Agent 2 or the dashboard searches for "beneficial ownership verification requirement", it finds the regulation whose `rule_text` describes the obligation semantically, not just "CDD Requirements".

**Fix — add `rule_text` to every `_build_regulations()` item in every connector:**

```python
# BEFORE (occ_bsa.py, fdic.py, fincen_sar.py, irr.py, cecl.py, etc.):
{
    "regulation_id": "OCC-BSA-WIRE-001",
    "regulation_name": "Wire Transfer Dual Approval Requirement",
    ...
}

# AFTER — add rule_text:
{
    "regulation_id": "OCC-BSA-WIRE-001",
    "regulation_name": "Wire Transfer Dual Approval Requirement",
    "rule_text": (
        "Wire transfers of $10,000 or more require approval from two separate "
        "authorized employees. The initiator and approver must be different "
        "individuals. Records must be retained for five years. "
        "31 C.F.R. § 1010.410(f); OCC BSA/AML Handbook."
    ),
    ...
}
```

**Connectors that need this fix:** `occ_bsa.py`, `fdic.py`, `cfpb.py`, `fincen_sar.py`, `irr.py`, `operational_risk.py`, `liquidity_risk.py`, `cecl.py`, `peer_benchmarks.py`.  
The newer connectors (`cra.py`, `ffiec_call_report.py`, `ffiec_cybersecurity.py`, `cre_concentration.py`) already have substantive `rule_text` in most items — verify each one.

---

## GAP-2 🔴 CRITICAL — `cfr_citation` missing from core connectors → no examiner-grade citations

**Missing from:** `occ_bsa.py`, `fdic.py`, `fincen_sar.py`, `irr.py`, `operational_risk.py`  
**Present in:** `cfpb.py` (2 items), `cra.py` (8 items), `ffiec_call_report.py` (8 items), `cecl.py` (10 items), `liquidity_risk.py` (9 items)

The dashboard exam report generator produces citations like "Source: https://occ.gov/..." instead of "31 C.F.R. § 1010.410(f)". Examiners cite by CFR section. Without `cfr_citation`, every OCC BSA/AML rule — the highest-volume category — has no proper citation.

**Fix — add `cfr_citation` to every `_build_requirements()` item in the listed connectors:**

```python
# occ_bsa.py — reference table:
"OCC-BSA-WIRE-001-R1":   "31 C.F.R. § 1010.410(f)"     # Travel Rule / wire
"OCC-BSA-WIRE-001-R2":   "31 C.F.R. § 1020.410(a)"     # dual approval
"OCC-BSA-CTR-001-R1":    "31 C.F.R. § 1010.311"         # CTR filing
"OCC-BSA-SAR-001-R1":    "31 C.F.R. § 1020.320"         # SAR filing
"OCC-BSA-CDD-001-R1":    "31 C.F.R. § 1010.230"         # beneficial ownership
"OCC-BSA-RET-001-R1":    "31 C.F.R. § 1010.430"         # 5yr retention

# fincen_sar.py — reference table:
"FINCEN-SAR-001-R1":     "31 C.F.R. § 1020.320(b)(3)"  # 30-day deadline
"FINCEN-CTR-001-R1":     "31 C.F.R. § 1010.311"         # CTR

# fdic.py — reference table:
"FDIC-CAPITAL-001-R1":   "12 C.F.R. § 324.10"           # CET1 ratio
"FDIC-CAPITAL-001-R2":   "12 C.F.R. § 324.11"           # Tier 1 ratio
"FDIC-CAPITAL-001-R3":   "12 C.F.R. § 324.12"           # Total capital ratio
```

**Also add `cfr_citation` to every `_build_regulations()` item** (used when the scheduler stores it to `ComplianceRule.cfr_citation`):

```python
{
    "regulation_id": "OCC-BSA-WIRE-001",
    "cfr_citation": "31 C.F.R. § 1010.410(f)",    # ADD
    ...
}
```

---

## GAP-3 🟠 HIGH — `enrich_document()` not called in 7 connectors → document chunks have no obligation metadata

**Connectors missing `enrich_document()` call:**
- `occ_bsa.py` — highest volume, most important
- `fdic.py`
- `cfpb.py`
- `fincen_sar.py`
- `ofac_sdn.py` (already correctly returns `scraped_documents: []` — skip)
- `cecl.py`
- `liquidity_risk.py`
- `operational_risk.py`
- `peer_benchmarks.py` (no scraped docs — skip)

Without `enrich_document()`, the Weaviate `ComplianceDocument` chunks for these connectors have:
- `is_obligation: false` (default) → ObligationExtractor skips all of them
- `cfr_citation: ""` → no citations in document search results
- `doc_type: ""` → agents cannot filter by document type

**Fix — add to each connector's `_scrape_live()` before `return docs`:**

```python
# In occ_bsa.py _scrape_live():
from src.connectors.regulatory.usa._scraper_utils import (
    ...,
    enrich_document,    # ADD THIS IMPORT
)

# After collecting all docs, before return:
enriched = []
for doc in docs:
    enriched.append(enrich_document(
        doc,
        regulation_id=self._infer_regulation_id(doc),  # or a static value
        state_code=None,        # None for federal connectors
        doc_type="reg_text",    # or "interpretive" / "guidance" / "faq"
    ))
return enriched

# For OCC BSA, use this helper for regulation_id inference:
def _infer_regulation_id(self, doc: dict) -> str:
    title = doc.get("section_title", "") + doc.get("document_title", "")
    if "wire" in title.lower(): return "OCC-BSA-WIRE-001"
    if "ctr" in title.lower() or "currency" in title.lower(): return "OCC-BSA-CTR-001"
    if "sar" in title.lower(): return "OCC-BSA-SAR-001"
    if "cdd" in title.lower() or "ownership" in title.lower(): return "OCC-BSA-CDD-001"
    return "OCC-BSA"
```

For `cfpb.py`, use `doc_type="reg_text"` for CFR pages, `doc_type="faq"` for HMDA FAQ pages, `doc_type="guidance"` for supervisory highlights.

---

## GAP-4 🟠 HIGH — `sync_api.py` stubs return wrong data → P2 never gets real obligations, deadlines, or enforcement scores

**The four sync endpoints are broken stubs.** They query `ComplianceRule` instead of the actual new tables.

### `sync_obligations` — queries `ComplianceRule` instead of `Obligation`

```python
# CURRENT (wrong):
result = await session.execute(select(ComplianceRule).where(ComplianceRule.is_active == True))
obligations = [{"rule_id": r.rule_id, "rule_text": r.rule_text, ...} for r in ...]

# CORRECT — query the obligations table:
from src.models.database import Obligation
result = await session.execute(
    select(Obligation).where(Obligation.is_active == True)
)
obligations = [
    {
        "obligation_id":    r.obligation_id,
        "regulation_id":    r.regulation_id,
        "regulator":        r.regulator,
        "category":         r.category,
        "source_text":      r.source_text,
        "obligation_type":  r.obligation_type,
        "section_reference":r.section_reference,
        "cfr_citation":     r.cfr_citation,
        "has_test_coverage":r.has_test_coverage,
        "extracted_at":     r.extracted_at.isoformat() if r.extracted_at else None,
    }
    for r in result.scalars().all()
]
return {"obligations": obligations}
```

### `sync_deadlines` — queries `ComplianceRule` instead of `ComplianceDeadline`

```python
# CORRECT:
from src.models.database import ComplianceDeadline
result = await session.execute(
    select(ComplianceDeadline)
    .where(ComplianceDeadline.is_active == True)
    .where(ComplianceDeadline.deadline_date >= func.current_date())
)
deadlines = [
    {
        "deadline_key":     r.deadline_key,
        "regulation_id":    r.regulation_id,
        "regulator":        r.regulator,
        "deadline_type":    r.deadline_type,
        "deadline_name":    r.deadline_name,
        "description":      r.description,
        "deadline_date":    r.deadline_date.isoformat() if r.deadline_date else None,
        "days_remaining":   r.days_remaining,
        "is_recurring":     r.is_recurring,
        "recurrence_rule":  r.recurrence_rule,
    }
    for r in result.scalars().all()
]
return {"deadlines": deadlines}
```

### `sync_risk_scores` — queries `ComplianceRule` instead of `EnforcementRiskScore`

```python
# CORRECT:
from src.models.database import EnforcementRiskScore
result = await session.execute(
    select(EnforcementRiskScore).where(EnforcementRiskScore.period_months == 24)
)
scores = [
    {
        "regulation_id":    r.regulation_id,
        "regulator":        r.regulator,
        "risk_score":       r.risk_score,
        "action_count":     r.action_count,
        "avg_penalty_usd":  float(r.avg_penalty_usd) if r.avg_penalty_usd else None,
        "total_penalty_usd":float(r.total_penalty_usd) if r.total_penalty_usd else None,
        "trend_direction":  r.trend_direction,
        "computed_at":      r.computed_at.isoformat() if r.computed_at else None,
    }
    for r in result.scalars().all()
]
return {"risk_scores": scores}
```

### `sync_change_records` — queries `ComplianceRule` instead of `RegulatoryChangeRecord`

```python
# CORRECT:
from src.models.database import RegulatoryChangeRecord
result = await session.execute(
    select(RegulatoryChangeRecord)
    .where(RegulatoryChangeRecord.is_active == True)
    .order_by(RegulatoryChangeRecord.detected_at.desc())
    .limit(200)
)
records = [
    {
        "change_ref":     r.change_ref,
        "rule_id":        r.rule_id,
        "regulator":      r.regulator,
        "detected_at":    r.detected_at.isoformat() if r.detected_at else None,
        "change_summary": r.change_summary,
        "impact_rating":  r.impact_rating,
        "change_type":    r.change_type,
        "status":         r.status,
        "signed_off_at":  r.signed_off_at.isoformat() if r.signed_off_at else None,
    }
    for r in result.scalars().all()
]
return {"records": records}
```

### Also add `sync_benchmarks` — currently returns `[]`

```python
# src/api/sync_api.py — sync_benchmarks:
from src.models.database import PeerBenchmark
result = await session.execute(
    select(PeerBenchmark)
    .order_by(PeerBenchmark.data_period.desc())
)
benchmarks = [
    {
        "regulation_id":        r.regulation_id,
        "regulator":            r.regulator,
        "asset_tier":           r.asset_tier,
        "source":               r.source,
        "avg_coverage_pct":     r.avg_coverage_pct,
        "median_coverage_pct":  r.median_coverage_pct,
        "top_quartile_pct":     r.top_quartile_pct,
        "bottom_quartile_pct":  r.bottom_quartile_pct,
        "data_period":          r.data_period.isoformat() if r.data_period else None,
    }
    for r in result.scalars().all()
]
return {"benchmarks": benchmarks}
```

---

## GAP-5 🟠 HIGH — `ObligationExtractor` does not write to Weaviate `ObligationRecord` collection

**File:** `src/services/obligation_extractor.py`  
**What it does:** Extracts obligations from `ComplianceRule.rule_text` → writes to Postgres `obligations` table. ✓  
**What it does NOT do:** Upsert into Weaviate `ObligationRecord` collection.

Without Weaviate `ObligationRecord` entries, the dashboard's "find uncovered obligations similar to this gap" semantic search returns nothing. Agent 2 (Understanding) also cannot find the most relevant uncovered obligations for a JIRA story.

**Fix — add Weaviate upsert at the end of `ObligationExtractor.process()`:**

```python
# In src/services/obligation_extractor.py — after the Postgres upsert loop, ADD:

async def _sync_to_weaviate(self, obligation_rows: list) -> None:
    """Upsert extracted obligations into Weaviate ObligationRecord collection."""
    try:
        from src.services.weaviate_client import bulk_insert_obligation_records
        docs = [
            {
                "obligation_id":    row.obligation_id,
                "regulation_id":    row.regulation_id,
                "regulator":        row.regulator,
                "source_text":      row.source_text,          # ← vectorized
                "obligation_type":  row.obligation_type,
                "section_reference":row.section_reference or "",
                "cfr_citation":     row.cfr_citation or "",
                "has_test_coverage":row.has_test_coverage,
                "extracted_at":     row.extracted_at.isoformat(),
                "doc_id":           row.doc_id or "",
            }
            for row in obligation_rows
        ]
        inserted = bulk_insert_obligation_records(docs)
        logger.info("obligation_weaviate_sync_done", inserted=inserted)
    except Exception as exc:
        logger.warning("obligation_weaviate_sync_failed", error=str(exc))
```

Then add `bulk_insert_obligation_records()` to `src/services/weaviate_client.py` following the same pattern as `bulk_insert_compliance_rules()`:

```python
def bulk_insert_obligation_records(obligations: list) -> int:
    try:
        collection = _get_client().collections.get("ObligationRecord")
        inserted = 0
        with collection.batch.dynamic() as batch:
            for obl in obligations:
                obj_uuid = _deterministic_uuid(
                    "ObligationRecord",
                    obl.get("obligation_id", ""),
                )
                batch.add_object(properties=obl, uuid=obj_uuid)
                inserted += 1
        failed = collection.batch.failed_objects
        if failed:
            logger.error("obligation_weaviate_partial_failure",
                         failed=len(failed), first=str(failed[0].message))
            inserted -= len(failed)
        return inserted
    except Exception as exc:
        logger.error("obligation_weaviate_insert_failed", error=str(exc))
        return 0
```

---

## GAP-6 🟡 MEDIUM — `last_updated` field causes hash to always change → spurious sync version bumps

**File:** `src/services/scraper_scheduler.py` — hash computation  
**Connectors affected:** `occ_bsa.py`, `fdic.py`, `fincen_sar.py` (set `"last_updated": now` in `_build_regulations()`)

The hash is computed over the entire `rule_data` dict. `last_updated` is `datetime.utcnow()` — changes every run even when regulatory content is identical. This causes the sync version to bump every 24h for every rule, making P2 pull "diffs" that have no real content change.

**Fix in `scraper_scheduler.py` — exclude volatile fields from hash:**

```python
# CURRENT:
hash_data = json.dumps(rule_data, sort_keys=True, default=str).encode()

# CORRECT:
_HASH_EXCLUDE = {"last_updated", "scraped_at", "updated_at", "fetched_at"}
hash_input = {k: v for k, v in rule_data.items() if k not in _HASH_EXCLUDE}
hash_data = json.dumps(hash_input, sort_keys=True, default=str).encode()
```

Also remove `"last_updated": now` from `_build_regulations()` in the three connectors listed above, or move it to a separate metadata field that is not hashed.

---

## GAP-7 🟡 MEDIUM — `enforcement_actions.py` service exists but is not called from scheduler

**File:** `src/services/scraper_scheduler.py`  
The scheduler calls `ObligationExtractor`, `EnforcementRiskScorer`, `DeadlineTracker`, and `ChangeManager`. It does NOT call `EnforcementActionService` to actually scrape enforcement action pages.

`EnforcementRiskScorer` computes risk scores from the `enforcement_actions` table — but that table will always be empty until `EnforcementActionService` runs.

**Fix — add enforcement action scraping to `_run_scrape_cycle()`:**

```python
# In scraper_scheduler.py — in the post-scrape-cycle block, ADD:
try:
    from src.services.enforcement_actions import EnforcementActionService
    svc = EnforcementActionService()
    async with get_db() as session:
        scraped = await svc.scrape_and_upsert(session)
    logger.info("enforcement_action_scrape_done", scraped=scraped)
except Exception as exc:
    logger.warning("enforcement_action_scrape_failed", error=str(exc))
```

Run this weekly (not daily) — enforcement actions update slowly. Add a `_enforcement_last_run` timestamp check inside `_run_scrape_cycle()`:

```python
# Only run enforcement scrape weekly
import time
_enforcement_run_interval = 7 * 24 * 3600  # 7 days
if not hasattr(self, '_last_enforcement_run') or \
   time.time() - self._last_enforcement_run > _enforcement_run_interval:
    # run enforcement scrape
    self._last_enforcement_run = time.time()
```

---

## Quick fix priority order

| Priority | Gap | File(s) | Time estimate |
|----------|-----|---------|---------------|
| 1 | GAP-4: Fix sync API stubs | `src/api/sync_api.py` | 1–2 hours |
| 2 | GAP-2: Add cfr_citation to occ_bsa, fdic, fincen_sar | 3 connector files | 2 hours |
| 3 | GAP-1: Add rule_text substance to _build_regulations | All connectors | 2–3 hours |
| 4 | GAP-3: Add enrich_document to 7 connectors | 7 connector files | 2–3 hours |
| 5 | GAP-6: Fix hash instability | `scraper_scheduler.py` | 30 min |
| 6 | GAP-5: Add Weaviate write to ObligationExtractor | `obligation_extractor.py` + `weaviate_client.py` | 1–2 hours |
| 7 | GAP-7: Call EnforcementActionService from scheduler | `scraper_scheduler.py` | 30 min |