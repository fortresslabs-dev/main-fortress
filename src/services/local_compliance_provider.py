"""
Local Compliance Provider — Reads compliance rules from the local DB replica.

Drop-in replacement for FortressAPIClient used by validators.
Instead of HTTP calls to Fortress Cloud, queries the local PostgreSQL
that is kept in sync by the UpdateService.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import structlog
from sqlalchemy import select

logger = structlog.get_logger(__name__)


class LocalComplianceProvider:
    """
    Provides compliance rules from the local PostgreSQL replica.

    Same interface as FortressAPIClient.get_rules() / get_ofac_sdn_list()
    so validators can be swapped without code changes.
    """

    async def get_rules(
        self,
        geography: str = "usa",
        regulator: Optional[str] = None,
        category: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query local compliance_rules table."""
        from src.services.database import get_db
        from src.models.database import ComplianceRule

        filters = [
            ComplianceRule.is_active == True,
            ComplianceRule.geography == geography,
        ]
        if regulator:
            filters.append(ComplianceRule.regulator == regulator)
        if category:
            filters.append(ComplianceRule.category == category)
        if domain:
            filters.append(ComplianceRule.domain == domain)

        try:
            async with get_db() as session:
                result = await session.execute(
                    select(ComplianceRule).where(*filters)
                )
                rows = result.scalars().all()

            return [
                {
                    "rule_id": r.rule_id,
                    "regulator": r.regulator,
                    "category": r.category,
                    "geography": r.geography,
                    "domain": r.domain,
                    "rule_type": r.rule_type,
                    "rule_text": r.rule_text,
                    "threshold_amount": r.threshold_amount,
                    "threshold_currency": r.threshold_currency,
                    "field_name": r.field_name,
                    "effective_date": r.effective_date,
                    "source_url": r.source_url,
                    "test_template": r.test_template,
                    "is_active": r.is_active,
                }
                for r in rows
            ]
        except Exception as exc:
            logger.error("local_rules_query_failed", error=str(exc))
            return []

    async def get_ofac_sdn_list(self, limit: int = 10000) -> List[Dict[str, Any]]:
        """Query local ofac_sdn_entries table."""
        from src.services.database import get_db
        from src.models.database import OFACSDNEntry

        try:
            async with get_db() as session:
                result = await session.execute(
                    select(OFACSDNEntry).limit(limit)
                )
                rows = result.scalars().all()

            return [
                {
                    "sdn_number": r.sdn_number,
                    "entity_name": r.entity_name,
                    "entity_type": r.entity_type,
                    "programs": r.programs,
                    "country": r.country,
                    "remarks": r.remarks,
                }
                for r in rows
            ]
        except Exception as exc:
            logger.error("local_sdn_query_failed", error=str(exc))
            return []

    async def close(self) -> None:
        """No-op — local provider has no connection to close."""
        pass
