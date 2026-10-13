"""Tests for migration 038 + ofac_sdn_loader.record_list_version().

Covers:
  * _parse_publish_metadata extracts version + publish date from real XML shapes
  * Last-Modified header fallback
  * Unknown-fallback synthesises a stable placeholder so UNIQUE never collides
  * record_list_version emits the right INSERT with SHA-256 of the bytes
  * idempotency via ON CONFLICT DO NOTHING
  * unknown list_name is rejected before touching the DB
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.ofac_sdn_loader import (
    _parse_publish_metadata,
    record_list_version,
)


class TestParsePublishMetadata:
    def test_publish_date_attribute_on_root(self):
        xml = b'<?xml version="1.0"?><sdnList Publish_Date="06/18/2026"><sdnEntry/></sdnList>'
        version_id, published_at = _parse_publish_metadata(xml, None)
        assert version_id == "06/18/2026"
        assert published_at == datetime(2026, 6, 18, tzinfo=timezone.utc)

    def test_version_element_preferred_over_publish_date(self):
        xml = (
            b'<?xml version="1.0"?>'
            b'<sdnList Publish_Date="06/18/2026">'
            b'<Publish_Information><Version>2026.06.18.1</Version></Publish_Information>'
            b'</sdnList>'
        )
        version_id, published_at = _parse_publish_metadata(xml, None)
        # Version wins over Publish_Date for the version_id, but Publish_Date
        # still drives published_at because it parses to a real timestamp.
        assert version_id == "2026.06.18.1"
        assert published_at == datetime(2026, 6, 18, tzinfo=timezone.utc)

    def test_last_modified_fallback(self):
        xml = b"<?xml version='1.0'?><sdnList/>"
        lm = "Wed, 18 Jun 2026 11:42:13 GMT"
        version_id, published_at = _parse_publish_metadata(xml, lm)
        assert version_id == lm
        assert published_at == datetime(2026, 6, 18, 11, 42, 13, tzinfo=timezone.utc)

    def test_unknown_fallback_is_deterministic(self):
        xml = b"<?xml version='1.0'?><sdnList/>"
        version_a, _ = _parse_publish_metadata(xml, None)
        version_b, _ = _parse_publish_metadata(xml, None)
        # Two calls with the same bytes synthesise the same placeholder so
        # UNIQUE (list_name, version_id) stays idempotent.
        assert version_a == version_b
        assert version_a.startswith("unknown-")
        assert len(version_a) == len("unknown-") + 12

    def test_unparseable_publish_date_still_returns_version(self):
        xml = b'<?xml version="1.0"?><sdnList Publish_Date="not-a-date"><sdnEntry/></sdnList>'
        version_id, published_at = _parse_publish_metadata(xml, None)
        assert version_id == "not-a-date"
        assert published_at is None

    def test_version_id_clipped_to_64_chars(self):
        long_lm = "x" * 200
        xml = b"<?xml version='1.0'?><sdnList/>"
        version_id, _ = _parse_publish_metadata(xml, long_lm)
        assert len(version_id) == 64


class TestRecordListVersion:
    @pytest.mark.asyncio
    async def test_inserts_with_sha256_and_returns_digest(self):
        session = MagicMock()
        session.execute = AsyncMock()

        xml = b"<?xml version='1.0'?><sdnList Publish_Date='06/18/2026'/>"
        expected_sha = hashlib.sha256(xml).hexdigest()

        sha = await record_list_version(
            session,
            list_name="sdn",
            xml_bytes=xml,
            source_url="https://www.treasury.gov/ofac/downloads/sdn_advanced.xml",
            last_modified_header=None,
            scrape_run_id=None,
        )

        assert sha == expected_sha
        assert session.execute.await_count == 1
        # Inspect the bound parameters of the INSERT call.
        bound = session.execute.await_args.args[1]
        assert bound["list_name"] == "sdn"
        assert bound["version_id"] == "06/18/2026"
        assert bound["sha256_of_xml"] == expected_sha
        assert bound["published_at"] == datetime(2026, 6, 18, tzinfo=timezone.utc)
        assert bound["scrape_run_id"] is None

    @pytest.mark.asyncio
    async def test_rejects_unknown_list_name_before_executing(self):
        session = MagicMock()
        session.execute = AsyncMock()
        with pytest.raises(ValueError, match="unknown OFAC list_name"):
            await record_list_version(
                session,
                list_name="bogus",
                xml_bytes=b"<sdnList/>",
                source_url="https://example.test",
            )
        session.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_idempotency_relies_on_on_conflict_clause(self):
        # We can't exercise the DB constraint without a real Postgres, but
        # we can assert the INSERT carries the ON CONFLICT DO NOTHING that
        # makes re-runs safe.
        session = MagicMock()
        session.execute = AsyncMock()
        await record_list_version(
            session,
            list_name="sdn",
            xml_bytes=b"<sdnList/>",
            source_url="https://example.test",
        )
        sql_text = session.execute.await_args.args[0]
        # SQLAlchemy text() renders to str via .text attribute.
        rendered = getattr(sql_text, "text", str(sql_text))
        assert "ON CONFLICT (list_name, version_id) DO NOTHING" in rendered

    @pytest.mark.asyncio
    async def test_source_url_clipped_to_2048_chars(self):
        session = MagicMock()
        session.execute = AsyncMock()
        long_url = "https://example.test/" + ("a" * 4000)
        await record_list_version(
            session,
            list_name="sdn",
            xml_bytes=b"<sdnList/>",
            source_url=long_url,
        )
        bound = session.execute.await_args.args[1]
        assert len(bound["source_url"]) == 2048
