"""
ComplianceRuleResolver — 3-tier compliance rule lookup for the 6-agent pipeline.

Lookup order per rule:
  Tier 1  PostgreSQL   exact match on (domain, rule_type). threshold_amount is
                       authoritative — if present, the rule is flagged immediately
                       without further lookup.
  Tier 2  Redis        1-hour TTL cache of previously resolved rules.
                       pgvector results are written back here so repeat queries
                       skip the vector search.
  Tier 3  pgvector     Hybrid semantic + BM25 search on compliance_rules
                       (HNSW cosine on embedding vector(1024) + ts_rank on tsv).
                       Used when a rule exists but has no structured threshold,
                       or when Postgres returns nothing (new/emerging rule).
  Tier 4  Hardcoded    In-code defaults, used only as last resort.

"Directly flag" semantics
  A rule is *flagged* (source="postgres") when Postgres returns a row with a
  non-null threshold_amount.  Flagged rules are passed to ValidationAgent as
  authoritative — the validator checks the generated test code against the
  EXACT flagged threshold, not a hardcoded constant.

Feedback
  Every resolved rule is returned with its source tier. ExecutionAgent calls
  record_feedback() to persist which rules were used, their source, and whether
  the generated tests passed. This drives the flywheel:
    postgres miss + pgvector hit → mark that rule for Postgres import on next sync
    pgvector hit + test pass     → store generated test_code as test_template on rule
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Literal, Optional

import structlog
from sqlalchemy import select

logger = structlog.get_logger(__name__)

# Redis key prefix and TTL
_REDIS_PREFIX = "compliance:resolved:"
_REDIS_TTL = 3600  # 1 hour

# Hardcoded last-resort thresholds (Tier 4 only)
_FALLBACK_THRESHOLDS: Dict[str, Dict[str, Any]] = {
    "wire_transfer": {
        "ctr_threshold":          {"threshold": 10000, "rule_text": "CTR required for cash transactions >= $10,000 (31 C.F.R. § 1010.311)"},
        # Dual approval is an INTERNAL_CONTROL from the OCC Comptroller's
        # Handbook (safety-and-soundness), not a CFR mandate. Kept in the
        # fallback set so the validator still surfaces it as an advisory
        # check, but must never gate a wire.
        "dual_approval_threshold": {"threshold": 10000, "rule_text": "Dual approval (OCC Comptroller's Handbook — INTERNAL CONTROL, not law)"},
        "travel_rule_threshold":  {"threshold": 3000,  "rule_text": "Travel Rule — transmittor + recipient info required for transmittals >= $3,000 (31 C.F.R. § 1010.410(f))"},
        "wire_recordkeeping":     {"threshold": 3000,  "rule_text": "Originator's-bank recordkeeping — 5-year retention for payment orders >= $3,000 (31 C.F.R. § 1020.410(a))"},
    },
    "sar": {
        "suspicious_threshold": {"threshold": 5000, "rule_text": "SAR required for suspicious activity >= $5,000 (FinCEN)"},
        "insider_threshold":    {"threshold": 0,    "rule_text": "SAR required for insider abuse at any amount (FinCEN)"},
    },
    "ctr": {
        "cash_threshold": {"threshold": 10000, "rule_text": "CTR required for cash transactions >= $10,000 (FinCEN)"},
    },
    "ofac": {
        "fuzzy_match_threshold":   {"threshold": 85, "rule_text": "OFAC fuzzy name match >= 85% triggers hold (OFAC/Treasury)"},
        "reporting_deadline_days": {"threshold": 10, "rule_text": "Blocked transactions reported to OFAC within 10 business days"},
    },
    "reg_e": {
        "reporting_window_days":        {"threshold": 60, "rule_text": "Consumer has 60 days to report unauthorized EFT (Reg E/CFPB)"},
        "provisional_credit_days":      {"threshold": 10, "rule_text": "Bank must issue provisional credit within 10 business days (Reg E)"},
        "investigation_days":           {"threshold": 45, "rule_text": "Dispute investigation must complete within 45 days (Reg E)"},
        "new_account_investigation_days":{"threshold": 90, "rule_text": "New-account dispute investigation window is 90 days (Reg E)"},
    },
    "kyc_cdd": {
        # 31 C.F.R. § 1010.230(d)(1) — 25% ownership prong (up to 4
        # individuals) + control prong (one individual). The "20% amended
        # April 11 2026" record was fabricated; no such FinCEN amendment
        # exists. Verified against eCFR 2026-07-09.
        "beneficial_ownership_pct": {"threshold": 25, "rule_text": "CDD beneficial ownership — each individual owning >= 25% of a legal entity customer (ownership prong, up to 4) plus one control-prong individual (31 C.F.R. § 1010.230(d)(1))"},
    },
}


@dataclass
class ResolvedRule:
    """A single compliance rule resolved through the 3-tier lookup."""
    domain: str
    rule_type: str
    threshold: Optional[float]
    rule_text: str
    source: Literal["postgres", "redis", "pgvector", "fallback"]
    rule_id: str = ""
    regulator: str = ""
    category: str = ""
    flagged: bool = False        # True when Postgres returned threshold_amount directly
    # §11.3 hard-gate fields. When populated, ValidationAgent and the
    # test factory route this rule through may_assert_as_law() rather
    # than the legacy severity-string heuristic.
    authority_type: Optional[str] = None
    citation_verified: bool = False
    citation_verified_source: Optional[str] = None
    cfr_citation: Optional[str] = None
    authority_source: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DomainRules:
    """All resolved rules for one banking domain (e.g. wire_transfer)."""
    domain: str
    rules: List[ResolvedRule] = field(default_factory=list)
    postgres_hits: int = 0
    redis_hits: int = 0
    pgvector_hits: int = 0
    fallback_hits: int = 0

    @property
    def flagged_rules(self) -> List[ResolvedRule]:
        return [r for r in self.rules if r.flagged]

    @property
    def primary_threshold(self) -> Optional[float]:
        """Return the threshold from the highest-priority flagged rule, if any."""
        for r in self.rules:
            if r.flagged and r.threshold is not None:
                return r.threshold
        for r in self.rules:
            if r.threshold is not None:
                return r.threshold
        return None


class ComplianceRuleResolver:
    """
    Resolves compliance rules through the 3-tier lookup chain.

    Usage (inside an async agent):
        resolver = ComplianceRuleResolver()
        domain_rules = await resolver.resolve_domain("wire_transfer")

        # Tier-1 flagged rules drive ValidationAgent checks
        for rule in domain_rules.flagged_rules:
            print(rule.threshold, rule.rule_text, rule.source)
    """

    # ── Public API ────────────────────────────────────────────────────

    async def resolve_domain(self, domain: str) -> DomainRules:
        """
        Resolve all compliance rules for a banking domain.
        Returns a DomainRules object containing rules from all tiers.
        """
        result = DomainRules(domain=domain)

        # Tier 1: PostgreSQL
        pg_rules = await self._from_postgres(domain)
        for rule in pg_rules:
            result.rules.append(rule)
            result.postgres_hits += 1

        # Collect rule_types already satisfied by Postgres
        covered = {r.rule_type for r in pg_rules if r.flagged}

        # Tier 2 + 3: Redis → pgvector for anything not flagged by Postgres
        fallback_types = set(_FALLBACK_THRESHOLDS.get(domain, {}).keys()) - covered
        for rule_type in fallback_types:
            rule = await self._from_redis(domain, rule_type)
            if rule:
                result.rules.append(rule)
                result.redis_hits += 1
                continue

            rule = await self._from_pgvector(domain, rule_type)
            if rule:
                result.rules.append(rule)
                result.pgvector_hits += 1
                await self._cache_in_redis(domain, rule_type, rule)
                continue

            # Tier 4: hardcoded fallback
            rule = self._from_fallback(domain, rule_type)
            if rule:
                result.rules.append(rule)
                result.fallback_hits += 1

        logger.info(
            "rules_resolved",
            domain=domain,
            total=len(result.rules),
            postgres=result.postgres_hits,
            redis=result.redis_hits,
            pgvector=result.pgvector_hits,
            fallback=result.fallback_hits,
            flagged=len(result.flagged_rules),
        )

        return result

    async def resolve_all_domains(
        self, banking_patterns: Dict[str, Any]
    ) -> Dict[str, DomainRules]:
        """Resolve rules for every detected banking pattern domain."""
        results: Dict[str, DomainRules] = {}
        for domain in banking_patterns.keys():
            results[domain] = await self.resolve_domain(domain)
        return results

    async def record_feedback(
        self,
        domain_rules_map: Dict[str, DomainRules],
        workflow_id: str,
        validation_passed: bool,
        quality_score: int,
        test_code: str = "",
    ) -> None:
        """
        Persist feedback for each resolved rule.

        - pgvector-sourced rules whose tests PASS are promoted:
          their test_code is stored as test_template on the ComplianceRule row
          so the next JIRA story benefits from a proven template.
        - pgvector-sourced rules that MISS structured Postgres lookup are
          flagged in logs for the next sync to import them as structured rows.
        """
        for domain, domain_rules in domain_rules_map.items():
            for rule in domain_rules.rules:
                try:
                    await self._write_feedback_record(
                        workflow_id=workflow_id,
                        rule=rule,
                        validation_passed=validation_passed,
                        quality_score=quality_score,
                    )

                    # Promote: if test passed and rule came from pgvector/fallback,
                    # store the generated test_code as test_template on the DB row
                    if validation_passed and quality_score >= 70 and test_code:
                        if rule.source in ("pgvector", "fallback") and rule.rule_id:
                            await self._promote_test_template(rule.rule_id, test_code)

                except Exception as exc:
                    logger.warning(
                        "feedback_record_failed",
                        rule_id=rule.rule_id,
                        error=str(exc),
                    )

    # ── Tier 1: PostgreSQL ────────────────────────────────────────────

    async def _from_postgres(self, domain: str) -> List[ResolvedRule]:
        """
        Query compliance_rules table for the domain.
        Rows with threshold_amount set are FLAGGED (authoritative).
        """
        from src.services.database import get_db
        from src.models.database import ComplianceRule as ComplianceRuleModel

        try:
            async with get_db() as session:
                stmt = (
                    select(ComplianceRuleModel)
                    .where(ComplianceRuleModel.domain == domain)
                    .where(ComplianceRuleModel.is_active.is_(True))
                )
                result = await session.execute(stmt)
                rows = result.scalars().all()

            rules = []
            for row in rows:
                flagged = row.threshold_amount is not None
                rules.append(ResolvedRule(
                    domain=domain,
                    rule_type=row.rule_type or domain,
                    threshold=row.threshold_amount,
                    rule_text=row.rule_text or "",
                    source="postgres",
                    rule_id=row.rule_id,
                    regulator=row.regulator,
                    category=row.category,
                    flagged=flagged,
                    authority_type=getattr(row, "authority_type", None),
                    citation_verified=bool(getattr(row, "citation_verified", False)),
                    citation_verified_source=getattr(row, "citation_verified_source", None),
                    cfr_citation=getattr(row, "cfr_citation", None),
                    authority_source=getattr(row, "authority_source", None),
                    raw={
                        "rule_id": row.rule_id,
                        "regulator": row.regulator,
                        "threshold_amount": row.threshold_amount,
                        "threshold_currency": row.threshold_currency,
                        "source_url": row.source_url,
                        "test_template": row.test_template,
                        "authority_type": getattr(row, "authority_type", None),
                        "citation_verified": bool(getattr(row, "citation_verified", False)),
                        "cfr_citation": getattr(row, "cfr_citation", None),
                    },
                ))

            logger.debug("postgres_rules_fetched", domain=domain, count=len(rules))
            return rules

        except Exception as exc:
            logger.warning("postgres_rules_failed", domain=domain, error=str(exc))
            return []

    # ── Tier 2: Redis ─────────────────────────────────────────────────

    async def _from_redis(
        self, domain: str, rule_type: str
    ) -> Optional[ResolvedRule]:
        """Return a previously cached ResolvedRule, or None on miss."""
        try:
            from src.services.redis_service import get_redis
            redis = await get_redis()
            if not redis:
                return None

            key = f"{_REDIS_PREFIX}{domain}:{rule_type}"
            data = await redis.get(key)
            if not data:
                return None

            obj = json.loads(data)
            return ResolvedRule(**{k: v for k, v in obj.items() if k != "raw"}, raw=obj.get("raw", {}))

        except Exception as exc:
            logger.debug("redis_rule_miss", domain=domain, rule_type=rule_type, error=str(exc))
            return None

    async def _cache_in_redis(
        self, domain: str, rule_type: str, rule: ResolvedRule
    ) -> None:
        """Write a resolved rule to Redis with TTL."""
        try:
            from src.services.redis_service import get_redis
            redis = await get_redis()
            if not redis:
                return

            key = f"{_REDIS_PREFIX}{domain}:{rule_type}"
            await redis.set(key, json.dumps(rule.to_dict()), ex=_REDIS_TTL)
            logger.debug("rule_cached_in_redis", domain=domain, rule_type=rule_type)

        except Exception as exc:
            logger.debug("redis_cache_write_failed", error=str(exc))

    # ── Tier 3: pgvector hybrid search ────────────────────────────────

    async def _from_pgvector(
        self, domain: str, rule_type: str
    ) -> Optional[ResolvedRule]:
        """
        Hybrid semantic + BM25 search on compliance_rules.

        Combines pgvector HNSW cosine similarity on the 1024-dim
        embedding column with ts_rank on the generated tsv tsvector
        column. Falls back to pure ts_rank if Bedrock embed fails
        (e.g. local dev without AWS creds).

        Returns the first hit with an extractable dollar threshold.
        """
        try:
            from src.services.database import get_db
            from sqlalchemy import text

            query = f"{domain} {rule_type}".replace("_", " ")
            query_emb = await self._embed_query(query)
            # If Bedrock embed failed we still serve a result via pure ts_rank,
            # but mark the source so callers + dashboard can show a degraded
            # badge instead of silently believing the hybrid score.
            degraded = query_emb is None
            source_tag = "pgvector_degraded" if degraded else "pgvector"
            if degraded:
                logger.warning(
                    "pgvector_query_degraded_no_embedding",
                    domain=domain, rule_type=rule_type,
                    reason="bedrock_embed_failed_falling_back_to_bm25",
                )
                # H7: emit a CW Embedded Metric Format record so the
                # existing log alarm in CW Alarms ("BM25FallbackHits >= 1
                # in 5min") fires without a separate metric pipeline.
                logger.info(
                    "metric_bm25_fallback",
                    _aws={
                        "Timestamp": int(__import__("time").time() * 1000),
                        "CloudWatchMetrics": [{
                            "Namespace": "Fortress/P2/Resolver",
                            "Dimensions": [["Domain"]],
                            "Metrics": [{"Name": "BM25FallbackHits", "Unit": "Count"}],
                        }],
                    },
                    Domain=domain,
                    BM25FallbackHits=1,
                )

            async with get_db() as session:
                if query_emb is not None:
                    stmt = text("""
                        SELECT rule_id, rule_text, regulator, category,
                               cfr_citation, threshold_amount,
                                 0.7 * (1 - (embedding <=> CAST(:vec AS vector)))
                               + 0.3 * COALESCE(
                                     ts_rank(tsv, plainto_tsquery('english', :q)),
                                     0
                                 ) AS score
                        FROM compliance_rules
                        WHERE is_active = TRUE
                          AND embedding IS NOT NULL
                        ORDER BY score DESC
                        LIMIT 3
                    """)
                    rows = (await session.execute(
                        stmt, {"vec": str(query_emb), "q": query}
                    )).fetchall()
                else:
                    stmt = text("""
                        SELECT rule_id, rule_text, regulator, category,
                               cfr_citation, threshold_amount,
                               ts_rank(tsv, plainto_tsquery('english', :q)) AS score
                        FROM compliance_rules
                        WHERE is_active = TRUE
                          AND tsv @@ plainto_tsquery('english', :q)
                        ORDER BY score DESC
                        LIMIT 3
                    """)
                    rows = (await session.execute(stmt, {"q": query})).fetchall()

            for row in rows:
                threshold = row.threshold_amount or _extract_first_threshold(
                    row.rule_text or ""
                )
                if threshold is not None:
                    return ResolvedRule(
                        domain=domain,
                        rule_type=rule_type,
                        threshold=float(threshold),
                        rule_text=row.rule_text or "",
                        source=source_tag,
                        rule_id=row.rule_id or "",
                        regulator=row.regulator or "",
                        category=row.category or "",
                        flagged=False,
                        raw={
                            "rule_id": row.rule_id,
                            "cfr_citation": row.cfr_citation,
                            "score": float(row.score) if row.score is not None else None,
                            "degraded": degraded,
                        },
                    )

            return None

        except Exception as exc:
            logger.warning(
                "pgvector_rule_failed",
                domain=domain,
                rule_type=rule_type,
                error=str(exc),
            )
            return None

    async def _embed_query(self, query_text: str) -> Optional[List[float]]:
        """Embed a query with Bedrock Titan v2 (1024-dim). None on failure."""
        try:
            import os
            import json as _json
            import boto3

            def _invoke() -> List[float]:
                client = boto3.client(
                    "bedrock-runtime",
                    region_name=os.getenv("AWS_REGION", "us-east-1"),
                )
                resp = client.invoke_model(
                    modelId="amazon.titan-embed-text-v2:0",
                    body=_json.dumps({
                        "inputText": query_text[:8000],
                        "dimensions": 1024,
                        "normalize": True,
                    }),
                )
                return _json.loads(resp["body"].read())["embedding"]

            return await asyncio.to_thread(_invoke)

        except Exception as exc:
            # Promoted from debug — silent embed failures used to mask AWS
            # cred / network issues for hours. Caller flips source to
            # pgvector_degraded so the degradation is observable.
            logger.warning("embed_query_failed",
                           error=str(exc), error_type=type(exc).__name__)
            return None

    # ── Tier 4: Hardcoded fallback ────────────────────────────────────

    def _from_fallback(self, domain: str, rule_type: str) -> Optional[ResolvedRule]:
        """Last resort: return hardcoded threshold for known rule types."""
        entry = _FALLBACK_THRESHOLDS.get(domain, {}).get(rule_type)
        if not entry:
            return None
        return ResolvedRule(
            domain=domain,
            rule_type=rule_type,
            threshold=float(entry["threshold"]),
            rule_text=entry["rule_text"],
            source="fallback",
            rule_id=f"fallback:{domain}:{rule_type}",
            regulator="",
            category=domain,
            flagged=False,
            raw=entry,
        )

    # ── Feedback persistence ──────────────────────────────────────────

    async def _write_feedback_record(
        self,
        workflow_id: str,
        rule: ResolvedRule,
        validation_passed: bool,
        quality_score: int,
    ) -> None:
        """Append rule feedback to the workflow_executions artifacts JSON."""
        try:
            from src.services.database import get_db
            from src.models.database import WorkflowExecution

            async with get_db() as session:
                wf = await session.get(WorkflowExecution, workflow_id)
                if not wf:
                    return

                artifacts = dict(wf.artifacts or {})
                feedback_list = artifacts.setdefault("rule_feedback", [])
                feedback_list.append({
                    "rule_id": rule.rule_id,
                    "domain": rule.domain,
                    "rule_type": rule.rule_type,
                    "source": rule.source,
                    "flagged": rule.flagged,
                    "threshold": rule.threshold,
                    "validation_passed": validation_passed,
                    "quality_score": quality_score,
                })
                wf.artifacts = artifacts
                await session.commit()

        except Exception as exc:
            logger.warning("feedback_write_failed", workflow_id=workflow_id, error=str(exc))

    async def _promote_test_template(self, rule_id: str, test_code: str) -> None:
        """
        Store the generated test_code as test_template on the ComplianceRule row.
        Called when a pgvector/fallback-sourced rule passes validation — the proven
        template improves future generation for the same rule.
        """
        try:
            from src.services.database import get_db
            from src.models.database import ComplianceRule as ComplianceRuleModel

            async with get_db() as session:
                stmt = select(ComplianceRuleModel).where(
                    ComplianceRuleModel.rule_id == rule_id
                )
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
                if row and not row.test_template:
                    row.test_template = test_code[:4000]   # cap at 4 KB
                    await session.commit()
                    logger.info("test_template_promoted", rule_id=rule_id)

        except Exception as exc:
            logger.warning("promote_template_failed", rule_id=rule_id, error=str(exc))


# ── Helpers ────────────────────────────────────────────────────────────

_DOLLAR_PATTERN = re.compile(
    r'(?:in excess of|over|above|exceeding|at least|more than|greater than|'
    r'equal to or greater|>=|>)\s+\$?([\d,]+(?:\.\d{1,2})?)',
    re.IGNORECASE,
)


def _extract_first_threshold(text: str) -> Optional[float]:
    """Extract the first dollar threshold from rule text."""
    if not text:
        return None
    for match in _DOLLAR_PATTERN.finditer(text):
        try:
            value = float(match.group(1).replace(",", ""))
            if 1 <= value <= 10_000_000:
                return value
        except ValueError:
            pass
    return None
