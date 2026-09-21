# Semantic-layer data product: overview

## Contents
- What this kit provides
- How public roles reach the tools
- The four auto-generated tools
- Authoring boundary

---

## What this kit provides

A semantic-layer data product exposes named metrics and dimensions through four
governed MCP tools. Authors declare the vocabulary with the public `nxd.spec`
DSL; `.semantic_tools(service=...)` compiles those roles and serves the tools at
pod boot. The compiler, dialect, and tool factory come from the installed
`nxd.data_product` wheel and are never copied into the DP.

The DP stays flat at its root:

- `models.py` defines physical `semantic_model(...)` bases and query-time
  `semantic_view(...)` metric views.
- `transform.py` seeds the physical base tables.
- `spec.py` promises physical models, registers metric views, and enables
  `.semantic_tools()`.

An agent asks for named concepts, not raw SQL. The compiler enforces compatible
dimensions, the one-model-per-query rule, read-only execution, and the row cap.

---

## How public roles reach the tools

```
models.py                         spec.py                 pod runtime
---------                         -------                 -----------
field(..., primary_key())  ┐      promise(base model)  ┐
field(..., dimension())    ├──→   model(metric view)   ├──→ compiled semantic
field(..., join())         │      semantic_tools(...)  │    payloads → 4 MCP tools
metric_field(metric(...))  ┘                           ┘
```

Physical bases carry only entity keys, dimensions, and N:1 joins. Metrics live
on a `semantic_view(...)` with `metric_field(metric(...))`; the view is
query-time only, not a table or DDL artifact. Promise every physical base on the
storage port and register every metric view with `.model(view)`, never
`.promise(view)`.

On the platform, the kernel compiles those public roles into per-model payloads
and delivers them to the runtime. In the split-pod RPC topology, the MCP server
can reconstruct the same payload from the bundled model definition when no
kernel-delivered payload is present; keep `pyyaml` in `requirements.txt` for that
path.

### What reaches the semantic surface — and what doesn't

**Anything not on one of those four arrows is absent from the semantic surface.**
A field with no role — a bare `dtype` in `.schema({...})` — never enters the
compiled model's fields, so it produces no metric, no dimension, and no join. It
is listed by none of them in `list_models` or `describe_model`. The structural
`data_model` block is the only place such a column appears; it is not a place an
agent can query from.

Naming one in a selection is a hard failure, not a silent omission:
`run_semantic_query` returns a structured `CompileError` naming the unknown
concept, so the consumer sees an error rather than a quietly narrower answer.
See `compiler-and-routing.md` for the compile-path failures and their wording.

Roles decide **whether** a field can be queried. Descriptions decide whether it
can be queried **correctly**: `describe_model` is the whole basis on which a
consuming agent maps a natural-language question to a concept, and a dimension
that arrives as a bare name carries no basis for that choice. Declare a role on
every field a metric does not already aggregate — an aggregated measure's
meaning travels on the metric — and a description on every **dimension and
metric** role. `primary_key()` and `join()` take no `description`, and neither
is a concept an agent selects. See `registry-authoring.md`.

> **Put the description on the field wrapper, once.** `field(...,
> description=...)` and `metric_field(..., description=...)` reach
> `describe_model`: a dimension or metric that declares none of its own inherits
> the field's at compile time, and the catalog UI shows the same sentence.
> `dimension(description=...)` / `metric(description=...)` still take precedence
> but are deprecated and emit a `FutureWarning`.

### Why the base tables still matter

`.semantic_tools()` does not create a warehouse view. `run_semantic_query`
compiles governed SQL against the seeded physical base tables. The transform uses
`CREATE OR REPLACE TABLE` plus `write_pandas`, and a marker row satisfies storage
produce-verification.

---

## The four auto-generated tools

| Tool | Purpose |
|------|---------|
| `list_models` | List semantic models, entity keys, metric/dimension counts, and joins. |
| `semantic_model` | Return a model's compiled registry projection. |
| `describe_model` | Explain metrics, compatible dimensions, PII flags, and joins. |
| `run_semantic_query` | Compile concept selections to governed SQL and return rows. |

`describe_model` keeps metrics associated with the physical model they measure.
Metrics from two models cannot be combined in one query, which structurally avoids
chasm traps.

---

## Authoring boundary

Use only the public builders:

```python
from nxd.spec import Agg, dimension, field, join, metric, metric_field
from nxd.spec import primary_key, semantic_model, semantic_view
```

Use `primary_key()` for an entity key; the older alias is not part of this kit.
Pair it with a `dimension(...)` on the same field — roles compose, and a key
carrying only `primary_key()` never appears in `describe_models`, so no query
can group by it.
See `registry-authoring.md` for the role grammar and a complete example.
