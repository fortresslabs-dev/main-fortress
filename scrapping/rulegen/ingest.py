#!/usr/bin/env python3
"""Move an extracted batch from the SQLite queue into the rulegen schema.

    python ingest.py --batch golden
    python ingest.py --batch golden --dry-run
    python ingest.py --batch golden --database-url postgresql://...

**One section, one transaction.** Killed at section 40 of 90? Resume
from 40 — nothing half-written, nothing double-written.

**span_verified is computed here, not trusted from the model.** The
ck_law_span_verified CHECK constraint on rulegen.rules blocks any LAW
insert with span_verified=false; a rule whose span vanished from the
fetched text is not law and must not be stored as such. To avoid
tripping the constraint for such rules we downgrade them here to
authority_type='GUIDANCE', route='block' — the SME still sees them in
the review queue, they just can't reach public.compliance_rules.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rulegen.batches import BY_KEY
from rulegen.ecfr import parse_citation
from rulegen.evaluate import score_run, verdict as evaluate_verdict
from rulegen.golden import SECTIONS as GOLDEN_SECTIONS
from rulegen.mechanical import span_present
from rulegen.prompts import load as load_prompt
from rulegen.queue import Queue

DB_PATH = Path(__file__).resolve().parent / "rulegen.db"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
DEFAULT_CORPUS = "rulegen_2026_08"


# ── regulator mapping ────────────────────────────────────────────────
# CFR title+part → regulator. Same convention used by fortress_server's
# connector layer; extend as new parts get pulled into a batch.
_REGULATOR: dict[tuple[int, str], str] = {
    (31, "1010"): "fincen",
    (31, "1020"): "fincen",
    (12, "748"): "ncua",
    (12, "760"): "ncua",
    (12, "701"): "ncua",
    (12, "722"): "ncua",
    (12, "723"): "ncua",
    (12, "21"): "occ",
    (12, "32"): "occ",
    (12, "208"): "frb",
    (12, "323"): "fdic",
    (12, "326"): "fdic",
    (12, "339"): "fdic",
    (12, "353"): "fdic",
}


def _regulator(title: int, part: str) -> str:
    return _REGULATOR.get((title, part), "unknown")


def _title_and_part(citation: str) -> tuple[int, str]:
    """Extract (title, part) from a citation. Falls back to (31, '1010')
    if unparseable — matches the _stub() convention in batch_run.py so a
    weird citation lands somewhere findable rather than crashing."""
    try:
        spec = parse_citation(citation)
        return spec.title, spec.part or "0000"
    except ValueError:
        return 31, "1010"


# ── DB helpers ───────────────────────────────────────────────────────
def _to_libpq(url: str) -> str:
    """Rewrite a SQLAlchemy-style URL to what libpq/psycopg2 accepts.

    fortress uses ``postgresql+asyncpg://...?ssl`` in Secrets Manager;
    psycopg2 wants ``postgresql://...?sslmode=require``.
    """
    import urllib.parse as u
    p = u.urlparse(url)
    scheme = p.scheme.split("+")[0]
    q = u.parse_qs(p.query)
    if "ssl" in q or p.query == "ssl":
        q.pop("ssl", None)
        q["sslmode"] = ["require"]
    return u.urlunparse(p._replace(scheme=scheme,
                                   query=u.urlencode(q, doseq=True)))


def _connect(url: str):
    return psycopg2.connect(_to_libpq(url))


# ── upserts ──────────────────────────────────────────────────────────
def _upsert_regulation(cur, title: int, part: str) -> str:
    reg_id = f"REG-{title}-{part}"
    cur.execute(
        "INSERT INTO rulegen.regulations "
        "(reg_id, name, cfr_title, cfr_part, regulator) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (reg_id) DO NOTHING",
        (reg_id, f"{title} CFR Part {part}", title, part,
         _regulator(title, part)))
    return reg_id


def _upsert_section(cur, reg_id: str, citation: str, title: int,
                    section_text: str, content_hash: str,
                    source_url: str, kind: str = "section"
                    ) -> tuple[str, bool]:
    """Return (section_id, is_new_hash).

    On changed content_hash: mark old row superseded_at=now(), insert
    new. Never update section_text in place — history is what makes the
    nightly diff meaningful.
    """
    cur.execute(
        "SELECT id, content_hash FROM rulegen.cfr_sections "
        "WHERE cfr_title = %s AND citation = %s "
        "AND superseded_at IS NULL "
        "ORDER BY retrieved_at DESC LIMIT 1",
        (title, citation))
    row = cur.fetchone()
    if row and row["content_hash"] == content_hash:
        return str(row["id"]), False
    if row:
        cur.execute(
            "UPDATE rulegen.cfr_sections SET superseded_at = now() "
            "WHERE id = %s", (row["id"],))
    new_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO rulegen.cfr_sections "
        "(id, reg_id, cfr_title, citation, kind, section_text, "
        " content_hash, source_url, retrieved_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())",
        (new_id, reg_id, title, citation, kind, section_text,
         content_hash, source_url))
    return new_id, True


def _upsert_obligations(cur, obligations: list[dict], section_id: str,
                        reg_id: str) -> None:
    for o in obligations:
        cur.execute(
            "INSERT INTO rulegen.obligations "
            "(obligation_id, section_id, reg_id, subsection, obligation_text, "
            " criticality, applies_bank, applies_credit_union) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (obligation_id) DO UPDATE SET "
            "  section_id = EXCLUDED.section_id, "
            "  obligation_text = EXCLUDED.obligation_text, "
            "  criticality = EXCLUDED.criticality, "
            "  applies_bank = EXCLUDED.applies_bank, "
            "  applies_credit_union = EXCLUDED.applies_credit_union",
            (o["obligation_id"], section_id, reg_id,
             o.get("subsection"), o.get("obligation_text", ""),
             o.get("criticality"),
             _yes(o.get("applies_bank")),
             _yes(o.get("applies_credit_union"))))


def _yes(v: Any) -> bool:
    """Booleanise queue-payload flags. Queue stores 'Y'/'N' strings from
    the model, or already-cast booleans on the obligation rollup."""
    if isinstance(v, bool):
        return v
    return str(v).strip().upper() == "Y"


def _insert_rules(cur, rules: list[dict], section_id: str,
                  section_text: str, prompt_version: str,
                  corpus_generation: str) -> dict[str, int]:
    """Insert rules; return route-count summary.

    span_verified is computed here from section_text via the same
    mechanical.span_present the extractor uses — so a rule that made it
    into the queue with a plausible-looking span but that no longer
    matches the stored text (e.g. queue got out of sync with a fresh
    fetch) is caught here, not later.

    A LAW rule whose span is not verified would trip ck_law_span_verified
    and abort the section's transaction. Instead we downgrade the rule
    to authority_type='GUIDANCE' + route='block' + a reason line, so the
    SME sees it in review and the rest of the section still lands.
    """
    counts = {"auto_promote": 0, "review": 0, "block": 0}
    for r in rules:
        source_span = r.get("source_span", "")
        span_ok = bool(source_span) and span_present(source_span, section_text)
        route = r.get("route", "review")
        authority_type = r.get("authority_type", "LAW")
        reasons = list(r.get("reasons") or [])
        if authority_type == "LAW" and not span_ok:
            reasons.append(
                "BLOCK: span_verified=false at ingest — LAW authority "
                "downgraded to GUIDANCE so ck_law_span_verified holds")
            authority_type = "GUIDANCE"
            route = "block"
        cur.execute(
            "INSERT INTO rulegen.rules ("
            "  rule_id, obligation_id, section_id, rule_class, rule_logic, "
            "  evidence_source, result_type, threshold_value, threshold_unit, "
            "  authority_type, authority_source, source_span, span_verified, "
            "  cfr_citation, applies_bank, applies_credit_union, confidence, "
            "  route, reasons, prompt_version, corpus_generation) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "        %s, %s, %s, %s, %s, %s::jsonb, %s, %s) "
            "ON CONFLICT (rule_id) DO UPDATE SET "
            "  obligation_id = EXCLUDED.obligation_id, "
            "  section_id = EXCLUDED.section_id, "
            "  rule_logic = EXCLUDED.rule_logic, "
            "  evidence_source = EXCLUDED.evidence_source, "
            "  source_span = EXCLUDED.source_span, "
            "  span_verified = EXCLUDED.span_verified, "
            "  authority_type = EXCLUDED.authority_type, "
            "  route = EXCLUDED.route, "
            "  reasons = EXCLUDED.reasons, "
            "  prompt_version = EXCLUDED.prompt_version, "
            "  corpus_generation = EXCLUDED.corpus_generation",
            (r["rule_id"], r["obligation_id"], section_id,
             r.get("rule_class", "REGULATORY"), r["rule_logic"],
             r.get("evidence_source", ""), "PASS/FAIL",
             r.get("threshold_value"), r.get("threshold_unit"),
             authority_type, r.get("authority_source"),
             source_span, span_ok, r["citation"],
             _yes(r.get("applies_bank")), _yes(r.get("applies_credit_union")),
             r.get("confidence"), route, json.dumps(reasons),
             prompt_version, corpus_generation))
        counts[route] = counts.get(route, 0) + 1
    return counts


def _record_run(cur, batch_key: str, prompt_version: str, started_at,
                sections: int, rules: int, model_calls: int,
                recall: Optional[float], verdict_label: Optional[str]) -> None:
    run_id = (f"run-{batch_key}-"
              f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
    cur.execute(
        "INSERT INTO rulegen.runs "
        "(run_id, batch_key, prompt_version, started_at, finished_at, "
        " sections, rules, model_calls, recall, verdict) "
        "VALUES (%s, %s, %s, %s, now(), %s, %s, %s, %s, %s)",
        (run_id, batch_key, prompt_version, started_at,
         sections, rules, model_calls, recall, verdict_label))


def _score_batch(rules_by_citation: dict[str, list[dict]],
                 prompt_version: str) -> tuple[Optional[dict], Optional[str]]:
    """Score the batch against the golden set using the shipped prompt's
    evidence vocabulary.

    Returns (run_dict, note). run_dict is what evaluate.score_run returned,
    or None if scoring couldn't run (no golden sections in this batch, or
    no prompt file resolvable from prompt_version). note is a short
    human-readable reason to print with the summary.

    prompt_version resolves to a file as `<prompt_version>.yaml` under
    scrapping/rulegen/prompts/ — matching the convention used by
    batch_run.py. A missing file is not an error; the scoring is optional
    telemetry, not a gate on ingest.
    """
    golden = {c: rs for c, rs in rules_by_citation.items()
              if c in GOLDEN_SECTIONS}
    if not golden:
        return None, "no golden sections in this batch — nothing to score"

    prompt_path = PROMPTS_DIR / f"{prompt_version}.yaml"
    if not prompt_version or not prompt_path.exists():
        return None, (f"prompt file not found at {prompt_path}; "
                      f"pass --prompt-version to enable recall scoring")
    cfg = load_prompt(prompt_path)
    return score_run(golden, cfg.evidence_vocabulary), None


def _health(cur, citations: list[str]) -> list[dict]:
    if not citations:
        return []
    cur.execute(
        "SELECT * FROM rulegen.v_decomposition_health "
        "WHERE citation = ANY(%s)", (citations,))
    return [dict(row) for row in cur.fetchall()]


# ── driver ───────────────────────────────────────────────────────────
def ingest(batch_key: str, database_url: str, *,
           dry_run: bool = False, prompt_version: str = "",
           corpus_generation: str = DEFAULT_CORPUS,
           db_path: Path = DB_PATH) -> int:
    if batch_key not in BY_KEY:
        print(f"unknown batch {batch_key!r}; try: {', '.join(BY_KEY)}",
              file=sys.stderr)
        return 2
    batch = BY_KEY[batch_key]

    q = Queue(db_path)
    rows = q.extracted_sections(list(batch.citations))
    if not rows:
        print(f"no extracted sections in queue for batch {batch_key}. "
              f"Run: python batch_run.py run {batch_key}",
              file=sys.stderr)
        return 3

    conn = _connect(database_url)
    conn.autocommit = False
    started = datetime.now(timezone.utc)

    n_new = n_same = 0
    n_obligations = 0
    route_totals: dict[str, int] = {"auto_promote": 0, "review": 0, "block": 0}
    failures: list[tuple[str, str, str]] = []
    total_model_calls = 0
    rules_by_citation: dict[str, list[dict]] = {}

    for row in rows:
        citation = row["citation"]
        title, part = _title_and_part(citation)
        obligations = json.loads(row["obligations_json"] or "[]")
        rules = q.rules_for(citation)
        rules_by_citation[citation] = rules
        total_model_calls += row["model_calls"] or 0

        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            reg_id = _upsert_regulation(cur, title, part)
            section_id, is_new = _upsert_section(
                cur, reg_id, citation, title,
                row["section_text"], row["content_hash"], row["source_url"])
            _upsert_obligations(cur, obligations, section_id, reg_id)
            counts = _insert_rules(
                cur, rules, section_id, row["section_text"],
                prompt_version, corpus_generation)
            if dry_run:
                conn.rollback()
                marker = "dry"
            else:
                conn.commit()
                marker = "   "
            print(f"  {marker} {citation:<20} "
                  f"{'NEW' if is_new else 'SAME'} "
                  f"obligations={len(obligations):>2} rules={counts}")
            n_new += 1 if is_new else 0
            n_same += 0 if is_new else 1
            n_obligations += len(obligations)
            for k, v in counts.items():
                route_totals[k] = route_totals.get(k, 0) + v
        except Exception as exc:
            conn.rollback()
            failures.append((citation, type(exc).__name__, str(exc)[:200]))
            print(f"      {citation:<20} FAILED  "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)

    score, score_note = _score_batch(rules_by_citation, prompt_version)
    recall = score.get("recall") if score else None
    ok, verdict_msg = (evaluate_verdict(score) if score
                       else (True, score_note or "not scored"))
    verdict_label = "promote" if ok else "hold"

    if not dry_run and (n_new + n_same) > 0:
        cur = conn.cursor()
        _record_run(cur, batch_key, prompt_version, started,
                    n_new + n_same, sum(route_totals.values()),
                    total_model_calls, recall, verdict_label)
        conn.commit()

    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    print("\n── v_decomposition_health ─────────────────────────────")
    for h in _health(cur, list(batch.citations)):
        print(f"  {h['citation']:<20} obligations={h['obligations']:>3} "
              f"rules={h['rules']:>3} avg_rules_per_obligation="
              f"{h['avg_rules_per_obligation']}")
    conn.close()

    print(f"\nsummary: {n_new} new, {n_same} unchanged, "
          f"{len(failures)} failed")
    print(f"obligations={n_obligations}, rules={route_totals}")
    if score:
        print(f"score: recall={score['recall']} "
              f"over_extraction={score['over_extraction']} "
              f"evidence_in_vocab={score['evidence_in_vocab']} "
              f"verdict={verdict_label} ({verdict_msg})")
    elif score_note:
        print(f"score: skipped — {score_note}")
    if dry_run:
        print("(dry-run: no rows committed)")
    if failures:
        print("\nfailures:")
        for c, tp, msg in failures:
            print(f"  {c}: {tp}: {msg}")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Move an extracted batch from the SQLite queue "
                    "into the rulegen schema.")
    ap.add_argument("--batch", required=True,
                    help="batch key (see scrapping/rulegen/rulegen/batches.py)")
    ap.add_argument("--dry-run", action="store_true",
                    help="run everything but ROLLBACK each section")
    ap.add_argument("--prompt-version", default="",
                    help="stamped on rulegen.rules.prompt_version and runs")
    ap.add_argument("--corpus-generation", default=DEFAULT_CORPUS,
                    help="corpus_generation tag written to rulegen.rules")
    ap.add_argument("--database-url",
                    default=os.environ.get("DATABASE_URL", ""),
                    help="libpq DSN (defaults to $DATABASE_URL)")
    args = ap.parse_args()
    if not args.database_url:
        print("DATABASE_URL not set (env var or --database-url)",
              file=sys.stderr)
        return 2
    return ingest(args.batch, args.database_url,
                  dry_run=args.dry_run,
                  prompt_version=args.prompt_version,
                  corpus_generation=args.corpus_generation)


if __name__ == "__main__":
    raise SystemExit(main())
