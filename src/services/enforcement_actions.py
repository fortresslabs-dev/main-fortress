"""
Enforcement Action Service — REQ-011.
Scrapes enforcement actions from OCC, FDIC, CFPB, and FRB enforcement pages
and upserts them into the enforcement_actions table.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

# Penalty amount patterns — matches "$1.5 million", "$500,000", "1.2 billion".
# Retained only as the fallback when the LLM extractor (preferred path, see
# extract_penalty in src/extraction/penalty.py) is unavailable or returns a
# schema-invalid payload. The known failure mode of this regex — grabbing
# asset/revenue figures as penalties — is exactly what the LLM path fixes.
_PENALTY_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|thousand)?",
    re.IGNORECASE,
)

# Regulation citation patterns
_REG_CITE_RE = re.compile(
    r"(?:12\s+C\.F\.R\.?\s*(?:Part\s*)?§?\s*[\d.]+|"
    r"31\s+C\.F\.R\.?\s*(?:Part\s*)?§?\s*[\d.]+|"
    r"15\s+U\.S\.C\.?\s*§?\s*[\d.]+|"
    r"(?:BSA|AML|CRA|HMDA|ECOA|TILA|RESPA|FCA|FDCA)\b)",
    re.I,
)

_AGENCY_KEYWORDS: Dict[str, List[str]] = {
    "OCC": ["occ.gov/enforcement", "comptroller", "OCC enforcement"],
    "FDIC": ["fdic.gov/enforcement", "fdic enforcement"],
    "CFPB": ["consumerfinance.gov/enforcement", "CFPB enforcement"],
    "FRB": ["federalreserve.gov/enforcement", "Federal Reserve enforcement"],
}

_SOURCE_URLS = {
    "OCC": "https://www.occ.gov/topics/supervision-and-examination/enforcement/index-enforcement.html",
    "FDIC": "https://www.fdic.gov/bank/individual/enforcement/",
    "CFPB": "https://www.consumerfinance.gov/enforcement/actions/",
    "FRB": "https://www.federalreserve.gov/apps/enforcementactions/enforcementactions",
}


def _parse_penalty_usd(text: str) -> Optional[Decimal]:
    """Extract the largest penalty amount from text as USD Decimal."""
    best: Optional[Decimal] = None
    for m in _PENALTY_RE.finditer(text):
        raw = m.group(1).replace(",", "")
        multiplier_word = (m.group(2) or "").lower()
        try:
            amount = Decimal(raw)
            if multiplier_word == "billion":
                amount *= Decimal("1000000000")
            elif multiplier_word == "million":
                amount *= Decimal("1000000")
            elif multiplier_word == "thousand":
                amount *= Decimal("1000")
            if best is None or amount > best:
                best = amount
        except Exception:
            continue
    return best


def _extract_regulations_cited(text: str) -> List[str]:
    """Return deduped list of regulatory citation strings found in text."""
    return list(dict.fromkeys(m.group(0).strip() for m in _REG_CITE_RE.finditer(text)))


def _action_ref(regulator: str, institution: str, action_date: str) -> str:
    """Generate a stable unique key for an enforcement action."""
    raw = f"{regulator}::{institution}::{action_date}".lower()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class EnforcementActionService:
    """Scrapes and persists enforcement actions from federal banking regulators.

    Called from ScraperScheduler._run_scrape_cycle() after connector data is
    written to ensure enforcement data stays fresh.
    """

    async def run(self, session) -> int:
        """Scrape all configured agency enforcement pages and upsert results.

        Returns:
            Count of new actions inserted.
        """
        inserted = 0
        for regulator, url in _SOURCE_URLS.items():
            try:
                actions = self._scrape_agency(regulator, url)
                # Penalty extraction runs after scraping (async, per-action)
                # so the synchronous scrape loop stays fast and the LLM
                # calls can fan out without blocking HTML parsing.
                for action in actions:
                    action["penalty_amount_usd"] = await self._extract_penalty(
                        action["regulator"],
                        action["action_ref"],
                        action.get("_penalty_text", ""),
                    )
                    action.pop("_penalty_text", None)
                    upserted = await self._upsert_action(session, action)
                    if upserted:
                        inserted += 1
            except Exception as exc:
                logger.warning("enforcement_scrape_failed", regulator=regulator, error=str(exc))

        logger.info("enforcement_actions_ingested", inserted=inserted)
        return inserted

    async def _extract_penalty(
        self, regulator: str, action_ref: str, text: str
    ) -> Optional[Decimal]:
        """LLM-first penalty extraction with regex fallback.

        Confidence below 0.5 is treated as "no signal" — better to store
        None than a low-confidence number that may be an asset figure.
        Regex fallback runs only when the LLM path raises (invoke error
        or schema validation failure); the fallback is the original
        greedy parser, so the caller should expect occasional asset/
        revenue contamination from that branch — logged for audit.
        """
        if not text:
            return None
        try:
            from src.extraction.penalty import extract_penalty
            result = await extract_penalty(text)
        except Exception as exc:
            fallback = _parse_penalty_usd(text)
            logger.warning(
                "penalty_extract_llm_failed_fallback_regex",
                regulator=regulator,
                action_ref=action_ref,
                fallback_amount=str(fallback) if fallback is not None else None,
                error=str(exc),
            )
            return fallback

        if result.amount_usd is None or result.confidence < 0.5:
            logger.info(
                "penalty_extract_no_penalty",
                regulator=regulator,
                action_ref=action_ref,
                confidence=result.confidence,
                is_penalty_not_asset=result.is_penalty_not_asset,
            )
            return None

        logger.info(
            "penalty_extract_ok",
            regulator=regulator,
            action_ref=action_ref,
            amount_usd=str(result.amount_usd),
            confidence=result.confidence,
        )
        return result.amount_usd

    def _scrape_agency(self, regulator: str, url: str) -> List[Dict[str, Any]]:
        """Scrape enforcement action page for a regulator and return parsed actions."""
        from src.connectors.regulatory.usa._scraper_utils import make_session, scrape_html_page

        session = make_session(url)
        raw_docs = scrape_html_page(session, url, source=regulator, category="Enforcement Actions")

        actions: List[Dict[str, Any]] = []
        today = date.today().isoformat()

        for doc in raw_docs:
            content = doc.get("content", "")
            title = doc.get("title", "")
            if not content:
                continue

            # Penalty resolved later in `run()` via LLM extractor; we hand
            # the prose forward in a private key the upsert path strips.
            regs_cited = _extract_regulations_cited(content + " " + title)

            # Infer institution type from title/content
            inst_type = "unknown"
            for keyword, itype in [
                ("national bank", "national_bank"),
                ("savings bank", "savings_bank"),
                ("credit union", "credit_union"),
                ("mortgage", "mortgage_company"),
                ("fintech", "fintech"),
            ]:
                if keyword in (title + content).lower():
                    inst_type = itype
                    break

            action_ref = _action_ref(regulator, title[:80], today)
            content_hash = hashlib.sha256(content.encode()).hexdigest()

            actions.append(
                {
                    "action_ref": f"{regulator}-{action_ref}",
                    "regulator": regulator,
                    "institution_type": inst_type,
                    "action_date": date.today(),
                    "action_type": self._infer_action_type(title, content),
                    "regulations_cited": regs_cited,
                    "violation_summary": content[:2000],
                    "penalty_amount_usd": None,
                    "_penalty_text": (title + "\n\n" + content)[:6000],
                    "source_url": doc.get("url", url),
                    "content_hash": content_hash,
                }
            )

        return actions

    @staticmethod
    def _infer_action_type(title: str, content: str) -> str:
        """Classify action type from title/content keywords."""
        combined = (title + " " + content).lower()
        for keyword, action_type in [
            ("consent order", "consent_order"),
            ("civil money penalty", "civil_money_penalty"),
            ("cease and desist", "cease_and_desist"),
            ("formal agreement", "formal_agreement"),
            ("memorandum of understanding", "mou"),
            ("prohibition", "prohibition_order"),
            ("removal", "removal_order"),
        ]:
            if keyword in combined:
                return action_type
        return "supervisory_action"

    async def _upsert_action(self, session, action: Dict[str, Any]) -> bool:
        """Insert or skip enforcement action. Returns True if inserted."""
        from sqlalchemy import select
        from src.models.database import EnforcementAction

        with session.no_autoflush:
            existing = await session.execute(
                select(EnforcementAction.id).where(
                    EnforcementAction.action_ref == action["action_ref"]
                )
            )
            if existing.scalar_one_or_none() is not None:
                return False

        session.add(
            EnforcementAction(
                action_ref=action["action_ref"],
                regulator=action["regulator"],
                institution_type=action["institution_type"],
                action_date=action["action_date"],
                action_type=action["action_type"],
                regulations_cited=action["regulations_cited"],
                violation_summary=action["violation_summary"],
                penalty_amount_usd=action["penalty_amount_usd"],
                source_url=action["source_url"],
                content_hash=action["content_hash"],
                is_active=True,
            )
        )
        return True
