"""
Source URL audit — detects rot in connector source lists.

Imports every USA regulatory connector, reads its module-level _DEFAULT_URLS
dict, and probes each URL using the same session-builder the scrapers use
(make_session — picks up browser-UA hosts and the TLS-relaxation allowlist).

Outputs:
  - Human-readable table to stdout, grouped by connector.
  - JSON report to infra/audits/source_urls_YYYY-MM-DD.json.
  - Exit code 1 if any URL returns 404 or DNS/connection failure — wire this
    into a quarterly cron / CI job. CF-gated hosts in _NO_SCRAPE_HOSTS are
    classified as 'skipped' and never fail the run (the connectors fall
    through to hardcoded output for these anyway).

Run:
  python3 scripts/audit_source_urls.py
  python3 scripts/audit_source_urls.py --json-only   # CI mode, no stdout table
"""
from __future__ import annotations

import argparse
import importlib
import json
import pkgutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests

# Project root → on path so `src.*` imports resolve when run directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.connectors.regulatory.usa import _scraper_utils as su  # noqa: E402

_PROBE_TIMEOUT = 20
_PARALLEL_WORKERS = 8


@dataclass
class ProbeResult:
    connector: str
    key: str
    url: str
    status: str  # "ok" | "404" | "error" | "skipped"
    http_code: Optional[int]
    error: Optional[str]


def _discover_connector_urls() -> Dict[str, Dict[str, str]]:
    """Return {connector_module_name: {key: url, ...}} for every USA connector."""
    out: Dict[str, Dict[str, str]] = {}
    for pkg_name in (
        "src.connectors.regulatory.usa",
        "src.connectors.regulatory.usa.states",
    ):
        pkg = importlib.import_module(pkg_name)
        for mod_info in pkgutil.iter_modules(pkg.__path__):
            if mod_info.name.startswith("_"):
                continue
            full = f"{pkg_name}.{mod_info.name}"
            mod = importlib.import_module(full)
            urls = getattr(mod, "_DEFAULT_URLS", None)
            if not isinstance(urls, dict) or not urls:
                continue
            string_urls = {k: v for k, v in urls.items() if isinstance(v, str) and v.startswith("http")}
            if string_urls:
                out[mod_info.name] = string_urls
    return out


def _probe(connector: str, key: str, url: str) -> ProbeResult:
    host = urlparse(url).hostname or ""
    if host in su._NO_SCRAPE_HOSTS:
        return ProbeResult(connector, key, url, "skipped", None, "cf_gated_no_scrape_host")

    session = su.make_session(url)
    try:
        # HEAD first — cheaper and most servers honor it.
        resp = session.head(url, timeout=_PROBE_TIMEOUT, allow_redirects=True)
        # Some servers (CFPB, FFIEC) return 403/405 to HEAD but 200 to GET.
        if resp.status_code in (403, 405, 501):
            resp = session.get(
                url, timeout=_PROBE_TIMEOUT, allow_redirects=True, stream=True
            )
            resp.close()
    except requests.exceptions.SSLError as exc:
        return ProbeResult(connector, key, url, "error", None, f"ssl: {exc.__class__.__name__}")
    except requests.exceptions.ConnectionError as exc:
        return ProbeResult(connector, key, url, "error", None, f"connection: {exc.__class__.__name__}")
    except requests.exceptions.Timeout:
        return ProbeResult(connector, key, url, "error", None, "timeout")
    except requests.exceptions.RequestException as exc:
        return ProbeResult(connector, key, url, "error", None, f"{exc.__class__.__name__}: {exc}")

    code = resp.status_code
    if code == 404:
        return ProbeResult(connector, key, url, "404", code, None)
    if 200 <= code < 400:
        return ProbeResult(connector, key, url, "ok", code, None)
    return ProbeResult(connector, key, url, "error", code, f"http_{code}")


def _run_probes(urls_by_connector: Dict[str, Dict[str, str]]) -> List[ProbeResult]:
    jobs = [
        (connector, key, url)
        for connector, urls in urls_by_connector.items()
        for key, url in urls.items()
    ]
    results: List[ProbeResult] = []
    with ThreadPoolExecutor(max_workers=_PARALLEL_WORKERS) as pool:
        futures = {pool.submit(_probe, *job): job for job in jobs}
        for fut in as_completed(futures):
            results.append(fut.result())
    return sorted(results, key=lambda r: (r.connector, r.key))


def _render_table(results: List[ProbeResult]) -> str:
    rows = []
    current = None
    for r in results:
        if r.connector != current:
            rows.append(f"\n── {r.connector} ──")
            current = r.connector
        icon = {"ok": "✓", "404": "✗ 404", "error": "✗ err", "skipped": "·"}[r.status]
        detail = r.error or (str(r.http_code) if r.http_code else "")
        rows.append(f"  {icon:<8} {r.key:<32} {r.url}  {detail}")
    return "\n".join(rows)


def _summarise(results: List[ProbeResult]) -> Dict[str, int]:
    counts: Dict[str, int] = {"ok": 0, "404": 0, "error": 0, "skipped": 0}
    for r in results:
        counts[r.status] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-only", action="store_true", help="suppress stdout table")
    parser.add_argument(
        "--out-dir",
        default=str(_REPO_ROOT / "infra" / "audits"),
        help="directory to write the JSON report into",
    )
    args = parser.parse_args()

    urls = _discover_connector_urls()
    if not urls:
        print("no connectors discovered — import path broken", file=sys.stderr)
        return 2

    results = _run_probes(urls)
    summary = _summarise(results)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = out_dir / f"source_urls_{today}.json"
    report_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "summary": summary,
                "results": [asdict(r) for r in results],
            },
            indent=2,
        )
    )

    if not args.json_only:
        print(_render_table(results))
        print(
            f"\nsummary  ok={summary['ok']}  404={summary['404']}  "
            f"error={summary['error']}  skipped={summary['skipped']}"
        )
        print(f"report   {report_path}")

    return 0 if summary["404"] == 0 and summary["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
