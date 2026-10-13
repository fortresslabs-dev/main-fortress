"""Find every section in a part, from eCFR's own table of contents.

A hand-written citation list cannot tell you what it omits. eCFR's
structure API can: walk it monthly, diff against the corpus, and a
section present upstream but absent locally is a coverage gap nobody
had to notice.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Iterator, Optional

STRUCTURE_URL = ("https://www.ecfr.gov/api/versioner/v1/structure/"
                 "{as_of}/title-{title}.json")


@dataclass(frozen=True)
class Discovered:
    title: int
    part: str
    section: str            # "1020.210"
    heading: str
    citation: str           # "31 CFR 1020.210"
    reserved: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _walk(node: dict) -> Iterator[dict]:
    """Depth-first over the structure tree."""
    yield node
    for child in node.get("children") or []:
        yield from _walk(child)


_SECTION_ID = re.compile(r"^(\d{1,4})\.(\d+)$")


def parse_structure(tree: dict, title: int,
                    parts: Optional[set[str]] = None) -> list[Discovered]:
    """Pull every SECTION node out of a title's structure tree.

    ``parts`` filters to the parts you care about; omit it and you get the
    whole title, which for title 31 is thousands of nodes.
    """
    out: list[Discovered] = []
    for node in _walk(tree):
        if node.get("type") != "section":
            continue
        ident = (node.get("identifier") or "").strip()
        m = _SECTION_ID.match(ident)
        if not m:
            continue
        part = m.group(1)
        if parts and part not in parts:
            continue
        heading = (node.get("label_description")
                   or node.get("label")
                   or "").strip()
        out.append(Discovered(
            title=title, part=part, section=ident, heading=heading,
            citation=f"{title} CFR {ident}",
            # eCFR marks these in the label; a reserved section has no text
            # and must not be queued for extraction.
            reserved=bool(re.search(r"\[reserved\]", heading, re.I)),
        ))
    return out


def fetch_structure(title: int, as_of: Optional[date] = None,
                    session=None) -> dict:
    import requests
    when = (as_of or date.today()).isoformat()
    url = STRUCTURE_URL.format(as_of=when, title=title)
    get = (session or requests).get
    r = get(url, timeout=90, headers={"Accept": "application/json"})
    r.raise_for_status()
    return r.json()


def discover(title: int, parts: set[str], as_of: Optional[date] = None,
             session=None) -> list[Discovered]:
    tree = fetch_structure(title, as_of=as_of, session=session)
    return parse_structure(tree, title, parts)


# ── coverage diff ─────────────────────────────────────────────────────
def coverage_gap(discovered: list[Discovered],
                 known_citations: set[str]) -> list[Discovered]:
    """Sections eCFR publishes that the corpus has never seen.

    This is the check a hand-maintained manifest cannot perform on
    itself. Run it monthly.
    """
    return [d for d in discovered
            if not d.reserved and d.citation not in known_citations]


def save(discovered: list[Discovered], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([d.to_dict() for d in discovered], indent=2))


def load(path: Path) -> list[Discovered]:
    return [Discovered(**d) for d in json.loads(path.read_text())]


# The parts that carry BSA/AML duties for community banks and credit unions.
BSA_PARTS = {
    31: {"1010", "1020"},          # FinCEN: general provisions, banks
    12: {"748", "760", "326",      # NCUA security/BSA, NCUA flood, FDIC BSA
         "21", "208", "353"},      # OCC BSA, FRB membership/BSA, FDIC SAR
}
