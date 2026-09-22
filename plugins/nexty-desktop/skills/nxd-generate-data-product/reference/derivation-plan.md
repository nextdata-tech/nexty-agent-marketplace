# Planning the derivation — from questions to required models

## Contents

- [Why this step exists](#why-this-step-exists)
- [Backward-chain from the questions](#backward-chain-from-the-questions)
- [A worked chain](#a-worked-chain)
- [Base or derived? the decision test](#base-or-derived-the-decision-test)
- [Reference data: rulings that exist in no source CSV](#reference-data-rulings-that-exist-in-no-source-csv)
- [The decisions model](#the-decisions-model)
- [Rulings you must NOT propose](#rulings-you-must-not-propose)
- [When the user supplies the ruling](#when-the-user-supplies-the-ruling)
- [Confirm the plan before authoring](#confirm-the-plan-before-authoring)

## Why this step exists

The semantic layer used by this desktop generation path is deliberately narrow.
A dimension is a pointer at a physical column — there is no dimension expression
surface, so no `CASE WHEN`, no `DATE_TRUNC`, no concatenation. A normal metric
is one aggregate over one column; the broader warehouse-backed builder DSL also
has `Agg.EXPRESSION`, but that is outside this desktop closure pattern and this
skill does not use it as a derivation substitute. That expression slot is a
port-level aggregate SQL expression, not a row-level surface and not a default
filter, so it cannot create dimensions, generate or remove rows, classify
records, normalize values for reuse, express ratios, or hide column arithmetic
such as net revenue. Nothing in the layer generates a row or removes one.

Every one of these is therefore **not** a query-time concern:

| Ruling | Why the semantic layer can't express it |
|---|---|
| "AWS spend is COGS, not opex" | needs a classification column that does not exist |
| "transfers are never expenses" | needs a filtered metric, or a row that isn't there |
| "amortize prepayments over the term" | needs one input row to become N output rows |
| "dedupe refund pairs" | needs rows removed — a self-anti-join |
| "normalize everything to USD" | needs an expression over an FX rate |
| "report monthly" | needs a month column, not a `DATE_TRUNC` at query time |

Each has to land as a physical column or row that the transform writes. The
step that decides *which* models must exist is this one, and nothing else in
the pipeline does it: inference reads the source you hand it, and code
generation places the models you name. If you skip planning, you generate a
faithful model of the raw rows that cannot answer the question that was asked.

## Backward-chain from the questions

Work **right to left** — from what the user wants to know, back to what must be
materialized. For each question:

1. **Name the answer's shape.** One number? A number per group? A ranking?
   Write it as a measure over a dimension: "net burn by month".
2. **Name the measure's column.** The measure is `Agg(one column)` and nothing
   more. Ask which single physical column it sums or averages.
   - "…minus…" or "…divided by…" **always** forces a derived column in this
     generation path: the closure needs a physical, reusable column or
     reporting-grain row, not a one-off port expression. `Agg.EXPRESSION` in
     the warehouse-backed builder surface is outside this desktop path and does
     not replace this derivation step.
   - "…where…" or "…but only…" no longer settles it by itself — the query
     grammar ships ANDed `filters[]`, so a scoping clause may be a query-time
     filter. Route it through the **Omission Test** (stated canonically in the
     **nxd-run-job-loop** skill, `reference/query-grammar.md`):
     if a consumer querying with **no filters** would get a *wrong* number, it is
     a standing ruling and a derived model must produce the column; if they would
     get a merely *broader* number, it is a per-question filter — derive nothing.
3. **Name the dimension's column.** Same test. "By month" over a date column is
   a derived column, not a query-time truncation.
4. **Ask what the column needs.** A classification needs a ruling. A ruling
   needs a rule source. A rule source may not exist — see
   [reference data](#reference-data-rulings-that-exist-in-no-source-csv), and
   [rulings you must NOT propose](#rulings-you-must-not-propose) when the
   missing piece is a number rather than a judgement.
5. **Stop when you reach a real source column.** Every chain terminates at a
   source column, at reference data the user must confirm, or at a measurement
   you cannot supply — in which case that chain stops **BLOCKED**, and the rest
   of the plan proceeds without it.

Collect the models the chains demand, then de-duplicate: several questions
usually converge on the same one or two derived models.

## A worked chain

> **Question:** "What is my burn multiple?"

- burn multiple = net burn ÷ net new ARR — **a ratio, which no metric can
  express.** So it is not one metric; it is two metrics the agent divides when
  presenting, or a derived column at the reporting grain.
- **net burn** = cash out − cash in, per month → needs a signed `amount` column
  and a `month` column at monthly grain. The source has a transaction date, not
  a month → **derived column**.
- cash out excludes internal transfers → "transfers are never expenses" is a
  standing ruling, not a scoping clause: a consumer querying spend with no
  filters would count transfers as spend — a confidently wrong number. So the
  transform **drops transfer rows from the derived model**, making the default
  read right. **In addition**, land the classification as a dimension so
  transfers stay queryable. Landing only the dimension and expecting the query
  to filter on it is a governance hole — the ruling would then depend on every
  caller remembering it.
- expenses must split **COGS vs opex** → needs a `category` column that no
  source row carries → **derived column** from a ruling.
- that ruling is a **merchant → category** mapping → exists in no source CSV →
  **reference data the user must confirm** (next section).
- merchants the mapping does not cover must not silently vanish or land in the
  wrong bucket → they get an explicit **`needs_review`** category, and the
  transform asserts every source row landed in exactly one bucket.

**Resulting model plan:**

| Model | Kind | Grain | Why |
|---|---|---|---|
| `transactions` | base | one source transaction | the pristine export |
| `merchant_categories` | base (reference) | one merchant | the user-confirmed ruling, landed as data |
| `classified_spend` | derived | transaction × category | adds `category` (incl. `needs_review`), `month`, signed `amount` |
| `spend_metrics` | view | — | `SUM(amount)` over the `category` / `month` dimensions |

Note what the plan makes possible: "which merchants are unclassified?" is now a
query, not a support ticket, because `needs_review` is a value in a dimension.

## Base or derived? the decision test

| The model's rows are… | Kind |
|---|---|
| exactly the supplied export's rows, 1:1 | **base** — `data/<name>/`, key from a source column |
| the same entities, with new columns (classification, FX normalization, month column) | **derived**, keeps the source key |
| the same entities minus some (dedupe, filter) | **derived**, keeps the source key |
| one input row expanded into N (amortize, unpivot) | **derived**, new composite key from the grain |
| many input rows collapsed into one (monthly regrain) | **derived**, new composite key from the grain |
| a judgement the user supplied, not the system | **base reference model** — see below |

A derived model needs a real reason to exist. If a question is answerable by a
metric over an existing column grouped by an existing column — optionally scoped
by ANDed `filters[]`, `order_by[]` and `limit` — do **not** derive anything: add
the view and stop. The **Omission Test** is the rule that decides this; it is
stated once in the **nxd-run-job-loop** skill, `reference/query-grammar.md`.
A scoping constraint the grammar cannot express is still not a reason to derive
a single-use column — that reference lists the sanctioned patterns.

## Reference data: rulings that exist in no source CSV

Merchant→category rules, account mappings, department rollups: these are
judgements, and they are not in the export. They are also the most tempting
thing to hardcode, because a dict literal in the transform "just works".

**Never do that.** A constant baked into generated transform code is an
unreviewable fabrication sitting inside a governed answer: the user cannot see
it, cannot query it, cannot correct it, and cannot tell your guess apart from
their own policy. A wrong rate silently changes every number downstream.

This section covers rulings you *may* propose when no user is available to
confirm. Not every missing ruling qualifies: an FX rate, a discount rate, a tax
rate is a **measurement**, and a measurement is never proposed — see
[Rulings you must NOT propose](#rulings-you-must-not-propose) for the
discriminator before you land anything as PROPOSED.

Handle a proposable ruling this way:

1. **Surface it as a gap, with your proposal.** State plainly that the ruling is
   not in the data, show the distinct values it must cover (every distinct
   merchant, every currency present), and propose a mapping if you have a
   defensible one.
2. **Get explicit confirmation.** The user confirms, edits, or supplies their
   own. Use `AskUserQuestion` when the choice set is small and closed.

   **When no user is available to confirm** — an autonomous or batch run —
   confirmation is not optional-away, it is *deferred*. Do not skip the ruling,
   do not hardcode it, and do not stall. Instead:

   - **Land the proposed mapping as its own model anyway**, exactly as step 3
     describes. A proposed ruling that is queryable data can be reviewed and
     corrected; one that never got landed cannot.
   - **Route every uncovered value to `needs_review`.** Never widen a proposed
     rule to swallow values it does not actually cover.
   - **Record each unconfirmed ruling as a row in the closure's
     [decisions model](#the-decisions-model)** with `status = proposed` AND
     `provenance = agent_authored`: the ruling, what it applies to,
     and the evidence you based it on. One row per ruling. Both axes are
     required on every row — settled-or-not and authored-by are separate
     questions, and the self-check fails a ledger missing either. A ledger that is itself landed data can be queried and corrected
     by the same governed path as every other answer — which a file at the
     closure root cannot. Never write a `DECISIONS.md`.
   - **State in the handoff that the rulings are PROPOSED, NOT CONFIRMED**, and
     name `nxd_decisions` as the model to query to review them. An answer built
     on a proposed ruling must never be narrated as if the user had agreed to
     it.
3. **Land it as its own model.** Write the confirmed mapping to
   `data/<name>/<name>.csv` and promise it as a base model like any other —
   `merchant_categories(merchant, category)`, `account_departments(account,
   department)`. It gets a validated key (the thing it maps from) exactly like
   any base model. A measurement the user *does* supply — `fx_rates(currency,
   month, rate)` they hand you — lands here too, by this same flow; what you
   must never do is author those numbers yourself.
4. **Join to it in the transform.** The derived model looks the ruling up from
   the landed rows. The transform reads a table; it does not embed a policy.
5. **Cover the misses explicitly.** Any source value the mapping does not cover
   gets an explicit bucket (`needs_review`, `unmapped`) — never a silent drop
   and never a default that quietly resembles a real category. Assert
   totality: every source row landed in exactly one bucket.

The payoff is that the ruling becomes queryable data. The user can ask what is
in `needs_review`, correct one row of the mapping, and rebuild — instead of
asking you to re-generate code they cannot read.

One narrow exception: a definition that is genuinely structural rather than a
business judgement — the number of months in a year, cents-per-dollar — is not
reference data. If a reasonable user could disagree with the value, it is a
ruling, and it gets landed.

## The decisions model

Every ruling the closure encodes — confirmed, proposed, or refused — is recorded
as a row in one landed model named **`nxd_decisions`**. Not a `DECISIONS.md`,
not a comment, not a line in the handoff that scrolls away.

The reason is the same one that forbids hardcoding reference data. A ruling
filed in a document is invisible to the thing that consumes the answer: a later
session asks a question, gets a number built on a proposed mapping, and has no
governed way to discover that. Landed as data, the ledger is reachable by the
same `run_semantic_query` path as every other fact in the product, so "which
decisions are still unconfirmed?" is a query.

### Shape

| Column | Role | Content |
|---|---|---|
| `decision_id` | `primary_key()` | stable snake_case slug — `merchant_categories`, `fx_rates`. Name it after what it rules on, so the same ruling keeps the same id across rebuilds. Never derive it from a timestamp or a random value. |
| `status` | `dimension()` | review state — exactly one of `confirmed`, `proposed`, `blocked` |
| `provenance` | `dimension()` | who authored it — exactly one of `user_confirmed`, `agent_authored`, `source_derived`, `deferred` |
| `ruling` | `dimension()` | the ruling in one sentence |
| `applies_to` | `dimension()` | the models and columns it materializes in — `classified_spend.category, merchant_categories`. Empty for `blocked`, which materializes nothing. |
| `detail` | `dimension()` | the evidence or basis a reviewer should check. For `blocked`: exactly the missing datum and which questions are limited until it arrives. |

There is deliberately **no timestamp column**. The transform must be
deterministic and rerunnable byte-identically, which forbids `now()`.

> **Not to be confused with an explanation row's `evidence_kind`.**
> `nxd_decisions.provenance` is per-ruling and answers *who authored the
> decision*. A scoring model's `evidence_kind` is per-explanation-row and answers
> *how firmly the source supports one reading* (`fact` / `inference`) — see
> [reference/derived-models.md](derived-models.md). Different grains, disjoint
> vocabularies; never populate one from the other.

A **sample-selection rule is a ruling** and gets a row like any other — which
rows entered the closure, and why, is a judgement the user can disagree with.
This holds whether the rule came from the user or from you, and whether or not
any other ruling exists in the closure.

### `status` and `provenance` are two different questions

They are **orthogonal**, and a reviewer needs both. `status` answers *is this
settled?*; `provenance` answers *who authored it?* Neither implies the other,
which is exactly why one column cannot carry both.

| Value | Question it answers | Use it when |
|---|---|---|
| `user_confirmed` | authored-by | the value came **from the user** — a weight, a gate, a verdict enum, a rubric they stated. Encode it verbatim. Ratifying a value *you* offered does NOT make it theirs: that row stays `agent_authored` and moves its `status` instead. |
| `agent_authored` | authored-by | **you** invented the value to make an underspecified rubric executable — an intermediate anchor, a tie-break, a band boundary the user never stated. The row is the disclosure. |
| `source_derived` | authored-by | the value is a fact read off the source — an observed row count, an enumerated category set, a captured-URL status. A reviewer checks it against the export, not against a preference. |
| `deferred` | authored-by | the step was deliberately not taken this session — live verification skipped, a rate not supplied. Nothing was authored; the row records the omission. |

> **Provenance never moves.** Authorship is a fact about how a row came to
> exist, so it is fixed the moment the row is written: a ruling the agent
> invented stays `agent_authored` forever, reviewed or not. `status` is the axis
> that moves as a ruling is accepted. Whether a ruling has been agreed to is
> read off `status`, never off `provenance`.

The pairing carries the information, and every combination is meaningful:

- `status = confirmed`, `provenance = user_confirmed` — the user's own weight.
  Settled, and nothing of yours in it.
- `status = confirmed`, `provenance = agent_authored` — a threshold
  **you** invented that the user then approved. Settled, but the value is still
  yours. This is the pair that a `status`-only ledger destroys: it is
  indistinguishable there from the row above, which is the whole defect.
- `status = proposed`, `provenance = agent_authored` — invented and not
  yet reviewed. The narration case: an answer resting on it is provisional.
- `status = confirmed`, `provenance = source_derived` — an observed fact. No
  judgement to confirm; it is settled because the source says so.
- `status = blocked`, `provenance = deferred` — the refused FX rate, or a
  verification step postponed. Nothing materializes.

Do not collapse the two. Writing `provenance = user_confirmed` on a value you
chose, because the user approved it afterwards, erases the authorship the column
exists to record — approval moves `status`, never `provenance`. Both columns are
required on every row: there is no blank and no fifth value.

`status` is what drives narration. A user confirms a proposed ruling by editing
that row to `confirmed` and rebuilding the same workflow. There is no approval
tool, no pending-state machine, and nothing in the supervisor enforces a status
— the only behaviour it drives is narration: an answer built on a model an
unconfirmed decision `applies_to` must say so. `provenance` never changes on
approval; it records who wrote the value, which approval does not alter.

Both columns are queryable through the metric view, so "which landed rulings did
the agent author?" is `decision_count` grouped by `decision_provenance` — a
`run_semantic_query` call, not a reading of `detail` prose.

### Emit it only when a ruling exists

A closure whose questions are all answerable by metrics over existing columns
has no rulings — so it has **no `nxd_decisions` model**. Do not emit an empty
ledger, and do not let its existence pressure you into inventing a ruling to
put in it. The rule from
[base or derived](#base-or-derived-the-decision-test) applies unchanged: a
model needs a real reason to exist.

### Landing it

`nxd_decisions` is a base reference model and is landed exactly like
`merchant_categories` — it is authored as data, not as code.

1. Write `data/nxd_decisions/nxd_decisions.csv`, one row per ruling.
2. Declare it in `models.py` with a metric view beside it, so the rows are
   selectable (`run_semantic_query` needs a measure in the selection):

```python
# The column meanings in the Shape table above belong IN the descriptions, not
# only in this doc. A ledger whose own columns are unexplained in the catalog
# repeats the failure it exists to prevent.
nxd_decisions = (
    semantic_model("nxd_decisions")
    .description(
        "One row per ruling the closure encodes, on two independent axes: "
        "'status' is whether it is settled (confirmed, proposed, blocked) and "
        "'provenance' is who authored it (user_confirmed, "
        "agent_authored, source_derived, deferred). A ruling can be confirmed "
        "and still be one the agent invented, so neither axis implies the "
        "other. "
        "The governed record of every judgement behind the numbers."
    )
    .schema(
        {
            "decision_id": field(string(), primary_key(), dimension(name="decision_id", description="Stable id of the ruling.")),
            "status": field(
                string(),
                dimension(name="decision_status", description=(
                    "Review state — exactly one of confirmed, proposed, "
                    "blocked. An answer built on a model a 'proposed' "
                    "decision applies_to must be reported as provisional."
                )),
            ),
            "provenance": field(
                string(),
                dimension(name="decision_provenance", description=(
                    "Who authored the ruling — exactly one of "
                    "user_confirmed (the value came from the user), "
                    "agent_authored (the agent invented "
                    "it to make an underspecified rubric executable — "
                    "including one the user later approved), "
                    "source_derived (read off the source data), deferred "
                    "(the step was deliberately not taken). Orthogonal to "
                    "status: approval moves status, never provenance."
                )),
            ),
            "ruling": field(
                string(),
                dimension(name="decision_ruling", description="The ruling itself, in one sentence."),
            ),
            "applies_to": field(
                string(),
                dimension(name="decision_applies_to", description=(
                    "The models and columns this ruling materializes in "
                    "(e.g. classified_spend.category). Empty for 'blocked', "
                    "which materializes nothing."
                )),
            ),
            "detail": field(
                string(),
                dimension(name="decision_detail", description=(
                    "The evidence or basis a reviewer should check. For "
                    "'blocked': the missing datum, and which questions stay "
                    "limited until it arrives."
                )),
            ),
        }
    )
)

nxd_decisions_metrics = semantic_view(
    "nxd_decisions_metrics", nxd_decisions
).schema(
    {
        "decision_count": metric_field(
            number(),
            metric(
                Agg.COUNT,
                of=nxd_decisions.field("decision_id"),
                name="decision_count",
                description="Number of recorded rulings, any status.",
            ),
        ),
    }
)
```

3. `.promise(nxd_decisions)` and `.model(nxd_decisions_metrics)` in `spec.py`,
   and add `"nxd_decisions"` to `BASE_MODELS` in the transform. On a **file
   connector** (CSV/JSON/JSONL/Parquet) it then flows through the same dlt
   reader loop as every other base model — no special casing anywhere.

   **On an `api-source` or `db-source` closure there is no such reader loop.**
   Neither brings a `data/` export: their ingest bodies pull each model from the
   remote, so a reference model that exists only as your CSV has nothing to
   carry it. Still write the CSV — add it as its own `@dlt.resource`, appended
   to the same `readers` list before the one `pipeline.run(...)`. The form is in
   `derived-models.md` § "The resource template"; `api-source.md` § "Landed
   reference data in an API closure" has the worked version, including how to
   reach the CSV without a `secrets["csv_source"]` to anchor on.

   Both connector templates already scope their reader loops to the models the
   remote actually serves — `db-source` iterates `table_map`, `api-source`
   iterates `fetched_models`, its endpoint-filtered subset of `API_MODELS` — so
   appending the resource is the whole change. Do not
   "fix" either loop back to `PHYSICAL_MODELS`: that is what made a reference
   model raise `KeyError` out of `table_map[model]`, pointing at the
   `db-source-tables` companion as though an entry were missing there.

   It stays a base model in every other respect — promised, declared in
   `models.py`, listed in `BASE_MODELS` and `PHYSICAL_MODELS`. What changes is
   how its rows reach the port **and their types**: a file connector's
   `read_csv()` infers column types, stdlib `csv` does not, so a `rate` yielded
   straight through reaches DuckDB as VARCHAR while `models.py` promises
   `number()`. Cast the measures — `api-source.md` § "Landed reference data in
   an API closure" has the rule.

The name is reserved. If a source file would snake_case to `nxd_decisions`,
that is the naming collision the Step-1 ambiguity rule already covers: stop and
surface it rather than silently overwriting one with the other.

## Rulings you must NOT propose

The section above has one hard boundary. **You may propose a CLASSIFICATION.
You may never propose a MEASUREMENT.**

| | Classification — *propose it* | Measurement — *refuse to propose it* |
|---|---|---|
| Example | merchant → category, account → department | FX rate, discount rate, tax rate |
| Value set | closed and **observed** — you can enumerate every value from the data | continuous — no set to enumerate, any number is admissible |
| Reviewability | each row is individually checkable: "Anthropic → COGS" is right or wrong on its face | a rate is a fact about the world outside the data; nothing in the export contradicts a wrong one |
| Blast radius of a wrong guess | bounded to that value's rows, and visible | multiplies through **every** downstream number, invisibly |
| Escape hatch | `needs_review` honestly carries what the proposal doesn't cover | there is no `needs_review` for a number — you either have it or you fabricate it |

The discriminator in one question: **could the user look at this value and tell
it was wrong?** For "Lufthansa → opex", yes. For "EUR → 1.08", no — 1.08 and
1.19 are equally plausible on their face, and both produce a confident total.
That asymmetry, not the data type, is what makes one proposable and the other
not.

### What refusal actually does

Refusal applies to the **one underivable ruling only**. Every other derivation
in the plan remains mandatory — you do not get to derive less elsewhere because
one input was missing.

1. **Do not fabricate the value.** Not in transform code, not as a PROPOSED
   model. "Proposed" is not a license to invent a number; it is a license to
   land a judgement the user can read back. There is no defensible proposal for
   an exchange rate the way there is for a category mapping.
2. **Leave that one derivation undone.** Everything else still derives. Here:
   keep amounts in their **source currency**, and keep `currency` as a dimension
   on the derived model so no aggregate can silently mix units. A per-currency
   answer is honest; a single blended number built on an invented rate is not.
3. **Surface it as a `blocked` row in the [decisions model](#the-decisions-model)**
   — a status distinct from `proposed`, carrying `provenance = deferred`: the
   ruling is not the agent's to make, so it is not agent-authored either. Name
   exactly the datum needed (which
   currencies, over what date range, at what precision) and which questions are
   limited until it arrives. This row is the *only* model the refused ruling
   produces: there is no `fx_rates` table, because there are no rates.
4. **Make the limitation travel.** State in the handoff that every
   cross-currency answer is per-currency until rates are supplied — the caveat
   belongs with every number it touches, not filed once in a doc nobody reopens.
   Once the user supplies rates, they land as a normal base reference model via
   the flow above and the derivation completes.

A worked contrast on the same closure: the merchant→category ruling is
**PROPOSED** — landed as `merchant_categories`, uncovered merchants routed to
`needs_review`, carried as an `nxd_decisions` row with `status = proposed` and
`provenance = agent_authored`, and the derived model carries a
`category` dimension. The FX ruling on that same closure is **BLOCKED** — no
`fx_rates` model, amounts stay in USD/GBP/EUR, `currency` is a dimension, and an
`nxd_decisions` row with `status = blocked` and `provenance = deferred` names the
missing rates as the reason "total opex" is reported per currency rather than as
one figure. Both rulings live in the same queryable ledger.

## When the user supplies the ruling

Everything above governs a ruling you must *propose* because the data does not
contain it. This section governs the opposite case, and it inverts the default.

When the user supplies the ruling — a rubric, gates, weights, thresholds, a
verdict vocabulary, a selection rule — it is the spec, not raw material. Encode
it verbatim, value-for-value; do not improve, reorder, or fill a missing case
with a default. It lands by the same flow as any confirmed ruling: as its own
model, with an `nxd_decisions` row at `status = confirmed` and
`provenance = user_confirmed`. A gap in a supplied procedure (an unhandled case,
an undefined tie-break, an unstated scale endpoint) is a question back to the
user; with no user available it lands `blocked` / `deferred` naming the missing
datum — never an agent default. If the user answers the gap by ratifying a value
**you** offered, that row is `agent_authored`, not `user_confirmed` —
the value is still yours, and the ledger must keep saying so.

### The data/code boundary, worked

A supplied rubric is **parameterization**, not logic. The values are landed
rows; the transform reads them. Concretely, for a rubric with weighted criteria
and score-banded verdicts:

```
rubric(criterion, weight, scale_min, scale_max)
verdict_thresholds(verdict, min_score)
→ transform reads both; contains no weight, no threshold, no verdict string
```

The test is textual and mechanical: **a supplied weight, threshold, or verdict
string that appears as a literal in `transform/main.py` is a defect**, however
faithfully it was copied. Copying the user's numbers into code is the same
unreviewable fabrication as inventing them — the user cannot query it, cannot
correct one row, and cannot rebuild without you. Landed as rows, a correction is
a data edit against an unchanged transform.

This cuts both ways: it is also why a gap must be surfaced rather than filled.
An agent-authored mid-scale anchor sitting in a landed rubric row is
indistinguishable from a user-authored one, so the ledger's `detail` must say
which is which.

## Confirm the plan before authoring

Before **any materialization** — not merely before `models.py`, but before the
closure directory exists, before the source is copied into it, and before any
file is written — state the plan back: the base models, the derived models with
their grain and key, any reference data you need confirmed, and which question
each derived model exists to answer. A derived model no question motivates
should not be built. A question no model answers is the gap to raise now — not
after a build.

When a supplied procedure is in play this is a **hard gate**, not a courtesy:
see the Workflow's "Gate — confirm the policy with the user before writing anything" in
`SKILL.md`. The read-back enumerates every gate, criterion, weight and verdict
string with the user's own value, every anchor you propose for an incomplete
scale, the verdict bands and precedence, and every gap you are about to land as
`blocked` or ask about — then **waits for the user's reply**. A procedure
summarized rather than enumerated is a procedure the user cannot check, and a
proposal the user never saw is an invention however carefully it was authored.
