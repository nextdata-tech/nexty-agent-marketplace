# The query grammar and the Omission Test

## Contents

- [What `run_semantic_query` accepts](#what-run_semantic_query-accepts)
- [Grouping by a time period](#grouping-by-a-time-period)
- [What it does not accept](#what-it-does-not-accept)
- [The Omission Test](#the-omission-test)
- [Enforcement corollary: the default read must be right](#enforcement-corollary-the-default-read-must-be-right)
- [Grammar-fit gate](#grammar-fit-gate)
- [Worked examples](#worked-examples)

## What `run_semantic_query` accepts

| Field | Accepts |
|---|---|
| `measures[]` | declared measure names |
| `dimensions[]` | declared dimension names; a time dimension may instead be `{"dimension": <name>, "grain": <grain>}` (see [Grouping by a time period](#grouping-by-a-time-period)) |
| `filters[]` | `=`, `!=`, `<>`, `>`, `>=`, `<`, `<=`, `LIKE`, `ILIKE`, `IN`, `NOT IN`, `IS NULL`, `IS NOT NULL` — **ANDed only**; `IN` / `NOT IN` take a non-empty list, null tests omit `value` |
| `order_by[]` | names that are **among the selected** measures/dimensions, else the compiler raises |
| `limit` | an integer — the DuckDB handler caps the result at **200 rows** regardless |

For a row-level question such as "list each current record", select every
projected value, including numeric values such as `amount`, as a dimension.
Do not replace a projected numeric dimension with a `total_<field>` or
`SUM(<field>)` measure. If the catalog exposes only an aggregate for a field
the approved output promises at row grain, repair the product or report the
blocker; never present aggregate rows as record rows.

Filter values are validated and normalized for the dimension's declared type;
they are not always strings. `IN` / `NOT IN` take a non-empty list. For null
tests, omit `value` entirely.

For a ranked question, make the endpoint do the ordering and limiting in the
same governed query. The order term uses `name` (a selected measure or
dimension) and `dir` (`asc` or `desc`):

```json
{
  "measures": ["revenue"],
  "dimensions": ["customer_name"],
  "order_by": [{"name": "revenue", "dir": "desc"}],
  "limit": 3
}
```

Do not fetch the unrestricted grouped result and sort or truncate it in the
agent. That loses the endpoint's ordering/limit contract and can turn a
bounded top-N question into an incomplete or misleading answer.

## Grouping by a time period

For "per month", "by quarter" or "weekly", group the date dimension at a grain
instead of grouping by day and adding the days up in the agent:

```json
{
  "measures": ["net_spend"],
  "dimensions": [{"dimension": "txn_date", "grain": "month"}],
  "filters": [{"dimension": "txn_date", "op": ">=", "value": "2025-01-01"}]
}
```

- Send the object form only for a dimension whose `describe_model` entry lists
  `grains`, and only a grain from that list. Without a `grains` list the
  dimension is not a time dimension, or the runtime predates time grains; group
  by the plain name instead.
- Dates allow `day`, `week`, `month`, `quarter` and `year`; timestamps also allow
  `hour`. Weeks are ISO weeks starting Monday. Timezone-aware timestamps are
  grouped in UTC.
- The output column keeps the dimension's name, so `order_by` uses the plain
  name: `{"name": "txn_date", "dir": "asc"}`.
- Filters apply to the raw date, not the period. For "March 2025", filter
  `>= "2025-03-01"` and `< "2025-04-01"`.
- The response's `time_grains` confirms the grain applied. If it is missing or
  empty, the query grouped by the raw value.
- Adding up days in the agent is correct for a total, but wrong for a distinct
  count or an average. Always use the grain.

## What it does not accept

`BETWEEN` · `NOT LIKE` · `HAVING` or query-time filtering on a selected measure ·
cross-condition `OR` / disjunction. The filter list is ANDed; same-dimension
alternatives use `IN` / `NOT IN`.

For a null test, use `IS NULL` or `IS NOT NULL` and omit the `value` key. Do
not send a null literal or the string `"null"`; null comparisons must use one
of those operators.

## The Omission Test

Filters exist to scope a question. They are not a place to put policy. The test
that separates the two, applied to every candidate constraint:

> Take the candidate constraint. Imagine a future consumer who has never heard
> it, querying the affected measure with **no filters**.
>
> - If their number would be **WRONG** — it silently counts rows the business
>   says must never count, or mixes meanings ("transfers inside expenses", "AWS
>   inside opex") — it is a **STANDING RULING**. Materialize it in the transform
>   so the measure is correct with no filter applied. A ruling enforced by a
>   filter is caller discipline: the caller who omits it gets a confident wrong
>   number and no signal.
> - If their number would be merely **BROADER** — a correct answer to a bigger
>   question ("all countries" instead of Spain, "all time" instead of May) — it
>   is a **PER-QUESTION CONSTRAINT**. Express it with `filters[]` / `order_by[]`
>   / `limit` at query time. Materializing it bloats the model with single-use
>   columns and burns a regenerate cycle.

**Secondary cues** (tie-breakers, not the rule): rulings are timeless
declarations about what the data MEANS ("X is Y", "X never counts"), usually
from the data owner during Teach. Constraints are scoping words attached to one
question ("in", "during", "only", "top N"). A constraint naming a **value of an
existing dimension** is a filter by construction.

**When genuinely ambiguous** ("exclude refunds" — policy or scope?): in Teach,
ask one question — *"always true of these numbers, or just for this question?"*
With no user available, **MATERIALIZE**. That is the safe direction: a
materialized ruling can still be filtered or grouped further; a filtered ruling
cannot be un-forgotten.

## Enforcement corollary: the default read must be right

A ruling is materialized **only when the default read is right** — the
ruling-bearing measure's model excludes or reclassifies the rows in the
transform. Landing an `is_transfer` dimension and expecting callers to filter on
it is the same silent failure wearing a column.

Keep the excluded slice reachable via a classification dimension or a companion
model — **never** by making correctness depend on a remembered filter. That
dimension carries a `description=` naming the ruling that created it: a consumer
who can see the column but not learn what it means has visibility without
legibility, which is the same governance hole one level down.

So for "transfers are never expenses", the correct shape is both of:

1. the expense measure's model already excludes transfer rows (default read is
   right), **and**
2. a `transaction_kind` dimension or a companion model keeps transfers visible
   and queryable.

Shipping only (2) is the governance hole.

## Grammar-fit gate

A genuine per-question constraint the grammar cannot express does **not** get
materialized just for that. Sanctioned patterns:

| Want | Sanctioned pattern |
|---|---|
| Membership in several values of one dimension | use `IN` / `NOT IN` with a non-empty value list |
| `OR` / disjunction across separate filter constraints | the filter list cannot express it; clarify or re-scope the request rather than silently omitting a branch |
| a range | two ANDed filters (`>=` and `<`) |
| `HAVING`-like threshold | `order_by` desc + `limit`; read qualifying rows from the ≤200-row grouped result |

Reading rows out of a governed grouped result is **presentation, not
emulation** — recomputing aggregates agent-side stays banned.

For a **per-question nullness** constraint, use `IS NULL` / `IS NOT NULL`.
When nullness is a standing data-quality **ruling**, materialize an explicit
bucket (the existing `needs_review` philosophy) so correctness does not depend
on a caller remembering a filter.

## Worked examples

**"Spain in Q2 2026" → filters.** A consumer querying revenue with no filters
gets all countries, all time — broader, not wrong. Both constraints name values
of existing dimensions. Use three ANDed filters; a range is two comparisons,
not `BETWEEN`:

```json
{
  "filters": [
    {"dimension": "country", "op": "=", "value": "ES"},
    {"dimension": "order_date", "op": ">=", "value": "2026-04-01"},
    {"dimension": "order_date", "op": "<", "value": "2026-07-01"}
  ]
}
```

**"Transfers are never expenses" → ruling.** A consumer querying total expenses
with no filters would count internal transfers as spend: a confidently wrong
number, no signal. Materialize — the expense model excludes transfer rows — and
keep `transaction_kind` as a dimension so transfers stay queryable. Do not make
correctness depend on a remembered `is_transfer` query filter.

**"Exclude refunds" → ambiguous.** Could be policy ("refunds never count as
revenue") or scope ("gross revenue for this one question"). In Teach, ask the
one question. With no user available, materialize: the refund-excluding measure
is still filterable afterwards, whereas a forgotten filter is unrecoverable.

**"Customers over 1M revenue" → order_by + limit + read.** This is a
`HAVING`-style measure threshold, which the grammar cannot express. Do not
materialize a single-use `is_over_1m` column. Select `revenue` by `customer`,
`order_by` revenue desc, `limit` 200, then read off the rows above 1M from the
governed result — presentation over a governed aggregate, not re-aggregation.
