"""
FinCEN 31 CFR Part 1010 Connector — General BSA/AML Program Provisions.

Covers the Part 1010 *general provisions* that apply across financial
institutions — the AML-program, CIP, CDD/beneficial-ownership, CTR, Form
8300, recordkeeping, Travel Rule, 314(a)/(b), and special-measures blocks.

This complements the existing bank-specific connectors:
  - ``fincen_sar.py``  → 31 CFR 1020.320 (SAR filing)
  - ``occ_bsa.py``     → selected 1010.x / 1020.x rules under regulator OCC
  - ``ffiec_bsa.py``   → 1020.210 (AML program) / 1020.220 (CIP) under FFIEC

Part 1010 (general provisions) and Part 1020 (banks) are distinct CFR parts;
the 1010.210/1010.220 rows here intentionally coexist with the FFIEC 1020.x
rows — they are the general-institution citations, not duplicates.

Source of record: ``data/fincen_part1010_seed.json`` — a curated obligation
map (from ``bsa.aml.latest``) of Part 1010 sections → obligations → atomic
sub-rules, each carrying a severity grade. Static seed (not live-scraped);
``source_url`` points at the authoritative eCFR section for later
citation-verification by the authority gate.

Priority: P0 — foundational BSA/AML program obligations.
Status: ✅ COMPLETE (static seed)
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from src.connectors.base import RegulatoryConnector

logger = structlog.get_logger(__name__)

_SEED_PATH = Path(__file__).parent / "data" / "fincen_part1010_seed.json"

# Map the seed's per-sub-rule control category → the rule_type slug persisted
# on requirement rows. REGULATORY sub-rules elaborate the codified obligation;
# BUSINESS/TECHNICAL ones are operational control expectations.
_CONTROL_RULE_TYPE = {
    "REGULATORY": "control_regulatory",
    "BUSINESS": "control_business",
    "TECHNICAL": "control_technical",
}


class FincenPart1010Connector(RegulatoryConnector):
    """FinCEN 31 CFR Part 1010 general BSA/AML provisions connector.

    Emits one ``regulation`` row per codified obligation (carrying the
    obligation's sub-citation + severity) and one ``requirement`` row per
    atomic sub-rule. All rows are attributed to regulator ``FinCEN`` with
    ``authority_type = LAW`` and the eCFR section as ``source_url``.
    """

    connector_name = "fincen_part1010"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self._seed: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ #
    # Connection lifecycle (static seed — no network dependency)
    # ------------------------------------------------------------------ #
    def connect(self) -> bool:
        self.connected = True
        logger.info("fincen_part1010_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return _SEED_PATH.exists()

    # ------------------------------------------------------------------ #
    # Seed loading
    # ------------------------------------------------------------------ #
    def _load_seed(self) -> Dict[str, Any]:
        if self._seed is None:
            with open(_SEED_PATH, encoding="utf-8") as fh:
                self._seed = json.load(fh)
        return self._seed

    # ------------------------------------------------------------------ #
    # Extraction
    # ------------------------------------------------------------------ #
    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        logger.info("fincen_part1010_extraction_started")

        regulations = self._build_regulations()
        requirements = self._build_requirements()

        logger.info(
            "fincen_part1010_extraction_completed",
            regulations=len(regulations),
            requirements=len(requirements),
            sections=len(self._load_seed().get("sections", [])),
        )
        return {
            "regulations": regulations,
            "requirements": requirements,
            "kpis": [],
            "test_scenarios": [],
            "scraped_documents": [],
        }

    def check_updates(self) -> List[Dict]:
        # Static seed — change detection is handled by the scheduler's
        # per-rule hash. Nothing to poll here.
        return []

    # ------------------------------------------------------------------ #
    # Regulations — one row per codified obligation
    # ------------------------------------------------------------------ #
    def _build_regulations(self) -> List[Dict[str, Any]]:
        regs: List[Dict[str, Any]] = []
        for section in self._load_seed().get("sections", []):
            sec = section["section"]
            source_url = section.get("source_url")
            for obl in section.get("obligations", []):
                regs.append({
                    "regulation_id": obl["rule_id"],
                    "regulation_name": self._short_name(obl["text"]),
                    "rule_text": obl["text"],
                    "cfr_citation": obl["cfr_citation"],
                    "severity": obl["severity"],
                    "category": "BSA/AML",
                    "subcategory": f"31 CFR {sec}",
                    "regulator": "FinCEN",
                    "geography": "usa",
                    "jurisdiction_level": "federal",
                    "authority_type": "LAW",
                    "effective_date": None,   # only sourced from the Federal Register
                    "source_url": source_url,
                })
        return regs

    # ------------------------------------------------------------------ #
    # Requirements — one row per atomic sub-rule
    # ------------------------------------------------------------------ #
    def _build_requirements(self) -> List[Dict[str, Any]]:
        reqs: List[Dict[str, Any]] = []
        for section in self._load_seed().get("sections", []):
            source_url = section.get("source_url")
            for obl in section.get("obligations", []):
                for idx, rule in enumerate(obl.get("rules", []), start=1):
                    cat = (rule.get("category") or "").upper()
                    reqs.append({
                        "requirement_id": f"{obl['rule_id']}-R{idx}",
                        "regulation_id": obl["rule_id"],
                        "cfr_citation": obl["cfr_citation"],
                        "requirement_text": rule["text"],
                        "rule_type": _CONTROL_RULE_TYPE.get(cat),
                        "severity": obl["severity"],
                        "category": "BSA/AML",
                        "regulator": "FinCEN",
                        "geography": "usa",
                        "jurisdiction_level": "federal",
                        "source_url": source_url,
                        "parameters": {
                            "control_category": cat or None,
                            "evidence": rule.get("evidence"),
                        },
                    })
        return reqs

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _short_name(text: str, max_len: int = 120) -> str:
        """First clause of the obligation text, for regulation_name."""
        first = text.split(".")[0].strip()
        if len(first) > max_len:
            first = first[: max_len - 1].rstrip() + "…"
        return first
