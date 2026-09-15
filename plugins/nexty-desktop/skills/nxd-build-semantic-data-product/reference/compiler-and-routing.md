# Compiler and routing

## Contents
- What the compiler does
- Compile paths (base-table default under .semantic_tools())
- Chasm-trap defence (load-bearing correctness property)
- Filter rendering
- Snowflake dialect specifics
- Adding a new backend dialect

---

## What the compiler does

`compile_selection(selection, *, registry, dialect, fqn, use_view)` takes a
concept-level selection and produces a single read-only, aggregated SQL query.

Input `selection` shape:

```python
{
    "measures":   ["metric_name", ...],       # required
    "dimensions": ["dimension_name", ...],    # optional
    "filters": [
        {"dimension": "dim_name", "op": "=", "value": "foo"},
        ...
    ],                                        # optional
}
```

Output: a SQL string ready to execute (wrapped in `LIMIT cap+1` by the MCP tool).

The routing and validation logic is dialect-independent. Only the leaf SQL
expressions (aggregation, filter literals, native view DDL/query) are
dialect-specific.

---

## Compile paths

Under the `.semantic_tools()` pattern the auto-generated `run_semantic_query`
compiles against the **base tables** directly (`use_view=False`) — there is no
pre-provisioned `<MODEL>_SEMANTIC` view object. Paths 1 and 2 (inline-join) are the
live paths; the view variant below is documented for completeness but is not used
under `.semantic_tools()`.

### Path 1: single-model

All selected metrics and dimensions live on the same table. The compiler
emits a plain `SELECT ... FROM table WHERE ... GROUP BY ...`.

```sql
SELECT
  REGION AS region,
  COUNT(DISTINCT order_id) AS order_count
FROM DB.SCHEMA.orders
GROUP BY REGION
```

### Path 2: cross-model N:1 join (when `use_view=False`)

A dimension is on a different model than the metric, and a MANY_TO_ONE join
connects them. The compiler finds the first MANY_TO_ONE join from the metric's
model (MANY) to the dimension's model (ONE) and emits an inline JOIN:

```sql
SELECT
  r.CATEGORY AS category,
  COUNT(DISTINCT l.order_id) AS order_count
FROM DB.SCHEMA.orders l
JOIN DB.SCHEMA.products r ON l.product_id = r.product_id
GROUP BY r.CATEGORY
```

### Path 2 variant: compile against a pre-joined plain view (when `use_view=True`)

> Not used under `.semantic_tools()` (which compiles base-table / inline-join,
> `use_view=False`). Documented for the legacy provisioned-view pattern only.

Instead of an inline JOIN, the compiler queries the pre-created view
`<FIRST_MODEL>_SEMANTIC` (or the view name set on the dialect). This path is
simpler and avoids repeating join logic at query time:

```sql
SELECT
  CATEGORY AS category,
  COUNT(DISTINCT order_id) AS order_count
FROM DB.SCHEMA.ORDERS_SEMANTIC
GROUP BY CATEGORY
```

The plain view must have been created by `plain_view_ddl()` during provisioning.

### Path 3: native semantic view (Snowflake) — legacy only

When a Snowflake `SEMANTIC VIEW` object exists in the session, the legacy
provisioned-view path preferred a native `SEMANTIC_VIEW(...)` query. Under
`.semantic_tools()` no view object is provisioned, so this path does not fire — the
tools compile base-table / inline-join SQL (paths 1 and 2). The native path remains
available in the library for the legacy pattern.

---

## Chasm-trap defence (load-bearing correctness property)

**This is the most important correctness invariant.** When a selection includes
metrics from two different model grains (e.g. `order_count` on `orders` and
`event_count` on `events`), the compiler raises:

```
CompileError: metrics span multiple grains (events, orders);
query one grain at a time to avoid fan-out double-counting.
Split into separate queries.
```

Fan-out double-counting occurs when two fact grains are joined: each row from
table A multiplies with each matching row from table B, so aggregates become
inflated without warning. The compiler detects this by checking that all selected
metrics share the same `model` field.

The `CompileError` message is intentionally phrased for the calling LLM so it
can correct its selection.

---

## Filter rendering

Filters reference dimension **concept names** (not physical columns). The
compiler resolves the concept name to the physical column internally.

Supported operators (exact symbols — not words):

```
=   !=   <>   >   >=   <   <=   LIKE   ILIKE   IN   NOT IN
```

For `IN` / `NOT IN`, `value` must be a list. For all others, a scalar.

Example filters:

```python
{"dimension": "region",   "op": "=",    "value": "EMEA"}
{"dimension": "category", "op": "IN",   "value": ["Electronics", "Software"]}
{"dimension": "event_date","op": ">=",  "value": "2024-01-01"}
```

An unknown dimension name raises `CompileError: unknown filter dimension 'X'`.
An unsupported `op` raises `CompileError: unsupported filter op '...'`.

---

## Snowflake dialect specifics

`SnowflakeDialect` generates SQL verified live against Snowflake.

### Aggregation expressions

| Agg | boolean=False | boolean=True |
|-----|---------------|--------------|
| COUNT(*) | `COUNT(*)` | — |
| COUNT_DISTINCT | `COUNT(DISTINCT col)` | — |
| SUM | `SUM(TRY_CAST(CAST(col AS VARCHAR) AS DOUBLE))` | `SUM(CASE WHEN CAST(col AS VARCHAR) IN ('true','TRUE','True','t','1','yes','YES') THEN 1 ELSE 0 END)` |
| AVG | `AVG(TRY_CAST(CAST(col AS VARCHAR) AS DOUBLE))` | — |
| MIN | `MIN(TRY_CAST(CAST(col AS VARCHAR) AS DOUBLE))` | — |
| MAX | `MAX(TRY_CAST(CAST(col AS VARCHAR) AS DOUBLE))` | — |
| EXPRESSION | the attached expression, else `col` emitted as raw SQL | — |

The double VARCHAR cast (`TRY_CAST(CAST(col AS VARCHAR) AS DOUBLE)`) tolerates
`NUMBER`, `FLOAT`, and `VARCHAR` physical column types, and staging text copies.
`TRY_CAST` returns NULL on parse failure (NULLs are ignored by aggregates).

An attached metric expression short-circuits this table for **any** `Agg`: when
one is present the dialect emits it verbatim, with no cast wrapping. For
`Agg.EXPRESSION` with no expression attached, the metric's `column` is emitted
as already-authored SQL. See the `Agg.EXPRESSION` note in
[registry-authoring.md](registry-authoring.md) for where that expression map
lives and why this is not a derivation surface.

### Fully-qualified name (fqn)

Pass `fqn="DB.SCHEMA."` (note trailing dot) to `compile_selection` or
`build_semantic_tools` so queries run independently of the Snowflake session's
active database/schema.

---

## No provisioning step under `.semantic_tools()`

The `.semantic_tools()` pattern provisions **no view object**. The transform seeds
the base tables (`CREATE OR REPLACE TABLE` + `write_pandas`) and that is all the
DDL there is; `run_semantic_query` compiles against those base tables directly. You
do not call `native_semantic_view_ddl` / `plain_view_ddl`, and there is no
`@on_provision` hook.

> The library still exposes `native_semantic_view_ddl(registry, dialect, fqn)` and
> `plain_view_ddl(registry, dialect, fqn)` for the legacy provisioned-view pattern,
> deriving the view name from the dialect (`<FIRST_MODEL_UPPER>_SEMANTIC`). They are
> not used under `.semantic_tools()`.

---

## Adding a new backend dialect

Implement the `Dialect` Protocol:

```python
class Dialect(Protocol):
    def agg_expr(self, metric: Metric) -> str: ...
    def native_view_ddl(self, registry: CompiledRegistry, fqn: str) -> str | None: ...
    def native_view_query(self, registry, selection, fqn) -> str | None: ...
    def supports_native_semantic_view(self, cursor, fqn) -> bool: ...
```

Pass your dialect instance to `compile_selection(..., dialect=my_dialect)` and
`build_semantic_tools(registry, dialect=my_dialect)`.

Backends that do not support native semantic views should return `None` from
`native_view_ddl` and `native_view_query`, and `False` from
`supports_native_semantic_view`. The compiler falls back to the base-table /
plain-view path automatically.
