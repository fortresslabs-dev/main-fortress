"""Unit tests for ``src.services.ecfr_client``.

Network is mocked. These tests verify:
  * Section-reference parsing tolerates paragraph suffixes.
  * ``parse_citation`` recognises appendix ("364 App B") and chapter
    ("Chapter V") forms deterministically — no LLM in the loop.
  * XML extraction picks the correct DIV by TYPE + N attribute for all
    three node kinds.
  * URL construction uses ``?section=``, ``?appendix=``, or ``?chapter=``
    per kind.
  * Retry logic recovers from 429 / 503 and gives up cleanly on 4xx.
  * The returned ECFRSection carries hash + citation + heading + text +
    verifier_source discriminator.

Integration against the live eCFR service belongs in a separate
``tests/integration`` file so this suite stays hermetic.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.services import ecfr_client
from src.services.ecfr_client import (
    ECFRError,
    _split_section,
    fetch_citation,
    fetch_section,
    parse_citation,
)


_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ECFR>
  <DIV1 TYPE="TITLE" N="31">
    <DIV5 TYPE="CHAPTER" N="X">
      <DIV8 TYPE="SECTION" N="&#167; 1020.210">
        <HEAD>&#167; 1020.210 Anti-money laundering program requirements.</HEAD>
        <P>A bank shall develop and provide for the continued administration
           of a program reasonably designed to assure and monitor compliance.</P>
        <P>The program shall, at a minimum, provide for:</P>
        <FP>(a) A system of internal controls.</FP>
        <AMDDATE>05/11/2018</AMDDATE>
      </DIV8>
      <DIV8 TYPE="SECTION" N="&#167; 1020.220">
        <HEAD>&#167; 1020.220 Customer identification.</HEAD>
        <P>Different section — should not be picked.</P>
      </DIV8>
    </DIV5>
  </DIV1>
</ECFR>"""


# Appendix XML — eCFR labels appendix DIVs with human labels in N,
# e.g. "Appendix B to Part 364", not the bare letter.
_APPENDIX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ECFR>
  <DIV1 TYPE="TITLE" N="12">
    <DIV5 TYPE="PART" N="364">
      <DIV9 TYPE="APPENDIX" N="Appendix B to Part 364">
        <HEAD>Appendix B to Part 364 — Interagency Guidelines Establishing Information Security Standards</HEAD>
        <P>Each bank shall implement a comprehensive written information security program.</P>
        <P>The program shall include administrative, technical, and physical safeguards.</P>
      </DIV9>
      <DIV9 TYPE="APPENDIX" N="Appendix A to Part 364">
        <HEAD>Appendix A — Interagency Guidelines Establishing Standards for Safety and Soundness</HEAD>
        <P>Wrong appendix — should not be picked.</P>
      </DIV9>
    </DIV5>
  </DIV1>
</ECFR>"""


# Chapter XML — OFAC lives at 31 CFR Chapter V. eCFR's N attribute is
# usually the bare roman numeral, but sometimes carries a label.
_CHAPTER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ECFR>
  <DIV1 TYPE="TITLE" N="31">
    <DIV3 TYPE="CHAPTER" N="V">
      <HEAD>CHAPTER V — Office of Foreign Assets Control, Department of the Treasury</HEAD>
      <P>Chapter V contains OFAC's sanctions regulations.</P>
    </DIV3>
    <DIV3 TYPE="CHAPTER" N="X">
      <HEAD>CHAPTER X — Financial Crimes Enforcement Network</HEAD>
      <P>Wrong chapter — should not be picked.</P>
    </DIV3>
  </DIV1>
</ECFR>"""


def _mock_response(body: str = _SAMPLE_XML, status: int = 200) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.text = body
    return resp


class TestSplitSection:
    def test_bare_section(self) -> None:
        assert _split_section("1020.210") == ("1020", "1020.210", "")

    def test_with_paragraph(self) -> None:
        assert _split_section("1020.220(a)(2)") == ("1020", "1020.220", "(a)(2)")

    def test_whitespace_tolerated(self) -> None:
        assert _split_section("  1010.230  ") == ("1010", "1010.230", "")

    def test_bad_input_raises(self) -> None:
        with pytest.raises(ValueError):
            _split_section("not a section")

    def test_appendix_rejected_by_section_shim(self) -> None:
        # Legacy shim keeps section semantics — appendix refs must not
        # sneak through to section-only callers.
        with pytest.raises(ValueError):
            _split_section("364 App B")


class TestParseCitation:
    """Deterministic parser covers all three kinds — no LLM path."""

    @pytest.mark.parametrize(
        "ref, expected_section, expected_part",
        [
            ("1020.210", "1020.210", "1020"),
            ("1020.220(a)(2)", "1020.220", "1020"),
            ("  1010.230  ", "1010.230", "1010"),
        ],
    )
    def test_section_forms(
        self, ref: str, expected_section: str, expected_part: str
    ) -> None:
        parsed = parse_citation(ref)
        assert parsed.kind == "section"
        assert parsed.section == expected_section
        assert parsed.part == expected_part

    @pytest.mark.parametrize(
        "ref, expected_part, expected_appendix",
        [
            ("364 App B", "364", "B"),
            ("30 App A", "30", "A"),
            ("208 App D-2", "208", "D-2"),
            ("208 App D 2", "208", "D-2"),          # space form normalises to dash
            ("30 Appendix A", "30", "A"),
            ("30 Appx. A", "30", "A"),
            ("30 App. A", "30", "A"),
            ("30 app b", "30", "B"),                # case-insensitive
        ],
    )
    def test_appendix_forms(
        self, ref: str, expected_part: str, expected_appendix: str
    ) -> None:
        parsed = parse_citation(ref)
        assert parsed.kind == "appendix"
        assert parsed.part == expected_part
        assert parsed.appendix == expected_appendix

    @pytest.mark.parametrize(
        "ref, expected_chapter",
        [
            ("Chapter V", "V"),
            ("Ch V", "V"),
            ("Ch. V", "V"),
            ("chapter x", "X"),
            ("Chapter 5", "5"),
        ],
    )
    def test_chapter_forms(self, ref: str, expected_chapter: str) -> None:
        parsed = parse_citation(ref)
        assert parsed.kind == "chapter"
        assert parsed.chapter == expected_chapter

    def test_unrecognised_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="unrecognized"):
            parse_citation("Public Law 91-508")


class TestFetchSection:
    def test_returns_section_with_text_hash_and_citation(self) -> None:
        with patch.object(ecfr_client, "requests") as mock_requests:
            session = MagicMock()
            session.get.return_value = _mock_response()
            mock_requests.Session.return_value = session
            mock_requests.RequestException = requests.RequestException

            result = fetch_section(31, "1020.210", as_of=date(2026, 7, 1))

        assert result.title == 31
        assert result.section == "1020.210"
        assert result.kind == "section"
        assert result.verifier_source == "ecfr_section"
        assert result.citation == "31 C.F.R. § 1020.210"
        assert "Anti-money laundering" in (result.heading or "")
        assert "internal controls" in result.text
        # Wrong section text must not leak in.
        assert "Different section" not in result.text
        assert len(result.content_hash) == 64
        assert result.source_url.startswith(
            "https://www.ecfr.gov/api/versioner/v1/full/2026-07-01/title-31.xml"
        )
        assert "part=1020" in result.source_url
        assert "section=1020.210" in result.source_url
        assert result.effective_date == date(2018, 5, 11)

    def test_retries_on_503_then_succeeds(self) -> None:
        with patch.object(ecfr_client, "requests") as mock_requests:
            session = MagicMock()
            session.get.side_effect = [
                _mock_response(status=503),
                _mock_response(),
            ]
            mock_requests.Session.return_value = session
            mock_requests.RequestException = requests.RequestException

            result = fetch_section(31, "1020.210")

        assert session.get.call_count == 2
        assert result.section == "1020.210"

    def test_4xx_raises_immediately(self) -> None:
        with patch.object(ecfr_client, "requests") as mock_requests:
            session = MagicMock()
            session.get.return_value = _mock_response(status=404)
            mock_requests.Session.return_value = session
            mock_requests.RequestException = requests.RequestException

            with pytest.raises(ECFRError):
                fetch_section(31, "1020.210")
            assert session.get.call_count == 1

    def test_missing_section_raises(self) -> None:
        with patch.object(ecfr_client, "requests") as mock_requests:
            session = MagicMock()
            session.get.return_value = _mock_response()
            mock_requests.Session.return_value = session
            mock_requests.RequestException = requests.RequestException

            with pytest.raises(ECFRError, match="not present"):
                fetch_section(31, "9999.999")

    def test_malformed_xml_raises(self) -> None:
        with patch.object(ecfr_client, "requests") as mock_requests:
            session = MagicMock()
            session.get.return_value = _mock_response(body="<not-xml")
            mock_requests.Session.return_value = session
            mock_requests.RequestException = requests.RequestException

            with pytest.raises(ECFRError, match="malformed"):
                fetch_section(31, "1020.210")


class TestFetchCitationAppendix:
    """Bug 1 — 12 CFR 364 App B / 30 App A / 208 App D-2 must verify."""

    def test_appendix_verifies_and_stamps_source(self) -> None:
        with patch.object(ecfr_client, "requests") as mock_requests:
            session = MagicMock()
            session.get.return_value = _mock_response(body=_APPENDIX_XML)
            mock_requests.Session.return_value = session
            mock_requests.RequestException = requests.RequestException

            result = fetch_citation(12, "364 App B", as_of=date(2026, 7, 1))

        assert result.kind == "appendix"
        assert result.section == "Part 364 App B"
        assert result.citation == "12 C.F.R. Part 364 App B"
        assert result.verifier_source == "ecfr_appendix"
        assert "comprehensive written information security" in result.text
        assert "Wrong appendix" not in result.text          # correct DIV picked
        assert "part=364" in result.source_url
        assert "appendix=B" in result.source_url

    def test_appendix_missing_raises(self) -> None:
        with patch.object(ecfr_client, "requests") as mock_requests:
            session = MagicMock()
            session.get.return_value = _mock_response(body=_APPENDIX_XML)
            mock_requests.Session.return_value = session
            mock_requests.RequestException = requests.RequestException

            with pytest.raises(ECFRError, match="not present"):
                fetch_citation(12, "364 App Z")


class TestFetchCitationChapter:
    """Bug 2 — 31 CFR Chapter V (nine OFAC rules) must verify."""

    def test_chapter_verifies_and_stamps_source(self) -> None:
        with patch.object(ecfr_client, "requests") as mock_requests:
            session = MagicMock()
            session.get.return_value = _mock_response(body=_CHAPTER_XML)
            mock_requests.Session.return_value = session
            mock_requests.RequestException = requests.RequestException

            result = fetch_citation(31, "Chapter V", as_of=date(2026, 7, 1))

        assert result.kind == "chapter"
        assert result.section == "V"
        assert result.citation == "31 C.F.R. Chapter V"
        assert result.verifier_source == "ecfr_chapter"
        assert "Office of Foreign Assets Control" in (result.heading or "")
        assert "sanctions regulations" in result.text
        assert "Financial Crimes Enforcement Network" not in (result.heading or "")
        # Chapter URL is title-scoped, not part-scoped.
        assert "chapter=V" in result.source_url
        assert "part=" not in result.source_url

    def test_chapter_short_form(self) -> None:
        # "Ch V" and "Ch. V" hit the same target as "Chapter V".
        with patch.object(ecfr_client, "requests") as mock_requests:
            session = MagicMock()
            session.get.return_value = _mock_response(body=_CHAPTER_XML)
            mock_requests.Session.return_value = session
            mock_requests.RequestException = requests.RequestException

            result = fetch_citation(31, "Ch. V")

        assert result.kind == "chapter"
        assert result.section == "V"


class TestVerifierSourceDiscriminator:
    """Bug 3 — a single boolean can't tell verifiers apart. Discriminator
    stamps the ECFRSection with which one flipped citation_verified."""

    def test_section_stamp(self) -> None:
        with patch.object(ecfr_client, "requests") as mock_requests:
            session = MagicMock()
            session.get.return_value = _mock_response()
            mock_requests.Session.return_value = session
            mock_requests.RequestException = requests.RequestException
            result = fetch_section(31, "1020.210")
        assert result.verifier_source == "ecfr_section"

    def test_appendix_stamp(self) -> None:
        with patch.object(ecfr_client, "requests") as mock_requests:
            session = MagicMock()
            session.get.return_value = _mock_response(body=_APPENDIX_XML)
            mock_requests.Session.return_value = session
            mock_requests.RequestException = requests.RequestException
            result = fetch_citation(12, "364 App B")
        assert result.verifier_source == "ecfr_appendix"

    def test_chapter_stamp(self) -> None:
        with patch.object(ecfr_client, "requests") as mock_requests:
            session = MagicMock()
            session.get.return_value = _mock_response(body=_CHAPTER_XML)
            mock_requests.Session.return_value = session
            mock_requests.RequestException = requests.RequestException
            result = fetch_citation(31, "Chapter V")
        assert result.verifier_source == "ecfr_chapter"
