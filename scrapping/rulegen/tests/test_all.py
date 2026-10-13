"""Self-tests. Run: python -m pytest tests/ -q   (or python tests/test_all.py)

The interesting tests are the ones asserting FAILURE — that regex alone
under-finds duties, and that the scope gate would drop 1020.210 without
the noun-phrase rescue. Those document why the expensive parts of the
pipeline exist. If they ever start passing, something changed upstream
and the design assumption needs re-checking.
"""
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rulegen.mechanical import split, find_numbers, span_present, stem
from rulegen.scope import assess, NOUN_DUTY_STEM, MODAL
from rulegen.discovery import parse_structure, coverage_gap, Discovered
from rulegen.queue import Queue
from rulegen.scope import ScopeVerdict
from rulegen.golden import SECTIONS
from rulegen.pipeline import extract, obligation_key, _norm as _pipeline_norm
from rulegen.ecfr import parse_citation, build_url, _find_target
from datetime import date as _date
from xml.etree import ElementTree as _ET
import importlib.util as _iu
_ingest_spec = _iu.spec_from_file_location(
    "ingest", Path(__file__).resolve().parents[1] / "ingest.py")
_ingest = _iu.module_from_spec(_ingest_spec)
_ingest_spec.loader.exec_module(_ingest)

FIVE_PILLARS = SECTIONS["31 CFR 1020.210"]["text"]
SAR = SECTIONS["31 CFR 1020.320"]["text"]
TRAVEL = SECTIONS["31 CFR 1010.410"]["text"]
NCUA = SECTIONS["12 CFR 748.1"]["text"]


# ── mechanical ────────────────────────────────────────────────────────
def test_split_keeps_parent_chain():
    units = split(FIVE_PILLARS)
    training = next(u for u in units if u.text.startswith("Training"))
    assert training.parent == "(a) (2)", (
        "the stem carrying the 'shall' lives two levels up; lose the chain "
        "and the model loses what makes this a duty")


def test_numbers_exact_on_sar():
    found = {(n["value"], n["unit"]) for n in find_numbers(SAR)}
    assert (5000.0, "usd") in found
    assert (30, "calendar_days") in found
    assert (60, "calendar_days") in found
    assert (5, "years") in found


def test_numbers_none_where_none_exist():
    assert find_numbers(FIVE_PILLARS) == [], "1020.210 has no thresholds"


def test_business_days_not_confused_with_calendar():
    found = {(n["value"], n["unit"]) for n in find_numbers(NCUA)}
    assert (5, "business_days") in found
    assert (5, "calendar_days") not in found
    assert (72, "hours") in found


def test_span_present_is_exact_not_fuzzy():
    assert span_present("Training for appropriate personnel", FIVE_PILLARS)
    assert not span_present("dual approval by two officers", FIVE_PILLARS), (
        "a fuzzy match here would destroy the only hard guarantee in the "
        "pipeline")


# ── the failures that justify the model ───────────────────────────────
def test_regex_alone_underfinds_duties():
    """There are 7 duties in 1020.210. Modal-verb regex finds at most 2."""
    units = split(FIVE_PILLARS)
    modal = [u for u in units if MODAL.search(u.text)]
    assert len(modal) <= 2, (
        f"regex found {len(modal)} duties; if this rises the section text "
        f"changed and the classifier prompt needs re-checking")


def test_five_pillars_have_no_modal_verb():
    """The specific reason. These are the most examined obligations in US
    banking and none of them contains shall or must."""
    for pillar in ("A system of internal controls",
                   "Independent testing for compliance",
                   "Designation of an individual",
                   "Training for appropriate personnel",
                   "Appropriate risk-based procedures"):
        assert pillar in FIVE_PILLARS
        assert not MODAL.search(pillar), f"{pillar!r} unexpectedly has a modal"


# ── scope gate ────────────────────────────────────────────────────────
def test_gate_keeps_the_five_pillars():
    v = assess("31 CFR 1020.210", "Anti-money laundering program requirements "
               "for banks.", FIVE_PILLARS)
    assert v.in_scope
    assert v.signal == "noun_duty_stem"


def test_gate_would_drop_1020_210_without_the_rescue():
    """Proves the rescue is load-bearing, not decoration."""
    assert not MODAL.search(FIVE_PILLARS.split("(2) Includes")[1][:400]), (
        "the enumerated pillars carry no modal verb")
    assert NOUN_DUTY_STEM.search(FIVE_PILLARS), (
        "the stem is the only thing keeping this section in scope")


def test_gate_drops_other_industries():
    v = assess("31 CFR 1023.210", "AML program for brokers or dealers.",
               "A broker-dealer shall establish an anti-money laundering program.")
    assert not v.in_scope and v.signal == "other_institution_part"


def test_gate_drops_agency_procedure():
    v = assess("31 CFR 1010.820", "Civil penalty.",
               "For any willful violation the Secretary may assess a civil "
               "penalty not to exceed $25,000.")
    assert not v.in_scope


def test_gate_keeps_borderline_rather_than_dropping():
    """A wrongly dropped section is invisible; a wrongly kept one costs a
    few model calls. The gate must lean the cheap way."""
    v = assess("31 CFR 1010.999", "Something unusual.",
               "A bank shall do a thing that is hard to classify.")
    assert v.in_scope


def test_gate_keeps_all_golden_sections():
    for citation, spec in SECTIONS.items():
        v = assess(citation, "", spec["text"])
        assert v.in_scope, f"{citation} was dropped: {v.reason}"


# ── discovery ─────────────────────────────────────────────────────────
TREE = {"type": "title", "children": [
    {"type": "part", "identifier": "1020", "children": [
        {"type": "section", "identifier": "1020.210",
         "label_description": "AML program requirements for banks."},
        {"type": "section", "identifier": "1020.230",
         "label_description": "[Reserved]"}]},
    {"type": "part", "identifier": "1023", "children": [
        {"type": "section", "identifier": "1023.210",
         "label_description": "AML program for brokers."}]}]}


def test_discovery_filters_to_requested_parts():
    found = parse_structure(TREE, 31, {"1020"})
    assert {d.section for d in found} == {"1020.210", "1020.230"}


def test_discovery_flags_reserved():
    found = {d.section: d for d in parse_structure(TREE, 31, {"1020"})}
    assert found["1020.230"].reserved
    assert not found["1020.210"].reserved


def test_coverage_gap_finds_the_unknown():
    found = parse_structure(TREE, 31, {"1020"})
    gaps = coverage_gap(found, known_citations=set())
    assert [g.section for g in gaps] == ["1020.210"], (
        "reserved sections are not gaps")


# ── queue ─────────────────────────────────────────────────────────────
def _queue():
    return Queue(Path(tempfile.mkdtemp()) / "t.db")


def test_discovery_is_idempotent():
    q = _queue()
    d = [Discovered(31, "1020", "1020.210", "H", "31 CFR 1020.210", False)]
    assert q.add_discovered(d) == 1
    assert q.add_discovered(d) == 0, "re-running discovery must not reset work"


def test_extracted_sections_are_not_re_queued():
    q = _queue()
    q.add_discovered([Discovered(31, "1020", "1020.210", "H",
                                 "31 CFR 1020.210", False)])
    q.set_scope("31 CFR 1020.210", ScopeVerdict(True, "ok", "modal", "certain"))
    assert len(q.next_batch()) == 1
    q.commit_section("31 CFR 1020.210", "hash", [], 5)
    assert q.next_batch() == [], "a killed run must resume, not restart"


def test_failed_sections_are_retried_after_pending():
    q = _queue()
    for n, state in (("210", True), ("220", True)):
        q.add_discovered([Discovered(31, "1020", "1020." + n, "H",
                                     f"31 CFR 1020.{n}", False)])
        q.set_scope(f"31 CFR 1020.{n}",
                    ScopeVerdict(True, "ok", "modal", "certain"))
    q.fail_section("31 CFR 1020.210", "timeout")
    order = [r["citation"] for r in q.next_batch()]
    assert order[0] == "31 CFR 1020.220", "fresh work before retries"


def test_review_worklist_puts_blocks_first():
    q = _queue()
    q.add_discovered([Discovered(31, "1020", "1020.210", "H",
                                 "31 CFR 1020.210", False)])
    q.commit_section("31 CFR 1020.210", "h", [
        {"rule_id": "R3", "route": "auto_promote", "criticality": "Critical"},
        {"rule_id": "R2", "route": "review", "criticality": "High"},
        {"rule_id": "R1", "route": "block", "criticality": "Medium"},
    ], 3)
    order = [r["rule_id"] for r in q.review_worklist()]
    assert order[0] == "R1", "blocks are real defects and go first"
    assert order[-1] == "R3", "auto-promoted rules are sampled, not read"


def test_reviewed_flag_survives_re_extraction():
    q = _queue()
    q.add_discovered([Discovered(31, "1020", "1020.210", "H",
                                 "31 CFR 1020.210", False)])
    rule = {"rule_id": "R1", "route": "review", "criticality": "High"}
    q.commit_section("31 CFR 1020.210", "h1", [rule], 1)
    q.mark_reviewed(["R1"])
    q.commit_section("31 CFR 1020.210", "h2", [rule], 1)
    assert q.review_worklist() == [], (
        "re-extraction must not resurrect already-reviewed rules")



# ── prompt config ─────────────────────────────────────────────────────
from rulegen.prompts import load as load_prompt
from rulegen.evaluate import score_run, verdict
from rulegen.batches import BATCHES, BY_KEY, total_citations

PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "bsa_aml.v1.yaml"


def test_shipped_prompt_is_valid():
    cfg = load_prompt(PROMPT)
    assert cfg.validate() == [], f"shipped prompt has problems: {cfg.validate()}"


def test_prompt_builds_alternating_messages():
    cfg = load_prompt(PROMPT)
    from rulegen.mechanical import Unit
    msgs = cfg.messages(Unit("(iv)", "Training...", "(a) (2)", 3), "stem")
    assert len(msgs) == len(cfg.few_shot) * 2 + 1
    roles = [m["role"] for m in msgs]
    assert roles[0] == "user" and roles[-1] == "user"
    assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1)), (
        "the API rejects consecutive same-role messages")


def test_config_validation_catches_unit_batching():
    import copy, tempfile, yaml
    raw = yaml.safe_load(PROMPT.read_text())
    raw["units_per_call"] = 5
    p = Path(tempfile.mkdtemp()) / "bad.yaml"
    p.write_text(yaml.safe_dump(raw))
    assert any("units_per_call" in i for i in load_prompt(p).validate())


def test_config_validation_catches_missing_noun_phrase_example():
    import tempfile, yaml
    raw = yaml.safe_load(PROMPT.read_text())
    raw["few_shot"] = [f for f in raw["few_shot"]
                       if f["name"] != "noun_phrase_duty"]
    p = Path(tempfile.mkdtemp()) / "bad.yaml"
    p.write_text(yaml.safe_dump(raw))
    issues = load_prompt(p).validate()
    assert any("noun_phrase_duty" in i for i in issues), (
        "removing this example collapses recall on 1020.210 and the config "
        "check is the only thing that would notice")


def test_few_shot_evidence_stays_in_vocabulary():
    cfg = load_prompt(PROMPT)
    vocab = set(cfg.evidence_vocabulary)
    for shot in cfg.few_shot:
        ev = shot["assistant"].get("evidence_source", "")
        assert not ev or ev in vocab, (
            f"{shot['name']} teaches evidence {ev!r} outside the vocabulary")


# ── evaluation gates ──────────────────────────────────────────────────
def _rule(rid, ev="training record", ab="Y", acu="Y", route="auto_promote"):
    return {"rule_id": rid, "evidence_source": ev, "applies_bank": ab,
            "applies_credit_union": acu, "route": route}


def test_good_run_promotes():
    cfg = load_prompt(PROMPT)
    run = score_run({"31 CFR 1020.210": [_rule(f"R{i}") for i in range(7)],
                     "31 CFR 1020.320": [_rule(f"S{i}", "filing record")
                                         for i in range(4)]},
                    cfg.evidence_vocabulary)
    assert verdict(run)[0]


def test_regex_level_recall_is_held():
    """A prompt that performs like bare regex must not advance."""
    cfg = load_prompt(PROMPT)
    run = score_run({"31 CFR 1020.210": [_rule("R1")]}, cfg.evidence_vocabulary)
    ok, why = verdict(run)
    assert not ok and "recall" in why


def test_charter_error_is_held():
    cfg = load_prompt(PROMPT)
    run = score_run({"12 CFR 748.1": [_rule("N1", "notification record"),
                                      _rule("N2", "notification record")]},
                    cfg.evidence_vocabulary)
    ok, why = verdict(run)
    assert not ok and "charter" in why


def test_invented_evidence_is_held():
    cfg = load_prompt(PROMPT)
    run = score_run({"31 CFR 1020.320": [_rule(f"S{i}", "the compliance binder")
                                         for i in range(4)]},
                    cfg.evidence_vocabulary)
    ok, why = verdict(run)
    assert not ok and "vocabulary" in why


# ── batches ───────────────────────────────────────────────────────────
def test_golden_batch_is_first():
    assert BATCHES[0].key == "golden", (
        "the first batch must be the one with known answers, so a prompt "
        "regression costs three sections and not fifty")


def test_batches_have_no_duplicate_citations():
    seen: dict[str, str] = {}
    for b in BATCHES:
        for c in b.citations:
            assert c not in seen, f"{c} in both {seen[c]} and {b.key}"
            seen[c] = b.key


def test_golden_batch_matches_golden_fixtures():
    for c in BY_KEY["golden"].citations:
        assert c in SECTIONS, f"{c} has no golden fixture to score against"


# ── obligation grouping (B1) ──────────────────────────────────────────
def _fake_classifier(answers_by_marker):
    """Return a canned answer for each unit marker; ignore anything else."""
    def call(unit, stem_text):
        return answers_by_marker.get(unit.marker,
                                     {"is_duty": False, "actor": "institution"})
    return call


def _duty(obligation, span, evidence="training record",
          rule_logic="IF x THEN y", criticality="Medium",
          applies_bank="Y", applies_credit_union="Y", confidence=0.9):
    return {"is_duty": True, "actor": "institution",
            "obligation": obligation, "rule_logic": rule_logic,
            "source_span": span, "evidence_source": evidence,
            "rule_class": "REGULATORY", "applies_bank": applies_bank,
            "applies_credit_union": applies_credit_union,
            "criticality": criticality, "confidence": confidence}


def test_obligation_key_normalises_whitespace_and_case():
    a = obligation_key("31 CFR 1020.210", "  Train  ALL  Personnel  ")
    b = obligation_key("31 CFR 1020.210", "train all personnel")
    assert a == b, "normalisation must collapse whitespace and case"
    c = obligation_key("31 CFR 1020.220", "train all personnel")
    assert a != c, "same text under a different citation is a different duty"


def test_extract_groups_rules_under_one_obligation():
    """Two units expressing the same duty must share an obligation. Without
    this fix N rules produce N obligations and the corpus 1:1-collapses."""
    text = ("(a) A bank shall train personnel.\n\n"
            "(b) A bank shall train personnel.")
    classify = _fake_classifier({
        "(a)": _duty(obligation="Train personnel.",
                     span="A bank shall train personnel."),
        "(b)": _duty(obligation="  train  personnel.  ",
                     span="A bank shall train personnel."),
    })
    rec = extract("31 CFR 1020.999", text, "http://example/999", classify)
    assert len(rec.rules) == 2
    assert len(rec.obligations) == 1, (
        f"expected 1 grouped obligation, got {len(rec.obligations)} — "
        f"grouping is broken and rulegen.v_decomposition_health will read "
        f"1.00 on load")
    ids = {r.obligation_id for r in rec.rules}
    assert ids == {rec.obligations[0].obligation_id}


def test_extract_keeps_distinct_obligations_apart():
    text = ("(a) A bank shall train personnel.\n\n"
            "(b) A bank shall test independently.")
    classify = _fake_classifier({
        "(a)": _duty(obligation="Train personnel.",
                     span="A bank shall train personnel."),
        "(b)": _duty(obligation="Test independently.",
                     span="A bank shall test independently.",
                     evidence="audit report"),
    })
    rec = extract("31 CFR 1020.999", text, "http://example/999", classify)
    assert len(rec.obligations) == 2, "distinct duties must not merge"


def test_extract_obligation_takes_max_criticality_across_rules():
    text = ("(a) A bank shall train personnel.\n\n"
            "(b) A bank shall train personnel.")
    classify = _fake_classifier({
        "(a)": _duty(obligation="Train personnel.",
                     span="A bank shall train personnel.",
                     criticality="Medium"),
        "(b)": _duty(obligation="Train personnel.",
                     span="A bank shall train personnel.",
                     criticality="Critical"),
    })
    rec = extract("31 CFR 1020.999", text, "http://example/999", classify)
    assert rec.obligations[0].criticality == "Critical", (
        "an obligation must be as critical as its most critical member rule")


def test_extract_obligation_widens_charter_across_rules():
    text = ("(a) A bank shall train personnel.\n\n"
            "(b) A bank shall train personnel.")
    classify = _fake_classifier({
        "(a)": _duty(obligation="Train personnel.",
                     span="A bank shall train personnel.",
                     applies_credit_union="N"),
        "(b)": _duty(obligation="Train personnel.",
                     span="A bank shall train personnel.",
                     applies_credit_union="Y"),
    })
    rec = extract("31 CFR 1020.999", text, "http://example/999", classify)
    assert rec.obligations[0].applies_credit_union == "Y", (
        "if any member rule covers a charter the obligation does too")


# ── citation forms (B3) ───────────────────────────────────────────────
def test_parse_citation_section():
    s = parse_citation("31 CFR 1010.410")
    assert s.kind == "section" and s.title == 31
    assert s.part == "1010" and s.section == "1010.410"


def test_parse_citation_appendix_short_and_long():
    for cit, exp in (("12 CFR 748 App A", "A"),
                     ("12 CFR 748 Appendix B", "B"),
                     ("12 CFR 748 App. A", "A")):
        s = parse_citation(cit)
        assert s.kind == "appendix", f"{cit!r} was parsed as {s.kind}"
        assert s.title == 12 and s.part == "748" and s.appendix == exp


def test_parse_citation_part():
    s = parse_citation("31 CFR Part 510")
    assert s.kind == "part" and s.title == 31 and s.part == "510"


def test_parse_citation_chapter():
    s = parse_citation("31 CFR Chapter V")
    assert s.kind == "chapter" and s.title == 31 and s.chapter == "V"


def test_parse_citation_rejects_garbage():
    import pytest
    try:
        parse_citation("nonsense")
    except ValueError as e:
        assert "nonsense" in str(e), "the bad citation should echo in the error"
    else:
        raise AssertionError("expected ValueError on unparseable citation")


def test_build_url_carries_the_right_query_params():
    when = _date(2026, 1, 1)
    url = build_url(parse_citation("31 CFR 1010.410"), when)
    assert "title-31.xml" in url and "part=1010" in url and "section=1010.410" in url
    url = build_url(parse_citation("12 CFR 748 App A"), when)
    assert "title-12.xml" in url and "part=748" in url and "appendix=A" in url
    url = build_url(parse_citation("31 CFR Part 510"), when)
    assert "part=510" in url and "section=" not in url
    url = build_url(parse_citation("31 CFR Chapter V"), when)
    assert "chapter=V" in url and "part=" not in url


def test_find_target_matches_each_kind():
    """No network — assemble a minimal XML that mimics eCFR's DIV shape."""
    xml = """<ROOT>
      <DIV1 TYPE="TITLE" N="31">
        <DIV3 TYPE="CHAPTER" N="V">
          <DIV5 TYPE="PART" N="510">
            <DIV8 TYPE="SECTION" N="510.101"><HEAD>Def.</HEAD><P>X.</P></DIV8>
            <DIV9 TYPE="APPENDIX" N="A"><HEAD>App A.</HEAD><P>Y.</P></DIV9>
          </DIV5>
        </DIV3>
      </DIV1>
    </ROOT>"""
    root = _ET.fromstring(xml)
    for cit, expected_tag in (
        ("31 CFR 510.101", "DIV8"),
        ("31 CFR 510 App A", "DIV9"),
        ("31 CFR Part 510", "DIV5"),
        ("31 CFR Chapter V", "DIV3"),
    ):
        node = _find_target(root, parse_citation(cit))
        assert node is not None, f"{cit!r} not located in XML"
        assert node.tag == expected_tag, (
            f"{cit!r} matched {node.tag}, expected {expected_tag}")


# ── ingest.py pure helpers ────────────────────────────────────────────
def test_ingest_regulator_maps_known_parts():
    assert _ingest._regulator(31, "1010") == "fincen"
    assert _ingest._regulator(31, "1020") == "fincen"
    assert _ingest._regulator(12, "748") == "ncua"
    assert _ingest._regulator(12, "21") == "occ"
    assert _ingest._regulator(12, "208") == "frb"
    assert _ingest._regulator(12, "326") == "fdic"
    assert _ingest._regulator(31, "9999") == "unknown", (
        "an unknown part must fall through to 'unknown' rather than blowing up")


def test_ingest_title_and_part_extracts_from_all_citation_forms():
    assert _ingest._title_and_part("31 CFR 1010.410") == (31, "1010")
    assert _ingest._title_and_part("12 CFR 748 App A") == (12, "748")
    assert _ingest._title_and_part("31 CFR Part 510") == (31, "510")
    assert _ingest._title_and_part("garbage") == (31, "1010"), (
        "unparseable citations fall back to (31, '1010') for a safe landing")


def test_ingest_to_libpq_strips_asyncpg_and_translates_ssl():
    url = _ingest._to_libpq("postgresql+asyncpg://u:p@h:5432/db?ssl")
    # scheme lost the +asyncpg suffix and ssl became sslmode=require
    assert url.startswith("postgresql://"), url
    assert "sslmode=require" in url, url
    assert "+asyncpg" not in url, url
    # non-asyncpg URLs pass through cleanly
    url2 = _ingest._to_libpq("postgresql://u:p@h:5432/db")
    assert url2 == "postgresql://u:p@h:5432/db"


def test_ingest_yes_booleanises_queue_flag_shapes():
    assert _ingest._yes("Y") is True
    assert _ingest._yes("y") is True
    assert _ingest._yes(" y ") is True
    assert _ingest._yes("N") is False
    assert _ingest._yes(True) is True
    assert _ingest._yes(False) is False
    assert _ingest._yes(None) is False


def test_ingest_score_batch_populates_recall_for_golden():
    """The runs row must carry a recall number when a batch overlaps
    the golden set — otherwise a prompt regression can't be spotted from
    the ingest log alone.
    """
    # A minimal per-citation rule shape that mimics what the queue stores.
    def _mk(rule_id, evidence):
        return {"rule_id": rule_id, "route": "auto_promote",
                "evidence_source": evidence,
                "applies_bank": "Y", "applies_credit_union": "Y"}

    rules_by_citation = {
        # golden citation with 7 expected duties — supply 7 valid rules
        "31 CFR 1020.210": [_mk(f"R{i}", "policy document") for i in range(7)],
        # a non-golden citation should be ignored by the scorer
        "31 CFR 9999.999": [_mk("R-noise", "policy document")],
    }
    score, note = _ingest._score_batch(rules_by_citation, "bsa_aml.v1")
    assert note is None, note
    assert score is not None
    assert score["scored"] == 1, "only the golden citation should score"
    assert score["recall"] == 1.0, score


def test_ingest_score_batch_skips_when_no_golden_in_batch():
    rules_by_citation = {"31 CFR 9999.999":
                         [{"rule_id": "R1", "route": "auto_promote",
                           "evidence_source": "policy document",
                           "applies_bank": "Y", "applies_credit_union": "Y"}]}
    score, note = _ingest._score_batch(rules_by_citation, "bsa_aml.v1")
    assert score is None
    assert note and "no golden sections" in note


def test_ingest_score_batch_skips_when_prompt_file_missing():
    rules_by_citation = {"31 CFR 1020.210": [
        {"rule_id": "R1", "route": "auto_promote",
         "evidence_source": "policy document",
         "applies_bank": "Y", "applies_credit_union": "Y"}]}
    score, note = _ingest._score_batch(rules_by_citation, "does_not_exist.v99")
    assert score is None
    assert note and "prompt file not found" in note


def test_queue_persists_section_text_and_obligations():
    """ingest.py needs section_text (span_verified) and obligations (grouped
    duty records) from the queue. If these are lost the ingest path breaks."""
    q = _queue()
    q.add_discovered([Discovered(31, "1020", "1020.999", "H",
                                 "31 CFR 1020.999", False)])
    obligations = [{"obligation_id": "OB-1", "citation": "31 CFR 1020.999",
                    "subsection": "(a)", "obligation_text": "Train.",
                    "criticality": "Critical", "applies_bank": "Y",
                    "applies_credit_union": "Y"}]
    rule = {"rule_id": "R1", "route": "auto_promote", "criticality": "Critical",
            "obligation_id": "OB-1"}
    q.commit_section("31 CFR 1020.999", "hash", [rule], 1,
                     section_text="A bank shall train.",
                     source_url="http://example/999",
                     obligations=obligations)
    rows = q.extracted_sections(["31 CFR 1020.999"])
    assert len(rows) == 1
    row = rows[0]
    assert row["section_text"] == "A bank shall train."
    assert row["source_url"] == "http://example/999"
    import json as _json
    assert _json.loads(row["obligations_json"]) == obligations


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in fns:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed.append((name, e))
            print(f"  FAIL  {name}\n        {e}")
    print(f"\n{len(fns) - len(failed)}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
