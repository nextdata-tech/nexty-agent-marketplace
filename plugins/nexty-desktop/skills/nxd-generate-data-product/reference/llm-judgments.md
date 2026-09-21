# Agent judgements: inference landed as reviewable data

## Contents

- [The third class: inference](#the-third-class-inference)
- [Where the judging happens: agent-side while exploring, bundled once packaged](#where-the-judging-happens-agent-side-while-exploring-bundled-once-packaged)
- [The rubric is landed first, as a ruling](#the-rubric-is-landed-first-as-a-ruling)
- [The judgement model: one row per entity × criterion](#the-judgement-model-one-row-per-entity--criterion)
- [The evidence-citation obligation](#the-evidence-citation-obligation)
- [Absence takes no score — it is not a 1](#absence-takes-no-score--it-is-not-a-1)
- [Batches: many CSV files in one model dir, not table-append](#batches-many-csv-files-in-one-model-dir-not-table-append)
- [judged_by and human override](#judged_by-and-human-override)
- [Landing it](#landing-it)
- [What the transform does with it (deferred)](#what-the-transform-does-with-it-deferred)
- [Recording it in nxd_decisions](#recording-it-in-nxd_decisions)

This reference extends [derivation-plan.md](derivation-plan.md) with one case it
does not yet cover: a judgement the **agent** produced by reading an entity's
evidence — a per-candidate score, a per-ticket verdict, a per-row
classification the source does not state. Read
[Reference data: rulings that exist in no source CSV](derivation-plan.md#reference-data-rulings-that-exist-in-no-source-csv)
and [Rulings you must NOT propose](derivation-plan.md#rulings-you-must-not-propose)
first; this file only adds the third class those two sections leave open.

## The third class: inference

`derivation-plan.md` draws one hard boundary — you may propose a
**classification**, you may never propose a **measurement**. Agent judgement is
a third class that sits alongside them:

| | Classification | Measurement | **Inference (agent judgement)** |
|---|---|---|---|
| Example | merchant → category | FX rate | candidate → score 4/5 on "agentic depth"; ticket → verdict `escalate` |
| Value set | closed and observed | continuous | **closed and rubric-defined** — the score scale or verdict enum is fixed by the landed rubric |
| Produced by | one mapping over distinct values | (never produced — refused) | **per entity, by reading that entity's evidence** |
| Reviewability | each mapping row checkable on its face | a wrong rate is invisible in the data | **each judgement row checkable against the cited evidence** |
| Channel | land as data, `status = proposed` + `provenance = agent_authored`, `needs_review` for misses | `blocked` row with `provenance = deferred`, no model | **inherits the classification channel** — land as data, `proposed`, `agent_authored`, `needs_review` — **plus one new obligation below** |

Inference is proposable for the same reason classification is: the output set is
closed and each row is individually reviewable. The user can look at "this
candidate scored 4/5 on agentic depth, cited by this résumé line" and tell
whether it is right — exactly the discriminator that makes a classification
proposable and a measurement not. What makes inference its own class is *how the
value is produced*: not by mapping a column, but by an agent reading per-entity
evidence and applying a rubric. That production step is nondeterministic, so it
carries one obligation the other two classes do not — [an evidence citation into
the source](#the-evidence-citation-obligation).

## Where the judging happens: agent-side while exploring, bundled once packaged

There are **two lanes**, and which one is correct depends on whether the product
is still being explored or is being packaged as a durable artifact.

| | **Exploration lane** | **Packaged lane** |
|---|---|---|
| When | the user is iterating — trying criteria, reshaping the rubric, deciding whether the product is worth having at all | the plan is settled and the closure is being built as something that ships, is handed off, or is rebuilt later |
| Who judges | the orchestrating agent, in its own session, before the build | the transform, through the field-mapper seam |
| How it lands | judgement rows written as CSV into the closure's export | judgement rows produced by `map_inputs` during the run |
| Rerun behaviour | same rows every time — the values are frozen | the values may move; that is disclosed, not prevented |
| Cost | free, fast, no grant, no spend | a consent grant, supervisor approval, and real model spend per build |

**The exploration lane is a scaffold, not a shipping shape.** It is genuinely the
right tool while the rubric is in flux: judging fifty tickets by hand in-session
costs nothing and can be thrown away when the criteria change an hour later.

**Once the product is packaged and built, the judging must be bundled.** A
closure whose scores were produced in some earlier agent session is not
self-contained, whatever its file list says: the prompt, the reading of the
evidence, and the model identity all lived outside it, and what ships is a frozen
output nobody who receives the closure can re-derive from the closure. That is
the defect — not the nondeterminism.

**Self-containment is a property of the logic, not of the values.** A closure is
self-contained when everything needed to *run its own procedure* travels inside
it: the rubric as landed rows, the mapper spec under `contracts/`, the grant
bound to that spec by hash. Rerunning that procedure may score a borderline
entity 3 today and 4 tomorrow. **That is understood and acceptable**, and the
honest response is to disclose it — the judgement rows carry `rubric_version`,
`judged_by` and `status = proposed` precisely so a moving value stays
attributable. A closure that guarantees stable values by keeping its logic
somewhere else has bought reproducibility with the thing reproducibility was for.

The seam for the packaged lane is the field mapper, and it is the **only** one:
`nxd.experimental.field_mapper`, called through `make_call`, gated by Phase G's
consent check. See [field-mapper.md](field-mapper.md). Two things it settles that
a hand-rolled model call does not:

- **The credential never enters the closure or the blueprint.** `make_call`
  resolves it lazily, from an explicit secrets mapping or the opt-in allowlisted
  `ANTHROPIC_API_KEY` environment fallback — outside the artifact, never in
  `infra-profile.yaml`, never in `dp-blueprint.md`, never in chat. In a Desktop
  build the user authorizes the subject through the supervisor-owned loopback
  review surface plus native OS presence decision. The MCP peer does not
  receive the one-time capability, and no agent-authored field can forge the
  supervisor's subject or admission.
- **A direct provider SDK import stays denied.** `import anthropic` in
  `transform/main.py` is still a Phase E failure, and correctly so: it bypasses
  the grant check, the approval boundary, and the sanitized credential handling.
  Reach for the harness, not the SDK.

What both lanes still share, unchanged: the rubric is landed as data first, every
judgement row cites verbatim evidence, absence takes no score, and nothing is
hardcoded in transform logic.

Both lanes invert the anti-pattern that motivated the class: a generated
scoring transform that **hardcoded** a per-entity score dict as a module-level
literal. That is hardcoded even though the downstream weighting was
computed — a per-entity judgement literal in transform code is a fabrication
sitting in a governed answer, invisible and uncorrectable, the same defect as an
invented FX rate. Land the judgements as rows; keep the transform free of
judgement content.

## The rubric is landed first, as a ruling

Before any entity is judged, the rubric is landed as data by the existing
supplied-ruling flow —
[When the user supplies the ruling](derivation-plan.md#when-the-user-supplies-the-ruling).
A rubric with weighted criteria and score-banded verdicts lands as its own base
models, with an `nxd_decisions` row per ruling classified on both axes —
`status = confirmed` / `provenance = user_confirmed` for what the user supplied,
`provenance = agent_authored` for anchors **you** filled in, whatever its
`status` later becomes: an anchor nobody has looked at is `agent_authored`
at `status = proposed`. The transform reads them and
contains no weight, threshold, or verdict string:

```
scoring_rubric(criterion, weight, scale_min, scale_max, scale_5_anchor, scale_1_anchor, kind)
verdict_thresholds(verdict, min_score)
```

Landing the rubric first is what makes the judgement rows checkable: a score is
"4 out of the 1–5 the rubric declares," a verdict is "one of the enum the rubric
declares." The judge step does not invent the scale — it applies the landed one.

**Rubric version.** The rubric carries a `rubric_version`, and every judgement
row records the version it was judged under. A rubric change is a new version
(`rubric_version = N+1`), landed explicitly and visibly — never an in-place edit
that silently invalidates existing judgements. Judgements from different rubric
versions coexist; the version column keeps drift attributable.

## The judgement model: one row per entity × criterion

> **Naming.** The prose calls the concept a *judgement*; every physical artifact
> — the model name, the `data/` dir, the model variable, dimension and metric
> names — uses the American **`judgment`** spelling (`candidate_judgments`,
> `judgment_count`). The naming invariant is byte-exact, so copy the model name
> from the code below, never from the prose: `semantic_model("candidate_judgments")`
> and `data/candidate_judgments/` must match to the byte.

Judgements land **long-form**: one row per `(entity_key, criterion,
rubric_version, judged_by)`, not one wide row per entity. Long-form keeps each
score individually addressable, keeps the key clean, and lets a new criterion or
a re-judge append rows without reshaping a table.

| Column | Role | Content |
|---|---|---|
| `entity_key` | part of `primary_key()` | the base entity's source key — joins back to the facts model |
| `criterion` | part of `primary_key()` | the rubric criterion this row scores — a value from `scoring_rubric.criterion` |
| `rubric_version` | part of `primary_key()` | the rubric version this judgement was made under |
| `judged_by` | part of `primary_key()` | model identity (e.g. the judging model's name) or `human` for an override row |
| `score` | `dimension()` | within `[scale_min, scale_max]` from the rubric row for this criterion — **empty when the evidence is absent**, never the minimum |
| `verdict` | `dimension()` | one of the landed verdict enum, or empty when the criterion is not a verdict criterion |
| `evidence_field` | `dimension()` | which source field the judgement read — a column name on the facts model |
| `evidence_quote` | `dimension()` | verbatim substring of `facts[entity_key][evidence_field]`, or literal `not stated` — see below |
| `limitation` | `dimension()` | empty, or the reason `score` is empty — never empty when it is. Absence kinds: `listed_uncaptured` (the source names the thing but its value was not extracted), `not_stated` (the source says nothing). Coverage kind: `no_band_matched` (the evidence was read, but the rubric had no band for it — a gap in the rubric, not in the source) |
| `flags` | `dimension()` | free-text notes (e.g. `jd-mirror`, `no-repo`). Kept on this model only, never on the derived score sheet. |
| `status` | `dimension()` | `proposed` by default (agent-produced); `confirmed` once reviewed |

The primary key is the four-tuple `(entity_key, criterion, rubric_version,
judged_by)`. That is what lets a human override row and an agent row for the same
entity-criterion coexist as distinct rows until precedence resolves them.

Long-form rows are queryable directly: land a metric view beside the model
(`judgment_count`, `avg_score`) so `run_semantic_query` can answer "how many
entities are judged?", "which judgements are still proposed?", "average score by
criterion?" — the same way `nxd_decisions` lands `decision_count`.

## The evidence-citation obligation

This is the one obligation inference adds over classification. **Every judgement
row carries an evidence citation into the source**: `evidence_field` names the
source column read, and `evidence_quote` is a **verbatim substring** of that
field's value for that entity — normalized on whitespace and case only, never
fuzzy. When the evidence is absent, the quote is the literal `not stated`,
matching the extraction convention — and the row's `score` is then **empty**, not
the scale's minimum. See [absence](#absence-takes-no-score--it-is-not-a-1) below.

The value of the citation is that it is **mechanically verifiable**: a build can
check that `evidence_quote` really is a substring of
`facts[entity_key][evidence_field]`, turning a hallucinated citation into a
build failure — what would distinguish a judgement grounded in the data from one
the agent invented. **The obligation is on the agent to write the citation
truthfully now; the build-time substring assert that enforces it is not part of
this landing — it belongs to the derived model that consumes the judgements** (see
[What the transform does with it](#what-the-transform-does-with-it-deferred)).
This file lands the `evidence_field` and `evidence_quote` columns and states the
truthfulness obligation; it does not add the assert.

**The same citation shape is used transform-side.** A deterministic scoring model
explains itself with the identical `evidence_field` / `evidence_quote` /
`not stated` vocabulary — see
[Score explainability](derived-models.md#score-explainability-one-row-per-scored-criterion),
which also carries the substring assert this section defers. One vocabulary, two
producers: a reviewer who has learned to check an agent judgement checks a
computed score exactly the same way.

> **Normalization is whitespace and case only — never fuzzy.** When the assert is
> built, fuzzy matching is exactly where the teeth fall out: a substring check
> that tolerates paraphrase stops catching fabrication. If it fires on an honest
> paraphrase, the fix is a tighter quote (cite the verbatim span the judgement
> rests on), not a looser matcher. And the assert must special-case the
> `not stated` sentinel — that literal is almost never a substring of a real
> source value, so an unguarded check would fail every honest "absent" row.

## Absence takes no score — it is not a 1

A criterion whose evidence the agent could not find is **not assessed**. Its
`score` is empty and `limitation` names why. It is never the scale's minimum,
because the minimum is a *rating* — "called an API once" — and the agent did not
observe that. Landing a 1 for an unread criterion makes an entity nobody could
assess indistinguishable from one assessed and rated lowest, and it puts the
score in direct contradiction with the `not stated` citation sitting beside it.

The two absence kinds are not interchangeable. `listed_uncaptured` means the
source names the thing and its value was not extracted — an **incomplete
extraction**, fixable by re-reading the source. `not_stated` means the source
genuinely says nothing. Only the first is recoverable, so collapsing them costs a
reviewer the one signal that says whether re-extraction would help. Both are
absent; neither is evidence against the entity.

Downstream, an absent criterion is **excluded from the weighted sum** rather than
folded in as a zero, and the composite lands beside the fraction of rubric weight
that actually scored. The full rule, its gate consequence (`UNKNOWN`, never
`FAIL`) and the worked branch are in
[Absence is never a score](derived-models.md#absence-is-never-a-score) — it binds
agent-produced rows exactly as it binds computed ones.

**When the supplied rubric's own bottom band is worded as the absence case** — "no
referee information at all" as the 1 — this rule does not silently override the
user's wording, and the wording does not silently override this rule. The band
applies where the source was read and genuinely says none; absence still scores
empty where the evidence was never captured; and a criterion no row can reach
goes to the read-back gate rather than being resolved by the agent. The
precedence is stated once, in
[Precedence: when the supplied rubric's bottom band *is* the absence
case](derived-models.md#precedence-when-the-supplied-rubrics-bottom-band-is-the-absence-case).

## Batches: many CSV files in one model dir, not table-append

Judgements accrue over runs — a second run judges entities the first did not.
This is landed as **multiple CSV files in the one model directory**, not as an
append to a table:

```
data/candidate_judgments/
  batch-001.csv
  batch-002.csv      # a later run's new rows
```

dlt globs `*.csv` per model directory and loads every file each build under
`write_disposition="replace"`. So "append-only" here means *the agent adds a new
CSV file*; the build still replace-loads the full set into a byte-identical
table. This does **not** contradict the replace-only transform invariant — there
is no table-level append, no upsert, and no mutation of an existing file. Each
batch file is written once and never edited.

A new batch is a legitimate **input change**, not drift: the build over a
superset of judgement files is a different, still-deterministic build. The
incremental discipline (which entities a new batch covers) lives in
**nxd-run-job-loop** — this file only fixes the landing shape.

## judged_by and human override

`judged_by` carries the identity that produced the row — the judging model's name
for an agent row, `human` for an override a reviewer lands. Because it is part of
the primary key, an agent judgement and a human override for the same
`(entity_key, criterion, rubric_version)` are two distinct rows that coexist in
the landed data.

**Resolving that coexistence — human wins over agent for the same
entity-criterion — is a transform-side (derived-model) concern and is out of
scope here.** This landing only writes the `judged_by` column so the override
rows can exist and be queried; the precedence dedupe that collapses them into one
surviving score per entity-criterion is part of judgement *application*, in the
derived
score model is specified.

## Landing it

This recipe lands the **judgement model** only. Land the `scoring_rubric` and
`verdict_thresholds` models FIRST, by the supplied-ruling flow in
[When the user supplies the ruling](derivation-plan.md#when-the-user-supplies-the-ruling)
— judgements are not checkable until the rubric they cite is landed data.

The judgement model is a **base reference model**, landed exactly like
`merchant_categories` or `nxd_decisions` — authored as data, flowing through the
same dlt reader loop with no special casing (on a file connector; an
`api-source` / `db-source` closure has no such loop and needs its own
`@dlt.resource` — see step 3 below):

1. Write judgement rows to `data/<name>/batch-00N.csv` (one file per batch).
2. Declare the model in `models.py` with a metric view beside it so the rows are
   selectable:

```python
# These rows are AGENT-PRODUCED. Nothing but the descriptions tells a later
# consumer that — so the model description says it once and the score says it
# again, because a score is what gets quoted out of context.
candidate_judgments = (
    semantic_model("candidate_judgments")
    .description(
        "One row per (entity, criterion, rubric_version, judged_by). Scores "
        "are AGENT-PRODUCED against a landed rubric, not source facts; each "
        "carries a verbatim evidence citation."
    )
    .schema(
        {
            "entity_key": field(string(), primary_key(), dimension(name="judged_entity"), description="Entity this judgement is about."),
            "criterion": field(string(), primary_key(), dimension(name="judged_criterion"), description="Rubric criterion this row scores."),
            "rubric_version": field(string(), primary_key(), dimension(name="rubric_version"), description="Rubric version this judgement was made under."),
            "judged_by": field(string(), primary_key(), dimension(name="judged_by"), description="Model identity, or human for an override."),
            "score": field(
                number(),
                dimension(name="judgment_score"),
                description=(
                    "Agent-assigned score, within [scale_min, scale_max] "
                    "from the scoring_rubric row for this criterion. Not a "
                    "source value — read it with rubric_version. EMPTY "
                    "when the evidence was absent: see limitation, and "
                    "read an empty score as not-assessed, never as a low "
                    "rating."
                ),
            ),
            "verdict": field(
                string(),
                dimension(name="judgment_verdict"),
                description=(
                    "One of the landed verdict enum; empty when the "
                    "criterion is not a verdict criterion."
                ),
            ),
            "evidence_field": field(
                string(),
                dimension(name="evidence_field"),
                description=(
                    "Which source column the judgement read — a column "
                    "name on the facts model."
                ),
            ),
            "evidence_quote": field(
                string(),
                dimension(name="evidence_quote"),
                description=(
                    "Verbatim substring of the cited field's value for "
                    "this entity, or the literal 'not stated' when the "
                    "evidence is absent."
                ),
            ),
            "limitation": field(
                string(),
                dimension(name="judgment_limitation"),
                description=(
                    "Empty when the criterion was scored. Otherwise the "
                    "why 'score' is empty — set whenever it is. "
                    "'listed_uncaptured' (the source names the thing but "
                    "its value was not extracted — recoverable by "
                    "re-extracting), 'not_stated' (the source says "
                    "nothing), or 'no_band_matched' (the evidence was "
                    "read, but no band covered it — fix the rubric, not "
                    "the extraction). Absence is never scored as the "
                    "scale minimum, so a set limitation means NOT "
                    "ASSESSED, not a low rating."
                ),
            ),
            "flags": field(
                string(),
                dimension(name="judgment_flags"),
                description=(
                    "Free-text reviewer notes (e.g. jd-mirror, no-repo). "
                    "Kept on this model only, never on a derived sheet."
                ),
            ),
            "status": field(
                string(),
                dimension(name="judgment_status"),
                description=(
                    "'proposed' by default (agent-produced); 'confirmed' "
                    "once a reviewer has checked it against the citation."
                ),
            ),
        }
    )
)

candidate_judgments_metrics = semantic_view(
    "candidate_judgments_metrics", candidate_judgments
).schema(
    {
        "judgment_count": metric_field(
            number(),
            metric(
                Agg.COUNT,
                of=candidate_judgments.field("entity_key"),
                name="judgment_count",
            ),
            description="Number of judgement rows, any status.",
        ),
        "avg_score": metric_field(
            number(),
            metric(
                Agg.AVG,
                of=candidate_judgments.field("score"),
                name="avg_score",
            ),
            description=(
                "Mean agent-assigned score. Averages across criteria "
                "unless the selection groups by criterion, and mixes "
                "rubric versions unless it filters on one. Skips every "
                "row whose score is empty — absent evidence AND evidence "
                "no band covered — so group by limitation to see how "
                "much of the rubric was assessed, and which gap is the "
                "rubric's rather than the source's, before quoting this."
            ),
        ),
    }
)
```

3. `.promise(candidate_judgments)` and `.model(candidate_judgments_metrics)` in
   `spec.py`, add the model to `BASE_MODELS` in the transform. It is a base model
   like any other — a composite `primary_key()` across the four key columns, and
   the naming invariant applies unchanged.

   On an `api-source` or `db-source` closure, `BASE_MODELS` alone does not land
   it: those connectors have no `data/` reader loop, so the rows need their own
   `@dlt.resource` appended to the same `readers` list. Same carve-out and same
   reasoning as `derivation-plan.md` § "Landing it" step 3.

## What the transform does with it (deferred)

The derived score model — join facts + judgements + rubric, resolve
human-over-agent precedence, weighted-sum the scores, dense-rank, and run the
coverage / evidence-substring / enum-range asserts, landing `candidate_score`
through the DuckDB port — is **judgement application**, and it is out of scope
for this file. It is an ordinary derived model in the sense of
[derived-models.md](derived-models.md) (flat, in-run, through the port, no DDL),
with judgement-specific asserts layered on. This file specifies only the landed
*inputs* to that model — the judgement rows and the rubric. On its own, without
that derived model, the judgement rows are already queryable through their metric
view, which is enough to review coverage and proposed status.

## Recording it in nxd_decisions

An inference ruling gets an `nxd_decisions` row like any other, so a later
session can discover that an answer rests on proposed agent judgements:

- `decision_id`: the rubric's stable slug (e.g. `candidate_scoring_rubric`).
- `status`: `proposed` while the judgements are agent-produced and unreviewed;
  `confirmed` once a reviewer accepts them.
- `provenance`: `agent_authored` — the scores are yours. It stays that
  way when a reviewer accepts them: acceptance moves `status`, while
  `provenance` keeps recording who produced the values. A rubric the user
  supplied is a **separate row** at `user_confirmed`, even though the judgements
  scored against it are not — one row per ruling, each classified on its own.
- `ruling`: one sentence — "candidates scored against the agentic-engineer
  rubric v1 by agent judgement."
- `applies_to`: the models the judgements materialize in.
- `detail`: names it agent-produced, the rubric version, and that each row
  carries an evidence citation reviewers can check.

An answer built on a model this ruling `applies_to` must narrate that it rests on
**proposed agent judgements** — never as if the scores were confirmed fact. This
is the same narration `status` drives for every other ruling; agent judgement
adds no new enforcement, only the same honest disclosure.

**Not claimed: judgement quality.** The build asserts coverage, evidence
anchoring, enum and range validity, and key uniqueness — it does not assert that
a score is *correct*. Quality is governed the way `merchant_categories` quality
is: proposed status, per-row human review through a query, overrule by landing a
`human` row. Say so plainly in any answer the judgements touch.
