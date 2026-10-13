"""
MVP1 Source Extraction Diagnostic
==================================
Tests all regulatory source URLs from config/connectors/usa.yaml:
  1. HEAD check — is the URL reachable? (HTTP status, content-type)
  2. Extraction test — how many chunks does DocumentProcessor produce?
  3. PDF analysis — which strategy was chosen? tables/images detected?

Usage:
    # Full run (HEAD + extraction, ~5-10 min for all PDFs)
    python -m scripts.test_source_extraction

    # HEAD-only (fast, ~30s)
    python -m scripts.test_source_extraction --head-only

    # Single connector
    python -m scripts.test_source_extraction --connector occ_bsa

    # Skip slow PDFs
    python -m scripts.test_source_extraction --skip-pdf
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
import yaml

# ── Source loading ────────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parents[1]
_USA_YAML = _ROOT / "config" / "connectors" / "usa.yaml"
_UA = "TextMind-Compliance-Diag/1.0"


def load_source_urls() -> Dict[str, Dict[str, str]]:
    with open(_USA_YAML) as f:
        data = yaml.safe_load(f)
    return data.get("source_urls", {})


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class HeadResult:
    connector: str
    key: str
    url: str
    status: int          # HTTP status code, -1 = connection error
    content_type: str
    size_hint_kb: int    # Content-Length if available, else -1
    redirect_url: str    # Final URL after redirects
    error: str = ""


@dataclass
class ExtractionResult:
    connector: str
    key: str
    url: str
    doc_type: str
    chunks: int
    avg_chars: float
    strategy: str        # PDF only: text_native / layout_aware / ocr / hybrid
    has_tables: bool
    has_images: bool
    duration_s: float
    warnings: List[str] = field(default_factory=list)
    error: str = ""


# ── HEAD check ────────────────────────────────────────────────────────────────

def head_check(connector: str, key: str, url: str, session: requests.Session) -> HeadResult:
    try:
        resp = session.head(url, timeout=15, allow_redirects=True)
        ct = resp.headers.get("Content-Type", "").split(";")[0].strip()
        cl = resp.headers.get("Content-Length", "")
        size_kb = int(cl) // 1024 if cl.isdigit() else -1
        return HeadResult(
            connector=connector,
            key=key,
            url=url,
            status=resp.status_code,
            content_type=ct,
            size_hint_kb=size_kb,
            redirect_url=resp.url if resp.url != url else "",
        )
    except requests.RequestException as exc:
        return HeadResult(
            connector=connector, key=key, url=url,
            status=-1, content_type="", size_hint_kb=-1,
            redirect_url="", error=str(exc)[:80],
        )


# ── Extraction test ───────────────────────────────────────────────────────────

def extract_url(connector: str, key: str, url: str) -> ExtractionResult:
    """Run DocumentProcessor.process_url() and collect metrics."""
    import logging
    logging.disable(logging.CRITICAL)  # suppress structlog noise during test

    try:
        from src.services.document_processor import DocumentProcessor
    except ImportError as exc:
        return ExtractionResult(
            connector=connector, key=key, url=url, doc_type="?",
            chunks=0, avg_chars=0, strategy="", has_tables=False,
            has_images=False, duration_s=0, error=f"import error: {exc}",
        )

    proc = DocumentProcessor(chunk_size=2000, chunk_overlap=200)
    t0 = time.monotonic()

    # For PDFs, monkey-patch PdfExtractionAgent to capture meta
    captured_meta = {}

    if url.lower().endswith(".pdf"):
        try:
            from src.services import pdf_extraction_agent as _pea_mod
            _orig_extract = _pea_mod.PdfExtractionAgent.extract

            def _patched_extract(self, pdf_bytes, **kwargs):
                chunks, meta = _orig_extract(self, pdf_bytes, **kwargs)
                captured_meta["strategy"] = meta.strategy.value
                captured_meta["has_tables"] = meta.has_tables
                captured_meta["has_images"] = meta.has_images
                captured_meta["warnings"] = list(meta.warnings)
                return chunks, meta

            _pea_mod.PdfExtractionAgent.extract = _patched_extract
            chunks = proc.process_url(url, source=connector.upper(), category=key)
            _pea_mod.PdfExtractionAgent.extract = _orig_extract
        except Exception as exc:
            return ExtractionResult(
                connector=connector, key=key, url=url, doc_type="pdf",
                chunks=0, avg_chars=0, strategy="", has_tables=False,
                has_images=False, duration_s=round(time.monotonic() - t0, 1),
                error=str(exc)[:120],
            )
    else:
        try:
            chunks = proc.process_url(url, source=connector.upper(), category=key)
        except Exception as exc:
            return ExtractionResult(
                connector=connector, key=key, url=url, doc_type="?",
                chunks=0, avg_chars=0, strategy="", has_tables=False,
                has_images=False, duration_s=round(time.monotonic() - t0, 1),
                error=str(exc)[:120],
            )

    elapsed = round(time.monotonic() - t0, 1)
    doc_type = proc.detect_type(url).value
    avg_chars = (
        round(sum(len(c.get("content", "")) for c in chunks) / len(chunks))
        if chunks else 0
    )

    return ExtractionResult(
        connector=connector,
        key=key,
        url=url,
        doc_type=doc_type,
        chunks=len(chunks),
        avg_chars=avg_chars,
        strategy=captured_meta.get("strategy", ""),
        has_tables=captured_meta.get("has_tables", False),
        has_images=captured_meta.get("has_images", False),
        duration_s=elapsed,
        warnings=captured_meta.get("warnings", []),
    )


# ── Reporting ─────────────────────────────────────────────────────────────────

_STATUS_EMOJI = {
    200: "OK ", 301: "→  ", 302: "→  ", 403: "403", 404: "404", -1: "ERR"
}

def _se(code: int) -> str:
    return _STATUS_EMOJI.get(code, str(code))


def print_head_report(results: List[HeadResult]) -> None:
    print("\n" + "=" * 90)
    print("HEAD CHECK RESULTS")
    print("=" * 90)
    print(f"{'ST':3} {'CONNECTOR':12} {'KEY':30} {'TYPE':20} {'KB':>5}  URL")
    print("-" * 90)

    ok = broken = redirected = 0
    for r in results:
        st = _se(r.status)
        kb = f"{r.size_hint_kb:>5}" if r.size_hint_kb >= 0 else "    ?"
        redir = f"  → {r.redirect_url[:40]}" if r.redirect_url else ""
        err = f"  ERR: {r.error}" if r.error else ""
        url_short = r.url[:55]
        print(f"{st} {r.connector:12} {r.key:30} {r.content_type:20} {kb}  {url_short}{redir}{err}")

        if r.status == 200:
            ok += 1
        elif r.status in (301, 302):
            redirected += 1
        else:
            broken += 1

    print("-" * 90)
    print(f"Total: {len(results)}  OK: {ok}  Broken: {broken}  Redirected: {redirected}")


def print_extraction_report(results: List[ExtractionResult]) -> None:
    print("\n" + "=" * 100)
    print("EXTRACTION RESULTS")
    print("=" * 100)
    print(f"{'CONNECTOR':12} {'KEY':28} {'TYPE':5} {'CHUNKS':>6} {'AVG_CHARS':>9} {'STRAT':12} {'TBL':3} {'IMG':3} {'TIME':>5}s")
    print("-" * 100)

    total_chunks = 0
    errors = []
    zero_chunks = []

    for r in results:
        tbl = "Y" if r.has_tables else "."
        img = "Y" if r.has_images else "."
        strat = r.strategy[:12] if r.strategy else "-"
        err = f"  ERROR: {r.error[:40]}" if r.error else ""
        warn = ""
        if r.warnings:
            warn = f"  WARN: {r.warnings[0][:40]}"
        print(
            f"{r.connector:12} {r.key:28} {r.doc_type:5} {r.chunks:>6} "
            f"{r.avg_chars:>9.0f} {strat:12} {tbl:3} {img:3} {r.duration_s:>5.1f}"
            f"{err}{warn}"
        )
        total_chunks += r.chunks
        if r.error:
            errors.append(r)
        elif r.chunks == 0:
            zero_chunks.append(r)

    print("-" * 100)
    print(f"Total: {len(results)} sources | {total_chunks} chunks | {len(errors)} errors | {len(zero_chunks)} zero-chunk sources")

    if errors:
        print("\nBROKEN SOURCES:")
        for r in errors:
            print(f"  {r.connector}/{r.key}: {r.error}")

    if zero_chunks:
        print("\nZERO CHUNKS (may need investigation):")
        for r in zero_chunks:
            print(f"  {r.connector}/{r.key}: {r.url}")


def print_pdf_deep_report(results: List[ExtractionResult]) -> None:
    pdfs = [r for r in results if r.doc_type == "pdf"]
    if not pdfs:
        return

    print("\n" + "=" * 80)
    print("PDF EXTRACTION DEEP DIVE")
    print("=" * 80)
    for r in pdfs:
        status = "ERROR" if r.error else ("EMPTY" if r.chunks == 0 else "OK")
        print(f"\n[{status}] {r.connector} / {r.key}")
        print(f"  URL:      {r.url}")
        print(f"  Strategy: {r.strategy or 'n/a'}")
        print(f"  Chunks:   {r.chunks}  |  Avg chars/chunk: {r.avg_chars:.0f}")
        print(f"  Tables:   {r.has_tables}  |  Images: {r.has_images}")
        if r.error:
            print(f"  Error:    {r.error}")
        for w in r.warnings:
            print(f"  Warning:  {w}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="MVP1 source extraction diagnostic")
    parser.add_argument("--head-only", action="store_true", help="Only run HEAD checks, skip extraction")
    parser.add_argument("--skip-pdf", action="store_true", help="Skip PDF extraction (fast mode)")
    parser.add_argument("--connector", help="Only test this connector (e.g. occ_bsa)")
    args = parser.parse_args()

    source_urls = load_source_urls()
    if args.connector:
        if args.connector not in source_urls:
            print(f"Unknown connector '{args.connector}'. Available: {list(source_urls)}")
            return
        source_urls = {args.connector: source_urls[args.connector]}

    session = requests.Session()
    session.headers.update({"User-Agent": _UA})

    # Build flat list
    all_urls: List[tuple] = []
    for connector, url_map in source_urls.items():
        for key, url in url_map.items():
            all_urls.append((connector, key, url))

    print(f"\nMVP1 Source Extraction Diagnostic — {len(all_urls)} URLs across {len(source_urls)} connectors")
    print(f"Config: {_USA_YAML}")

    # ── Phase 1: HEAD checks ─────────────────────────────────────────────────
    print(f"\nRunning HEAD checks...")
    head_results: List[HeadResult] = []
    for connector, key, url in all_urls:
        r = head_check(connector, key, url, session)
        head_results.append(r)
        status_str = f"HTTP {r.status}" if r.status > 0 else f"ERR: {r.error[:30]}"
        print(f"  {'OK' if r.status == 200 else 'XX'} [{status_str}] {connector}/{key}")

    print_head_report(head_results)

    if args.head_only:
        return

    # ── Phase 2: Extraction tests ─────────────────────────────────────────────
    reachable = [r for r in head_results if r.status in (200, 301, 302)]
    skip_keys = {"base_url"}  # not real documents

    to_extract = [
        (r.connector, r.key, r.url) for r in reachable
        if r.key not in skip_keys
    ]

    if args.skip_pdf:
        to_extract = [
            (c, k, u) for c, k, u in to_extract
            if not u.lower().endswith(".pdf")
        ]

    print(f"\nRunning extraction on {len(to_extract)} reachable URLs...")
    extraction_results: List[ExtractionResult] = []
    for i, (connector, key, url) in enumerate(to_extract, 1):
        doc_hint = "[PDF]" if url.lower().endswith(".pdf") else "[HTML/CSV/XML]"
        print(f"  [{i:02d}/{len(to_extract):02d}] {doc_hint} {connector}/{key} ...")
        result = extract_url(connector, key, url)
        extraction_results.append(result)
        chunk_str = f"{result.chunks} chunks" if not result.error else f"ERROR: {result.error[:40]}"
        print(f"         → {chunk_str} ({result.duration_s}s)")

    print_extraction_report(extraction_results)
    print_pdf_deep_report(extraction_results)


if __name__ == "__main__":
    main()
