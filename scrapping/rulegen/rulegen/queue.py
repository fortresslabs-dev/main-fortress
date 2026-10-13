"""Resumable work queue. A run that dies at section 180 resumes at 180.

SQLite because it needs one file, no server, and safe concurrent reads.
Section-level checkpoints: a section is either fully extracted and
committed, or not started. Never half.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS sections (
    citation         TEXT PRIMARY KEY,
    title            INTEGER NOT NULL,
    part             TEXT NOT NULL,
    heading          TEXT,
    state            TEXT NOT NULL DEFAULT 'discovered',
    scope_signal     TEXT,
    scope_reason     TEXT,
    content_hash     TEXT,
    section_text     TEXT,
    source_url       TEXT,
    obligations_json TEXT,
    rule_count       INTEGER DEFAULT 0,
    model_calls      INTEGER DEFAULT 0,
    error            TEXT,
    updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS ix_state ON sections(state);

CREATE TABLE IF NOT EXISTS rules (
    rule_id       TEXT PRIMARY KEY,
    citation      TEXT NOT NULL REFERENCES sections(citation),
    payload       TEXT NOT NULL,
    route         TEXT NOT NULL,
    criticality   TEXT,
    reviewed      INTEGER DEFAULT 0,
    updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_route ON rules(route, criticality, reviewed);

CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    started_at    TEXT, finished_at TEXT,
    sections_done INTEGER DEFAULT 0, model_calls INTEGER DEFAULT 0
);
"""

# discovered -> in_scope -> extracted
#            -> out_of_scope (terminal, but revisitable if the gate changes)
#            -> failed (retryable)
STATES = ("discovered", "in_scope", "out_of_scope", "extracted", "failed")

# Columns added after the initial ship. SQLite's ALTER TABLE ADD COLUMN has
# no IF NOT EXISTS clause, so we introspect and add manually.
_LATE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("sections", "section_text", "TEXT"),
    ("sections", "source_url", "TEXT"),
    ("sections", "obligations_json", "TEXT"),
)


class Queue:
    def __init__(self, path: Path | str = "rulegen.db"):
        self.path = str(path)
        with self._conn() as c:
            c.executescript(SCHEMA)
            self._add_missing_columns(c)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _add_missing_columns(conn: sqlite3.Connection) -> None:
        """Backfill columns onto older on-disk queues without touching data."""
        for table, name, spec in _LATE_COLUMNS:
            existing = {r["name"] for r in
                        conn.execute(f"PRAGMA table_info({table})")}
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {spec}")

    # ── discovery ─────────────────────────────────────────────────────
    def add_discovered(self, discovered: list) -> int:
        """Idempotent. Re-running discovery does not reset progress."""
        added = 0
        with self._conn() as c:
            for d in discovered:
                cur = c.execute(
                    "INSERT OR IGNORE INTO sections "
                    "(citation,title,part,heading,state,updated_at) "
                    "VALUES (?,?,?,?,'discovered',?)",
                    (d.citation, d.title, d.part, d.heading, self._now()))
                added += cur.rowcount
        return added

    # ── scope ─────────────────────────────────────────────────────────
    def set_scope(self, citation: str, verdict) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE sections SET state=?, scope_signal=?, scope_reason=?, "
                "updated_at=? WHERE citation=?",
                ("in_scope" if verdict.in_scope else "out_of_scope",
                 verdict.signal, verdict.reason, self._now(), citation))

    def pending_scope(self) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM sections WHERE state='discovered' "
                "ORDER BY title, part, citation").fetchall()

    # ── extraction ────────────────────────────────────────────────────
    def next_batch(self, limit: int = 10) -> list[sqlite3.Row]:
        """Sections ready for extraction. Already-extracted work is skipped,
        which is what makes a killed run resumable."""
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM sections WHERE state IN ('in_scope','failed') "
                "ORDER BY CASE state WHEN 'in_scope' THEN 0 ELSE 1 END, "
                "title, part, citation LIMIT ?", (limit,)).fetchall()

    def commit_section(self, citation: str, content_hash: str,
                       rules: list, model_calls: int,
                       *,
                       section_text: Optional[str] = None,
                       source_url: Optional[str] = None,
                       obligations: Optional[list] = None) -> None:
        """All-or-nothing. A section is extracted or it is not.

        section_text and source_url are needed by ingest.py: it upserts
        rulegen.cfr_sections from the queue, and computes span_verified
        against the stored text so the model cannot re-assert grounding.
        obligations are grouped duty records (one per unique obligation
        under this citation); rules point at them by obligation_id.
        """
        obligations_json = None
        if obligations is not None:
            obligations_json = json.dumps(
                [o.to_dict() if hasattr(o, "to_dict") else o for o in obligations])
        with self._conn() as c:
            for r in rules:
                d = r.to_dict() if hasattr(r, "to_dict") else r
                c.execute(
                    "INSERT OR REPLACE INTO rules "
                    "(rule_id,citation,payload,route,criticality,reviewed,updated_at) "
                    "VALUES (?,?,?,?,?,COALESCE((SELECT reviewed FROM rules "
                    "WHERE rule_id=?),0),?)",
                    (d["rule_id"], citation, json.dumps(d), d["route"],
                     d.get("criticality"), d["rule_id"], self._now()))
            c.execute(
                "UPDATE sections SET state='extracted', content_hash=?, "
                "section_text=COALESCE(?, section_text), "
                "source_url=COALESCE(?, source_url), "
                "obligations_json=COALESCE(?, obligations_json), "
                "rule_count=?, model_calls=?, error=NULL, updated_at=? "
                "WHERE citation=?",
                (content_hash, section_text, source_url, obligations_json,
                 len(rules), model_calls, self._now(), citation))

    def fail_section(self, citation: str, error: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE sections SET state='failed', error=?, "
                      "updated_at=? WHERE citation=?",
                      (error[:500], self._now(), citation))

    def extracted_sections(self, citations: Optional[list[str]] = None
                           ) -> list[sqlite3.Row]:
        """Sections ready for ingest, with the text and obligations they were
        extracted from. ingest.py reads this and never re-fetches."""
        q = ("SELECT * FROM sections WHERE state='extracted' "
             "AND section_text IS NOT NULL")
        args: tuple = ()
        if citations:
            placeholders = ",".join("?" for _ in citations)
            q += f" AND citation IN ({placeholders})"
            args = tuple(citations)
        with self._conn() as c:
            return c.execute(q + " ORDER BY citation", args).fetchall()

    def rules_for(self, citation: str) -> list[dict]:
        with self._conn() as c:
            return [json.loads(r["payload"]) for r in
                    c.execute("SELECT payload FROM rules WHERE citation=? "
                              "ORDER BY rule_id", (citation,))]

    # ── review worklist ───────────────────────────────────────────────
    def review_worklist(self, limit: int = 50) -> list[sqlite3.Row]:
        """Ordered so the scarce resource - SME attention - goes first to
        the things that are actually wrong, then to Critical judgement
        calls. Auto-promoted rules are sampled, not read."""
        with self._conn() as c:
            return c.execute("""
                SELECT *, CASE
                    WHEN route='block' THEN 0
                    WHEN route='review' AND criticality='Critical' THEN 1
                    WHEN route='review' AND criticality='High' THEN 2
                    WHEN route='review' THEN 3
                    ELSE 4 END AS priority
                FROM rules WHERE reviewed=0
                ORDER BY priority, citation, rule_id LIMIT ?""",
                (limit,)).fetchall()

    def mark_reviewed(self, rule_ids: list[str]) -> None:
        with self._conn() as c:
            c.executemany("UPDATE rules SET reviewed=1, updated_at=? "
                          "WHERE rule_id=?",
                          [(self._now(), r) for r in rule_ids])

    # ── reporting ─────────────────────────────────────────────────────
    def stats(self) -> dict:
        with self._conn() as c:
            states = {r["state"]: r["n"] for r in c.execute(
                "SELECT state, COUNT(*) n FROM sections GROUP BY state")}
            routes = {r["route"]: r["n"] for r in c.execute(
                "SELECT route, COUNT(*) n FROM rules GROUP BY route")}
            row = c.execute(
                "SELECT COUNT(*) rules, SUM(reviewed) reviewed, "
                "(SELECT SUM(model_calls) FROM sections) calls FROM rules"
            ).fetchone()
            dropped = {r["scope_signal"]: r["n"] for r in c.execute(
                "SELECT scope_signal, COUNT(*) n FROM sections "
                "WHERE state='out_of_scope' GROUP BY scope_signal")}
        return {"sections": states, "rules_by_route": routes,
                "rules_total": row["rules"] or 0,
                "rules_reviewed": row["reviewed"] or 0,
                "model_calls": row["calls"] or 0,
                "dropped_by_signal": dropped}

    def rejects(self, signal: Optional[str] = None) -> list[sqlite3.Row]:
        """The reject list. Sample this by hand before trusting the gate -
        a wrong reject loses a section silently."""
        q = "SELECT * FROM sections WHERE state='out_of_scope'"
        args: tuple = ()
        if signal:
            q += " AND scope_signal=?"
            args = (signal,)
        with self._conn() as c:
            return c.execute(q + " ORDER BY citation", args).fetchall()
