"""
Utility helpers for the LangGraph Compliance Agent.

Document analysis, code cleaning, strategy selection, and storage helpers.
All functions are pure / side-effect-free where possible so they can be
tested independently.
"""
from __future__ import annotations

import io
import json
import os
import re
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

# ── Document format detection ───────────────────────────────────────

def detect_format(url: str, content_type: Optional[str] = None) -> str:
    """Return 'pdf', 'csv', 'xml', or 'html' based on URL / content-type."""
    url_lower = url.lower()
    if url_lower.endswith(".pdf"):
        return "pdf"
    if url_lower.endswith(".csv"):
        return "csv"
    if url_lower.endswith(".xml"):
        return "xml"
    if content_type:
        if "pdf" in content_type:
            return "pdf"
        if "csv" in content_type:
            return "csv"
        if "xml" in content_type:
            return "xml"
    return "html"


# ── Structure analysers (one per format) ────────────────────────────

def analyze_pdf_structure(content: bytes) -> Dict[str, Any]:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        total_pages = len(pdf.pages)
        sample = pdf.pages[: min(10, total_pages)]

        sections: List[str] = []
        tables_found = 0
        for page in sample:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                stripped = line.strip()
                if stripped and stripped == stripped.upper() and 5 < len(stripped) < 120:
                    sections.append(stripped)
            tables_found += len(page.extract_tables() or [])

    return {
        "total_pages": total_pages,
        "sections": sections[:30],
        "tables_found": tables_found,
        "has_toc": len(sections) > 5,
        "estimated_count": total_pages * 2,
    }


def analyze_csv_structure(content: bytes) -> Dict[str, Any]:
    import pandas as pd

    text = content.decode("utf-8", errors="replace")
    df = pd.read_csv(io.StringIO(text), nrows=5)
    total_rows = text.count("\n")
    return {
        "columns": list(df.columns),
        "sample_rows": df.head(3).to_dict(orient="records"),
        "estimated_count": total_rows,
        "tables_found": 1,
        "has_toc": False,
    }


def analyze_html_structure(content: bytes) -> Dict[str, Any]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "lxml")
    headings = [h.get_text(strip=True) for h in soup.find_all(re.compile(r"^h[1-3]$"))]
    tables = soup.find_all("table")
    links = soup.find_all("a", href=True)
    pdf_links = [a["href"] for a in links if a["href"].lower().endswith(".pdf")]
    return {
        "headings": headings[:30],
        "tables_found": len(tables),
        "pdf_links": pdf_links[:20],
        "has_toc": len(headings) > 5,
        "estimated_count": max(len(headings), len(tables) * 5, 10),
    }


def analyze_xml_structure(content: bytes) -> Dict[str, Any]:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(content)
    tags = set()
    count = 0
    for elem in root.iter():
        tags.add(elem.tag)
        count += 1
        if count > 5000:
            break
    return {
        "root_tag": root.tag,
        "unique_tags": list(tags)[:30],
        "estimated_count": count,
        "tables_found": 0,
        "has_toc": False,
    }


# ── Strategy selection ──────────────────────────────────────────────

def choose_strategy(doc_format: str, structure: Dict[str, Any]) -> str:
    if doc_format == "csv":
        return "direct_parse"
    if doc_format == "xml":
        return "direct_parse"
    if doc_format == "html":
        return "table_based" if structure.get("tables_found", 0) > 5 else "section_based"
    if doc_format == "pdf":
        if structure.get("has_toc"):
            return "toc_based"
        if structure.get("tables_found", 0) > 10:
            return "table_based"
        if structure.get("total_pages", 0) < 50:
            return "full_text_llm"
        return "chunked_llm"
    return "chunked_llm"


# ── Code helpers ────────────────────────────────────────────────────

def clean_generated_code(raw: str) -> str:
    """Strip markdown fences and leading/trailing whitespace."""
    code = re.sub(r"^```(?:python)?\n?", "", raw, flags=re.MULTILINE)
    code = re.sub(r"\n?```$", "", code, flags=re.MULTILINE)
    return code.strip()


def save_scraper_code(code: str, file_path: str) -> str:
    """Persist generated scraper code to disk and return the absolute path."""
    abs_path = os.path.abspath(file_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as fh:
        fh.write(code)
    logger.info("scraper_code_saved", path=abs_path, size=len(code))
    return abs_path


# ── Validation helpers ──────────────────────────────────────────────

def validate_extracted_data(
    data: List[Dict[str, Any]],
    expected_count: int,
    required_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run count / schema / quality / uniqueness checks and return a report."""
    if required_fields is None:
        required_fields = ["requirement_id", "requirement_text"]

    validations: Dict[str, bool] = {}
    errors: List[str] = []
    warnings: List[str] = []
    actual_count = len(data)

    # 1. Count check
    if expected_count > 0:
        variance = abs(actual_count - expected_count) / max(expected_count, 1)
        validations["count_check"] = variance < 0.3
        if not validations["count_check"]:
            warnings.append(f"Count mismatch: expected ~{expected_count}, got {actual_count}")
    else:
        validations["count_check"] = actual_count > 0

    # 2. Schema check
    if data:
        sample = data[0]
        missing = [f for f in required_fields if f not in sample]
        validations["schema_check"] = len(missing) == 0
        if missing:
            errors.append(f"Missing fields in first record: {missing}")
    else:
        validations["schema_check"] = False
        errors.append("No data extracted")

    # 3. Quality check (< 10 % short / empty texts)
    quality_issues = 0
    text_field = "requirement_text" if "requirement_text" in required_fields else None
    if text_field:
        for rec in data:
            val = rec.get(text_field, "")
            if not val or len(str(val)) < 10:
                quality_issues += 1
    quality_rate = 1.0 - (quality_issues / max(actual_count, 1))
    validations["quality_check"] = quality_rate > 0.9

    # 4. Uniqueness
    id_field = "requirement_id" if "requirement_id" in required_fields else None
    if id_field and data:
        ids = [r.get(id_field) for r in data]
        validations["uniqueness_check"] = len(set(ids)) == len(ids)
        if not validations["uniqueness_check"]:
            errors.append(f"Duplicate IDs found: {len(ids) - len(set(ids))}")
    else:
        validations["uniqueness_check"] = True

    quality_score = (sum(validations.values()) / max(len(validations), 1)) * 100

    return {
        "success": all(validations.values()) and len(errors) == 0,
        "validations": validations,
        "errors": errors,
        "warnings": warnings,
        "quality_score": quality_score,
        "actual_count": actual_count,
    }


# ── Prompt builders ─────────────────────────────────────────────────

def build_scraper_prompt(source_spec: Dict, doc_format: str, strategy: str, structure: Dict) -> str:
    fields_desc = ", ".join(source_spec.get("fields", ["requirement_id", "requirement_text"]))
    return f"""You are a senior Python developer building a compliance data scraper.

SOURCE:
- Name: {source_spec['name']}
- URL: {source_spec['url']}
- Format: {doc_format}
- Extraction Strategy: {strategy}

DOCUMENT STRUCTURE (sample):
{json.dumps(structure, indent=2, default=str)[:3000]}

TARGET OUTPUT:
The script MUST print a single JSON object to stdout with this schema:
{{
  "regulation_id": "<source_id>",
  "regulation_name": "<name>",
  "regulator": "<regulator>",
  "requirements": [
    {{
      "requirement_id": "<unique id>",
      "requirement_text": "<extracted text>",
      {', '.join(f'"{f}": "<value>"' for f in source_spec.get("fields", []) if f not in ("requirement_id", "requirement_text"))}
    }}
  ]
}}

Expected ~{source_spec.get('expected_count', 'unknown')} requirements.
Fields to extract: {fields_desc}

RULES:
1. Use requests to download. Do NOT use selenium.
2. Use pdfplumber for PDF, BeautifulSoup+lxml for HTML, pandas for CSV, xml.etree for XML.
3. Print ONLY the JSON object to stdout—no other output.
4. Include error handling; on failure print {{"error": "<message>"}}.
5. The script must be fully self-contained and runnable with `python <file>`.

OUTPUT: Complete, runnable Python code only. No markdown fences, no explanations."""


def build_fix_prompt(errors: List[str], scraper_code: str, source_spec: Dict, doc_format: str, strategy: str) -> str:
    return f"""The scraper you previously generated failed. Fix it.

ERRORS:
{json.dumps(errors, indent=2)}

ORIGINAL CODE:
{scraper_code}

SOURCE:
- Name: {source_spec['name']}
- URL: {source_spec['url']}
- Format: {doc_format}
- Strategy: {strategy}

COMMON FIXES:
- PDF parsing errors → try page-by-page extraction or different table settings
- Missing fields → broaden regex or CSS selectors
- Timeout/network → add retries with backoff
- Schema mismatch → ensure JSON output matches the required schema
- Empty results → check if site requires headers or JS rendering

OUTPUT: Complete, fixed Python code only. No markdown, no explanations."""
