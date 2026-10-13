"""Thin eCFR fetch wrapper.

Handles four citation shapes that BSA/AML corpus actually uses:

    12 CFR 1010.410         section
    12 CFR 748 App A        appendix
    31 CFR Part 510         whole part
    31 CFR Chapter V        whole chapter

A regex that matched only ``\\d+\\.\\d+`` sections dropped every one of
the appendix/chapter/part citations silently, which is how ``12 CFR 748
App A`` and ``31 CFR Part 510`` were quietly excluded from earlier runs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional
from xml.etree import ElementTree as ET

BASE = "https://www.ecfr.gov/api/versioner/v1/full/{as_of}/title-{title}.xml"
TEXT_ELEMENTS = {"P", "FP", "FL"}

_SECTION = re.compile(r"^(\d{1,2})\s+CFR\s+(\d{1,4})\.(\d+[a-z]?)\s*$", re.I)
_APPENDIX = re.compile(
    r"^(\d{1,2})\s+CFR\s+(\d{1,4})\s+App(?:endix)?\.?\s+([A-Z0-9]+)\s*$", re.I)
_PART = re.compile(r"^(\d{1,2})\s+CFR\s+Part\s+(\d{1,4})\s*$", re.I)
_CHAPTER = re.compile(r"^(\d{1,2})\s+CFR\s+Chapter\s+([IVXLC]+)\s*$", re.I)


@dataclass(frozen=True)
class CitationSpec:
    """Parsed citation. ``kind`` selects which eCFR query params we use."""
    citation: str
    title: int
    kind: str                     # section | appendix | part | chapter
    part: Optional[str] = None
    section: Optional[str] = None
    appendix: Optional[str] = None
    chapter: Optional[str] = None


@dataclass
class Section:
    citation: str
    heading: str
    text: str
    source_url: str


def parse_citation(citation: str) -> CitationSpec:
    """Parse a citation string. Raises ValueError with the string echoed so
    a wrong citation is easy to spot in a batch of hundreds."""
    m = _SECTION.match(citation)
    if m:
        title, part, sec = int(m.group(1)), m.group(2), f"{m.group(2)}.{m.group(3)}"
        return CitationSpec(citation, title, "section", part=part, section=sec)
    m = _APPENDIX.match(citation)
    if m:
        return CitationSpec(citation, int(m.group(1)), "appendix",
                            part=m.group(2), appendix=m.group(3).upper())
    m = _PART.match(citation)
    if m:
        return CitationSpec(citation, int(m.group(1)), "part", part=m.group(2))
    m = _CHAPTER.match(citation)
    if m:
        return CitationSpec(citation, int(m.group(1)), "chapter",
                            chapter=m.group(2).upper())
    raise ValueError(f"unparseable citation: {citation!r}")


def build_url(spec: CitationSpec, as_of: Optional[date] = None) -> str:
    """eCFR's ``full`` endpoint accepts part/section/appendix/chapter as
    query params. Empty strings would return the whole title, so only the
    relevant fields are sent."""
    when = (as_of or date.today()).isoformat()
    params: list[str] = []
    if spec.part:
        params.append(f"part={spec.part}")
    if spec.section:
        params.append(f"section={spec.section}")
    if spec.appendix:
        params.append(f"appendix={spec.appendix}")
    if spec.chapter:
        params.append(f"chapter={spec.chapter}")
    url = BASE.format(as_of=when, title=spec.title)
    return f"{url}?{'&'.join(params)}" if params else url


def _text_from(node) -> str:
    parts = [re.sub(r"\s+", " ", "".join(el.itertext()).strip())
             for el in node.iter() if el.tag in TEXT_ELEMENTS]
    return "\n\n".join(p for p in parts if p)


def _heading_from(node) -> str:
    head = node.find("HEAD")
    return "".join(head.itertext()).strip() if head is not None else ""


def _find_target(root, spec: CitationSpec):
    """Locate the DIV node the citation refers to inside the returned XML.

    eCFR returns a document rooted at the title; the actual section /
    appendix / part / chapter sits inside DIV elements typed accordingly.
    """
    want_types = {
        "section": ("SECTION",),
        "appendix": ("APPENDIX",),
        "part": ("PART",),
        "chapter": ("CHAPTER",),
    }[spec.kind]
    target_n = None
    if spec.kind == "section":
        target_n = spec.section
    elif spec.kind == "appendix":
        target_n = spec.appendix
    elif spec.kind == "part":
        target_n = spec.part
    elif spec.kind == "chapter":
        target_n = spec.chapter

    for div in root.iter():
        if not div.tag.startswith("DIV"):
            continue
        if div.get("TYPE") not in want_types:
            continue
        if target_n is None:
            return div
        n_attr = (div.get("N") or "").replace("\u00a7", "").strip()
        if n_attr.upper() == target_n.upper():
            return div
    return None


def fetch_section(citation: str, as_of: Optional[date] = None,
                  session=None) -> Section:
    """Fetch and materialise a citation's text.

    Named ``fetch_section`` for backward compat with the earlier
    section-only version — it now handles all four kinds.
    """
    import requests
    spec = parse_citation(citation)
    url = build_url(spec, as_of=as_of)
    get = (session or requests).get
    r = get(url, timeout=90, headers={"Accept": "application/xml"})
    if r.status_code == 404:
        raise LookupError(f"{citation}: not found at eCFR — the citation is "
                          f"wrong, not the fetch")
    r.raise_for_status()

    root = ET.fromstring(r.text)
    target = _find_target(root, spec)
    if target is None:
        raise LookupError(f"{citation}: node not found in eCFR response")
    return Section(citation, _heading_from(target), _text_from(target), url)
