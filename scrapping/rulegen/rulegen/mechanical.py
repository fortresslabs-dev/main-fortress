"""The parts of my process that are mechanical.

Splitting at markers, pulling numbers out, checking a quote is really in
the text. No judgement in any of it, so no model call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MARKER = re.compile(r"\((?:[a-z]|\d{1,2}|[ivxlc]{1,5}|[A-Z])\)")


@dataclass
class Unit:
    marker: str
    text: str
    parent: str
    ordinal: int

    @property
    def path(self) -> str:
        return f"{self.parent} {self.marker}".strip()


def _depth(marker: str) -> int:
    """The CFR's own nesting: (a) -> (1) -> (i) -> (A)."""
    inner = marker.strip("()")
    if not inner:
        return 0
    if inner.isdigit():
        return 1
    if re.fullmatch(r"[ivxlc]+", inner):
        return 2
    if inner.isupper():
        return 3
    return 0


def split(section_text: str) -> list[Unit]:
    """Cut a section at its own paragraph markers, keeping the parent chain.

    The parent chain matters more than it looks. '(iv) Training for
    appropriate personnel' is only an obligation because of a sentence
    forty words earlier under (a). Send the unit without its chain and
    the model loses what makes it a duty.
    """
    pieces = [p.strip() for p in
              re.split(r"(?=" + MARKER.pattern + r"\s)", section_text)
              if p.strip()]
    units, chain = [], []
    for i, piece in enumerate(pieces):
        m = MARKER.match(piece)
        marker = m.group(0) if m else ""
        body = piece[len(marker):].strip() if marker else piece
        d = _depth(marker)
        chain = chain[:d]
        units.append(Unit(marker, body, " ".join(chain), i))
        chain.append(marker)
    return units


def stem(section_text: str, chars: int = 340) -> str:
    """The section opening, which usually carries the operative 'shall'."""
    return section_text[:chars]


# ── numbers ───────────────────────────────────────────────────────────
_PATTERNS = [
    (re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)"), "usd",
     lambda m: float(m.group(1).replace(",", ""))),
    (re.compile(r"\b(\d+)\s+business\s+days?\b", re.I), "business_days",
     lambda m: int(m.group(1))),
    (re.compile(r"\b(\d+)\s+(?:calendar\s+)?days?\b", re.I), "calendar_days",
     lambda m: int(m.group(1))),
    (re.compile(r"\b(\d+)\s+hours?\b", re.I), "hours",
     lambda m: int(m.group(1))),
    (re.compile(r"\b(twelve|six|three|two|\d+)\s+months?\b", re.I), "months",
     lambda m: _word(m.group(1))),
    (re.compile(r"\b(five|three|ten|two|\d+)\s+years?\b", re.I), "years",
     lambda m: _word(m.group(1))),
    (re.compile(r"\b([\d.]+)\s?(?:percent|%)"), "percent",
     lambda m: float(m.group(1)) / 100),
]
_WORDS = {"two": 2, "three": 3, "five": 5, "six": 6, "ten": 10, "twelve": 12}


def _word(raw: str):
    raw = raw.lower()
    return _WORDS.get(raw, int(raw) if raw.isdigit() else raw)


def find_numbers(text: str) -> list[dict]:
    """Every quantity, typed. Never sent to a model — there is a right
    answer and it is written in the text."""
    out, seen = [], set()
    for pattern, unit, cast in _PATTERNS:
        for m in pattern.finditer(text):
            key = (m.start(), unit)
            if key in seen:
                continue
            seen.add(key)
            out.append({"value": cast(m), "unit": unit, "quote": m.group(0)})
    return out


# ── span verification ─────────────────────────────────────────────────
def _tidy(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def span_present(span: str, section_text: str) -> bool:
    """Is this quote really in the text?

    Whitespace and case normalised because XML extraction inserts breaks
    a model will not reproduce. NOTHING ELSE IS RELAXED. Make this fuzzy
    and the pipeline loses its only hard guarantee.
    """
    return bool(span) and _tidy(span) in _tidy(section_text)
