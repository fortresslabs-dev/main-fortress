"""
Production Validators — Run INSIDE customer infrastructure (Pipeline 2).

All customer data (transaction amounts, account numbers, beneficiary names)
stays local. Only compliance rules are fetched from Fortress API.

Validators:
  - WireTransferValidator: OCC BSA/AML wire transfer compliance
  - OFACScreener: OFAC SDN sanctions screening (fuzzy match)
  - LoanApplicationValidator: CFPB Section 1071 demographic field checks
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import uuid as _uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

import structlog

from src.services.authority_gate import may_assert_as_law
from src.services.fortress_api_client import FortressAPIClient

logger = structlog.get_logger(__name__)


# Canonical OFAC similarity threshold. Imported by ofac_sdn.py and
# compliance_rule_resolver.py — single source of truth (review §10).
OFAC_SIMILARITY_THRESHOLD = 0.85

# Auto-HOLD threshold. Matches at or above this are blocked outright;
# below trigger REVIEW. Aligns with the "Fuzzy match triggers review ≥92%"
# control surfaced on OfacHealth.jsx.
OFAC_HOLD_THRESHOLD = 0.92

# Staleness bands for the freshness gate. Below SOFT we screen normally;
# between SOFT and HARD we bump decision one level; above HARD we refuse.
_SDN_STALENESS_SOFT_HOURS = 24
_SDN_STALENESS_HOURS = 36  # kept name — read by callers as the hard cutoff


# ── Shared types ────────────────────────────────────────────────────

@dataclass
class MatchDetail:
    """Per-match metadata + subscores. Required by audit/explainability —
    compliance reviewers need the breakdown, not just the blended score.

    `scores` keys are open-ended but standard names are: trgm, fuzz, vector,
    final. Keys absent for a given engine path are omitted (e.g. legacy mode
    only computes fuzz+final, vector mode without query_embedding omits
    vector).
    """
    sdn_number: Optional[str] = None
    matched_name: Optional[str] = None
    is_primary: Optional[bool] = None
    entity_type: Optional[str] = None
    programs: List[str] = field(default_factory=list)
    country: Optional[str] = None
    scores: Dict[str, float] = field(default_factory=dict)


@dataclass
class Violation:
    regulation: str
    rule_id: str
    description: str
    severity: str = "high"  # high | medium | low | advisory
    recommendation: str = ""
    match_detail: Optional[MatchDetail] = None
    # §11.3 hard gate — LAW | STATUTE | GUIDANCE | INTERNAL_CONTROL.
    # Present when the violation was emitted from a DB-fetched rule so the
    # dashboard and examiner_package can split blocking vs advisory rails
    # without re-interpreting severity strings.
    authority_type: Optional[str] = None
    cfr_citation: Optional[str] = None
    authority_source: Optional[str] = None


@dataclass
class ValidationResult:
    """
    Engine result envelope.

    `action` is the legacy outcome (`allow | block | review | clear`) kept for
    one P2 release; downstream callers should migrate to `decision`
    (`PASS | REVIEW | HOLD`) which the audit schema and screening API
    require. `screening_id` is set by the screener when an audit row is
    written — the API layer surfaces it to the caller.
    """
    action: str  # allow | block | review | clear  (legacy)
    violations: List[Violation] = field(default_factory=list)
    screening_timestamp: str = ""
    latency_ms: float = 0.0
    decision: Optional[str] = None       # PASS | REVIEW | HOLD
    screening_id: Optional[str] = None   # set when audit row written
    case_id: Optional[str] = None        # user-facing CASE-XXXX, set only when a case is opened/attached
    final_score: float = 0.0
    # D5 — surfaced to the analyst so they can distinguish a REVIEW caused by
    # low fuzzy confidence from a REVIEW caused by stale SDN data.
    sdn_data_age_hours: float = 0.0
    staleness_bumped: bool = False
    # G4 — when an SDN search lands on a party that already has an open case,
    # the audit attaches to it. The analyst needs to know they're working an
    # existing case, not opening a fresh one.
    case_was_pre_existing: bool = False
    case_opened_at: Optional[str] = None  # ISO-8601 of original case open

    def __post_init__(self):
        if not self.screening_timestamp:
            self.screening_timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "decision": self.decision,
            "screening_id": self.screening_id,
            "case_id": self.case_id,
            "final_score": self.final_score,
            "violations": [asdict(v) for v in self.violations],
            "screening_timestamp": self.screening_timestamp,
            "latency_ms": self.latency_ms,
            "sdn_data_age_hours": self.sdn_data_age_hours,
            "staleness_bumped": self.staleness_bumped,
            "case_was_pre_existing": self.case_was_pre_existing,
            "case_opened_at": self.case_opened_at,
        }


# ── §11.3 authority-gate helpers ─────────────────────────────────────

def _severity_from_rule(rule: Dict[str, Any], default_severity: str) -> str:
    """Downgrade to advisory when the rule fails the §11.3 hard gate.

    * No ``authority_type`` on the rule → trust the caller's default
      (transitional: not every DB row has been backfilled yet).
    * LAW + citation_verified → keep the caller's default severity.
    * Anything else (STATUTE, GUIDANCE, INTERNAL_CONTROL, unverified LAW)
      → force ``"advisory"``. The wire path treats ``"advisory"`` as
      non-blocking / non-review, so a fabricated CFR mapping in the DB
      can no longer gate a customer's transaction.
    """
    if not rule.get("authority_type"):
        return default_severity
    return default_severity if may_assert_as_law(rule) else "advisory"


# ── Decision helpers (used by OFACScreener) ─────────────────────────

def _normalise_criteria(criteria: Dict[str, Any]) -> Dict[str, Any]:
    """Canonicalise screening criteria for stable hashing.

    Keys must be deterministic across PII variations so dedupe works:
    uppercase + collapse-ws on name, uppercase 2-letter country, ISO date.
    """
    out: Dict[str, Any] = {}
    if "name" in criteria and criteria["name"]:
        out["name"] = _normalise_name(str(criteria["name"]))
    if criteria.get("country"):
        out["country"] = str(criteria["country"]).strip().upper()[:2]
    if criteria.get("dob"):
        out["dob"] = str(criteria["dob"]).strip()[:10]
    if criteria.get("address"):
        out["address"] = " ".join(str(criteria["address"]).split()).upper()
    return out


def _criteria_hash(criteria: Dict[str, Any]) -> str:
    canonical = json.dumps(_normalise_criteria(criteria), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _bump_decision(decision: str) -> str:
    """PASS → REVIEW → HOLD. HOLD is the ceiling."""
    return {"PASS": "REVIEW", "REVIEW": "HOLD", "HOLD": "HOLD"}.get(decision, decision)


# G2 — FRESH rule weight is engine-active. When > 0 the staleness bump
# fires; when <= 0 (or the row is inactive) it doesn't. Cached at module
# level with a TTL so a screen call doesn't pay a DB round-trip per
# invocation. Reload by calling `_invalidate_fresh_cache()` (test hook).
_FRESH_WEIGHT_TTL_SECONDS = 300.0
_fresh_cache: Dict[str, Any] = {"enabled": None, "fetched_at": 0.0}


def _invalidate_fresh_cache() -> None:
    _fresh_cache["enabled"] = None
    _fresh_cache["fetched_at"] = 0.0


async def _get_fresh_enabled() -> bool:
    """Read OFAC-SCREEN-FRESH-001 weight + is_active with TTL caching.

    Fail-open semantics — a DB error, a missing row (pre-034 DB), or a
    NULL weight all leave the staleness bump enabled. Disabling a safety
    control on a DB hiccup is exactly the wrong direction.
    """
    import time
    now = time.monotonic()
    if (
        _fresh_cache["enabled"] is not None
        and now - _fresh_cache["fetched_at"] < _FRESH_WEIGHT_TTL_SECONDS
    ):
        return _fresh_cache["enabled"]
    try:
        from sqlalchemy import text
        from src.services.database import get_db
        async with get_db() as session:
            row = (await session.execute(text("""
                SELECT weight::float AS weight, is_active
                FROM screening_rules
                WHERE rule_id = 'OFAC-SCREEN-FRESH-001'
            """))).first()
        if row is None or row.weight is None:
            enabled = True
        else:
            enabled = bool(row.is_active) and float(row.weight) > 0.0
    except Exception:
        enabled = True
    _fresh_cache["enabled"] = enabled
    _fresh_cache["fetched_at"] = now
    return enabled


def _derive_decision(
    matches: List["Violation"],
    stale_hours: float,
    fresh_enabled: bool = True,
) -> tuple[str, float]:
    """Return (decision, top_final_score).

    `fresh_enabled` gates the soft-staleness bump. Default True keeps
    pre-G2 behaviour for any caller that hasn't been updated; production
    callers should pass the result of `_get_fresh_enabled()`.
    """
    if not matches:
        decision = "PASS"
        top_score = 0.0
    else:
        top_score = max(
            (m.match_detail.scores.get("final", 0.0) if m.match_detail else 0.0)
            for m in matches
        )
        has_block_violation = any(
            m.rule_id.startswith("OFAC_BLOCK") for m in matches
        )
        if top_score >= OFAC_HOLD_THRESHOLD or has_block_violation:
            decision = "HOLD"
        else:
            decision = "REVIEW"
    # Soft-stale data bumps decision one level — unless the FRESH rule
    # has been disabled by compliance (weight <= 0 or is_active=false).
    if fresh_enabled and stale_hours > _SDN_STALENESS_SOFT_HOURS:
        decision = _bump_decision(decision)
    return decision, float(top_score)


# D3 — case attribution. Maps the violations that opened the case to one of
# the four `screening_rules` rows. Only FUZZY / OWN50 ever appear: CLEAN is a
# pass-through (no case) and FRESH only *amplifies* (REVIEW→HOLD) an already-
# open case — it can't open one alone because _write_audit requires a
# top_violation. Priority: OWN50 > FUZZY (strictest control wins).
_RULE_OWN50 = "OFAC-SCREEN-OWN50-001"
_RULE_FUZZY = "OFAC-SCREEN-FUZZY-001"


def _primary_screening_rule(violations: List["Violation"]) -> Optional[str]:
    if any(v.rule_id.startswith("OFAC_BLOCK") for v in violations):
        return _RULE_OWN50
    if violations:
        return _RULE_FUZZY
    return None


# =====================================================================
# WIRE TRANSFER VALIDATOR
# =====================================================================

class WireTransferValidator:
    """
    Validates wire transfers against OCC BSA/AML rules.

    Runs entirely LOCAL — customer transaction data never leaves infrastructure.
    Rules fetched from Fortress API are public regulatory data.
    """

    def __init__(self, fortress_client: FortressAPIClient):
        self._client = fortress_client

    async def validate(self, transfer: Dict[str, Any]) -> ValidationResult:
        """
        Validate a wire transfer for compliance.

        Args:
            transfer: {
                "amount": 15000.00,
                "currency": "USD",
                "sender_name": "...",
                "beneficiary_name": "...",
                "beneficiary_country": "US",
                "approvals": [{"approver": "john", "timestamp": "..."}],
                "is_international": False,
            }

        Returns:
            ValidationResult with action = allow | block | review
        """
        import time
        start = time.monotonic()

        # Fetch rules from Fortress API (public regulatory data only)
        rules = await self._client.get_rules(
            geography="usa", regulator="occ", domain="wire_transfer"
        )

        violations: List[Violation] = []
        amount = float(transfer.get("amount", 0))
        approvals = transfer.get("approvals", [])
        currency = transfer.get("currency", "USD")
        is_international = transfer.get("is_international", False)

        # Travel Rule ($3K threshold) applies to ALL wires, not just
        # international. 31 C.F.R. § 1010.410(f). The prior implementation
        # only fired when is_international=True — which meant we did NOT
        # apply the Travel Rule to the $3,000–$10,000 domestic band.
        # `travel_rule_fired` guarantees the check runs at least once
        # regardless of what the DB rule set contains.
        travel_rule_fired = False
        recordkeeping_fired = False

        for rule in rules:
            rule_type = rule.get("rule_type", "")
            threshold = rule.get("threshold_amount")
            rule_id = rule.get("rule_id", "")

            # Dual approval — INTERNAL_CONTROL, NOT law.
            # 31 C.F.R. § 1010.410(f) is the Travel Rule, not a dual-
            # approval mandate; OCC Comptroller's Handbook supervisory
            # expectation. Advisory only — never blocks.
            if rule_type in ("dual_approval", "dual_approval_limit") and threshold:
                if amount >= threshold and len(approvals) < 2:
                    violations.append(Violation(
                        regulation="OCC Comptroller's Handbook — Wire Transfer",
                        rule_id=rule_id or "BSA-DUALCTRL-001",
                        description=(
                            f"Wire transfer ${amount:,.2f} >= ${threshold:,.2f} "
                            f"lacks dual approval (only {len(approvals)} approval(s)). "
                            "Internal control expectation — not a federal requirement."
                        ),
                        severity=_severity_from_rule(rule, "advisory"),
                        recommendation=(
                            "Bank policy typically requires a second approver; "
                            "confirm dual-control policy is applied."
                        ),
                        authority_type=rule.get("authority_type") or "INTERNAL_CONTROL",
                        cfr_citation=rule.get("cfr_citation"),
                        authority_source=rule.get("authority_source")
                            or "OCC Comptroller's Handbook — Wire Transfer",
                    ))

            # CTR threshold — 31 C.F.R. § 1010.311 ($10,000 cash)
            if rule_type == "ctr_threshold" and threshold:
                if amount >= threshold:
                    violations.append(Violation(
                        regulation="BSA Currency Transaction Report",
                        rule_id=rule_id or "OCC-BSA-CTR-001",
                        description=f"Wire transfer ${amount:,.2f} >= ${threshold:,.2f} triggers CTR filing requirement.",
                        severity=_severity_from_rule(rule, "medium"),
                        recommendation="File CTR within 15 days.",
                        authority_type=rule.get("authority_type"),
                        cfr_citation=rule.get("cfr_citation") or "31 C.F.R. § 1010.311",
                    ))

            # Travel Rule — 31 C.F.R. § 1010.410(f), $3K threshold, ALL wires.
            if rule_type in ("travel_rule", "travel_rule_threshold"):
                tr_threshold = threshold or 3000
                if amount >= tr_threshold:
                    travel_rule_fired = True
                    violations.append(Violation(
                        regulation="BSA Travel Rule (31 CFR 1010.410(f))",
                        rule_id=rule_id or "BSA-TRAVEL-001",
                        description=(
                            f"Wire transfer ${amount:,.2f} >= ${tr_threshold:,.2f} "
                            "requires transmittor name/address/account, amount, date, "
                            "recipient FI, and recipient name/address/account in the "
                            "transmittal order."
                        ),
                        severity=_severity_from_rule(rule, "medium"),
                        recommendation="Ensure all Travel Rule fields are present in the transmittal order.",
                        authority_type=rule.get("authority_type"),
                        cfr_citation=rule.get("cfr_citation") or "31 C.F.R. § 1010.410(f)",
                    ))

            # Originator's-bank recordkeeping — 31 C.F.R. § 1020.410(a), $3K, 5-yr retention.
            if rule_type in ("recordkeeping", "wire_recordkeeping"):
                rk_threshold = threshold or 3000
                if amount >= rk_threshold:
                    recordkeeping_fired = True
                    violations.append(Violation(
                        regulation="BSA Recordkeeping (31 CFR 1020.410(a))",
                        rule_id=rule_id or "BSA-RECKEEP-001",
                        description=(
                            f"Payment order ${amount:,.2f} >= ${rk_threshold:,.2f} "
                            "requires 5-year retention of originator name/address, amount, "
                            "date, payment instructions, and beneficiary bank identity."
                        ),
                        severity=_severity_from_rule(rule, "medium"),
                        recommendation="Confirm wire audit trail is stored per 5-year retention.",
                        authority_type=rule.get("authority_type"),
                        cfr_citation=rule.get("cfr_citation") or "31 C.F.R. § 1020.410(a)",
                    ))

            # International wire additional checks (retained for parity with
            # legacy rule sets that emit rule_type=international_wire).
            if rule_type == "international_wire" and is_international:
                if amount >= (threshold or 3000):
                    violations.append(Violation(
                        regulation="BSA Travel Rule (International)",
                        rule_id=rule_id or "BSA-TRAVEL-001",
                        description=f"International wire ${amount:,.2f} requires full originator/beneficiary info.",
                        severity=_severity_from_rule(rule, "medium"),
                        recommendation="Ensure Travel Rule fields are complete.",
                        authority_type=rule.get("authority_type"),
                        cfr_citation=rule.get("cfr_citation") or "31 C.F.R. § 1010.410(f)",
                    ))

        # Fail-safe: if the DB rule set omits travel_rule/recordkeeping,
        # apply them anyway at their statutory $3K thresholds. The prior
        # code silently skipped Travel Rule for the $3K–$10K domestic band
        # if the rule set didn't include an `international_wire` row.
        if not travel_rule_fired and amount >= 3000:
            violations.append(Violation(
                regulation="BSA Travel Rule (31 CFR 1010.410(f))",
                rule_id="BSA-TRAVEL-001",
                description=(
                    f"Wire transfer ${amount:,.2f} >= $3,000.00 requires "
                    "transmittor + recipient information in the transmittal order."
                ),
                severity="medium",
                recommendation="Ensure all Travel Rule fields are present.",
                # Statutory fail-safe: this is codified law regardless of DB
                # state, so we stamp LAW here so downstream (§11.3 gate,
                # examiner package) treats it as blocking, not advisory.
                authority_type="LAW",
                cfr_citation="31 C.F.R. § 1010.410(f)",
            ))
        if not recordkeeping_fired and amount >= 3000:
            violations.append(Violation(
                regulation="BSA Recordkeeping (31 CFR 1020.410(a))",
                rule_id="BSA-RECKEEP-001",
                description=(
                    f"Payment order ${amount:,.2f} >= $3,000.00 requires "
                    "5-year originator record retention."
                ),
                severity="medium",
                recommendation="Confirm wire audit trail is stored per 5-year retention.",
                authority_type="LAW",
                cfr_citation="31 C.F.R. § 1020.410(a)",
            ))

        elapsed = (time.monotonic() - start) * 1000

        # Determine action. `advisory` never blocks and never triggers review.
        high_violations = [v for v in violations if v.severity == "high"]
        review_violations = [v for v in violations if v.severity in ("medium",)]
        if high_violations:
            action = "block"
        elif review_violations:
            action = "review"
        else:
            action = "allow"

        logger.info(
            "wire_transfer_validated",
            amount=amount,
            action=action,
            violations=len(violations),
            latency_ms=f"{elapsed:.1f}",
        )

        return ValidationResult(action=action, violations=violations, latency_ms=elapsed)


# =====================================================================
# OFAC SCREENER
# =====================================================================

# Intra-syllable marks used in OFAC transliterations (`Ch'o'l` ≡ `Chol`).
# Stripped WITHOUT a space so syllables fuse.
_INTRA_WORD_MARKS_RE = re.compile("[\u0027\u0060\u2018\u2019\u201B\u02BC\u02BB]")


def _normalise_name(name: str) -> str:
    """NFD strip, uppercase, strip punct, collapse ws.

    Must stay byte-for-byte equivalent with
    src/services/ofac_sdn_loader.py:normalize_name — both sides feed the
    same name_normalized column / query path.
    """
    if not name:
        return ""
    nfd = unicodedata.normalize("NFD", name)
    no_accent = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    upper = no_accent.upper()
    no_marks = _INTRA_WORD_MARKS_RE.sub("", upper)
    no_punct = re.sub(r"[^\w\s]", " ", no_marks, flags=re.UNICODE)
    return " ".join(no_punct.split())


class OFACScreener:
    """OFAC SDN screener — feature-flagged between legacy and vector backends.

    Mode controlled by env var `OFAC_SCREENER_MODE`:
      - "legacy" (default): fetches SDN list via FortressAPIClient, does
        in-memory difflib.SequenceMatcher scan. Compares primary name only.
      - "vector": queries ofac_sdn_vectors directly via pg_trgm + tsvector,
        re-ranks top-50 with rapidfuzz token_sort_ratio, optionally re-ranks
        with pgvector cosine if a query embedding is provided. Screens
        primary names AND every alias (rows-per-variant schema).

    Both paths share the same public `screen()` signature and ValidationResult
    shape so the surrounding agents and audit log are unchanged.
    """

    def __init__(
        self,
        fortress_client: Optional[FortressAPIClient] = None,
        match_threshold: float = OFAC_SIMILARITY_THRESHOLD,
        mode: Optional[str] = None,
    ):
        self._client = fortress_client
        self._match_threshold = match_threshold
        self._mode = (mode or os.getenv("OFAC_SCREENER_MODE", "legacy")).lower()

    async def screen(
        self,
        party_name: str,
        party_country: Optional[str] = None,
        *,
        criteria: Optional[Dict[str, Any]] = None,
        actor_kind: str = "api",
        actor_user_id: Optional[str] = None,
        request_kind: str = "customer",
        request_group_id: Optional[str] = None,
        external_customer_id: Optional[str] = None,
        party_role: Optional[str] = None,
        write_audit: bool = True,
    ) -> ValidationResult:
        """Screen a party against the OFAC SDN list.

        The keyword-only args are audit context — they shape the
        screening_events row and the case-upsert key. Default
        `write_audit=True` is safe for all callers because the helper
        catches DB errors and degrades to a no-op rather than failing the
        screen itself; the engine's job is to return a result.
        """
        if self._mode == "vector":
            result = await self._screen_vector(party_name, party_country)
        else:
            result = await self._screen_legacy(party_name, party_country)

        # Compute decision + audit. Done here (post-mode) so both legacy
        # and vector paths get identical treatment.
        crit = criteria or {"name": party_name, "country": party_country}
        stale_hours = await self._stale_hours_safe()
        fresh_enabled = await _get_fresh_enabled()
        decision, top_score = _derive_decision(
            result.violations, stale_hours, fresh_enabled=fresh_enabled,
        )
        result.decision = decision
        result.final_score = top_score
        result.sdn_data_age_hours = float(stale_hours)
        result.staleness_bumped = bool(
            fresh_enabled and stale_hours > _SDN_STALENESS_SOFT_HOURS
        )

        if write_audit:
            try:
                (
                    screening_id, case_id,
                    case_opened_at, case_was_pre_existing,
                ) = await self._write_audit(
                    result=result,
                    criteria=crit,
                    actor_kind=actor_kind,
                    actor_user_id=actor_user_id,
                    request_kind=request_kind,
                    request_group_id=request_group_id,
                    external_customer_id=external_customer_id,
                    party_role=party_role,
                    party_name=party_name,
                    party_country=party_country,
                    sdn_data_age_hours=stale_hours,
                )
                result.screening_id = screening_id
                result.case_id = case_id
                result.case_opened_at = case_opened_at
                result.case_was_pre_existing = case_was_pre_existing
            except Exception as exc:
                # Audit failure must not break a screening decision — log
                # and continue. Ops alarms watch screening_audit_failed.
                logger.warning("screening_audit_failed", error=str(exc))
        return result

    # ── Legacy (preserved for one release as feature-flag fallback) ──────

    async def _screen_legacy(self, party_name: str, party_country: Optional[str]) -> ValidationResult:
        import time
        start = time.monotonic()

        if self._client is None:
            raise RuntimeError("OFACScreener legacy mode requires a FortressAPIClient")

        sdn_entries = await self._client.get_ofac_sdn_list()
        matches: List[Violation] = []
        normalised_name = _normalise_name(party_name)
        query_tokens = set(normalised_name.split())

        for entry in sdn_entries:
            entity_name = entry.get("entity_name", "")
            entity_norm = _normalise_name(entity_name)
            # AND semantics — ALL query tokens must be present in the SDN
            # variant. Mirrors OFAC's own tool: "kim chol" surfaces every
            # variant containing both "KIM" and "CHOL" at score 100, but
            # not "KIM" alone. Apostrophe transliterations (`Ch'o'l`) fuse
            # to `CHOL` upstream in `_normalise_name`.
            token_hit = bool(query_tokens and query_tokens.issubset(set(entity_norm.split())))
            if token_hit:
                score = 1.0
                fuzz_component = SequenceMatcher(None, normalised_name, entity_norm).ratio()
            else:
                score = SequenceMatcher(None, normalised_name, entity_norm).ratio()
                fuzz_component = score
            if score >= self._match_threshold:
                sdn_no = entry.get("sdn_number")
                matches.append(Violation(
                    regulation="OFAC Sanctions",
                    rule_id=f"OFAC-SDN-{sdn_no or 'UNKNOWN'}",
                    description=f"Potential SDN match: '{entity_name}' (score: {score:.2f})",
                    severity="high",
                    recommendation=f"Block transaction. File SAR. SDN programs: {entry.get('programs', [])}",
                    match_detail=MatchDetail(
                        sdn_number=str(sdn_no) if sdn_no is not None else None,
                        matched_name=entity_name,
                        is_primary=True,  # legacy path screens primary names only
                        entity_type=entry.get("entity_type"),
                        programs=list(entry.get("programs") or []),
                        country=entry.get("country"),
                        scores={
                            "fuzz": float(fuzz_component),
                            "token_hit": 1.0 if token_hit else 0.0,
                            "final": float(score),
                        },
                    ),
                ))

        elapsed = (time.monotonic() - start) * 1000
        action = "block" if matches else "clear"
        logger.info(
            "ofac_screening_complete",
            mode="legacy", party=party_name[:30], action=action,
            matches=len(matches), latency_ms=f"{elapsed:.1f}",
        )
        return ValidationResult(action=action, violations=matches, latency_ms=elapsed)

    # ── Vector path — DB-backed hybrid query ─────────────────────────────

    async def _screen_vector(
        self,
        party_name: str,
        party_country: Optional[str],
        query_embedding: Optional[List[float]] = None,
    ) -> ValidationResult:
        import time
        start = time.monotonic()
        from src.services.database import get_db
        from sqlalchemy import text

        normalised = _normalise_name(party_name)
        if not normalised:
            return ValidationResult(action="clear", violations=[], latency_ms=0.0)

        async with get_db() as session:
            await self._enforce_freshness(session)

            # pg_trgm.similarity_threshold controls the `%` operator. Set
            # below the screening threshold so we still see borderline rows
            # for re-ranking, but tight enough that the GIN index prunes.
            await session.execute(text(
                "SET LOCAL pg_trgm.similarity_threshold = :t"
            ), {"t": max(self._match_threshold - 0.10, 0.50)})

            candidates = (await session.execute(text("""
                SELECT
                    sdn_number, name_variant, name_normalized, is_primary,
                    entity_type, programs, country,
                    similarity(name_normalized, :q)                          AS trgm_score,
                    ts_rank(tsv, plainto_tsquery('simple', :q))              AS ts_score
                FROM ofac_sdn_vectors
                WHERE is_active = TRUE
                  AND (
                        name_normalized %% :q
                     OR tsv @@ plainto_tsquery('simple', :q)
                  )
                ORDER BY trgm_score DESC
                LIMIT 50
            """), {"q": normalised})).all()

            if not candidates:
                elapsed = (time.monotonic() - start) * 1000
                logger.info("ofac_screening_complete", mode="vector",
                            party=party_name[:30], action="clear",
                            matches=0, latency_ms=f"{elapsed:.1f}")
                return ValidationResult(action="clear", violations=[], latency_ms=elapsed)

            scored = self._rerank(normalised, candidates)

            if query_embedding:
                scored = await self._vector_rerank(session, scored, query_embedding)

        # Dedupe by sdn_number — multiple aliases of the same entity collapse
        # to the highest-scoring variant in the violation list.
        best_by_sdn: dict[str, dict] = {}
        for c in scored:
            cur = best_by_sdn.get(c["sdn_number"])
            if cur is None or c["final_score"] > cur["final_score"]:
                best_by_sdn[c["sdn_number"]] = c

        matches: List[Violation] = []
        for c in best_by_sdn.values():
            if c["final_score"] >= self._match_threshold:
                scores = {
                    "trgm": float(c["trgm_score"]),
                    "fuzz": float(c["fuzz_score"]),
                    "token_hit": 1.0 if c.get("token_hit") else 0.0,
                    "final": float(c["final_score"]),
                }
                if c.get("vector_score") is not None:
                    scores["vector"] = float(c["vector_score"])
                matches.append(Violation(
                    regulation="OFAC Sanctions",
                    rule_id=f"OFAC-SDN-{c['sdn_number']}",
                    description=(
                        f"Potential SDN match: '{c['name_variant']}' "
                        f"(score: {c['final_score']:.2f}, alias: {not c['is_primary']})"
                    ),
                    severity="high",
                    recommendation=(
                        f"Block transaction. File SAR within 10 business days. "
                        f"SDN programs: {c.get('programs')}"
                    ),
                    match_detail=MatchDetail(
                        sdn_number=str(c["sdn_number"]),
                        matched_name=c["name_variant"],
                        is_primary=bool(c["is_primary"]),
                        entity_type=c.get("entity_type"),
                        programs=list(c.get("programs") or []),
                        country=c.get("country"),
                        scores=scores,
                    ),
                ))

        elapsed = (time.monotonic() - start) * 1000
        action = "block" if matches else "clear"
        logger.info(
            "ofac_screening_complete",
            mode="vector", party=party_name[:30], action=action,
            matches=len(matches), candidates=len(candidates),
            latency_ms=f"{elapsed:.1f}",
        )
        return ValidationResult(action=action, violations=matches, latency_ms=elapsed)

    async def _enforce_freshness(self, session) -> None:
        """Hard stale-data gate. Refuse to screen if either side is past
        `_SDN_STALENESS_HOURS`. Between SOFT and HARD bands the decision
        derivation bumps the verdict one level (handled in
        `_derive_decision`), so we still return matches but flag elevated
        risk.
        """
        hrs = await self._effective_stale_hours(session)
        if hrs > _SDN_STALENESS_HOURS:
            raise RuntimeError(
                f"OFAC SDN data is stale ({hrs:.1f}h > "
                f"{_SDN_STALENESS_HOURS}h) — refusing to screen per OFAC-SDN-DAILY-001"
            )

    @staticmethod
    async def _effective_stale_hours(session) -> float:
        """Worse of local-replica age vs upstream-scrape age. NULL on
        upstream means "unknown — don't penalise" (e.g. local-dev DBs)."""
        from sqlalchemy import text
        local_row = (await session.execute(text("""
            SELECT EXTRACT(EPOCH FROM (now() - MAX(last_seen_at)))/3600 AS hrs
            FROM ofac_sdn_vectors WHERE is_active = TRUE
        """))).first()
        local_hrs = float(local_row[0]) if local_row and local_row[0] is not None else 999.0

        p1_row = (await session.execute(text("""
            SELECT EXTRACT(EPOCH FROM (now() - MAX(p1_sdn_last_scraped_at)))/3600 AS hrs
            FROM sync_metadata WHERE p1_sdn_last_scraped_at IS NOT NULL
        """))).first()
        p1_hrs = float(p1_row[0]) if p1_row and p1_row[0] is not None else None

        return max(local_hrs, p1_hrs) if p1_hrs is not None else local_hrs

    async def _stale_hours_safe(self) -> float:
        """Best-effort lookup that never raises. Used to feed `_derive_decision`
        when we don't already have a session open (legacy mode path)."""
        try:
            from src.services.database import get_db
            async with get_db() as session:
                return await self._effective_stale_hours(session)
        except Exception:
            return 0.0

    async def _write_audit(
        self,
        *,
        result: ValidationResult,
        criteria: Dict[str, Any],
        actor_kind: str,
        actor_user_id: Optional[str],
        request_kind: str,
        request_group_id: Optional[str],
        external_customer_id: Optional[str],
        party_role: Optional[str],
        party_name: str,
        party_country: Optional[str],
        sdn_data_age_hours: float,
    ) -> tuple[str, Optional[str], Optional[str], bool]:
        """Insert a screening_events row + upsert screening_cases on
        REVIEW/HOLD. Returns
        `(screening_id, case_id, case_opened_at, case_was_pre_existing)`.
        `case_id` is None when no case was opened (e.g. PASS); the last
        two are populated whenever `case_id` is — `case_opened_at` is the
        original open time (existing case) or `now()` (newly opened), and
        `case_was_pre_existing` is True iff we attached to an open case
        instead of inserting a new row."""
        from sqlalchemy import text
        from src.services.database import get_db

        screening_id = _uuid.uuid4().hex
        opened_case_id: Optional[str] = None
        case_opened_at: Optional[str] = None
        case_was_pre_existing: bool = False
        crit_hash = _criteria_hash(criteria)
        engine_payload = result.to_dict()

        # Decode top match for the case row (if any).
        top_violation = None
        if result.violations:
            top_violation = max(
                result.violations,
                key=lambda v: (
                    v.match_detail.scores.get("final", 0.0)
                    if v.match_detail else 0.0
                ),
            )

        async with get_db() as session:
            ev_row = (await session.execute(text("""
                INSERT INTO screening_events (
                    screening_id, request_kind, request_group_id,
                    external_customer_id, party_role, party_name, party_country,
                    criteria, criteria_hash, decision, final_score, match_count,
                    engine_mode, engine_response, sdn_data_age_hours,
                    latency_ms, actor_kind, actor_user_id
                ) VALUES (
                    :screening_id, :request_kind, :request_group_id,
                    :external_customer_id, :party_role, :party_name, :party_country,
                    CAST(:criteria AS JSONB), :criteria_hash, :decision,
                    :final_score, :match_count, :engine_mode,
                    CAST(:engine_response AS JSONB), :sdn_age,
                    :latency_ms, :actor_kind, :actor_user_id
                )
                RETURNING id
            """), {
                "screening_id":         screening_id,
                "request_kind":         request_kind,
                "request_group_id":     request_group_id,
                "external_customer_id": external_customer_id,
                "party_role":           party_role,
                "party_name":           party_name,
                "party_country":        (party_country or "")[:8] or None,
                "criteria":             json.dumps(criteria, default=str),
                "criteria_hash":        crit_hash,
                "decision":             result.decision,
                "final_score":          round(float(result.final_score), 4),
                "match_count":          len(result.violations),
                "engine_mode":          self._mode,
                "engine_response":      json.dumps(engine_payload, default=str),
                "sdn_age":              round(float(sdn_data_age_hours), 2),
                "latency_ms":           round(float(result.latency_ms), 2),
                "actor_kind":           actor_kind,
                "actor_user_id":        actor_user_id,
            })).first()
            event_uuid = ev_row[0]

            if result.decision in ("REVIEW", "HOLD") and top_violation:
                md = top_violation.match_detail
                sdn_number = md.sdn_number if md else None
                matched_name = md.matched_name if md else None
                priority = "high" if result.decision == "HOLD" else "medium"

                # Find an open case to attach to. Two lookup keys: the
                # unique partial index on (customer_id, sdn) covers the
                # common case; falls back to criteria_hash within 24h for
                # anonymous flows (no external_customer_id supplied).
                existing = (await session.execute(text("""
                    SELECT c.id, c.case_id, c.created_at FROM screening_cases c
                    WHERE c.status NOT IN ('cleared','closed')
                      AND (
                          (c.external_customer_id IS NOT NULL
                           AND c.external_customer_id = :customer_id
                           AND c.sdn_number = :sdn_number)
                          OR
                          (c.external_customer_id IS NULL
                           AND EXISTS (
                               SELECT 1 FROM screening_events ev
                               WHERE ev.id = c.first_event_id
                                 AND ev.criteria_hash = :crit_hash
                                 AND ev.created_at > now() - INTERVAL '24 hours'
                           )
                           AND c.sdn_number = :sdn_number)
                      )
                    ORDER BY c.created_at DESC LIMIT 1
                """), {
                    "customer_id": external_customer_id,
                    "sdn_number":  sdn_number,
                    "crit_hash":   crit_hash,
                })).first()

                if existing:
                    case_uuid = existing[0]
                    opened_case_id = existing[1]
                    case_was_pre_existing = True
                    case_opened_at = (
                        existing[2].isoformat() if existing[2] else None
                    )
                    await session.execute(text("""
                        UPDATE screening_cases
                        SET last_event_id = :event_id,
                            event_count   = event_count + 1,
                            priority      = CASE
                                WHEN priority = 'medium' AND :priority = 'high'
                                THEN 'high' ELSE priority END,
                            decision_at_open = CASE
                                WHEN decision_at_open = 'REVIEW' AND :decision = 'HOLD'
                                THEN 'HOLD' ELSE decision_at_open END
                        WHERE id = :case_uuid
                    """), {
                        "event_id":  event_uuid,
                        "priority":  priority,
                        "decision":  result.decision,
                        "case_uuid": case_uuid,
                    })
                else:
                    opened_case_id = f"CASE-{_uuid.uuid4().hex[:12].upper()}"
                    matched_rule_id = _primary_screening_rule(result.violations)
                    case_row = (await session.execute(text("""
                        INSERT INTO screening_cases (
                            case_id, external_customer_id, sdn_number,
                            matched_name, priority, decision_at_open,
                            first_event_id, last_event_id, event_count,
                            matched_rule_id
                        ) VALUES (
                            :case_id, :customer_id, :sdn_number,
                            :matched_name, :priority, :decision,
                            :event_id, :event_id, 1,
                            :matched_rule_id
                        )
                        RETURNING id, created_at
                    """), {
                        "case_id":         opened_case_id,
                        "customer_id":     external_customer_id,
                        "sdn_number":      sdn_number,
                        "matched_name":    matched_name,
                        "priority":        priority,
                        "decision":        result.decision,
                        "event_id":        event_uuid,
                        "matched_rule_id": matched_rule_id,
                    })).first()
                    case_uuid = case_row[0]
                    case_was_pre_existing = False
                    case_opened_at = (
                        case_row[1].isoformat() if case_row[1] else None
                    )

                await session.execute(text("""
                    INSERT INTO screening_case_events (case_id, event_id)
                    VALUES (:case_uuid, :event_id)
                    ON CONFLICT DO NOTHING
                """), {"case_uuid": case_uuid, "event_id": event_uuid})

        return screening_id, opened_case_id, case_opened_at, case_was_pre_existing

    @staticmethod
    def _rerank(normalised: str, candidates: list) -> list[dict]:
        try:
            from rapidfuzz import fuzz
            scorer = lambda a, b: fuzz.token_sort_ratio(a, b) / 100.0
        except ImportError:
            scorer = lambda a, b: SequenceMatcher(None, a, b).ratio()

        query_tokens = set(normalised.split())
        out = []
        for c in candidates:
            row = dict(c._mapping) if hasattr(c, "_mapping") else dict(c)
            fuzz_score = scorer(normalised, row["name_normalized"])
            row["fuzz_score"] = fuzz_score
            # Token-containment fast path — ALL query tokens must appear
            # as tokens in the SDN variant. Mirrors OFAC's own tool: bare
            # surname "kim" surfaces 117 KIM variants at score 100; "kim
            # chol" narrows to variants containing both KIM *and* CHOL.
            # Would otherwise land at ~0.3 under the whole-string
            # trgm+token_sort blend and be filtered by the threshold gate.
            variant_tokens = set((row["name_normalized"] or "").split())
            row["token_hit"] = bool(query_tokens and query_tokens.issubset(variant_tokens))
            if row["token_hit"]:
                row["final_score"] = 1.0
            else:
                # 50/50 blend of pg_trgm and fuzz — both are token-level fuzzy
                # but pg_trgm is character-trigram while rapidfuzz uses tokens,
                # so they catch different misspelling patterns.
                row["final_score"] = 0.5 * float(row["trgm_score"]) + 0.5 * fuzz_score
            out.append(row)
        out.sort(key=lambda r: r["final_score"], reverse=True)
        return out

    @staticmethod
    async def _vector_rerank(session, candidates: list[dict], query_emb: list[float]) -> list[dict]:
        from sqlalchemy import text
        sdn_numbers = list({c["sdn_number"] for c in candidates})
        rows = (await session.execute(text("""
            SELECT DISTINCT ON (sdn_number)
                sdn_number,
                1 - (embedding <=> CAST(:vec AS vector)) AS cosine_score
            FROM ofac_sdn_vectors
            WHERE sdn_number = ANY(:sdns)
              AND embedding IS NOT NULL
            ORDER BY sdn_number, cosine_score DESC
        """), {"vec": str(query_emb), "sdns": sdn_numbers})).all()
        cosine_map = {r.sdn_number: float(r.cosine_score) for r in rows}
        for c in candidates:
            cs = cosine_map.get(c["sdn_number"], 0.0)
            c["vector_score"] = cs
            if c.get("token_hit"):
                # Token-containment from _rerank wins over the trilinear
                # blend — cosine can't outrank an exact-token OFAC hit.
                c["final_score"] = 1.0
            else:
                # 40% trgm, 40% rapidfuzz token, 20% cosine — cosine catches
                # transliteration ("Хусейн" vs "Hussein") where lexical methods
                # have nothing to match.
                c["final_score"] = (
                    0.40 * float(c["trgm_score"])
                    + 0.40 * float(c["fuzz_score"])
                    + 0.20 * cs
                )
        candidates.sort(key=lambda r: r["final_score"], reverse=True)
        return candidates


# =====================================================================
# LOAN APPLICATION VALIDATOR (Section 1071)
# =====================================================================

class LoanApplicationValidator:
    """
    Validates loan applications for CFPB Section 1071 compliance.

    Runs entirely LOCAL — applicant demographic data never leaves customer infrastructure.
    """

    REQUIRED_1071_FIELDS = [
        "race_of_principal_owner",
        "sex_of_principal_owner",
        "ethnicity_of_principal_owner",
        "naics_code",
        "business_gross_revenue",
        "time_in_business",
        "number_of_workers",
        "minority_owned_business",
        "women_owned_business",
    ]

    def __init__(self, fortress_client: FortressAPIClient):
        self._client = fortress_client

    async def validate(self, application: Dict[str, Any]) -> ValidationResult:
        """
        Validate a small business loan application for Section 1071 compliance.

        Args:
            application: Dict with loan application fields (stays LOCAL)

        Returns:
            ValidationResult with action = allow | block
        """
        import time
        start = time.monotonic()

        # Optionally fetch latest field requirements from Fortress API
        rules = await self._client.get_rules(
            geography="usa", regulator="cfpb", domain="section_1071"
        )

        violations: List[Violation] = []

        # Check required demographic fields
        required_fields = self.REQUIRED_1071_FIELDS
        # Overlay dynamic fields from API if available
        for rule in rules:
            if rule.get("rule_type") == "required_field":
                field_name = rule.get("field_name") or rule.get("rule_id", "")
                if field_name and field_name not in required_fields:
                    required_fields.append(field_name)

        for fld in required_fields:
            val = application.get(fld)
            if val is None or (isinstance(val, str) and not val.strip()):
                violations.append(Violation(
                    regulation="CFPB Section 1071",
                    rule_id=f"1071-FIELD-{fld.upper()}",
                    description=f"Required field '{fld}' is missing or empty.",
                    severity="high",
                    recommendation=f"Collect '{fld}' from applicant before processing.",
                ))

        # NAICS code validation
        naics = application.get("naics_code", "")
        if naics and (not naics.isdigit() or len(naics) not in (2, 3, 4, 5, 6)):
            violations.append(Violation(
                regulation="CFPB Section 1071",
                rule_id="1071-NAICS-INVALID",
                description=f"NAICS code '{naics}' is not a valid 2-6 digit code.",
                severity="medium",
                recommendation="Verify NAICS code against census.gov lookup.",
            ))

        elapsed = (time.monotonic() - start) * 1000
        action = "block" if violations else "allow"

        logger.info(
            "loan_application_validated",
            action=action,
            violations=len(violations),
            latency_ms=f"{elapsed:.1f}",
        )

        return ValidationResult(action=action, violations=violations, latency_ms=elapsed)
