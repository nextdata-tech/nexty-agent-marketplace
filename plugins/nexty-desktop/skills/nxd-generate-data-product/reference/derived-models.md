# Authoring derived models and their asserts

Worked code and invariant-selection detail for Step 3a / Step 3b of
nxd-generate-data-product. SKILL.md keeps the mandatory Tier 1/Tier 2 rules; this file
shows the source-independent checks and concrete failure shapes they require.

## Contents

- [The source-checkout shim](#the-source-checkout-shim)
- [`models.py`: base and derived side by side](#modelspy-base-and-derived-side-by-side)
- [Classifying free text: two defects that ship silently](#classifying-free-text-two-defects-that-ship-silently)
- [Naming the ruling on the dimension it created](#naming-the-ruling-on-the-dimension-it-created)
- [Absence is never a score](#absence-is-never-a-score)
  - [Precedence: when the supplied rubric's bottom band *is* the absence case](#precedence-when-the-supplied-rubrics-bottom-band-is-the-absence-case)
- [Score explainability: one row per scored criterion](#score-explainability-one-row-per-scored-criterion)
- [The resource template](#the-resource-template)
- [Reading the sources yourself](#reading-the-sources-yourself)
- [Flat dicts, and why](#flat-dicts-and-why)
  - [A column that is all-None is DROPPED, not landed as nulls](#a-column-that-is-all-none-is-dropped-not-landed-as-nulls)
  - [Closing over rows: use a factory, not a default argument](#closing-over-rows-use-a-factory-not-a-default-argument)
- [The assert template](#the-assert-template)
- [Choosing the invariant](#choosing-the-invariant)
- [Chained derivations stay in memory](#chained-derivations-stay-in-memory)
- [Worked example: enrichment via a reference-data join](#worked-example-enrichment-via-a-reference-data-join)
- [A complete ingest with both kinds of model](#a-complete-ingest-with-both-kinds-of-model)

## The source-checkout shim

The Step-3 transform template opens with this shim. Copy it verbatim, directly
below the stdlib imports and **above** `import dlt`.

The shim exists for a developer running against a source checkout whose bindings
are built locally. **Installed wheels always win.** The supervisor exports
`NXD_DESKTOP_REPO_ROOT` on every run — including runs whose interpreter has
perfectly good wheels installed — so the presence of that variable says nothing
about whether the checkout should be used.

```python
import importlib.util
import sys


def _configure_nxd_imports() -> None:
    """Prefer installed nxd wheels; fall back to a source checkout only if it
    carries COMPILED bindings.

    Two guards, both load-bearing:

    * If ``nxd.core`` / ``nxd.drivers`` / ``nxd.data_product`` already resolve,
      the wheels are installed and this is a no-op. Prepending the checkout here
      would SHADOW a healthy runtime with a source tree.
    * A checkout is only usable when its compiled extension modules are present.
      A tree with just ``_bindings.pyi`` (the type stub) is not a runtime: the
      child dies with ``ModuleNotFoundError: No module named
      'nxd.core._bindings'`` the instant it imports nxd.
    """
    try:
        installed = all(
            importlib.util.find_spec(module) is not None
            for module in ("nxd.core", "nxd.drivers", "nxd.data_product")
        )
    except (ImportError, ValueError):
        installed = False
    if installed:
        return

    repo_root = os.environ.get("NXD_DESKTOP_REPO_ROOT")
    if not repo_root:
        return
    root = Path(repo_root)
    sources = (
        root / "components/nxd_py/data_product",
        root / "components/nxd_py/core",
        root / "components/nxd_py/drivers",
    )
    # Compiled bindings must exist for BOTH core and drivers, or the fallback
    # produces a tree that imports halfway and then fails. A ".pyi" stub next to
    # a missing ".so" is exactly the trap this guards.
    compiled = (
        root / "components/nxd_py/core/nxd/core",
        root / "components/nxd_py/drivers/nxd/drivers",
    )
    if not all(any(directory.glob("_bindings*.so")) for directory in compiled):
        return
    for source in reversed(sources):
        sys.path.insert(0, str(source))


_configure_nxd_imports()
```

`reversed(...)` matters: each `insert(0, ...)` pushes onto the front of
`sys.path`, so iterating in reverse leaves the three source roots in the listed
order once the loop finishes.

Never replace this with a bare `if os.environ.get("NXD_DESKTOP_REPO_ROOT"):`
prepend. That shape shipped once and broke every generated closure on a machine
whose desktop runtime pointed at a checkout without built bindings: the
transform crashed instantly, and — because the kernel host only waits for a
staging file that never appears — it surfaced as an opaque ~170s
"did not materialize staging output" timeout rather than an import error.

## `models.py`: base and derived side by side

Nothing in `models.py` marks a model as derived — the DSL, the role vocabulary
and the `.schema({...})` shape are identical. The difference is only in where
the schema keys come from (yielded dict keys, not CSV headers) and that the
primary key may be the grain-derived composite.

```python
"""Base models, a derived model, and query-time metrics for the desktop DP."""

from nxd.spec import Agg, semantic_model, semantic_view
from nxd.spec.data_types import number, string
from nxd.spec import dimension, field, join, metric, metric_field, primary_key

# BASE — landed 1:1 from data/invoices/*.csv. Key is an existing source column.
invoices = (
    semantic_model("invoices")
    .description("One row per issued invoice, landed unchanged from the export.")
    .schema(
        {
            # number() because every observed invoice_id is numeric. Check the
            # source first: a "T1257"-style ID is string(), not number().
            "invoice_id": field(number(), primary_key(), dimension(name="invoice_id"), description="Invoice key."),
            "customer": field(
                string(),
                dimension(name="customer"),
                description="Billed customer name as it appears on the invoice.",
            ),
            "start_month": field(
                string(),
                dimension(name="start_month"),
                description="First month of the invoice's service term, as YYYY-MM.",
            ),
            # No metric aggregates these, so they take dimensions rather than
            # staying bare — a roleless column never reaches describe_models.
            "term_months": field(
                number(),
                dimension(name="term_months"),
                description="Length of the service term in months. A duration, not an additive measure.",
            ),
            "amount": field(
                number(),
                dimension(name="invoice_amount"),
                description="Invoice face value. Recognised revenue is amortised over the term — see amortization_schedule.",
            ),
        }
    )
)

# DERIVED — row-EXPANDING, one row per (invoice, month) of its term. No
# data/amortization_schedule/ directory backs it. Its key is the synthetic
# composite the invoice x month grain implies, which is exactly why it is a
# derived model and not a view.
amortization_schedule = (
    semantic_model("amortization_schedule")
    .description(
        "One row per invoice x month of its service term. Derived: the source "
        "carries no such rows, they are computed by the transform."
    )
    .schema(
        {
            "schedule_id": field(string(), primary_key(), dimension(name="schedule_id"), description="Invoice-month key: <invoice_id>-<period>."),
            "invoice_id": field(
                number(),
                join(to="invoices", to_column="invoice_id"),
            ),
            "customer": field(
                string(),
                dimension(name="schedule_customer"),
                description="Billed customer, carried from the source invoice.",
            ),
            "period_month": field(
                string(),
                dimension(name="period_month"),
                description="The month this row recognises revenue for, as YYYY-MM.",
            ),
            "period_index": field(
                number(),
                dimension(name="period_index"),
                description="1-based ordinal of this month within the invoice's term.",
            ),
            # Bare is correct here: recognized_revenue aggregates this column.
            "recognized_amount": number(),
        }
    )
)

amortization_metrics = semantic_view(
    "amortization_metrics", amortization_schedule
).schema(
    {
        "recognized_revenue": metric_field(
            number(),
            metric(
                Agg.SUM,
                of=amortization_schedule.field("recognized_amount"),
                name="recognized_revenue",
            ),
            description=(
                "Revenue recognised in the selected period(s), straight-line "
                "amortised from invoice face value over the service term. "
                "Not invoiced amount — group by period_month for a schedule."
            ),
        ),
    }
)
```

In `spec.py`, both are promised the same way — that is what puts the derived
model in `model_tables`:

```python
_output = (
    data_product_output()
    .promise(invoices)
    .promise(amortization_schedule)     # derived, promised identically
    .model(amortization_metrics)
    .port("duckdb", storage(_duckdb))
)
```

A derived model that keeps its source key (a dedupe) declares that source
column as `primary_key()` — only a regrain introduces a synthetic composite.

## Classifying free text: two defects that ship silently

A derived classification over prose — a resume, a description, a note — is
usually a keyword scan. Two mistakes in that scan produce a closure that passes
every check and is still wrong, and both have shipped:

**Match tokens, never substrings.** `"api" in text` is true for *therapist*,
*rapid*, *capital*. A gate written that way passes almost everything, and its
distribution read-back shows `UNIFORM` — which reads as "everyone qualifies"
rather than "this test is broken". Split into words and compare:

```python
# WRONG — substring: "therapist" contains "api"
if any(k in text.lower() for k in ("api", "rest", "sql")):

# RIGHT — token equality against a normalized word set
words = set(re.findall(r"[a-z0-9+#.]+", text.lower()))
if words & {"api", "rest", "sql"}:
```

Multi-word phrases (`"data platform"`) still need a substring test — scope that
to the phrases that genuinely contain a space, and keep single tokens on the
word-set path.

**Every declared band needs a reachable branch, and the fall-through must be
honest.** A ladder whose last branch and whose fall-through return the same value
has a dead branch; a fall-through returning a *labelled* value claims something
about rows that merely failed every test:

```python
# WRONG — declares a band 2 it can never return, and labels an unmatched
# row as if it had matched.
if "spain" in c: return 5, "SF/Spain"
if "utc-8" in c: return 4, "US West"
if "usa"   in c: return 3, "US East/Central"
return 3, "US East/Central"          # an AU row scores 3, labelled "US"

# RIGHT — every declared band has a branch; absence and no-match are
# separated, and neither is scored. Returns (score, band_label, limitation):
# the label and the limitation are DIFFERENT columns, so a no-match sets the
# limitation and leaves the label empty rather than inventing a band name.
if not c.strip() or c.strip() == "not stated":
    return None, "", "not_stated"    # nothing was read — no score at all
if "spain" in c: return 5, "SF/Spain", ""
if "utc-8" in c: return 4, "US West", ""
if "usa"   in c: return 3, "US East/Central", ""
if words & {"brazil", "australia", "chile"}: return 2, "LatAm/AU/NZ", ""
return None, "", "no_band_matched"   # read, matched nothing — still not a 1
```

The self-check's **ABSENT** line catches half of this — a band declared in landed
policy that no row ever received. It cannot catch a mislabelled fall-through,
because that row *did* get a value. Read your own ladder: if a band appears in the
ruling you landed it needs a branch that can return it, and an unmatched row gets
no band label and a `limitation` saying why.

**Absence is not the bottom band.** The fall-through above returns `None`, not
`1`. Returning the scale's minimum for a row the ladder could not read is the
defect [Absence is never a score](#absence-is-never-a-score) exists to stop: it
is indistinguishable, in the landed column and in every metric over it, from a
row that was read in full and genuinely earned a 1.

## Naming the ruling on the dimension it created

A derived column that exists because of a **ruling** — a classification, a
reclassification, an exclusion — must say so in `description=`. This is not
documentation polish. `describe_models` is the entire surface a later consumer
sees: a `category` dimension with no description looks like it came from the
source, and the ruling behind it becomes invisible exactly when someone is
about to trust a number built on it.

```python
# DERIVED — classification. `category` exists ONLY because of the confirmed
# merchant_categories ruling; the source carries no such column.
classified_spend = (
    semantic_model("classified_spend")
    .description(
        "One row per transaction, with a category assigned by the confirmed "
        "merchant_categories ruling. Derived: the source carries no category."
    )
    .schema(
        {
            "transaction_id": field(number(), primary_key(), dimension(name="transaction_id"), description="Transaction key."),
            "merchant": field(
                string(),
                dimension(name="merchant"),
                description="Merchant name as it appears on the source transaction, unnormalised.",
            ),
            "category": field(
                string(),
                dimension(name="category"),
                description=(
                    "COGS/opex classification from the confirmed "
                    "merchant_categories mapping. Merchants the mapping does "
                    "not cover land in 'needs_review', not in a real category."
                ),
            ),
            # Bare: classified_spend_metrics below aggregates it.
            "amount": number(),
        }
    )
)

# The view that discharges the bare `amount` above — without it the column has
# no role and no metric, so it would be absent from describe_models entirely.
classified_spend_metrics = semantic_view(
    "classified_spend_metrics", classified_spend
).schema(
    {
        "total_spend": metric_field(
            number(),
            metric(
                Agg.SUM,
                of=classified_spend.field("amount"),
                name="total_spend",
            ),
            description=(
                "Total classified spend. Group by category to see the "
                "split, and check the needs_review share before quoting "
                "the headline number."
            ),
        ),
        "transaction_count": metric_field(
            number(),
            metric(
                Agg.COUNT,
                of=classified_spend.field("transaction_id"),
                name="transaction_count",
            ),
            description="Number of classified transactions.",
        ),
    }
)
```

Two rules the example encodes:

- **State the basis, not just the meaning.** "Category of the transaction" is
  useless; naming the mapping tells the reader the number is only as good as
  that ruling — and where to go to correct it.
- **Name the review bucket in the description.** A consumer who groups by
  `category` and sees `needs_review` must be able to learn what it means from
  the catalog alone. The bucket is the honest edge of the classification;
  hiding it in transform code is how an unmapped merchant silently becomes a
  rounding error in someone's total.

The same applies to a dimension whose values were **narrowed** by a ruling
(rows reclassified or excluded upstream): say what was excluded and why, since
the default read of the measure now silently reflects that decision.

## Absence is never a score

A field the derivation could not read is **absent**. Absence is a statement about
the *evidence*, never about the *entity* — so it never becomes a score, a gate
failure, or a cap. The defect this rule exists to stop has shipped: a screening
closure read `portfolio_url` as `LISTED - URL NOT CAPTURED`, treated it as "no
portfolio", and capped the candidate. The source said the portfolio exists and
the URL was not captured; the closure scored it as though the candidate had
none. That is an extraction gap converted into negative evidence about a person.

Two absence kinds, and they are not interchangeable. The upstream half of the
distinction is **required-capture** — declared in the plan as
`models[].fields[].required_capture: true` and observed, row by row, in
`build-record.json` `evidence.required_capture`
([closure-record.md](closure-record.md)). This is its scoring-side consequence:

| Kind | Source says | Meaning | Recoverable |
|---|---|---|---|
| `listed_uncaptured` | the thing exists, its value was not extracted | **incomplete extraction** — a defect in the capture step | yes, by re-extracting |
| `not_stated` | the source was asked and says nothing | genuinely absent from the source | no, without a new source |

Both are absent. Neither is evidence *against* the entity. The scored column
holds `None` for both, and the reason is carried in its own column so a reviewer
can tell an extraction bug from a real gap — and so re-extraction is targetable.

```python
# WRONG — absence collapses into the bottom band. A candidate whose URL was
# merely not captured is now indistinguishable from one with no portfolio,
# and every avg/min metric over c1_score is dragged down by a capture bug.
score = 5 if row["portfolio_url"].startswith("http") else 1

# WRONG in a different way — absence collapses into the FAIL side of a gate.
g1_pass = row["portfolio_url"].startswith("http")   # uncaptured => False

# RIGHT — read the absence kind first; it short-circuits both score and gate.
raw = row["portfolio_url"].strip()
if raw == "LISTED - URL NOT CAPTURED":
    return {"score": None, "limitation": "listed_uncaptured", "gate": "UNKNOWN"}
if raw == "not stated" or not raw:
    return {"score": None, "limitation": "not_stated", "gate": "UNKNOWN"}
return {"score": _band(raw), "limitation": "", "gate": "PASS" if ... else "FAIL"}
```

Three consequences that must hold together, or absence leaks back in:

- **A gate whose input is absent is `UNKNOWN`, never `FAIL`.** `UNKNOWN` is a
  third outcome the gate vocabulary must declare and land, alongside `PASS` and
  `FAIL`. A two-valued gate has nowhere to put absence and will always fold it
  into one side — in practice always `FAIL`, because the pass test is written as
  a positive match. This is the same shape as the supplied-rubric case where a
  gate the form never asks is `UNKNOWN` for every row.
- **An absent criterion is excluded from the weighted sum, not scored zero.**
  Re-normalize over the weights that actually scored, and land the covered-weight
  fraction beside the composite so a composite computed from half the rubric is
  visible as such. Scoring absence as `0` or as the scale minimum silently
  penalizes the entity for the capture gap, and no assert can see it afterwards.
- **A cap keyed on absence is a cap on the closure's knowledge, not the entity.**
  "No captured URL caps at `NEEDS_MORE_INFO`" is legitimate *because that verdict
  names the uncertainty*. The same cap landing `REJECT` would be absence scored
  as a fail. Read a supplied cap for which of the two it does, and raise it at the
  read-back gate when the supplied verdict is a judgement rather than a
  hold-for-information.

The composite's own description must say that absent criteria were excluded and
name the covered-weight column — otherwise a consumer reads a re-normalized
composite as if the whole rubric had been applied.

### Precedence: when the supplied rubric's bottom band *is* the absence case

A supplied rubric will sometimes define its minimum as an absence: `| C5
reference strength | 10 | two named referees with contact details | no referee
information at all |`. That reads as a direct instruction to score absence as a
1, and it collides with this section. Encode-verbatim and the absence rule are
both binding, so neither may be applied silently over the other.

**They are not actually in conflict, because they answer different questions.**
Encode-verbatim governs *what the rule says*; the absence rule governs *which
rows are eligible for it*. A band worded "no referee information at all" is a
claim about an entity the source **describes as having none** — it cannot be a
claim about a row the extraction never read, because the rubric's author was
describing candidates, not describing a gap in your capture step. So:

- **The user's wording wins wherever the source actually speaks.** A row whose
  referee field was read and is genuinely empty earns the band's 1 verbatim.
  That is the case the author had in mind, and re-deriving it as `None` would be
  overriding a rubric the user is entitled to have encoded as written.
- **The absence rule wins wherever the source is silent or uncaptured.** A row
  whose evidence is `listed_uncaptured` or `not_stated` scores `None` with a
  `limitation`, whatever the bottom band's prose says. The band's 1 is a
  *rating*; that row was never rated.

**Neither branch is taken on your own authority when the whole criterion is
absent.** When the source cannot distinguish the two — no referee column exists,
so every row is uncaptured and *nothing* can reach the band as worded — the
distinction above has no data to bite on, and scoring the fall-through as a 1
would land a fabricated 1 for every entity. That is the read-back gate's
**"Fires when"** condition (`evidence with no provenance or missing-evidence
rule`; a band no row can reach) — so **raise it**, do not resolve it. Say which
criterion is unreachable, that its bottom band cannot be told apart from a
capture gap, and offer the two readings — score the absence a 1 as written, or
land `None` + `limitation` and re-normalize. Encode whichever the user picks,
verbatim.

The gate is the resolution, not a third option: a rubric whose minimum is an
absence is precisely a missing-evidence rule the user has not yet stated, and
this file never licenses inventing one. What is **never** correct is the silent
path — folding uncaptured rows into the bottom band because the prose seemed to
allow it, which is the shipped defect this section opens with.

## Score explainability: one row per scored criterion

A scoring model that lands only the final numbers — the per-criterion scores, the
composite, the verdict — cannot answer *why*. A reviewer who wants to check one
cell has to re-read the transform and re-derive it by hand, which is exactly the
review the landed-data discipline is supposed to make unnecessary. The scores are
governed; their basis is not.

So a derived model that **scores** carries an explanation row per scored
criterion, keyed `(entity_key, criterion)`. This is the deterministic
transform-side twin of the agent-side judgement row in
[llm-judgments.md](llm-judgments.md), and it **reuses that file's evidence
vocabulary unchanged** — `evidence_field` names the source column read,
`evidence_quote` is a verbatim substring of that column's value for that entity,
or the literal `not stated` when nothing was read. Do not invent a second
citation vocabulary: a reviewer who has learned to check one must be able to
check the other the same way.

Prefer a **child explanation model** over widening the score sheet. Nine criteria
× five explanation columns is forty-five columns on the score model, most empty
per row; long-form keeps the score sheet readable, keeps each explanation
individually addressable, and lets a new criterion append rows rather than
reshape a table. It is the same long-form argument judgement rows make.

| Column | Role | Content |
|---|---|---|
| `entity_key` | `primary_key()` | joins back to the score model |
| `criterion` | `primary_key()` | the criterion this row explains — a value from the landed rubric |
| `score` | `dimension()` | the score this criterion received, or empty when absent |
| `band_id` | `dimension()` | the landed rule/band that fired — the addressable identity of the branch, not its prose |
| `evidence_field` | `dimension()` | which source column the band read |
| `evidence_quote` | `dimension()` | verbatim substring of that column's value, or `not stated` |
| `evidence_kind` | `dimension()` | `fact` when the band matched a source value directly; `inference` when it rests on a heuristic over the value |
| `limitation` | `dimension()` | empty when `score` is set; otherwise the reason it is empty. Absence kinds: `listed_uncaptured`, `not_stated` (the absence kind above). Coverage kind: `no_band_matched` — the evidence was read, but no band covered it |

> **Not to be confused with `nxd_decisions.provenance`.** `evidence_kind` is
> per-explanation-row and answers *how firmly the source supports this one
> reading* (`fact` / `inference`). `nxd_decisions.provenance` is per-ruling and
> answers *who authored the decision* (`user_confirmed`, `agent_authored`,
> `source_derived`, `deferred`) — see
> [reference/derivation-plan.md](derivation-plan.md). Different grains, disjoint
> vocabularies; never populate one from the other.

`band_id` is what makes the row queryable rather than merely readable. A prose
justification string cannot be grouped, counted, or diffed across runs; a band id
can — "how many rows did band `c1_multi_service` fire for?" is a
`run_semantic_query` away, and a band that fired for every row is the same
`UNIFORM` defect the self-check read-back reports. The ids come from the landed
rubric, never from a transform literal, for the same reason weights do.

`evidence_kind` is the honesty column. A band that matched `degree` containing
`"BSc Computer Science"` is a **fact** — the source says it. A band that inferred
"multi-service backend systems" from a skills list mentioning three technologies
is an **inference** — defensible, but the source never said it. Both are
legitimate; conflating them is not, because a reviewer triaging a disputed score
needs to know whether to argue with the reading or with the rule.

The scoring function returns the explanation alongside the score, so the two
cannot drift:

```python
def _score_c1(row: dict[str, str], bands: list[dict[str, str]]) -> dict[str, Any]:
    """One criterion's score plus the explanation that produced it."""
    raw = row["skills"].strip()
    base = {"entity_key": row["applicant_id"], "criterion": "c1_backend_depth",
            "evidence_field": "skills"}
    if raw == "not stated" or not raw:
        # Absent: no score, no band fired, and the quote takes the sentinel.
        return {**base, "score": None, "band_id": "", "evidence_quote": "not stated",
                "evidence_kind": "", "limitation": "not_stated"}
    words = set(re.findall(r"[a-z0-9+#.]+", raw.lower()))
    for band in bands:                      # landed rows, ordered by the rubric
        matched = words & set(band["tokens"].split())
        if matched:
            return {**base,
                    "score": int(band["score"]),
                    "band_id": band["band_id"],
                    # Verbatim: the span of `skills` the band actually matched,
                    # never a paraphrase and never the band's own prose.
                    "evidence_quote": next(t for t in raw.split("; ")
                                           if set(t.lower().split()) & matched),
                    "evidence_kind": band["evidence_kind"],  # 'fact' or 'inference'
                    "limitation": ""}
    # Read, matched no band. `score` is empty, so `limitation` MUST name why:
    # an empty one lands this row in the "assessed" bucket when consumers group
    # by `limitation`, counting a rubric gap as a completed assessment.
    # `no_band_matched` stays distinct from `not_stated` — the evidence WAS
    # present and readable, the rubric simply had no band for it. That is a
    # defect in the rubric, not in the source, and the two want different fixes.
    return {**base, "score": None, "band_id": "", "evidence_quote": raw,
            "evidence_kind": "", "limitation": "no_band_matched"}
```

Four asserts, on top of the Tier-1 pair, and each is falsifiable — coverage,
absence, unexplained gap, and anchoring. They are separate because each is blind
to the others' defect: coverage cannot see a row that scored an absence, the
absence assert fires only when a score and a limitation appear TOGETHER so it
cannot see a row carrying neither, and anchoring skips both because their quotes
are the exempt sentinel.

```python
def _assert_explanations(
    scores: list[dict[str, Any]], expl: list[dict[str, Any]],
    source: list[dict[str, str]]
) -> None:
    """Every scored cell is explained, and every citation is real."""
    # Coverage: read the scored cells off the SCORE SHEET, never off `expl`.
    # Deriving both sides from `expl` makes the check self-referential — a
    # criterion that scored but emitted no explanation row AT ALL is missing
    # from both sets, subtracts to nothing, and ships green. That vanished row
    # is precisely the defect this assert exists to catch.
    scored = {
        (r["entity_key"], crit)
        for r in scores
        for crit in CRITERIA               # the landed rubric's criterion ids
        if r.get(f"{crit}_score") is not None
    }
    explained = {(r["entity_key"], r["criterion"]) for r in expl if r["band_id"]}
    if scored - explained:
        missing = sorted(scored - explained)
        raise RuntimeError(
            f"{len(missing)} scored cells have no explanation row carrying a "
            f"band_id, e.g. {missing[:3]}"
        )
    # Absence: no explanation row may carry BOTH a score and a limitation.
    # That pair is the silent path this file's absence section forbids — an
    # uncaptured row folded into the bottom band — and neither check above can
    # see it: the row has a band_id, so coverage is satisfied, and its quote is
    # the exempt sentinel, so anchoring skips it. It needs its own assert.
    scored_absences = [
        (r["entity_key"], r["criterion"], r["limitation"])
        for r in expl
        if r["limitation"] and r["score"] is not None
    ]
    if scored_absences:
        raise RuntimeError(
            f"{len(scored_absences)} cells scored despite an absence limitation, "
            f"e.g. {scored_absences[:3]} — absence takes no score"
        )
    # The inverse, and just as silent: unscored with NO limitation saying why.
    # Such a row is skipped by every average (no score) yet groups under the
    # empty limitation, so a consumer counting assessed coverage reads it as
    # assessed. The unmatched-band fall-through is the path that produces it.
    unexplained_gaps = [
        (r["entity_key"], r["criterion"])
        for r in expl
        if r["score"] is None and not r["limitation"]
    ]
    if unexplained_gaps:
        raise RuntimeError(
            f"{len(unexplained_gaps)} cells are unscored with no limitation "
            f"naming why, e.g. {unexplained_gaps[:3]} — an empty score must "
            f"always say what stopped it"
        )
    # Anchoring: the quote really is a substring of the field it cites. This is
    # the substring assert llm-judgments.md defers to the consuming model — it
    # lands here. Normalize on whitespace and case ONLY; a fuzzy match that
    # tolerates paraphrase stops catching a fabricated citation.
    by_key = {r["applicant_id"]: r for r in source}
    for r in expl:
        if r["evidence_quote"] == "not stated" or not r["evidence_field"]:
            continue                      # the absence sentinel, correctly exempt
        haystack = " ".join(by_key[r["entity_key"]][r["evidence_field"]].lower().split())
        needle = " ".join(r["evidence_quote"].lower().split())
        if needle not in haystack:
            raise RuntimeError(
                f"{r['entity_key']}/{r['criterion']} cites {r['evidence_field']} "
                f"with a quote that is not a substring of it: {r['evidence_quote']!r}"
            )
```

**Both sides of the coverage assert must not come from the same list.** Reading
`scored` and `explained` off `expl` is the tempting one-liner and it is inert:
the only cells it can compare are ones that already have an explanation row, so
a criterion whose row was never emitted is invisible to it. The score sheet is
the independent witness — take the scored set from there, and the assert can
actually fail. Test it by deleting one explanation row and confirming the build
goes red; an assert that stays green under that edit is decoration.

The substring assert is what turns the citation from a claim into a check —
without it a fabricated quote ships green, and the explainability model becomes
decoration that is *more* dangerous than no explanation, because it invites trust
it has not earned. Exempt the `not stated` sentinel explicitly: that literal is
almost never a substring of a real source value, so an unguarded check fails
every honest absent row.

Note what a row with `score: None` and `limitation: "not_stated"` gives a
reviewer that a bare `1` never could: the criterion was not assessed, the reason
is an absent source field, and re-extracting that field is the fix. The
explainability model and the absence rule are the same discipline read from two
sides.

## The resource template

```python
@dlt.resource(name=duckdb.model_tables["<derived_model>"])
def <derived_model>_resource() -> Iterator[dict[str, Any]]:
    yield from derived_rows          # flat scalar dicts only


resources.append(<derived_model>_resource())
```

The rows are computed **before** the resource is defined, so the assert
(below) can run over the complete set before dlt pulls the first row. A
generator that computes lazily while dlt consumes it cannot be asserted as a
whole, and a violation would surface only after partial rows were written.

## Reading the sources yourself

dlt's `read_csv` transformer streams straight to the destination; it cannot
hand rows back to Python. Derived logic therefore needs its own read of the
base export. Use the standard library — no new dependency:

```python
import csv


def _read_source_rows(source_root: Path, model: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted((source_root / model).glob("*.csv")):
        with path.open(newline="") as handle:
            rows.extend(csv.DictReader(handle))
    return rows
```

`sorted(...)` is not cosmetic: glob order is filesystem-dependent, and an
unsorted read makes a derivation whose output depends on file arrival order.

**This applies only where there IS an export to re-read.** On an `api-source` or
`db-source` closure the fetched models have no `data/` directory — there is no
second copy of what the API returned — so a derived model computed from fetched
rows needs them captured as they stream past, and lands in its own run. That
pattern, and why the two obvious alternatives are wrong, is in
[api-source.md](api-source.md) § "Deriving from a fetched source".

`csv.DictReader` yields strings for every column. Convert **measures, not
identifiers**: `Decimal(row["amount"])` (`from decimal import Decimal`) — while
an ID stays the string `csv.DictReader` gave you unless you have checked that
every observed value is numeric. Look at the actual source values before typing
or casting an ID column: `int()` on a `"T1257"`-style key raises
`ValueError: invalid literal for int() with base 10: 'T1257'`. Use `Decimal` for
money so cent-level reconciliation asserts hold. Cast to `float` only in the final dict, since dlt
has no `Decimal` mapping here; do the reconciliation in `Decimal` **before**
that cast.

## Flat dicts, and why

The pinned desktop runtime venv has **no pyarrow**. dlt requires pyarrow to
route a pandas or polars frame to a destination, so yielding a DataFrame raises
at run time. If pandas is convenient for the computation, convert at the
boundary:

```python
yield from frame.to_dict(orient="records")
```

Every value must be a scalar. A nested dict or list makes dlt emit a child
table named `<parent>__<field>`, which appears in
`pipeline.default_schema.data_table_names()` and fails the read-back assert —
correctly, because the promised model's shape is then not what landed.

### A column that is all-None is DROPPED, not landed as nulls

dlt infers each column's type from the values it sees, so a column whose value
is `None` in every row gets no type and is silently left out of the destination
table. It warns and continues:

```
The following columns in table 'open_tickets' did not receive any data during
this load and therefore could not have their types inferred:
  - days_to_due
Unless type hints are provided, these columns will not be materialized in the
destination.
```

This is a shape that depends on the DATA rather than on the closure, which is
exactly what a promised model must not have: a source with no due dates lands a
table missing `days_to_due`, `models.py` still declares the dimension, and the
query fails at consume time on a column the catalog advertises. The read-back
assert does not catch it — the TABLE is present, only a column is missing.

Pin the shape with explicit column hints, one entry per declared column:

```python
@dlt.resource(name=table_name, columns={
    "identifier": {"data_type": "text"},
    "days_to_due": {"data_type": "bigint"},
    ...
})
def _emit() -> Iterator[dict[str, Any]]:
    yield from rows
```

Hints do NOT create a table for a resource that yields zero rows — that lands
nothing at all. If that is a valid state for an append-only or human-review
physical output, whether base or derived, declare it explicitly in
`transform/main.py`:

```python
# `optional_model` is already listed in BASE_MODELS or DERIVED_MODELS.
PHYSICAL_MODELS = BASE_MODELS + DERIVED_MODELS
OPTIONAL_EMPTY_MODELS = ("optional_model",)
```

The tuple is metadata, not a way to hide a failed resource: every name must be
in `PHYSICAL_MODELS`, the resource must genuinely be allowed to yield zero
rows, and required outputs stay outside it. Register the optional model with
`.model(optional_model)` in `spec.py`, not `.promise(optional_model)`, because
the latter asks the kernel to verify a table that dlt correctly omitted. The
read-back assert must compare `actual` with the required physical tables and
allow only the missing names in `OPTIONAL_EMPTY_MODELS`; unexpected tables and
missing required tables still fail. Do not manufacture a placeholder row.

When the optional table is absent, it remains in the compiled catalog if it was
registered with `.model(...)`, so `describe_models` can explain the surface. The
closure contract requires a semantic view over that table to surface the missing
physical table rather than report a fabricated zero; the self-check does not
execute that semantic query. Once a row exists, the same model and semantic
view are queryable without any spec change.

### Closing over rows: use a factory, not a default argument

`dlt.resource` inspects the generator's signature and treats its parameters as
configuration, so the obvious `def _emit(rows=rows)` raises before any row is
yielded:

```
ValueError: mutable default <class 'list'> for field rows is not allowed:
use default_factory
```

Bind through an enclosing function instead:

```python
def _resource_for(table_name: str, rows: list[dict[str, Any]]):
    @dlt.resource(name=table_name, columns=COLUMN_HINTS[table_name])
    def _emit() -> Iterator[dict[str, Any]]:
        yield from rows
    return _emit()
```

## The assert template

```python
def _assert_<derived_model>_reconciles(
    source: list[dict[str, str]], derived: list[dict[str, Any]]
) -> None:
    """<one sentence naming the invariant, not the implementation>."""
    expected_rows = sum(int(row["<term_column>"]) for row in source)
    if len(derived) != expected_rows:
        raise RuntimeError(
            f"<derived_model> produced {len(derived)} rows, expected "
            f"{expected_rows} (one per <grain unit>)"
        )
    keys = [row["<key_column>"] for row in derived]
    if len(set(keys)) != len(keys):
        raise RuntimeError("<derived_model> has duplicate <key_column> keys")
```

Raise `RuntimeError` carrying the actual-vs-expected numbers. The failure is
read from the supervisor's run log, so the message is the entire diagnostic —
`assert` with no message, or a bare boolean, wastes the one signal available.

## Choosing the invariant

An assert earns its place by being **falsifiable if the derivation is wrong**.
Restating the transform's own arithmetic (`sum(outputs) == sum(outputs)`)
proves nothing and gives false confidence.

**This is not a menu.** The table below is a floor, not a set of options to
pick the most convenient one from. Picking the invariant that is easiest to
satisfy is how a wrong derivation ships green.

### Tier 1 — mandatory for EVERY derived model, no exceptions

1. **Declared-key uniqueness.** `len(set(keys)) == len(keys)` over the
   complete derived set. This is what upgrades the derived-model key gate from
   a claim to a proof: the grain-derived composite is *asserted* unique, never
   assumed.
2. **Row count computed from the grain**, checked against source rows you
   **read independently** from the base CSVs — not against a count the
   derivation itself produced. State the grain in one sentence, turn it into an
   arithmetic expectation over the source rows, and compare.

### Tier 2 — additionally mandatory when the model carries a MEASURE column

If the derived model has any column a metric will aggregate — an amount, a
quantity, a duration, anything summable — you MUST also reconcile the measure:

- **The signed measure total of the derived rows must equal the signed total
  read independently from the base CSVs**, with **every intentional divergence
  itemized as its own named term**. Not "approximately", not "within
  tolerance" — an equation whose terms you can name:

  ```
  derived_total == source_total
                   - refund_pairs_total      # 18 pairs, each an exact negative
                   - transfer_rows_total     # internal transfers, not expenses
  ```

- **Signed, not absolute.** Sum the values with their sign. A refund that is
  the exact negative of a charge nets to zero only if you never took
  `abs()`; an unsigned total hides exactly the error this catches.
- **Use `Decimal`, not `float`.** Cent-level reconciliation does not survive
  binary floating point.
- **Reconcile per source currency, BEFORE any FX conversion.** Converting
  first folds the rate into the comparison and makes a wrong rate
  unfalsifiable. Reconcile each currency against its own source total, then
  convert.

An unreconciled measure is the failure mode this rule exists for: a
classification-totality assert can pass — every row bucketed, counts summing
perfectly — while every monetary answer is silently overstated, because
totality says nothing about magnitude.

### An itemized exclusion means you have a REMOVAL

If the reconciliation needs a `- something_total` term, the derivation is
**removing rows or value**, not merely enriching. Say so out loud:
**reclassify it as a removal** and apply the removal invariants below **in
addition** to the ones you already have. A derivation described as an
"enrichment" whose totals only balance after subtracting some rows is a removal
wearing the wrong label — and it will carry an enrichment's asserts, which
cannot test the thing it actually does.

### Per-shape invariants (on top of Tier 1 and Tier 2)

| Derivation shape | Invariant that actually tests it |
|---|---|
| **Row-preserving enrichment** (adds columns, same rows) | output count **==** independently-read source count, AND the signed measure total is preserved exactly — no row lost, no value changed |
| **Expansion** (one row → N) | row count equals the summed term; each parent's parts sum back to the parent's exact total |
| **Removal** (dedupe, pair cancel) | the dropped count matches the removal rule's arity (pairs drop an even count); a net-zero removal leaves the source total unchanged; no row the rule should have removed survives |
| **Collapse** (regrain) | every source row is accounted for in exactly one output group; the measure total is preserved across the regrain |
| **Classification** | every source row lands in exactly one bucket, `needs_review` included; the per-bucket counts sum to the source count. **Never sufficient on its own** — pair it with the Tier-2 measure reconciliation |
| **Scoring** | every scored cell has an explanation row carrying a `band_id`, and every `evidence_quote` is a verbatim substring of the field it cites — see [Score explainability](#score-explainability-one-row-per-scored-criterion). Additionally: no scored cell whose `limitation` is set, since [absence is never a score](#absence-is-never-a-score) |

## Chained derivations stay in memory

A derivation chain — enrich, then collapse — shares the **in-memory Python row
lists**: the collapse consumes the enrichment's list directly, as a plain
`list[dict]` argument. **Never read a derived model back from the output port
mid-run.** The rows are not queryable until `pipeline.run(...)` completes and
the supervisor promotes the artifact, so a mid-run read either fails or reads a
stale prior run's table.

## Worked example: enrichment via a reference-data join

The most common real shape: the same entities, with columns added from a
landed reference model. Every source row survives; nothing is aggregated.

```python
def _classify_spend(
    txns: list[dict[str, str]], rulings: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """One output row per source transaction, plus category / month."""
    by_merchant = {r["merchant"]: r["category"] for r in rulings}
    return [
        {
            # ID passes through as the source string — no int() cast. These
            # keys are "T1257"-style; casting would raise.
            "txn_id": t["txn_id"],
            "merchant": t["merchant"],
            # Uncovered merchants get an explicit bucket, never a silent drop.
            "category": by_merchant.get(t["merchant"], "needs_review"),
            "month": t["txn_date"][:7],           # derived column, not DATE_TRUNC
            "currency": t["currency"],
            "amount": float(Decimal(t["amount"])),  # signed; refunds stay negative
        }
        for t in txns
    ]


def _assert_classified_spend(
    txns: list[dict[str, str]], derived: list[dict[str, Any]]
) -> None:
    """Row-preserving enrichment: same rows, same signed money, one bucket each."""
    if len(derived) != len(txns):                       # Tier 1: grain row count
        raise RuntimeError(
            f"classified_spend produced {len(derived)} rows, expected "
            f"{len(txns)} (enrichment is row-preserving)"
        )
    keys = [row["txn_id"] for row in derived]           # Tier 1: key uniqueness
    if len(set(keys)) != len(keys):
        raise RuntimeError("classified_spend has duplicate txn_id keys")

    # Tier 2: signed measure reconciliation, per currency, pre-FX, in Decimal.
    for currency in {t["currency"] for t in txns}:
        source_total = sum(
            Decimal(t["amount"]) for t in txns if t["currency"] == currency
        )
        derived_total = sum(
            Decimal(str(r["amount"])) for r in derived if r["currency"] == currency
        )
        if derived_total != source_total:               # no exclusion terms:
            raise RuntimeError(                          # nothing is removed here
                f"classified_spend {currency} total {derived_total} != "
                f"source {source_total}"
            )

    unbucketed = [r for r in derived if not r["category"]]
    if unbucketed:
        raise RuntimeError(f"{len(unbucketed)} rows landed in no category bucket")
```

Note what the reconciliation says here: **zero exclusion terms**, because this
derivation removes nothing. The moment a real requirement adds one — "net out
refund pairs" — the equation grows a named `- refund_pairs_total` term, and
per the rule above the model is reclassified as a **removal** and gets the
removal invariants too.

## A complete ingest with both kinds of model

Take the Step-3 template in SKILL.md and insert the derived block between the
base-reader loop and `pipeline.run(...)` — that is the whole change:

```python
    # Base models: one dlt CSV reader per data/<model>/ directory.
    for model in BASE_MODELS:
        reader = filesystem(
            bucket_url=str(source_root / model), file_glob="*.csv"
        ) | read_csv()
        resources.append(reader.with_name(duckdb.model_tables[model]))

    # Derived models: computed here, yielded into the SAME run.
    invoice_rows = _read_source_rows(source_root, "invoices")
    schedule = _amortize_invoices(invoice_rows)       # pure, deterministic
    _assert_schedule_reconciles(invoice_rows, schedule)

    @dlt.resource(name=duckdb.model_tables["amortization_schedule"])
    def amortization_schedule_resource() -> Iterator[dict[str, Any]]:
        yield from schedule

    resources.append(amortization_schedule_resource())

    pipeline.run(resources, write_disposition="replace")
```

One pipeline, one `pipeline.run(...)`, one `write_disposition="replace"`. Base
readers and derived resources are peers in the same list — that is what keeps
every write on the port path and the DDL ban intact. The optional-aware
read-back assert then covers base and derived tables alike, allows only an
explicitly empty optional resource to be absent, and catches a stray
`parent__field` child table from a nested value.
