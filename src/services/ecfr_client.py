"""eCFR (Electronic Code of Federal Regulations) client.

Fetches primary CFR text from the free, keyless eCFR JSON/XML API and
returns a normalized record that the regulatory connectors can splice
into their outputs — replacing the hardcoded ``_build_regulations()``
fallbacks that Item 6 of ``docs/correction_smb.md`` flags as the least
trustworthy content in the corpus.

The client is deliberately narrow. It knows how to:

* Fetch a single CFR node — section, appendix, or chapter — as of a date.
* Extract the heading and the inline text from the returned XML.
* Compute a sha256 content hash of the raw XML for provenance.
* Return the source URL, fetch timestamp, and best-effort amendment
  date, so the connector can populate ``source_url``,
  ``source_content_hash``, ``scraped_at``, ``citation_verified`` and
  ``citation_verified_source`` per the §11 schema in the correction doc.

Citation forms handled — all recognised by deterministic regex, no LLM:

    Section    "1020.210"            "1020.220(a)(2)"
    Appendix   "364 App B"           "30 App A"          "208 App D-2"
    Chapter    "Chapter V"           "Ch V"              "Ch. V"

Prior versions accepted only ``\\d+\\.\\d+`` section refs and rejected
appendix / chapter cites at the parser boundary — with the effect that
GLBA InfoSec rows (12 CFR 30 App B / 364 App B / 208 App D-2) and every
OFAC-SDN LAW row (31 CFR Chapter V) stayed ``citation_verified=False``
even when the underlying eCFR node existed. See verification report in
the ``feature/rule_correction`` branch history.

It does not:

* Cache. Connector runs are infrequent (nightly at most) and eCFR
  serves fast. A cache would only add a staleness class to debug.
* Batch. eCFR does not offer batch endpoints; issuing N requests in
  parallel is what a caller with a `requests.Session` already gets
  through connection reuse.
* Interpret paragraph structure. Sub-paragraphs ``(a)(2)(iii)`` are
  parsed on the caller side if needed — the client returns the full
  node text and lets the caller slice.

eCFR API docs: https://www.ecfr.gov/developers/documentation/api/v1
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Literal, Optional
from xml.etree import ElementTree as ET

import requests
import structlog

logger = structlog.get_logger(__name__)

_BASE = "https://www.ecfr.gov/api/versioner/v1"
_USER_AGENT = "Fortress-Compliance-eCFR/1.0 (+https://fortresslabs.dev)"
_TIMEOUT_SECONDS = 30
_MAX_RETRIES = 3
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class ECFRError(RuntimeError):
    """eCFR fetch or parse failed in a way the caller must handle."""


CitationKind = Literal["section", "appendix", "chapter"]


@dataclass(frozen=True)
class ParsedCitation:
    """Deterministic parse of a CFR reference string.

    ``kind`` drives URL construction and DIV walk in ``_extract_node``.
    Fields not relevant to the kind are empty strings, not None — keeps
    the dataclass shape flat for the debugger.
    """
    kind: CitationKind
    part: str = ""              # section / appendix
    section: str = ""           # e.g. "1020.210"
    paragraph: str = ""         # e.g. "(a)(2)"
    appendix: str = ""          # e.g. "A", "D-2"
    chapter: str = ""           # e.g. "V"


@dataclass(frozen=True)
class ECFRSection:
    """Normalized eCFR node fetched from the versioner API.

    The class name is historic (section-only); the ``kind`` field says
    which CFR node type this actually represents. ``section`` continues
    to carry the caller-visible identifier for backward compatibility
    with the existing ffiec_bsa call site — for appendix / chapter
    fetches it holds ``"Part 364 App B"`` / ``"V"`` respectively.
    """

    title: int
    section: str                       # identifier — see docstring
    heading: Optional[str]             # from <HEAD>
    text: str                          # concatenated paragraph text
    xml_raw: str                       # source XML for hash / audit
    effective_date: Optional[date]     # best-effort from <AMDDATE>, else None
    source_url: str
    content_hash: str                  # sha256(xml_raw)
    kind: CitationKind = "section"
    fetched_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def citation(self) -> str:
        if self.kind == "section":
            return f"{self.title} C.F.R. § {self.section}"
        if self.kind == "appendix":
            return f"{self.title} C.F.R. {self.section}"
        return f"{self.title} C.F.R. Chapter {self.section}"

    @property
    def verifier_source(self) -> str:
        """Discriminator stamp for ``compliance_rules.citation_verified_source``.

        Lets the DB record *which* verifier flipped ``citation_verified``
        — the reviewer's Bug-3 point that a single boolean can't tell an
        eCFR section verification from a chapter or state-code one.
        """
        return {
            "section": "ecfr_section",
            "appendix": "ecfr_appendix",
            "chapter": "ecfr_chapter",
        }[self.kind]


# ── Citation-reference parsing ────────────────────────────────────────
# All three forms are extracted with deterministic regex — no LLM. The
# section form still accepts paragraph tails ("(a)(2)") for downstream
# slicing; the fetch normalises to section granularity because eCFR
# queries at that level.
_SECTION_RE = re.compile(r"^\s*(\d+\.\d+)\s*((?:\([^\)]+\))*)\s*$")

# "30 App A" | "364 App B" | "208 App D-2" | "Appendix", "Appx.", "App." accepted.
# Body allows single letter, optionally followed by dash/space + digits.
_APPENDIX_RE = re.compile(
    r"^\s*(\d+)\s+(?:App(?:endix|x)?|Appx)\.?\s*"
    r"([A-Z](?:[-\s]?\d+)?)\s*$",
    re.IGNORECASE,
)

# "Chapter V" | "Ch V" | "Ch. V" — roman numeral or plain integer.
_CHAPTER_RE = re.compile(
    r"^\s*Ch(?:apter|\.)?\s+([IVXLCM]+|\d+)\s*$",
    re.IGNORECASE,
)


def parse_citation(ref: str) -> ParsedCitation:
    """Parse a CFR reference string into a ``ParsedCitation``.

    Order of tries: section (numeric part.section), appendix, chapter.
    Section and appendix are mutually exclusive by shape; chapter is
    the fallback for reference strings that carry no digits at the top.

    Raises ``ValueError`` if none of the three regex forms match — the
    caller must decide whether to skip the row or route to advisory.
    """
    if (m := _SECTION_RE.match(ref)):
        section = m.group(1)
        return ParsedCitation(
            kind="section",
            part=section.split(".", 1)[0],
            section=section,
            paragraph=m.group(2) or "",
        )
    if (m := _APPENDIX_RE.match(ref)):
        return ParsedCitation(
            kind="appendix",
            part=m.group(1),
            appendix=m.group(2).upper().replace(" ", "-"),
        )
    if (m := _CHAPTER_RE.match(ref)):
        return ParsedCitation(
            kind="chapter",
            chapter=m.group(1).upper(),
        )
    raise ValueError(f"unrecognized CFR reference: {ref!r}")


def _split_section(section_ref: str) -> tuple[str, str, str]:
    """Legacy shim — return (part, section, paragraph) for section refs.

    Kept because the unit tests bind directly to this name. New callers
    should use ``parse_citation`` and dispatch on ``kind``.
    """
    parsed = parse_citation(section_ref)
    if parsed.kind != "section":
        raise ValueError(f"unrecognized CFR section: {section_ref!r}")
    return parsed.part, parsed.section, parsed.paragraph


# ── HTTP ──────────────────────────────────────────────────────────────
def _session(session: Optional[requests.Session]) -> requests.Session:
    sess = session or requests.Session()
    sess.headers.setdefault("User-Agent", _USER_AGENT)
    sess.headers.setdefault("Accept", "application/xml, text/xml, */*")
    return sess


def _get_with_retry(sess: requests.Session, url: str) -> requests.Response:
    last_exc: Optional[Exception] = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = sess.get(url, timeout=_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == _MAX_RETRIES:
                raise ECFRError(f"eCFR unreachable after {attempt} tries: {exc}") from exc
            continue
        if resp.status_code == 200:
            return resp
        if resp.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES:
            logger.info(
                "ecfr_retryable_status",
                url=url,
                status=resp.status_code,
                attempt=attempt,
            )
            continue
        raise ECFRError(f"eCFR HTTP {resp.status_code} for {url}")
    # Unreachable — loop either returns or raises. Guard for type-checkers.
    raise ECFRError(f"eCFR fetch exhausted retries: {last_exc}")


# ── XML extraction ────────────────────────────────────────────────────
# eCFR wraps nodes as ``<DIVn TYPE="…" N="…">``. TYPE is one of
# SECTION | APPENDIX | CHAPTER (upper case). The N attribute holds the
# node identifier — always with the "§ " prefix for sections; for
# appendices/chapters the string form varies (e.g. "A", "Appendix A to
# Part 30", "V", "Chapter V"), so we match by containment of the
# identifier rather than exact equality.
_DIV_TYPE_BY_KIND: dict[CitationKind, str] = {
    "section": "SECTION",
    "appendix": "APPENDIX",
    "chapter": "CHAPTER",
}
_TEXT_ELEMENTS = {"P", "FP", "FL"}


def _n_matches(n_attr: str, identifier: str, kind: CitationKind) -> bool:
    """Return True iff a DIV's ``N`` attribute names our target node.

    Sections match by strict suffix (after stripping the ``§`` sigil).
    Appendix and chapter identifiers vary in eCFR (bare letter vs.
    "Appendix A to Part 30", roman numeral vs. "Chapter V"), so we
    accept either the bare identifier or a whole-word occurrence inside
    a longer label.
    """
    stripped = n_attr.replace("§", "").strip()
    if kind == "section":
        return stripped == identifier
    upper = stripped.upper()
    ident = identifier.upper()
    if upper == ident:
        return True
    # Match whole-word occurrence so "A" doesn't collide with "APPENDIX".
    return bool(re.search(rf"\b{re.escape(ident)}\b", upper))


def _extract_node(
    xml_raw: str,
    identifier: str,
    kind: CitationKind,
) -> tuple[Optional[str], str, Optional[date]]:
    try:
        root = ET.fromstring(xml_raw)
    except ET.ParseError as exc:
        raise ECFRError(f"eCFR returned malformed XML: {exc}") from exc

    want_type = _DIV_TYPE_BY_KIND[kind]
    target = None
    for div in root.iter():
        if not div.tag.startswith("DIV"):
            continue
        if div.get("TYPE") != want_type:
            continue
        if _n_matches(div.get("N", ""), identifier, kind):
            target = div
            break
    if target is None:
        raise ECFRError(
            f"{kind} {identifier!r} not present in returned XML"
        )

    heading_elem = target.find("HEAD")
    heading = (
        heading_elem.text.strip()
        if heading_elem is not None and heading_elem.text
        else None
    )

    parts: list[str] = []
    for elem in target.iter():
        if elem.tag in _TEXT_ELEMENTS:
            piece = "".join(elem.itertext()).strip()
            if piece:
                parts.append(piece)
    text = "\n\n".join(parts)

    effective: Optional[date] = None
    amd = target.find("AMDDATE")
    if amd is not None and amd.text:
        raw = amd.text.strip()
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                effective = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                continue
    return heading, text, effective


# Legacy alias — the test suite binds to _extract_section; keep it live.
def _extract_section(xml_raw: str, section_id: str) -> tuple[Optional[str], str, Optional[date]]:
    return _extract_node(xml_raw, section_id, "section")


# ── URL construction ──────────────────────────────────────────────────
def _build_url(title: int, on_date: str, parsed: ParsedCitation) -> str:
    """eCFR versioner URL for the parsed citation.

    URL parameter set follows the eCFR versioner scope selection —
    section/appendix are part-scoped, chapter is title-scoped.
    """
    base = f"{_BASE}/full/{on_date}/title-{title}.xml"
    if parsed.kind == "section":
        return f"{base}?part={parsed.part}&section={parsed.section}"
    if parsed.kind == "appendix":
        return f"{base}?part={parsed.part}&appendix={parsed.appendix}"
    return f"{base}?chapter={parsed.chapter}"


def _identifier_for(parsed: ParsedCitation) -> str:
    """Caller-visible identifier stored on the returned ECFRSection."""
    if parsed.kind == "section":
        return parsed.section
    if parsed.kind == "appendix":
        # Reconstruct the human-readable form so ECFRSection.citation
        # renders as "12 C.F.R. Part 364 App B" cleanly.
        return f"Part {parsed.part} App {parsed.appendix}"
    return parsed.chapter


# ── Public API ────────────────────────────────────────────────────────
def fetch_citation(
    title: int,
    ref: str,
    *,
    as_of: Optional[date] = None,
    session: Optional[requests.Session] = None,
) -> ECFRSection:
    """Fetch a CFR node (section / appendix / chapter) from eCFR.

    Args:
        title:   CFR title number (e.g. 12, 31).
        ref:     Reference string in any of the three deterministic
                 forms — see module docstring. Parsed with regex only.
        as_of:   Optional historical date. Defaults to today (UTC).
        session: Optional ``requests.Session`` for connection reuse.

    Raises:
        ``ValueError`` if ``ref`` matches none of the three regex forms.
        ``ECFRError`` on transport / parse / missing-node failure.
    """
    parsed = parse_citation(ref)
    on_date = (as_of or date.today()).isoformat()
    url = _build_url(title, on_date, parsed)

    sess = _session(session)
    resp = _get_with_retry(sess, url)

    xml_raw = resp.text
    content_hash = hashlib.sha256(xml_raw.encode("utf-8")).hexdigest()
    # Match by the raw parsed identifier (bare letter / bare roman
    # numeral / section number); _n_matches handles the label variants.
    xml_ident = (
        parsed.section if parsed.kind == "section"
        else parsed.appendix if parsed.kind == "appendix"
        else parsed.chapter
    )
    heading, text, effective = _extract_node(xml_raw, xml_ident, parsed.kind)

    identifier = _identifier_for(parsed)
    logger.info(
        "ecfr_node_fetched",
        title=title,
        kind=parsed.kind,
        identifier=identifier,
        as_of=on_date,
        content_hash=content_hash[:12],
        text_chars=len(text),
    )
    return ECFRSection(
        title=title,
        section=identifier,
        heading=heading,
        text=text,
        xml_raw=xml_raw,
        effective_date=effective,
        source_url=url,
        content_hash=content_hash,
        kind=parsed.kind,
    )


def fetch_section(
    title: int,
    section: str,
    *,
    as_of: Optional[date] = None,
    session: Optional[requests.Session] = None,
) -> ECFRSection:
    """Backward-compatible wrapper for section-only fetches.

    Existing callers (ffiec_bsa) pass raw section refs like
    ``"1020.210"``; keep the signature stable. New callers that need
    appendix or chapter support should call ``fetch_citation`` directly.
    """
    return fetch_citation(title, section, as_of=as_of, session=session)
