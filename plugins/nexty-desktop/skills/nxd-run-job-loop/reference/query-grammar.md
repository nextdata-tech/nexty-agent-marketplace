# The query grammar and the Omission Test

## Contents

- [What `run_semantic_query` accepts](#what-run_semantic_query-accepts)
- [Grouping by a time period](#grouping-by-a-time-period)
- [Filter groups and metric-value filters](#filter-groups-and-metric-value-filters)
- [Filtering a dimension that has a label](#filtering-a-dimension-that-has-a-label)
- [Derived metrics and native grains](#derived-metrics-and-native-grains)
- [Snapshot metrics](#snapshot-metrics)
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
| `filters[]` | dimension filters `{"dimension", "op", "value"}` with `=`, `!=`, `<>`, `>`, `>=`, `<`, `<=`, `LIKE`, `ILIKE`, `IN`, `NOT IN`, `IS NULL`, `IS NOT NULL`; `{"any": [...]}` (OR) and `{"all": [...]}` (AND) groups of filters; metric-value filters `{"measure", "op", "value"}` on a selected measure. Top-level entries are ANDed. `IN` / `NOT IN` take a non-empty list, null tests omit `value`. See [Filter groups and metric-value filters](#filter-groups-and-metric-value-filters) |
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
  `>= "2025-03-01"` and `< "2025-04-01"` (on a dimension without a native
  `time_grain`; with one, filter on period starts, since a non-period-start
  value is refused with SQ-403).
- The response's `time_grains` confirms the grain applied. If it is missing or
  empty, the query grouped by the raw value.
- Adding up days in the agent is correct for a total, but wrong for a distinct
  count or an average. Always use the grain.

## Filter groups and metric-value filters

A `filters[]` entry is one of three shapes. Every leaf names its operator in
`op`; a leaf without `op`, with a misspelled key such as `operator`, or with
an unknown key is refused rather than read as `=`.

- **Dimension filter** — `{"dimension": "region", "op": "=", "value": "EU"}`,
  as above. It tests rows before aggregation.
- **Group** — `{"any": [<entry>, ...]}` keeps a row when any entry holds (OR);
  `{"all": [<entry>, ...]}` when every entry holds (AND). A group holds only
  `any` or only `all`, no other keys, and is never empty. Groups nest up to 8
  levels deep; deeper is refused with SQ-101. Each leaf keeps every check a
  plain filter gets (operators, value types, PII rules, model access). A row
  that meets several entries of an `any` group is counted once.
- **Metric-value filter** — `{"measure": "revenue", "op": ">=", "value": 1000}`
  keeps the groups whose value for that metric meets the condition, after
  aggregation (what SQL calls `HAVING`). `op` is `=`, `!=`, `<>`, `>`, `>=`,
  `<` or `<=`; `value` is a JSON number. The metric must also be in
  `measures`; it may be a derived metric. A group whose value is `NULL` is not
  kept. `order_by` and `limit` apply after it, so a top-N over a threshold is
  correct even when more groups passed than the 200-row cap. A grand total
  with a metric-value filter returns one row or none.

"EU or web orders that are paid":

```json
{
  "measures": ["order_count"],
  "filters": [
    {"any": [
      {"dimension": "region", "op": "=", "value": "EU"},
      {"dimension": "channel", "op": "=", "value": "web"}
    ]},
    {"dimension": "status", "op": "=", "value": "paid"}
  ]
}
```

"Regions with at least 1000 revenue, largest first":

```json
{
  "measures": ["revenue"],
  "dimensions": ["region"],
  "filters": [{"measure": "revenue", "op": ">=", "value": 1000}],
  "order_by": [{"name": "revenue", "dir": "desc"}]
}
```

Use `IN` rather than an `any` group when the alternatives are values of one
dimension. An `any` group may hold only dimension filters or only
metric-value filters, because the two run at different stages; mixing them is
refused with SQ-504. An `all` group may hold both.

`filters_applied` in the response echoes groups and metric-value filters as
sent where the endpoint supports it; some Python-served tools report only
`dimension` / `op` / `value` per entry, so trust the request you sent for a
group's structure.

**Recovering from a refusal** (never drop the filter to get a result):

- **SQ-504** (metric-value filter cannot be applied): add the metric to
  `measures`; send a numeric `value`; use one of the seven comparison
  operators; move a metric-value filter out of an `any` group that also holds
  a dimension filter into its own top-level entry.
- **SQ-505** (group cannot be placed exactly): the group would change which
  rows the metrics count. Put each model's entries in a group of their own;
  select metrics from one model when a group reads several models; group only
  by dimensions of the metrics' own model; use one filter (or an `IN` list) on
  a model that repeats the metrics' rows; filter a native-time-grain dimension
  or a labelled dimension outside any group. A refusal reading
  "dimension '<d>' has a label, so a filter group cannot match it by id or
  label. Filter it outside a group." means exactly that: move that leaf to a
  top-level filter (an `IN` list when the alternatives are its own values). If none fits, use plain dimension filters or run separate
  queries and say so; do not answer a different question silently.
- **SQ-307** now means only that a *dimension* filter names a derived metric.
  Filter its value with `{"measure": <derived metric>, ...}` instead.
- If the endpoint rejects the `any`, `all` or `measure` keys as an unknown
  filter shape, the runtime predates them. Fall back to `IN`, separate queries,
  or `order_by` + `limit` and reading the governed rows, and say which.

## Filtering a dimension that has a label

A dimension the catalog describes with a display label (declared `label=`)
returns the label in results while the stored id stays in the data. A
query-time filter on it compares against both:

| Operator | A row matches when |
|---|---|
| `=`, `IN` | the id **or** the label equals the value |
| `!=`, `<>`, `NOT IN` | **neither** the id nor the label equals any value; a row whose id is `NULL` (or that has no joined record) never matches |
| `LIKE`, `ILIKE` | the label matches (the id is not searched) |
| `>`, `>=`, `<`, `<=`, `IS NULL`, `IS NOT NULL` | the id satisfies it, as for any dimension |

So a value copied from a result works as a filter, and so does the stored id.
A value that cannot be an id (text for an integer id) is compared against the
label alone. If one value is the label of one row and the id of another, both
rows match; add a second filter to separate them. Such a filter always runs
against the base tables, so a native semantic view reports a fallback in
`notice`. Metric-local filters in the spec still compare the id column only.

**Not inside an OR group.** An `any` group of two or more entries, or an
`all` group nested inside one, refuses a filter on a labelled dimension with
SQ-505. A top-level `all` group and a single-entry `any` group count as plain
filters and match id or label as above. So "Enterprise customers or web
orders" cannot be one `any` group when `segment` has a label: run separate
queries and say the totals overlap, or ask whether AND was meant. Never drop
the labelled branch, and never add up the two results as if they were
disjoint.

## Derived metrics and native grains

A **derived metric** is a metric computed from metrics, often on different
models (for example total patients = specialty-pharmacy patients + buy-and-bill
patients). When the question needs such a combination, select the derived
metric by name in `measures`; never run one query per model and add or divide
the results yourself.

- Find it in the catalog: `list_models` lists the `semantic_composition` that
  holds derived metrics beside the models; `describe_model(<composition>)`
  returns rows with `aggregation: "derived"`, the `expression`, its `inputs`
  and `compatible_dimensions`. `semantic_model` carries `derived_metrics`. Read
  the description and the expression: they say which populations it covers.
- Group only by the row's `compatible_dimensions`.
- A group one input has no rows for reads `0` for a count and `NULL` for any
  other aggregate, and `NULL` propagates through the expression unless the
  author wrapped it in `COALESCE`. A `NULL` in a derived total is a missing
  sum input, not a zero; say so rather than reporting it as zero. A division
  by zero is `NULL`; division returns a float.
- Filter a derived metric's value with a metric-value filter,
  `{"measure": <derived metric>, "op": ">", "value": 10}`, with it in
  `measures`. A `dimension` filter that names it is refused with SQ-307. A
  derived metric the service cannot plan is refused with SQ-306; report it as
  a data product defect.
- **Native time grain.** A model or metric (and a derived metric, at the
  coarsest grain of its inputs) may carry `time_grain` as `{dimension: grain}`.
  Group that dimension at the native grain or coarser (a weekly metric: `week`,
  or omit the grain); weeks do not roll up to month, quarter or year. Grouping
  finer, or filtering with `>`, `<=`, `LIKE` or a non-period-start value, is
  refused with SQ-403; use `>=` / `<` on period starts. `=` / `IN` on a period
  start match the whole period, and the response `notice` states the range.

## Snapshot metrics

A metric whose catalog row lists `non_additive_over` (a balance, stock level
or headcount) must not be added across the dimensions it names. For each named
dimension, the query must either:

- **group by it** at the grain its rows are recorded at: no grain, or its
  native `time_grain`. A `DATE` with no native grain allows `day` only; a
  timestamp with none allows no grain object. Other dimensions may be grouped
  alongside it.
- **filter it to one value** with `=` or a single-value `IN`. The query then
  reads one snapshot and may group by anything else.

Anything else — a grand total, a grouping that leaves the dimension out, a
coarser grain (`month` over daily snapshots), a range or several values — is
refused with **SQ-308**, as is a derived metric over such an input. A pinned
date is also refused when the query reads another metric recorded at a coarser
grain on that dimension. There is no "latest snapshot" aggregation: for "stock
now", filter the date to the latest day; for "stock by month", group by the
date at its stored grain and report each snapshot, or pin one date per month in
separate queries. Never add snapshot rows up yourself. When a query mixes
additive and snapshot metrics, query them separately if pinning the dimension
does not fit the question.

## What it does not accept

`BETWEEN` · `NOT LIKE` · a filter without `op` · an `any` group that mixes
metric-value and dimension filters · groups nested deeper than 8 levels. A
range is two filters (`>=` and `<`); alternatives within one dimension use
`IN` / `NOT IN`.

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
| `OR` / disjunction across separate filter constraints | an `{"any": [...]}` group (not on a labelled dimension); if it is refused with SQ-505, follow the recovery above rather than silently omitting a branch |
| a range | two ANDed filters (`>=` and `<`) |
| `HAVING`-like threshold on a metric | a metric-value filter `{"measure", "op", "value"}` on a selected measure |

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

**"Customers over 1M revenue" → metric-value filter.** This is a threshold
on an aggregated value. Do not materialize a single-use `is_over_1m` column,
and do not fetch every customer and read the large ones off the result: the
200-row cap can hide some. Select `revenue` by `customer` with
`{"measure": "revenue", "op": ">", "value": 1000000}`, ordered by revenue
desc.

**"Orders from Spain or placed online" → an `any` group.** The two conditions
are on different dimensions, so `IN` cannot express them:
`{"any": [{"dimension": "country", "op": "=", "value": "ES"}, {"dimension": "channel", "op": "=", "value": "online"}]}`.
An order that meets both is counted once.
