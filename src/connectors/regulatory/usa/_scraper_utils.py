"""
Shared scraping helpers for US regulatory connectors.

Tools used (all $0):
  unstructured[pdf]   — PDF text extraction
  beautifulsoup4      — HTML parsing
  requests            — HTTP
  feedparser          — RSS/Atom feeds
  lxml                — fast HTML parser backend
  xml.etree           — Sitemap XML parsing
"""
import hashlib
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

# ── Obligation enrichment patterns (used by enrich_document) ──────────
_OBLIGATION_PATTERN = re.compile(
    r"\b(shall|must|required to|prohibited from|may not|is required|are required)\b",
    re.IGNORECASE,
)
_OBLIGATION_TYPES = [
    (re.compile(r"\bprohibited\b|\bmay not\b", re.I), "prohibited"),
    (re.compile(r"\bshall\b", re.I), "shall"),
    (re.compile(r"\bmust\b", re.I), "must"),
    (re.compile(r"\brequired\b", re.I), "required"),
]
_CFR_PATTERN = re.compile(
    r"(\d+\s+C\.F\.R\.?\s*§\s*[\d.]+(?:\([a-z0-9]+\))*)", re.I
)
_SECTION_PATTERN = re.compile(r"§\s*([\d.]+(?:\([a-z0-9]+\))*)", re.I)

import requests
import structlog
from bs4 import BeautifulSoup

logger = structlog.get_logger(__name__)

_UA = "TextMind-Compliance/1.0"
# Browser UA used as fallback for sites that block bot UAs (e.g., www.ffiec.gov → 403)
_UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
# Domains known to require a browser-like User-Agent.
# FFIEC properties (bsaaml, ithandbook, cdr, ffiec.cfpb.gov) sit behind
# Cloudflare/Akamai and block bot UAs. Plain browser UA alone is not enough
# for some — they also check Accept-Language and Sec-Fetch-* headers, which
# _BROWSER_HEADERS supplies.
_BROWSER_UA_DOMAINS = {
    "www.ffiec.gov",
    "ffiec.gov",
    "ithandbook.ffiec.gov",
    "bsaaml.ffiec.gov",
    "cdr.ffiec.gov",
    "ffiec.cfpb.gov",
    "www.federalreserve.gov",
    "federalreserve.gov",
    "dfpi.ca.gov",
}
# Full browser-like header set for domains that look past the UA string.
_BROWSER_HEADERS = {
    "User-Agent": _UA_BROWSER,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
_TIMEOUT_HTML = 30
_TIMEOUT_PDF = 120

# Domains protected by Cloudflare Bot Management JS challenge that no header
# trick (full browser UA, Accept, Sec-Fetch, JA3 impersonation) defeats —
# verified live 2026-05-17 (CF-Mitigated: challenge, CAPTCHA Error page).
# For these, _fetch_html falls back to the archive.org Wayback Machine when
# a snapshot exists. bsaaml.ffiec.gov and ithandbook.ffiec.gov are listed but
# have no wayback snapshots, so the fallback returns nothing and the connector
# relies on its hardcoded _build_regulations() / _build_requirements() output.
_WAYBACK_FALLBACK_DOMAINS = {
    "www.ffiec.gov",
    "ffiec.gov",
    "bsaaml.ffiec.gov",
    "ithandbook.ffiec.gov",
    "cdr.ffiec.gov",
    "ffiec.cfpb.gov",
    "dfpi.ca.gov",
}

# Hosts that are both CF-gated AND have zero wayback coverage. Verified live
# 2026-05-17: every URL under these hosts returns no wayback snapshot, so
# even the availability API call is wasted work. _fetch_html short-circuits
# before any network request and raises, letting the connector fall through
# to its hardcoded _build_regulations() / _build_requirements() output.
_NO_SCRAPE_HOSTS = {
    "bsaaml.ffiec.gov",
    "ithandbook.ffiec.gov",
}

# Negative cache: URLs known to have no wayback snapshot. Avoids re-querying
# the availability API on every retry within a single process.
_wayback_no_snapshot_urls: Set[str] = set()

# Hosts whose TLS chain is not in our certifi bundle. We disable SSL
# verification for these only, with a logged warning, rather than
# globally relaxing TLS or shipping a custom CA bundle in the image.
# Texas DoB uses a state-issued intermediate CA absent from certifi.
_NO_SSL_VERIFY_DOMAINS = {"www.dob.texas.gov", "dob.texas.gov"}

_insecure_warning_suppressed = False


def _suppress_insecure_warning() -> None:
    """Suppress urllib3 InsecureRequestWarning once per process."""
    global _insecure_warning_suppressed
    if _insecure_warning_suppressed:
        return
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        _insecure_warning_suppressed = True
    except Exception:
        pass


def _get_request_delay() -> float:
    """Return the configured delay between HTTP requests (seconds)."""
    try:
        from src.utils.config import settings
        return settings.SCRAPER_REQUEST_DELAY
    except Exception:
        return float(os.getenv("SCRAPER_REQUEST_DELAY", "1.0"))


# ── Per-domain rate limiter ───────────────────────────────────────
# Tracks the last request time per hostname so we never hit the same
# government domain faster than _get_request_delay() seconds apart.
_domain_last_request: Dict[str, float] = {}


def _rate_limit_for_domain(url: str) -> None:
    """Sleep if needed to enforce per-domain rate limiting."""
    host = urlparse(url).hostname or "unknown"
    delay = _get_request_delay()
    now = time.monotonic()
    last = _domain_last_request.get(host, 0.0)
    wait = delay - (now - last)
    if wait > 0:
        time.sleep(wait)
    _domain_last_request[host] = time.monotonic()


# Local PDF storage for manual analysis
_PROJECT_ROOT = Path(__file__).resolve().parents[4]  # src/connectors/regulatory/usa → project root
_PDF_DIR = _PROJECT_ROOT / "data" / "pdfs"


def make_session(url: str = "") -> requests.Session:
    """Return a requests Session with appropriate headers for the target URL.

    Domains in _BROWSER_UA_DOMAINS get the full browser-like header set
    (UA + Accept-Language + Sec-Fetch-* etc.). Others get the project UA
    and a permissive Accept. SSL verification is relaxed only for hosts
    listed in _NO_SSL_VERIFY_DOMAINS (see that constant for rationale).
    """
    s = requests.Session()
    host = urlparse(url).hostname or "" if url else ""
    if host in _BROWSER_UA_DOMAINS:
        s.headers.update(_BROWSER_HEADERS)
    else:
        s.headers.update(
            {"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.9"}
        )
    if host in _NO_SSL_VERIFY_DOMAINS:
        s.verify = False
        _suppress_insecure_warning()
        logger.info("ssl_verify_disabled_for_known_quirky_host", host=host)
    return s


_HTML_CONTENT_TYPE_TOKENS = ("text/html", "application/xhtml", "application/xml", "text/xml", "text/plain")


def _resolve_wayback_snapshot(url: str) -> Optional[str]:
    """Return the raw-content Wayback Machine URL for *url* if a snapshot exists.

    Uses the id_ variant (web.archive.org/web/{ts}id_/{url}) so the response is
    the original archived bytes without the wayback toolbar injection.
    Returns None and caches the negative result if no snapshot is available.
    """
    if url in _wayback_no_snapshot_urls:
        return None
    # The availability API returns empty for full URLs in some cases
    # (e.g. https://www.ffiec.gov/cra/ratings.htm returns no hits, but the
    # same URL without the scheme returns a 2025 snapshot). Query with the
    # scheme stripped so the host is treated as a bare URL.
    query_url = re.sub(r"^https?://", "", url)
    try:
        resp = requests.get(
            "https://archive.org/wayback/available",
            params={"url": query_url},
            timeout=15,
        )
        resp.raise_for_status()
        snap = (resp.json() or {}).get("archived_snapshots", {}).get("closest") or {}
        if snap.get("status") == "200" and snap.get("timestamp") and snap.get("url"):
            return f"https://web.archive.org/web/{snap['timestamp']}id_/{url}"
    except Exception as exc:
        logger.warning("wayback_availability_lookup_failed", url=url, error=str(exc))
        return None
    _wayback_no_snapshot_urls.add(url)
    return None


def _is_cloudflare_challenge(resp: requests.Response) -> bool:
    """True if the response is a Cloudflare Bot Management challenge page."""
    if resp.status_code != 403:
        return False
    if resp.headers.get("CF-Mitigated", "").lower() == "challenge":
        return True
    return resp.headers.get("Server", "").lower().startswith("cloudflare")


def _fetch_html(session: requests.Session, url: str) -> requests.Response:
    """GET an HTML page, retrying with full browser headers if the server returns 403.

    Uses stream=True so the Content-Type header can be inspected before the body
    is loaded into memory. An extensionless TOC link can redirect to a large
    media file (FDIC: `…/section-16-1` → 344 MB `section-16-1.mp4` on S3);
    without this guard, the body downloads in full and BeautifulSoup/lxml then
    burns CPU scanning the binary for tag patterns.
    """
    host = urlparse(url).hostname or ""
    if host in _NO_SCRAPE_HOSTS:
        logger.info("html_skipped_no_scrape_host", url=url, host=host)
        raise requests.exceptions.HTTPError(
            f"host {host} is CF-gated with no wayback coverage; "
            f"connector should use hardcoded output"
        )

    resp = session.get(url, timeout=_TIMEOUT_HTML, stream=True)
    if resp.status_code == 403 and session.headers.get("User-Agent") != _UA_BROWSER:
        resp.close()
        logger.info("html_403_retrying_with_browser_headers", url=url)
        session.headers.update(_BROWSER_HEADERS)
        time.sleep(1)
        resp = session.get(url, timeout=_TIMEOUT_HTML, stream=True)

    # Cloudflare CAPTCHA challenge: try Wayback Machine for allow-listed hosts.
    if _is_cloudflare_challenge(resp):
        host = urlparse(url).hostname or ""
        if host in _WAYBACK_FALLBACK_DOMAINS:
            resp.close()
            wb_url = _resolve_wayback_snapshot(url)
            if wb_url:
                logger.info("cf_challenge_falling_back_to_wayback", url=url, wayback=wb_url)
                resp = session.get(wb_url, timeout=_TIMEOUT_HTML, stream=True)
            else:
                logger.warning("cf_challenge_no_wayback_snapshot", url=url, host=host)
                # Raise so caller logs html_scrape_failed and connector falls
                # back to hardcoded _build_regulations() output.
                raise requests.exceptions.HTTPError(
                    f"403 CF challenge with no wayback fallback: {url}"
                )
    resp.raise_for_status()

    ct = resp.headers.get("Content-Type", "").lower()
    if ct and not any(token in ct for token in _HTML_CONTENT_TYPE_TOKENS):
        final_url = resp.url
        resp.close()
        logger.warning(
            "html_skipped_non_html_content_type",
            url=url,
            final_url=final_url,
            content_type=ct,
        )
        raise requests.exceptions.HTTPError(f"non-html content-type: {ct}")

    # Materialize the body now so callers can use resp.content normally.
    _ = resp.content
    return resp


def scrape_html_page(
    session: requests.Session,
    url: str,
    source: str,
    category: str,
    content_selectors: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch an HTML page and extract text sections by heading structure.

    Returns a list of scraped document dicts with keys:
      source, category, title, content, url, scraped_at, content_hash
    """
    _rate_limit_for_domain(url)
    try:
        resp = _fetch_html(session, url)
    except requests.RequestException as exc:
        logger.warning("html_scrape_failed", url=url, error=str(exc))
        return []

    soup = BeautifulSoup(resp.content, "lxml")

    # Try caller-supplied selectors first, then common gov-site patterns.
    # Skip selectors whose matched element has very little text — avoids picking
    # up empty Drupal wrapper divs (e.g., div.field--name-body on FDIC) before
    # reaching the actual content container (main / article).
    _MIN_CONTENT_CHARS = 500
    content_div = None
    for sel in (content_selectors or []) + [
        "div.article-body",
        "div.field--name-body",
        "div.content-wrapper",
        "div.regulation-content",
        "main",
        "article",
    ]:
        candidate = soup.select_one(sel)
        if candidate and len(candidate.get_text(strip=True)) >= _MIN_CONTENT_CHARS:
            content_div = candidate
            break

    if not content_div:
        content_div = soup.body or soup

    docs: List[Dict[str, Any]] = []
    now = datetime.utcnow().isoformat()

    for heading in content_div.find_all(["h1", "h2", "h3"]):
        title = heading.get_text(strip=True)
        if not title:
            continue

        parts: List[str] = []
        for sibling in heading.find_next_siblings():
            if sibling.name in ("h1", "h2", "h3"):
                break
            if sibling.name in ("p", "ul", "ol", "div", "table"):
                text = sibling.get_text(strip=True)
                if text:
                    parts.append(text)

        content = " ".join(parts)
        if not content:
            continue

        docs.append(
            {
                "source": source,
                "category": category,
                "title": title,
                "content": content,
                "url": url,
                "scraped_at": now,
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            }
        )

    # Fallback: if heading-based extraction found nothing, grab the whole
    # article/main content as a single chunk (common on gov Drupal sites).
    if not docs:
        full_text = content_div.get_text(separator=" ", strip=True)
        if len(full_text) > 100:
            page_title = (
                (content_div.find("h1") or soup.find("h1") or soup.find("title"))
            )
            title = page_title.get_text(strip=True) if page_title else category
            docs.append(
                {
                    "source": source,
                    "category": category,
                    "title": title,
                    "content": full_text[:10000],
                    "url": url,
                    "scraped_at": now,
                    "content_hash": hashlib.sha256(full_text.encode()).hexdigest(),
                }
            )

    logger.info("html_scrape_completed", url=url, sections=len(docs))
    return docs


def scrape_linked_pages(
    session: requests.Session,
    index_url: str,
    source: str,
    category: str,
    link_pattern: str,
    base_url: str = "",
    max_pages: int = 50,
    content_selectors: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch an index/TOC page, extract all href links matching *link_pattern*,
    then scrape each linked page with heading-based chunking.

    Useful for sites where the top-level URL is a table of contents (e.g.,
    FDIC Consumer Compliance Examination Manual).

    Args:
        index_url:   URL of the index/TOC page.
        link_pattern: Substring that href must contain to be followed.
        base_url:    Prepended to relative hrefs (e.g., "https://www.fdic.gov").
        max_pages:   Safety cap on number of chapter pages scraped.
    """
    from urllib.parse import urljoin

    _rate_limit_for_domain(index_url)
    try:
        resp = _fetch_html(session, index_url)
    except requests.RequestException as exc:
        logger.warning("toc_fetch_failed", url=index_url, error=str(exc))
        return []

    soup = BeautifulSoup(resp.content, "lxml")
    seen: set = set()
    chapter_urls: List[str] = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if link_pattern not in href:
            continue
        full_url = href if href.startswith("http") else urljoin(base_url or index_url, href)
        # Deduplicate and skip anchors
        canonical = full_url.split("#")[0]
        if canonical in seen or canonical == index_url.split("#")[0]:
            continue
        seen.add(canonical)
        chapter_urls.append(canonical)
        if len(chapter_urls) >= max_pages:
            break

    logger.info("toc_links_found", index_url=index_url, count=len(chapter_urls))

    docs: List[Dict[str, Any]] = []
    delay = _get_request_delay()
    for url in chapter_urls:
        try:
            page_docs = scrape_html_page(
                session, url, source, category,
                content_selectors=content_selectors,
            )
            docs.extend(page_docs)
        except Exception as exc:
            logger.warning("chapter_scrape_failed", url=url, error=str(exc))
        time.sleep(delay)

    logger.info("linked_pages_scraped", index_url=index_url, pages=len(chapter_urls), docs=len(docs))
    return docs


def scrape_pdf_url(
    session: requests.Session,
    pdf_url: str,
    source: str,
    category: str,
    max_chars_per_chunk: int = 2000,
    save_local: bool = True,
) -> List[Dict[str, Any]]:
    """
    Download a PDF and extract text chunks via unstructured.

    When save_local=True, keeps a copy under data/pdfs/<SOURCE>/ for manual analysis.
    Falls back gracefully if unstructured is not installed or PDF fails.
    """
    _rate_limit_for_domain(pdf_url)

    # Guard against large PDFs that cause OOM (e.g. FDIC 10MB complete manual)
    _MAX_PDF_BYTES = 5 * 1024 * 1024  # 5 MB
    try:
        head = session.head(pdf_url, timeout=10, allow_redirects=True)
        content_length = int(head.headers.get("content-length", 0))
        if content_length > _MAX_PDF_BYTES:
            logger.warning(
                "pdf_skipped_too_large",
                url=pdf_url,
                size_kb=content_length // 1024,
            )
            return []
    except Exception:
        pass  # HEAD failed — proceed; download will still be size-checked below

    try:
        resp = session.get(pdf_url, timeout=_TIMEOUT_PDF, stream=True)
        resp.raise_for_status()
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > _MAX_PDF_BYTES:
                logger.warning("pdf_skipped_too_large_streaming", url=pdf_url, size_kb=total // 1024)
                return []
            chunks.append(chunk)
        resp._content = b"".join(chunks)
    except requests.RequestException as exc:
        logger.warning("pdf_download_failed", url=pdf_url, error=str(exc))
        return []

    # Reject non-PDF responses (HTML error pages served with application/pdf
    # Content-Type, redirect chains landing on login pages, etc.) before
    # pdfplumber raises the noisier "No /Root object" error.
    body = resp.content
    if not body.startswith(b"%PDF-"):
        logger.warning(
            "pdf_skipped_not_a_pdf",
            url=pdf_url,
            content_type=resp.headers.get("Content-Type", ""),
            head=body[:20].hex(),
        )
        return []

    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber_not_installed_skipping_pdf", url=pdf_url)
        return []

    # Save a local copy for manual analysis
    if save_local:
        parsed = urlparse(pdf_url)
        raw_name = os.path.basename(parsed.path) or "document.pdf"
        filename = re.sub(r"[^\w\-.]", "_", raw_name)
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        local_dir = _PDF_DIR / source.upper().replace(" ", "_").replace("/", "_")
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / filename
        local_path.write_bytes(resp.content)
        logger.info("pdf_saved_locally", path=str(local_path), size_kb=len(resp.content) // 1024)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name

    docs: List[Dict[str, Any]] = []
    now = datetime.utcnow().isoformat()

    try:
        with pdfplumber.open(tmp_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text() or ""
                page_text = page_text.strip()
                if not page_text:
                    continue
                # Chunk the page text into max_chars_per_chunk slices
                for i in range(0, len(page_text), max_chars_per_chunk):
                    text = page_text[i : i + max_chars_per_chunk].strip()
                    if not text:
                        continue
                    docs.append(
                        {
                            "source": source,
                            "category": category,
                            "title": category,
                            "content": text,
                            "page_number": page_num,
                            "url": pdf_url,
                            "scraped_at": now,
                            "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                        }
                    )
    except Exception as exc:
        logger.warning("pdf_parse_failed", url=pdf_url, error=str(exc))
    finally:
        os.unlink(tmp_path)

    logger.info("pdf_scrape_completed", url=pdf_url, chunks=len(docs))
    return docs


def check_rss_feed(
    feed_url: str,
    keywords: Optional[List[str]] = None,
    max_items: int = 20,
) -> List[Dict[str, str]]:
    """
    Parse an RSS/Atom feed and return recent items, optionally filtered by keyword.
    Returns [] if feedparser is not installed or feed is unreachable.
    """
    try:
        import feedparser  # noqa: PLC0415
    except ImportError:
        logger.warning("feedparser_not_installed_skipping_rss", url=feed_url)
        return []

    try:
        feed = feedparser.parse(feed_url)
    except Exception as exc:
        logger.warning("rss_parse_failed", url=feed_url, error=str(exc))
        return []

    items: List[Dict[str, str]] = []
    for entry in feed.entries[:max_items]:
        title = getattr(entry, "title", "")
        if keywords and not any(kw.lower() in title.lower() for kw in keywords):
            continue
        items.append(
            {
                "title": title,
                "link": getattr(entry, "link", ""),
                "published": getattr(entry, "published", ""),
                "summary": getattr(entry, "summary", ""),
            }
        )

    logger.info("rss_feed_checked", url=feed_url, items=len(items))
    return items


# ------------------------------------------------------------------ #
# URL Health Check
# ------------------------------------------------------------------ #


def url_health_check(
    session: requests.Session,
    urls: Dict[str, str],
    source: str,
) -> Dict[str, Dict[str, Any]]:
    """
    HEAD-check a dict of {label: url} and return status for each.

    Returns:
        {label: {"url": str, "status": int, "ok": bool, "content_type": str}}
    """
    results: Dict[str, Dict[str, Any]] = {}
    for label, url in urls.items():
        try:
            resp = session.head(url, timeout=15, allow_redirects=True)
            results[label] = {
                "url": url,
                "status": resp.status_code,
                "ok": resp.status_code < 400,
                "content_type": resp.headers.get("Content-Type", ""),
            }
        except requests.RequestException as exc:
            results[label] = {
                "url": url,
                "status": 0,
                "ok": False,
                "content_type": "",
                "error": str(exc),
            }
            logger.warning("url_health_check_failed", source=source, label=label, url=url, error=str(exc))

    ok_count = sum(1 for r in results.values() if r["ok"])
    logger.info("url_health_check_completed", source=source, total=len(urls), ok=ok_count, failed=len(urls) - ok_count)
    return results


# ------------------------------------------------------------------ #
# FinCEN SAR PDF Year-Walking Resolver
# ------------------------------------------------------------------ #


def resolve_fincen_sar_pdf(
    session: requests.Session,
    base_pattern: str = "https://www.fincen.gov/sites/default/files/shared/sar_tti_{yy}.pdf",
    fallback_patterns: Optional[List[str]] = None,
    max_years_back: int = 5,
) -> Optional[str]:
    """
    Resolve the current FinCEN SAR Activity Review PDF URL by probing
    year suffixes from the current year backwards.

    The URL pattern rotates annually: sar_tti_25.pdf, sar_tti_24.pdf, etc.
    Also tries alternate path patterns since FinCEN has moved files between
    /system/files/ and /sites/default/files/.

    Returns the first URL that responds with HTTP 200, or None.
    """
    from datetime import datetime

    current_year = datetime.utcnow().year
    patterns = [base_pattern]
    if fallback_patterns:
        patterns.extend(fallback_patterns)

    for years_back in range(0, max_years_back):
        yy = str(current_year - years_back)[-2:]
        for pattern in patterns:
            url = pattern.format(yy=yy)
            try:
                resp = session.head(url, timeout=15, allow_redirects=True)
                if resp.status_code < 400:
                    content_type = resp.headers.get("Content-Type", "")
                    if "pdf" in content_type.lower() or url.endswith(".pdf"):
                        logger.info("fincen_sar_pdf_resolved", url=url, year=f"20{yy}")
                        return url
            except requests.RequestException:
                continue

    logger.warning("fincen_sar_pdf_not_found", patterns=patterns, years_checked=max_years_back)
    return None


# ------------------------------------------------------------------ #
# CFPB HMDA FIG HTML Crawler
# ------------------------------------------------------------------ #


def scrape_hmda_fig_pages(
    session: requests.Session,
    overview_url: str,
    source: str = "CFPB",
    category: str = "HMDA Filing Guide",
    max_pages: int = 50,
) -> List[Dict[str, Any]]:
    """
    Crawl the FFIEC HMDA Filing Instructions Guide (FIG) HTML documentation.

    The FIG at ffiec.cfpb.gov/documentation/fig/{year}/overview is a React SPA,
    but the individual sub-pages under /documentation/fig/{year}/ render as
    static HTML and can be crawled directly.

    Strategy:
      1. Fetch the overview page
      2. Extract all internal links matching /documentation/fig/
      3. Scrape each sub-page with heading-based chunking
    """
    from urllib.parse import urljoin

    try:
        resp = session.get(overview_url, timeout=_TIMEOUT_HTML)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("hmda_fig_overview_failed", url=overview_url, error=str(exc))
        return []

    soup = BeautifulSoup(resp.content, "lxml")

    # Extract the year from the URL for link matching (e.g., "2026")
    year_match = re.search(r"/fig/(\d{4})/", overview_url)
    fig_year = year_match.group(1) if year_match else ""
    link_pattern = f"/documentation/fig/{fig_year}/" if fig_year else "/documentation/fig/"

    # Collect all sub-page links
    seen: set = set()
    page_urls: List[str] = []
    base_url = overview_url.rsplit("/documentation/", 1)[0]

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if link_pattern not in href:
            continue
        full_url = href if href.startswith("http") else urljoin(base_url, href)
        canonical = full_url.split("#")[0].rstrip("/")
        if canonical in seen or canonical == overview_url.rstrip("/"):
            continue
        seen.add(canonical)
        page_urls.append(full_url)
        if len(page_urls) >= max_pages:
            break

    logger.info("hmda_fig_links_found", overview_url=overview_url, count=len(page_urls))

    # Scrape the overview page itself first
    docs: List[Dict[str, Any]] = []
    overview_docs = scrape_html_page(
        session, overview_url, source, category,
        content_selectors=["div.App", "main", "article", "div#root"],
    )
    docs.extend(overview_docs)

    # Scrape each sub-page with rate limiting
    delay = _get_request_delay()
    for url in page_urls:
        try:
            page_docs = scrape_html_page(
                session, url, source, category,
                content_selectors=["div.App", "main", "article", "div#root"],
            )
            docs.extend(page_docs)
        except Exception as exc:
            logger.warning("hmda_fig_page_failed", url=url, error=str(exc))
        time.sleep(delay)

    logger.info("hmda_fig_scrape_completed", total_docs=len(docs), pages_crawled=len(page_urls) + 1)
    return docs


# ------------------------------------------------------------------ #
# FDIC Risk Management Manual Section Crawler
# ------------------------------------------------------------------ #


def scrape_fdic_risk_manual_sections(
    session: requests.Session,
    toc_url: str = "https://www.fdic.gov/risk-management-manual-examination-policies",
    source: str = "FDIC",
    category: str = "Risk Management Manual",
    max_sections: int = 60,
) -> List[Dict[str, Any]]:
    """
    Crawl the FDIC Risk Management Manual of Examination Policies
    section-by-section.

    The full combined PDF no longer exists. Instead, FDIC now serves the manual
    as individual section pages/PDFs linked from the TOC at:
      https://www.fdic.gov/risk-management-manual-examination-policies

    Strategy:
      1. Fetch the TOC page
      2. Find all links matching /risk-management-manual-examination-policies/
      3. For each section: if it's a PDF, use scrape_pdf_url; otherwise scrape HTML
    """
    from urllib.parse import urljoin

    try:
        resp = _fetch_html(session, toc_url)
    except requests.RequestException as exc:
        logger.warning("fdic_risk_manual_toc_failed", url=toc_url, error=str(exc))
        return []

    soup = BeautifulSoup(resp.content, "lxml")

    seen: set = set()
    section_urls: List[str] = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if "/risk-management-manual-examination-policies/" not in href:
            # Also match section links that are just relative paths
            if "section-" not in href.lower() and "risk-management" not in href.lower():
                continue
        full_url = href if href.startswith("http") else urljoin("https://www.fdic.gov", href)
        canonical = full_url.split("#")[0].rstrip("/")
        if canonical in seen or canonical == toc_url.rstrip("/"):
            continue
        seen.add(canonical)
        section_urls.append(full_url)
        if len(section_urls) >= max_sections:
            break

    logger.info("fdic_risk_manual_sections_found", toc_url=toc_url, count=len(section_urls))

    docs: List[Dict[str, Any]] = []

    # Also scrape the TOC page itself for overview content
    toc_docs = scrape_html_page(session, toc_url, source, category)
    docs.extend(toc_docs)

    delay = _get_request_delay()
    for url in section_urls:
        # Skip bulk archives, media files, and the complete-manual PDF (OOM risk)
        lower_url = url.lower()
        if (
            lower_url.endswith((".zip", ".mp4", ".mp3", ".webm", ".mov", ".avi"))
            or any(ext in lower_url for ext in _SKIP_EXTENSIONS)
            or "complete-manual" in lower_url
            or "complete_manual" in lower_url
        ):
            logger.info("fdic_risk_manual_section_skipped", url=url)
            continue
        try:
            if lower_url.endswith(".pdf"):
                section_docs = scrape_pdf_url(session, url, source, category)
            else:
                section_docs = scrape_html_page(session, url, source, category)
            docs.extend(section_docs)
        except Exception as exc:
            logger.warning("fdic_risk_manual_section_failed", url=url, error=str(exc))
        time.sleep(delay)

    logger.info(
        "fdic_risk_manual_scrape_completed",
        total_docs=len(docs),
        sections_crawled=len(section_urls),
    )
    return docs


# ================================================================== #
# Sitemap-Driven Compliance URL Discovery
# ================================================================== #

# Agency sitemaps — all 5 publish sitemap.xml or sitemap_index.xml
AGENCY_SITEMAPS: Dict[str, str] = {
    "occ": "https://www.occ.gov/sitemap.xml",
    "fdic": "https://www.fdic.gov/sitemap.xml",
    "cfpb": "https://www.consumerfinance.gov/sitemap.xml",
    "fincen": "https://www.fincen.gov/sitemap.xml",
    "ofac": "https://ofac.treasury.gov/sitemap.xml",
}

# Path keywords that indicate compliance-relevant content
COMPLIANCE_KEYWORDS: List[str] = [
    "/regulations/",
    "/guidance/",
    "/manual/",
    "/bulletins/",
    "/bsa/",
    "/aml/",
    "/compliance/",
    "/examination/",
    "/supervision/",
    "/enforcement/",
    "/advisories/",
    "/rulings/",
    "/handbook/",
    "/filing/",
    "/sanctions/",
    "/deposit-insurance/",
    "/capital/",
    "/risk-management/",
    "/consumer-compliance/",
    "/rules-policy/",
    "/statutes-regulations/",
    "/financial-institution-letters/",
    "/comptrollers-handbook/",
]

# File extensions to classify discovered URLs
_PDF_EXTENSIONS = {".pdf"}
_SKIP_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".css", ".js", ".ico", ".woff", ".woff2", ".ttf", ".mp4", ".webp"}

# Sitemap XML namespace
_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _parse_sitemap_xml(
    session: requests.Session,
    sitemap_url: str,
    max_depth: int = 2,
    _depth: int = 0,
) -> List[str]:
    """
    Parse a sitemap XML (or sitemap index) and return all <loc> URLs.

    Handles:
      - Standard sitemap.xml with <url><loc>
      - Sitemap index files with <sitemap><loc> (recursive, up to max_depth)
      - Gzipped sitemaps (.xml.gz)

    Returns a flat list of page URLs.
    """
    if _depth > max_depth:
        return []

    try:
        resp = session.get(sitemap_url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("sitemap_fetch_failed", url=sitemap_url, error=str(exc))
        return []

    content = resp.content

    # Handle gzipped sitemaps
    if sitemap_url.endswith(".gz"):
        import gzip
        try:
            content = gzip.decompress(content)
        except Exception:
            logger.warning("sitemap_gzip_decompress_failed", url=sitemap_url)
            return []

    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        logger.warning("sitemap_parse_failed", url=sitemap_url, error=str(exc))
        return []

    urls: List[str] = []

    # Check if this is a sitemap index (contains <sitemap> elements)
    sub_sitemaps = root.findall("sm:sitemap/sm:loc", _SITEMAP_NS)
    if not sub_sitemaps:
        # Try without namespace (some gov sites use bare XML)
        sub_sitemaps = root.findall("sitemap/loc")

    if sub_sitemaps:
        # Sitemap index — recurse into each child sitemap
        for loc_elem in sub_sitemaps:
            child_url = (loc_elem.text or "").strip()
            if child_url:
                urls.extend(_parse_sitemap_xml(session, child_url, max_depth, _depth + 1))
        return urls

    # Standard sitemap — extract <url><loc> entries
    loc_elements = root.findall("sm:url/sm:loc", _SITEMAP_NS)
    if not loc_elements:
        loc_elements = root.findall("url/loc")

    for loc_elem in loc_elements:
        url = (loc_elem.text or "").strip()
        if url:
            urls.append(url)

    logger.info("sitemap_parsed", url=sitemap_url, url_count=len(urls))
    return urls


def discover_compliance_urls(
    session: requests.Session,
    agency: str,
    sitemap_url: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    exclude_extensions: Optional[Set[str]] = None,
    existing_urls: Optional[Set[str]] = None,
    max_urls: int = 500,
) -> Dict[str, List[str]]:
    """
    Discover compliance-relevant URLs from an agency's sitemap.

    Fetches the sitemap, filters by compliance keywords, classifies into
    HTML vs PDF, and returns only URLs not already in `existing_urls`.

    Args:
        session:            HTTP session with user-agent set.
        agency:             Agency key (e.g., "occ", "fdic").
        sitemap_url:        Override sitemap URL. If None, uses AGENCY_SITEMAPS.
        keywords:           Override compliance keywords. If None, uses defaults.
        exclude_extensions: File extensions to skip. If None, uses defaults.
        existing_urls:      URLs already configured — discovered URLs matching
                            these are tagged as "known" rather than "new".
        max_urls:           Safety cap on total discovered URLs.

    Returns:
        {
            "html_urls": [list of compliance HTML page URLs],
            "pdf_urls":  [list of compliance PDF URLs],
            "new_urls":  [URLs not in existing_urls],
            "known_urls": [URLs already in existing_urls],
            "all_sitemap_urls": int,   # total URLs in sitemap
            "compliance_urls": int,     # after keyword filter
        }
    """
    sitemap = sitemap_url or AGENCY_SITEMAPS.get(agency)
    if not sitemap:
        logger.warning("sitemap_url_not_configured", agency=agency)
        return {"html_urls": [], "pdf_urls": [], "new_urls": [], "known_urls": [],
                "all_sitemap_urls": 0, "compliance_urls": 0}

    kw_list = keywords or COMPLIANCE_KEYWORDS
    skip_ext = exclude_extensions or _SKIP_EXTENSIONS
    known = existing_urls or set()

    # 1. Fetch & parse sitemap
    all_urls = _parse_sitemap_xml(session, sitemap)

    # 2. Filter by compliance keywords
    compliance_urls: List[str] = []
    for url in all_urls:
        url_lower = url.lower()
        # Skip binary assets
        parsed = urlparse(url_lower)
        ext = os.path.splitext(parsed.path)[1]
        if ext in skip_ext:
            continue
        # Check keyword match
        if any(kw in url_lower for kw in kw_list):
            compliance_urls.append(url)
            if len(compliance_urls) >= max_urls:
                break

    # 3. Classify into HTML vs PDF, new vs known
    html_urls: List[str] = []
    pdf_urls: List[str] = []
    new_urls: List[str] = []
    known_urls: List[str] = []

    for url in compliance_urls:
        parsed = urlparse(url.lower())
        ext = os.path.splitext(parsed.path)[1]
        is_pdf = ext in _PDF_EXTENSIONS

        if is_pdf:
            pdf_urls.append(url)
        else:
            html_urls.append(url)

        # Normalize for comparison (strip trailing slash and fragment)
        canonical = url.split("#")[0].rstrip("/")
        if canonical in known or canonical + "/" in known:
            known_urls.append(url)
        else:
            new_urls.append(url)

    logger.info(
        "sitemap_discovery_completed",
        agency=agency,
        total_sitemap_urls=len(all_urls),
        compliance_matches=len(compliance_urls),
        html=len(html_urls),
        pdf=len(pdf_urls),
        new=len(new_urls),
        already_known=len(known_urls),
    )

    return {
        "html_urls": html_urls,
        "pdf_urls": pdf_urls,
        "new_urls": new_urls,
        "known_urls": known_urls,
        "all_sitemap_urls": len(all_urls),
        "compliance_urls": len(compliance_urls),
    }


def scrape_discovered_urls(
    session: requests.Session,
    discovered: Dict[str, List[str]],
    source: str,
    category: str = "Discovered Content",
    max_html_pages: int = 30,
    max_pdfs: int = 10,
    content_selectors: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Scrape URLs returned by discover_compliance_urls().

    Processes HTML pages and PDFs from the discovery result, with safety
    caps to avoid unbounded scraping.

    Args:
        session:          HTTP session.
        discovered:       Result dict from discover_compliance_urls().
        source:           Source label (e.g., "OCC", "FDIC").
        category:         Default category for discovered content.
        max_html_pages:   Cap on HTML pages to scrape.
        max_pdfs:         Cap on PDFs to process.
        content_selectors: CSS selectors for HTML content extraction.

    Returns:
        List of document dicts (same format as scrape_html_page).
    """
    docs: List[Dict[str, Any]] = []
    delay = _get_request_delay()

    # Scrape new HTML pages
    for url in discovered.get("new_urls", []):
        if url.lower().endswith(".pdf"):
            continue
        if len([d for d in docs if not d.get("page_number")]) >= max_html_pages:
            break
        try:
            page_docs = scrape_html_page(
                session, url, source, category,
                content_selectors=content_selectors,
            )
            docs.extend(page_docs)
        except Exception as exc:
            logger.warning("discovered_html_scrape_failed", url=url, error=str(exc))
        time.sleep(delay)

    # Scrape new PDFs
    pdf_count = 0
    for url in discovered.get("new_urls", []):
        if not url.lower().endswith(".pdf"):
            continue
        if pdf_count >= max_pdfs:
            break
        try:
            pdf_docs = scrape_pdf_url(session, url, source, category)
            docs.extend(pdf_docs)
            pdf_count += 1
        except Exception as exc:
            logger.warning("discovered_pdf_scrape_failed", url=url, error=str(exc))
        time.sleep(delay)

    logger.info(
        "discovered_urls_scraped",
        source=source,
        total_docs=len(docs),
        html_pages=len([d for d in docs if not d.get("page_number")]),
        pdf_chunks=len([d for d in docs if d.get("page_number")]),
    )

    return docs


# ================================================================== #
# Obligation Enrichment
# ================================================================== #


def enrich_document(
    doc: Dict[str, Any],
    regulation_id: str = "",
    state_code: Optional[str] = None,
    doc_type: str = "reg_text",
) -> Dict[str, Any]:
    """Add compliance audit enrichment fields to a scraped document.

    Call on every item before returning from _scrape_live(). The doc dict
    is mutated in place and also returned for chaining.

    doc_type values: reg_text | interpretive | faq | enforcement | guidance | examination
    """
    content = doc.get("content", "")
    obligation_type = "none"
    for pattern, label in _OBLIGATION_TYPES:
        if pattern.search(content):
            obligation_type = label
            break
    markers = _OBLIGATION_PATTERN.findall(content)
    cfr_match = _CFR_PATTERN.search(content)
    section_match = _SECTION_PATTERN.search(content)
    doc.update(
        {
            "is_obligation": len(markers) > 0,
            "obligation_type": obligation_type,
            "obligation_markers": " | ".join(
                dict.fromkeys(m.lower() for m in markers[:5])
            ),
            "cfr_citation": cfr_match.group(1).strip() if cfr_match else "",
            "section_reference": section_match.group(0).strip() if section_match else "",
            "state_code": state_code,
            "jurisdiction": "state" if state_code else "federal",
            "regulation_id": regulation_id,
            "doc_type": doc_type,
            "token_count": len(content.split()),
        }
    )
    return doc


def stamp_state_metadata(
    items: List[Dict[str, Any]],
    regulator: str,
    state_code: str,
) -> List[Dict[str, Any]]:
    """Backfill regulator/state_code/jurisdiction_level on state-connector dicts.

    State builders for KPIs and test scenarios historically only carried
    domain-specific fields (kpi_id, scenario_id, etc.) and relied on the
    scheduler to infer regulator/jurisdiction downstream. That inference
    failed for these categories, producing regulator='unknown' and
    jurisdiction_level='federal'. This helper stamps the canonical values
    so the source of truth is in the connector, not the inference layer.

    Uses setdefault — never overwrites fields a builder already set.
    """
    for d in items:
        d.setdefault("regulator", regulator)
        d.setdefault("state_code", state_code)
        d.setdefault("jurisdiction_level", "state")
    return items
