# rulegen

Turns a CFR section into testable compliance rules. Reproduces, in code, the
process used by hand on five BSA/AML sections.

```bash
pip install requests
export ANTHROPIC_API_KEY=sk-ant-...     # optional
python run.py                            # all five golden sections
python run.py "31 CFR 1020.210"          # one section
python run.py --recheck                  # simulate tomorrow night
```

Without a key it still runs and shows the mechanical layer plus the measured
gap the model has to close.

## What was mechanised and what was not

The hand process had seven steps. Four are mechanical, three are not.

| Step | Where it lives |
|---|---|
| Split at `(a)(1)(i)` markers | `mechanical.split` — regex |
| Pull numbers out | `mechanical.find_numbers` — regex |
| Check a quote is really in the text | `mechanical.span_present` — regex |
| Decide which units impose a duty | `judgement` — **model** |
| Group units into obligations | `judgement` — **model** |
| Compose the IF/THEN | `judgement` — **model** |
| Name the evidence artifact | `judgement` — **model** |

Regex was measured at **54% recall** across the five sections (14 duties of 26).
On 31 CFR 1020.210 specifically it finds 1 of 7, because the five pillars are
noun phrases — *"A system of internal controls"*, *"Training for appropriate
personnel"* — hanging off a `shall` forty words earlier. That is normal
regulatory drafting, not an edge case, and no regex reaches it.

## Nothing is hardcoded per section

There is no threshold table keyed by citation. A curated table cannot cover 250
sections, and worse, when a regulation changes the table becomes authoritatively
wrong and starts blocking the correct rule.

Instead, **the span is the anchor.** Each rule stores the verbatim quote it came
from. Tonight, re-fetch and ask one question: is that string still present?

```
hash same       -> nothing to do. zero model calls.
hash different  -> per rule, is its span still there?
                     yes -> still grounded, note the section moved
                     no  -> flag, re-classify that unit
                   re-run the section to catch ADDED duties
                   open a change record. never rewrite a rule.
```

This works on a section nobody has curated, which is what lets it cover all of
BSA/AML rather than the parts someone had time to register.

| | Curated table | Span anchoring |
|---|---|---|
| Coverage | only what is registered | every rule, automatically |
| Maintenance | grows with the corpus | none |
| New section | unhandled | handled |
| Law changes | table goes wrong | fires correctly |
| False negatives | possible | impossible — text moved, hash moved |

The only hardcoded list is `NEVER_AGAIN` in `pipeline.py`: four values that have
been wrong in production. It grows when something goes wrong, never with the
corpus.

## The few-shot example that carries the file

`judgement.FEW_SHOT[0]` teaches the model that a noun phrase can be an
obligation:

```
UNIT: (iv) Training for appropriate personnel; and
-> is_duty: true
```

Trained only on `shall`-style examples, a model repeats the regex failure and
finds 1 duty in 1020.210. That single example is the difference.

## Measuring a run

`golden.py` holds the hand-derived answers for five sections — expected duty
counts, paths, evidence artifacts and numbers. Run against it and you can see
whether a prompt change helped:

- **fewer duties than expected** — the prompt is too strict, or missing the
  noun-phrase case
- **more duties than expected** — it is promoting scaffolding like
  *"(2) Includes, at a minimum:"*
- **wrong charter on 12 CFR 748.1** — it applied the Chapter X default where
  Title 12 flips
- **`(e)` and `(f)` merged on 1010.410** — the defect the pipeline exists to
  catch, now in the model instead of the analyst

Two sections carry deliberate traps, documented in `golden.py`.

## Adding sections

Nothing to configure. Pass any citation and its text:

```python
from rulegen.pipeline import extract
from rulegen.judgement import anthropic_api

record = extract("31 CFR 1010.230", section_text, url, anthropic_api())
```

Cost is one model call per unit on first encounter, and zero every night after
unless the text moves.

## Files

```
rulegen/mechanical.py   split, numbers, span check — no model
rulegen/judgement.py    prompt, few-shot, API and Bedrock transports
rulegen/pipeline.py     extract, gates, span-anchored recheck
rulegen/golden.py       five hand-derived sections as the eval set
run.py                  CLI
```
