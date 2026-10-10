# Compiler and routing

## Contents
- What the compiler does
- Compile paths (base-table default and opt-in native routing)
- Chasm-trap defence (load-bearing correctness property)
- Filter rendering
- Snowflake dialect specifics
- Base-table default and optional native-view provisioning
- Adding a new backend dialect

---

## What the compiler does

`compile_selection(selection, *, registry, dialect=None, fqn="", use_view=False)`
takes a concept-level selection and produces a single read-only, aggregated SQL
query. Base-table compilation is the default. The explicit plain-view path
(`use_view=True`) remains available but is deprecated and emits a
`DeprecationWarning`.

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

`compile_selection` defaults to base tables. The generated
`run_semantic_query` also uses base tables unless Snowflake native routing is
explicitly enabled with `view_name` or `view_names` on `.semantic_tools()`.
Neither logical semantic models nor `.semantic_tools()` create native objects.

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

### Path 2: cross-model N:1 join

When selected concepts span related models, base-table compilation uses the
unified assembler. It resolves a join path from the query spine, keeps each
metric at its home grain in aggregate CTEs, and combines those results along
the path so a many-side join does not multiply raw fact rows. Single-model and
single N:1 selections are degenerate cases of the same assembler. With default
complete behavior, the per-query spine can preserve empty dimension groups and
unmatched metric-home rows in a `NULL` group; see [result differences from the
deprecated fixed view](#result-differences-from-the-deprecated-fixed-view).

### Deprecated path: compile against a pre-joined plain view

Passing `use_view=True` explicitly still compiles a single-hop cross-model
selection against a pre-created plain view. The default no longer selects this
path, and both this option and `plain_view_ddl()` are deprecated.

It reads a pre-created plain view whose name comes from
`dialect.view_name(registry)`. Snowflake derives that name from the registry,
including a content hash, unless the caller supplies an explicit dialect
`view_name`.

The plain view must already exist. `plain_view_ddl()` remains available for
existing direct callers and emits a deprecation warning; do not add it to a
transform as a provisioning recipe.

### Result differences from the deprecated fixed view

Direct callers that relied on the previous default can observe different rows
with base-table compilation. The fixed view starts at the MANY side, while the
inline assembler starts at the query's per-selection spine. With default
complete behavior, the inline query can include an empty dimension group the
view omits (`0` for count aggregates, `NULL` for other aggregates). It can also
retain unmatched metric-home rows in a `NULL` dimension group and merge them
into an existing `NULL` group. Matched rows remain equivalent. An explicit
`complete` value forces inline compilation, including when `use_view=True`.

### Native semantic view routing (Snowflake)

Native routing is opt-in. The public spec method accepts these routing
arguments:

```python
DataProductSpec.semantic_tools(
    service,
    *,
    port_name="mcp-api",
    mcp_path="/mcp",
    backend="snowflake",
    view_name=None,
    view_names=None,
)
```

Set `view_name` to the bare name of one provisioned Snowflake semantic view, or
set `view_names` to map logical model owners to provisioned views; they are
mutually exclusive. A selected metric belongs to the model where it was
declared; a metric on a `semantic_view` remains owned by that view even when it
aggregates a base-model field. A selected dimension or filter dimension belongs
to its declaring model. With `view_names`, every selected concept must be
mapped and all of them must map to the same object. Mixed or unmapped selections
skip the native probe and use the base-table compiler with a fallback notice.

The native eligibility guard runs before the existence probe. It keeps native
routing to supported single-model selections; any resolved join, including a
filter-only join, uses the base-table compiler with a fallback notice. A
metric-local filter on the metric's own model can use native routing:
`native_semantic_view_ddl(...)` folds it into that metric's aggregate. Filters on
another model, filtered expression metrics, and metric dependencies use the
base-table path with a fallback notice. Label dimensions, time grains, and
unsupported raw final-grain selections use the base-table path; eligibility
admits a limited subset of validated raw final-grain shapes. An explicit
`complete` value does not by itself disable native routing for a single-model
selection; joined selections already use the base-table path.

If you author the native view yourself, declare each metric-local filter inside
its aggregate. The runtime selects a metric by name and probes only that the
view exists; it cannot detect a custom view that omits the filter and would
therefore return an unfiltered value. `native_semantic_view_ddl(...)` raises
`CompileError` when the registry contains a metric filter it cannot fold. Keep
that filter in the logical registry for base-table queries; do not remove or
change it to make DDL generation succeed. A custom native view may omit metrics
that native eligibility rejects, because those selections fall back before the
native probe. Otherwise, leave native routing disabled.

Provision every named native object in the database and schema of the storage
context supplied to the semantic tools. Ensure the base relations exist before
running native-view DDL. The existence probe checks only that an object is
present; it cannot detect a stale definition behind an explicit `view_name` or
`view_names`. Reprovision custom native views whenever the semantic definition
changes. Fallback queries do not inherit filters or policies that exist only in
custom native-view SQL; enforce equivalent access on the base tables or through
warehouse permissions that also apply to the fallback query.

For an eligible selection, NXD probes the configured object for existence on
that query. A missing object or failed probe falls back to base tables with a
notice. A native execution failure also falls back with a notice. If the
base-table query cannot be built or executed, the query returns an error.

---

## Chasm-trap defence (load-bearing correctness property)

**This is the most important correctness invariant.** Metrics may come from
different model grains in one selection. A cross-home selection is supported
when its join path is resolvable and collapsible: the compiler pre-aggregates
each metric at its home grain before combining results. Dimensionless totals can
be aggregated independently and combined as scalar rows; grouped selections use
the resolved join tree. This prevents a many-side join from multiplying raw
fact rows and inflating measures. A cross-home selection is not itself a chasm
trap.

The chasm-trap refusal is path-based: the compiler raises `CompileError` when
the selected models have no resolvable join path, because it cannot construct a
fan-out-safe result. It also raises `CompileError` for a dimension that is not
compatible with a selected metric, or an aggregation shape whose partial
results cannot be rolled up without changing their meaning. For example, a
cross-grain `AVG` is refused when its rendered expression cannot be decomposed
into numeric `SUM` and `COUNT` partials; a synthetic partial-column name
collision is also rejected rather than silently shadowing a selected metric.

---

## Filter rendering

Filters reference dimension **concept names** (not physical columns). The
compiler resolves the concept name to the physical column internally.

Supported operators (exact symbols — not words):

```
=   !=   <>   >   >=   <   <=   LIKE   ILIKE   IN   NOT IN
IS NULL   IS NOT NULL
```

`IN` / `NOT IN` take a non-empty list. `IS NULL` / `IS NOT NULL` omit `value`;
other operators take a scalar. Values are checked against the dimension's
declared type, so they are not always strings.

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

## Base-table default and optional native-view provisioning

The default `.semantic_tools()` setup needs no view DDL. The transform seeds
ordinary table-backed outputs, and `run_semantic_query` compiles against those
base tables. NXD does not create a native semantic view automatically.

To opt into native Snowflake routing, provision an author-owned native view and
configure its name on `.semantic_tools()`. For a transform-backed data product,
keep output ports table-backed and register the SQL with a separate
`.provision(sql(...).compute(...))` hook. Provisioning does not run per query;
make the provisioning script idempotently create the model relations before
its native-view DDL. Do not rely on a transform to create them:

```python
spec = (
    data_product(...)
    .output(ordinary_table_backed_output)
    .transform(...)
    .provision(
        sql("provision/orders_semantic_view.sql").compute(SNOWFLAKE)
    )
    .semantic_tools(
        service="mcp-api-service",
        view_name="ORDERS_SEMANTIC_VIEW",
    )
)
```

`native_semantic_view_ddl(...)` can generate native-view DDL for that separate
provisioning hook, but raises `CompileError` if the registry has a metric filter
it cannot fold. Keep the original metric and filter in the logical registry so
base-table queries retain their semantics; do not remove or alter the filter to
make DDL generation succeed. For an optional custom native view, omit metrics
that native eligibility rejects, or leave native routing disabled; those
selections use base tables before the probe. The helper does not create the
base-table output relations needed for fallback and promise verification. A
no-transform facade can instead use `as_view(...)` and must provision every
declared model-shaped output relation.
Do not use deprecated `plain_view_ddl()` as a transform provisioning recipe.

---

## Adding a new backend dialect

Implement the `Dialect` Protocol:

```python
class Dialect(Protocol):
    def agg_expr(self, metric: Metric) -> str: ...
    def native_view_ddl(self, registry: CompiledRegistry, fqn: str) -> str | None: ...
    def native_view_query(self, registry, selection, fqn) -> str | None: ...
    def supports_native_semantic_view(
        self, cursor: Any, fqn: str = "", *, registry: CompiledRegistry
    ) -> bool: ...
```

Pass your dialect instance to `compile_selection(..., dialect=my_dialect)` and
`build_semantic_tools(registry, dialect=my_dialect)`.

Backends that do not support native semantic views should return `None` from
`native_view_ddl` and `native_view_query`, and `False` from
`supports_native_semantic_view`. These methods describe native-view support for
the generated MCP route. Native-view selection and fallback are owned by that
runtime; direct `compile_selection` calls default to base tables and do not
automatically select a native or plain view. The deprecated plain-view path
requires an explicit `use_view=True` argument.
