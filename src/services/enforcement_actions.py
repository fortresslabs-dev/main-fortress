"""
Enforcement Action Service — REQ-011.
Scrapes enforcement actions from OCC, FDIC, CFPB, and FRB enforcement pages
and upserts them into the enforcement_actions table.
"""
from __future__ import annotations

import hashlib
import os
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

# Legacy landing-page path — DISABLED pending per-regulator index→detail
# migration. The landing-page scraper stamps the listing URL on every row (no
# per-action link) and these regulators don't expose a clean HTML
# list-of-detail-links, so they produced same-link, low-signal rows. Keeping
# them OFF the active feed so the dashboard only ever shows uniquely-linked,
# real actions. Re-enable by moving an entry into _DETAIL_CRAWL once migrated:
#   FDIC — verify the orders-listing link_pattern (likely static-crawlable)
#   OCC  — PDF orders behind a search tool: needs PDF handling + listing endpoint
#   FRB  — JavaScript search app: needs the data feed or a headless browser
_SOURCE_URLS: Dict[str, str] = {}
_LEGACY_DISABLED = {
    "OCC": "https://www.occ.gov/topics/supervision-and-examination/enforcement/index-enforcement.html",
    "FDIC": "https://www.fdic.gov/bank/individual/enforcement/",
    "FRB": "https://www.federalreserve.gov/apps/enforcementactions/enforcementactions",
}

# Regulators ingested via an index→detail crawl (the correct pattern): parse the
# enforcement *listing* for links, then fetch each action's own detail page so
# every row carries a unique, working source_url (not the landing page) and real
# prose for the extractors. FinCEN is the BSA/AML-primary regulator and the
# reference implementation; others can be migrated onto this path the same way.
_DETAIL_CRAWL: Dict[str, Dict[str, Any]] = {
    "FINCEN": {
        "index": "https://www.fincen.gov/news-room/enforcement-actions",
        "base": "https://www.fincen.gov",
        # Follow news-room detail pages; non-enforcement items that slip through
        # are removed by _is_enforcement_noise() after extraction.
        "link_pattern": "/news-room/",
        "max_pages": 25,
    },
    "CFPB": {
        "index": "https://www.consumerfinance.gov/enforcement/actions/",
        "base": "https://www.consumerfinance.gov",
        # Each action has its own /enforcement/actions/<slug>/ detail page; the
        # listing itself (== index) and pagination links are skipped/gated.
        "link_pattern": "/enforcement/actions/",
        # CFPB throttles rapid sequential fetches: >~10-12 hits in a burst and
        # it serves minimal pages (chrome, no order body). Cap the per-run crawl
        # to the recent actions; a deeper historical backfill needs real backoff.
        "max_pages": 10,
        # Scope extraction to the CFPB order-body block. Without this the
        # generic 'main' selector + heading-chunking grabbed page chrome
        # (docket #, date, category tags) instead of the order text, so the
        # penalty/citations were never in the scraped content.
        "content_selectors": [
            "div.m-full-width-text",
            "div.o-full-width-text-group",
            "main",
        ],
    },
}

# Titles that signal a scraped chunk is site navigation / boilerplate, not an
# enforcement action (the OCC landing-page failure mode: "Upcoming Events",
# "Featured Publications", "View All …").
_NOISE_TITLE_RE = re.compile(
    r"\b(events?|publications?|view all|featured|newsroom|news room|subscribe|"
    r"careers|about us|speeches?|testimony|reports?|statistics|data releases?|"
    r"follow us|contact us|site map)\b",
    re.I,
)


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DATE_PATTERNS = [
    # "September 11, 2024" / "Sep 11, 2024" / "NOV 21, 2024"
    (re.compile(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b",
        re.I), "mdy"),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso"),       # 2024-09-11
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "slash"),  # 09/11/2024
]


def _extract_action_date(text: str) -> date:
    """Best-effort real action date from the prose; falls back to today.

    The landing-page scraper set action_date = scrape date, so the dashboard
    showed today's date on every row. The real date is in the action text
    ("On September 11, 2024, the Bureau issued an order…") — pull it out.
    """
    if not text:
        return date.today()
    max_year = date.today().year + 1
    for rx, kind in _DATE_PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        try:
            if kind == "mdy":
                y, mo, d = int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2))
            elif kind == "iso":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:  # slash — US m/d/y
                y, mo, d = int(m.group(3)), int(m.group(1)), int(m.group(2))
            if 2000 <= y <= max_year:
                return date(y, mo, d)
        except (ValueError, KeyError):
            continue
    return date.today()


def _is_enforcement_noise(action: Dict[str, Any]) -> bool:
    """True when a scraped row is almost certainly NOT an enforcement action.

    Keeps the row if it carries ANY hard signal — a parsed penalty, a cited
    regulation, or a recognized institution type. Otherwise (no signal at all)
    it drops rows whose title reads like site navigation or whose body is too
    short to be a real action. Deliberately conservative: a genuine action
    nearly always has a penalty, a citation, or names an institution.
    """
    if action.get("penalty_amount_usd") is not None:
        return False
    if action.get("regulations_cited"):
        return False
    if action.get("institution_type") not in (None, "unknown"):
        return False
    title = action.get("_title", "") or ""
    if _NOISE_TITLE_RE.search(title):
        return True
    if len((action.get("violation_summary") or "").strip()) < 120:
        return True
    return False


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


async def extract_regulations_cited_llm(
    text: str,
    candidate_citations: List[str],
    *,
    regulator: Optional[str] = None,
    action_type: Optional[str] = None,
    model_id: Optional[str] = None,
) -> List[str]:
    """LLM fallback for `regulations_cited` when the regex finds nothing.

    Enforcement press releases rarely cite CFRs verbatim, so the regex above
    usually returns []. This asks the model which regulations the action
    *clearly concerns*, CONSTRAINED to a supplied candidate vocabulary (the
    distinct ``compliance_rules.cfr_citation`` values) so the output always
    maps back to a rule via the scorer's normalized join — and so the model
    cannot invent a citation outside the corpus. Returns [] when nothing
    clearly applies (grounded, not fabricated). Provenance is the caller's to
    record (method='llm_inferred').
    """
    text = (text or "").strip()
    if not text or not candidate_citations:
        return []
    candidates = candidate_citations[:200]
    listing = "\n".join(f"- {c}" for c in candidates)
    prompt = (
        "You are a U.S. banking enforcement analyst. Decide which regulations "
        "the enforcement action below clearly concerns or alleges a violation of.\n\n"
        f"Regulator: {regulator or 'unknown'}   Action type: {action_type or 'unknown'}\n"
        f'Enforcement action:\n"""\n{text[:4000]}\n"""\n\n'
        "Choose ONLY from this candidate list, returning each citation EXACTLY as written:\n"
        f"{listing}\n\n"
        "Rules:\n"
        "- Return a JSON array of the citations clearly implicated by the text.\n"
        "- If none clearly apply, return [].\n"
        "- Never invent a citation outside the candidate list.\n"
        "Return ONLY the JSON array."
    )
    try:
        from src.services.bedrock_service import BedrockService
        from src.utils.config import settings
        model = (model_id or getattr(settings, "P1_OBLIGATION_EXTRACTION_MODEL", None)
                 or settings.BEDROCK_MODEL)
        raw = await BedrockService(model_id=model).generate_code(
            prompt=prompt, max_tokens=500, temperature=0.0)
    except Exception as exc:  # Bedrock unavailable / throttled → safe no-op
        logger.warning("enforcement_llm_cite_failed", error=str(exc)[:160])
        return []
    import json as _json
    m = re.search(r"\[.*\]", raw or "", re.DOTALL)
    if not m:
        return []
    try:
        parsed = _json.loads(m.group(0))
    except Exception:
        return []
    allowed = set(candidates)
    return [c for c in parsed if isinstance(c, str) and c in allowed]


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

        # LLM citation fallback vocabulary — load the corpus' distinct CFR
        # citations once per run. The regex (_extract_regulations_cited) rarely
        # matches enforcement prose, so when it comes back empty we ask the
        # model which of these the action concerns (constrained → no invented
        # citations). Gated by ENFORCEMENT_LLM_CITATIONS (default on); the
        # extractor itself soft-fails to [] if Bedrock is unavailable.
        llm_citations = os.getenv("ENFORCEMENT_LLM_CITATIONS", "1") != "0"
        candidate_citations: List[str] = []
        if llm_citations:
            try:
                from sqlalchemy import text as _sql
                candidate_citations = [
                    r[0] for r in (await session.execute(_sql(
                        "SELECT DISTINCT cfr_citation FROM compliance_rules "
                        "WHERE cfr_citation IS NOT NULL AND cfr_citation <> ''"))).all()
                ]
            except Exception as exc:
                logger.warning("enforcement_citation_candidates_failed", error=str(exc))

        # Landing-page regulators (legacy path) + index→detail regulators.
        for regulator, url in _SOURCE_URLS.items():
            try:
                actions = self._scrape_agency(regulator, url)
                inserted += await self._process_actions(
                    session, actions, llm_citations, candidate_citations)
            except Exception as exc:
                logger.warning("enforcement_scrape_failed", regulator=regulator, error=str(exc))

        for regulator, cfg in _DETAIL_CRAWL.items():
            try:
                actions = self._scrape_agency_detail(regulator, cfg)
                inserted += await self._process_actions(
                    session, actions, llm_citations, candidate_citations)
            except Exception as exc:
                logger.warning("enforcement_scrape_failed", regulator=regulator, error=str(exc))

        logger.info("enforcement_actions_ingested", inserted=inserted)
        return inserted

    async def _process_actions(
        self, session, actions: List[Dict[str, Any]],
        llm_citations: bool, candidate_citations: List[str],
    ) -> int:
        """Per-action: extract penalty + citations, drop noise, then upsert.

        Penalty/citation extraction runs here (async, per-action) so the
        synchronous scrape stays fast. The relevance gate runs AFTER extraction
        so it can keep any row that turned out to carry a penalty or citation.
        """
        inserted = 0
        for action in actions:
            action["penalty_amount_usd"] = await self._extract_penalty(
                action["regulator"],
                action["action_ref"],
                action.get("_penalty_text", ""),
            )
            # Citation fallback — only when the regex found nothing.
            if (llm_citations and candidate_citations
                    and not action.get("regulations_cited")):
                try:
                    action["regulations_cited"] = await extract_regulations_cited_llm(
                        action.get("_penalty_text", "") or action.get("violation_summary", ""),
                        candidate_citations,
                        regulator=action.get("regulator"),
                        action_type=action.get("action_type"),
                    )
                except Exception as exc:
                    logger.warning("enforcement_citation_llm_failed",
                                   action_ref=action.get("action_ref"), error=str(exc))

            # Drop navigation/boilerplate rows (the landing-page failure mode).
            if _is_enforcement_noise(action):
                logger.info("enforcement_noise_skipped",
                            regulator=action.get("regulator"),
                            title=(action.get("_title") or "")[:80],
                            source_url=action.get("source_url"))
                continue

            action.pop("_penalty_text", None)
            action.pop("_title", None)
            if await self._upsert_action(session, action):
                inserted += 1
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

            content_hash = hashlib.sha256(content.encode()).hexdigest()
            # Stable, content-keyed ref — NOT date-keyed, so re-scraping the
            # same page doesn't mint a new row every day.
            action_ref = f"{regulator}-{content_hash[:16]}"

            actions.append(
                {
                    "action_ref": action_ref,
                    "regulator": regulator,
                    "institution_type": inst_type,
                    "action_date": _extract_action_date(title + " " + content),
                    "action_type": self._infer_action_type(title, content),
                    "regulations_cited": regs_cited,
                    "violation_summary": content[:2000],
                    "penalty_amount_usd": None,
                    "_penalty_text": (title + "\n\n" + content)[:6000],
                    "_title": title,
                    "source_url": doc.get("url", url),
                    "content_hash": content_hash,
                }
            )

        return actions

    def _scrape_agency_detail(self, regulator: str, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Index→detail crawl: one action per detail page, each with its own URL.

        Parses the regulator's enforcement *listing* for links, fetches each
        linked detail page, and collapses the (possibly heading-chunked) detail
        docs into a single action keyed on that detail URL. This is what makes
        source_url unique per action and gives the extractors real prose.
        """
        from src.connectors.regulatory.usa._scraper_utils import (
            make_session, scrape_linked_pages,
        )

        index_url = cfg["index"]
        session = make_session(index_url)
        docs = scrape_linked_pages(
            session,
            index_url=index_url,
            source=regulator,
            category="Enforcement Actions",
            link_pattern=cfg.get("link_pattern", "/"),
            base_url=cfg.get("base", ""),
            max_pages=cfg.get("max_pages", 25),
            content_selectors=cfg.get("content_selectors"),
        )

        # Collapse all chunks of one detail page into a single action.
        by_url: Dict[str, Dict[str, Any]] = {}
        for doc in docs:
            page_url = doc.get("url") or index_url
            if page_url == index_url:  # never treat the listing page as an action
                continue
            entry = by_url.setdefault(page_url, {"title": doc.get("title", ""), "parts": []})
            text = doc.get("content", "")
            if text:
                entry["parts"].append(text)

        actions: List[Dict[str, Any]] = []
        for page_url, entry in by_url.items():
            content = " ".join(entry["parts"]).strip()
            if not content:
                continue
            title = entry["title"]
            blob = f"{title}\n\n{content}"
            inst_type = "unknown"
            for keyword, itype in [
                ("national bank", "national_bank"),
                ("savings bank", "savings_bank"),
                ("credit union", "credit_union"),
                ("mortgage", "mortgage_company"),
                ("fintech", "fintech"),
            ]:
                if keyword in blob.lower():
                    inst_type = itype
                    break
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            actions.append(
                {
                    # Stable + unique per detail page — kills the "same link" bug.
                    "action_ref": f"{regulator}-{hashlib.sha256(page_url.encode()).hexdigest()[:16]}",
                    "regulator": regulator,
                    "institution_type": inst_type,
                    "action_date": _extract_action_date(blob),
                    "action_type": self._infer_action_type(title, content),
                    "regulations_cited": _extract_regulations_cited(blob),
                    "violation_summary": content[:2000],
                    "penalty_amount_usd": None,
                    "_penalty_text": blob[:6000],
                    "_title": title,
                    "source_url": page_url,
                    "content_hash": content_hash,
                }
            )

        logger.info("enforcement_detail_crawl", regulator=regulator,
                    detail_pages=len(by_url), actions=len(actions))
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
            ("memorandum of understanding", "informal_action"),
            ("prohibition", "prohibition"),
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
