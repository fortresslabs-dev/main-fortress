"""Tests for the OFAC → eCFR Chapter V wiring in ``ofac_sdn.py``.

Scope is narrow: the connector's own extraction path (SDN XML download,
KPI/scenario builders) is not exercised here. What matters is that

  * ``_apply_ecfr`` stamps LAW rows with the discriminator + hash + URL
    when the fetch succeeds, and
  * ``_apply_ecfr`` leaves rows in the advisory-safe state (
    ``citation_verified=False``, explanatory ``authority_source``) when
    the fetch failed and ``fetched`` is ``None``.

The wider ecfr_client is exercised by ``tests/unit/services/
test_ecfr_client.py`` — this suite is deliberately additive.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.connectors.regulatory.usa.ofac_sdn import OfacSdnConnector
from src.services.ecfr_client import ECFRSection


def _fake_fetched() -> ECFRSection:
    return ECFRSection(
        title=31,
        section="V",
        heading="CHAPTER V — OFAC",
        text="Chapter V body …",
        xml_raw="<xml/>",
        effective_date=None,
        source_url="https://ecfr.example/v1/full/2026-07-13/title-31.xml?chapter=V",
        content_hash="a" * 64,
        kind="chapter",
        fetched_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )


class TestApplyEcfrSuccess:
    def test_stamps_law_row_with_discriminator(self) -> None:
        row = {
            "requirement_id": "OFAC-SCREEN-001",
            "authority_type": "LAW",
            "cfr_citation": "31 C.F.R. Chapter V",
        }
        OfacSdnConnector._apply_ecfr(row, _fake_fetched())

        assert row["citation_verified"] is True
        assert row["citation_verified_source"] == "ecfr_chapter"
        assert row["source_content_hash"] == "a" * 64
        assert row["source_url"].endswith("chapter=V")
        assert row["scraped_at"].startswith("2026-07-13")
        assert row["citation_verified_at"].startswith("2026-07-13")


class TestApplyEcfrFailure:
    def test_fetch_failure_leaves_row_advisory_safe(self) -> None:
        row = {
            "requirement_id": "OFAC-SCREEN-001",
            "authority_type": "LAW",
            "cfr_citation": "31 C.F.R. Chapter V",
        }
        OfacSdnConnector._apply_ecfr(row, None)

        assert row["citation_verified"] is False
        # Explanation must land in authority_source so the operator can
        # see *why* the row degraded — silent False is what §11.3 flags.
        assert "eCFR fetch failed" in row["authority_source"]
        # Absence of stamp — the class-boundary check
        # ck_cr_verified_source_present would otherwise error at insert.
        assert "citation_verified_source" not in row
        assert "source_content_hash" not in row

    def test_fetch_failure_does_not_overwrite_existing_authority_source(
        self,
    ) -> None:
        row = {
            "authority_type": "LAW",
            "cfr_citation": "31 C.F.R. Chapter V",
            "authority_source": "OFAC — pre-existing note",
        }
        OfacSdnConnector._apply_ecfr(row, None)

        # setdefault semantics — we don't clobber an operator-set
        # authority_source.
        assert row["authority_source"] == "OFAC — pre-existing note"
