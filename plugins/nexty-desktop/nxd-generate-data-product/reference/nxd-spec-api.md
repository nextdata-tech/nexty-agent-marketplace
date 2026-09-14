# The `nxd.spec` Python DSL — verified API surface

## Contents

- Why this file exists
- Data types (`nxd.spec.data_types`)
- Semantic role builders (`nxd.spec`)
- `Agg` — the closed aggregation vocabulary
- `SemanticModelSpec` / `semantic_model()` / `semantic_view()`
- `spec.py` builders: `data_product`, `script`, `data_product_output`, `storage`
- Version pin and drift

## Why this file exists

Every signature below was read directly from the installed `nxd.spec` package
source (not inferred, not from a worked example) and is pinned to the package
version in "Version pin and drift" below. **Trust this file. Do not
re-verify these signatures against the nxd source tree unless a concrete
runtime error contradicts what's written here** — a `TypeError`, an
`AttributeError`, or a validation error whose message doesn't match this
doc. Pre-emptively re-reading `nxd/spec/*.py` "just to be sure" before every
`models.py`/`spec.py` you write is the exact cost this file exists to remove.
If the installed package version differs from the pin below, treat this file
as unverified for that version and re-derive the changed piece only — not
the whole surface.

This file is also the **normative input to the Phase A structural check** in
[reference/self-check.md](self-check.md): the closed sets that check enforces
(data-type constructors, role-builder keyword names, `Agg` members,
`semantic_model`/`semantic_view` and `spec.py` builder shapes) are transcribed
from the tables below. The check therefore inherits this file's version pin and
its drift caveat — it validates against **v0.41.139 as written here, not against
the installed wheel**. When a signature below changes, update both files in the
same edit, or the check will reject valid code.

## Data types (`nxd.spec.data_types`)

All zero-argument unless noted; import what you need, e.g.
`from nxd.spec.data_types import string, number, boolean, date32`.

| Constructor | Notes |
|---|---|
| `string()`, `boolean()` | |
| `date()`, `date32()`, `date64()` | `date32()` is the proven pin used by the worked CSV example |
| `double()`, `float()`, `float16()`, `float32()`, `float64()` | |
| `int8()`, `int16()`, `int32()`, `int64()` | |
| `uint()`, `uint8()`, `uint16()`, `uint32()`, `uint64()` | `uint()` is an alias for `uint16()` |
| `number()` | A generic numeric type distinct from the sized int/float variants — what the inferred-type mapping below uses for every numeric |
| `vector()` | |
| `binary(length=None)`, `vector_embeddings(dimensions)`, `timestamp(unit, timezone=None)`, `decimal(precision, scale)`, `duration(unit)`, `time32(unit)`, `time64(unit)` | Parameterized; `unit` is a `DurationUnit` from `nxd.core.yaml_schemas` |
| `list(value_type)`, `list_view(value_type)`, `large_list(value_type)`, `large_list_view(value_type)`, `map(key_type, value_type)`, `dictionary(key_type, value_type)`, `struct(fields)` | Complex/nested types — `value_type`/`fields` are `Field` instances from `nxd.core.yaml_schemas` |
| `variant()` | Free-form semi-structured data (arbitrary JSON keys); wire-equivalent to `struct([])` |

The desktop closure pattern only ever needs `string()`, `number()`,
`boolean()`, `date32()` — the rest exist for the k8s/cloud topology's richer
source types.

**Inferred type → constructor** (what Step 2 places from the inferred model):

| Inferred | Constructor |
|---|---|
| string | `string()` |
| int, number, double, float | `number()` |
| bool | `boolean()` |
| date | `date32()` |

## Semantic role builders (`nxd.spec`)

All return a `RoleSpec` (a `(kind, blob)` pair) except `field()`/`metric_field()`,
which return an `AttributeSpec` (a full column) already carrying the role blob.

- **`primary_key()`** — no arguments. The entity-key role.

  **Pair it with a `dimension(...)` on the same field.** Roles compose —
  `field(string(), primary_key(), dimension(name=..., description=...))` is
  accepted — and a key that carries only `primary_key()` is **not groupable**:
  it never appears in `describe_models`, so no query can return which entity a
  row belongs to. The product still builds, publishes and answers aggregate
  counts, which is what makes this one expensive to find: there is no error, no
  failed assert, and no missing table — only every entity-level question
  quietly having no answerable form. The same applies to a `join()` column a
  consumer needs to group by — and give that companion dimension a
  model-tagged name, per `dimension` below: naming it after the key it
  points at duplicates the dimension the target already declares, and a
  join makes the two models connected by construction.
- **`dimension(name=None, pii=False, label=None, description="")`** — a
  groupable/filterable column. `label` attaches a companion display column
  (`label_column` in the compiled blob).

  **`name` is unique across the join-connected registry, not per model.** Two
  models that a `join()` relates may not both declare a dimension called
  `created_at`, and the compiler refuses the whole spec:

  ```
  error: Duplicate dimension name 'created_at', declared 2 times across models
  'm1', 'm2'. Every dimension must have a unique name within the registry —
  rename all but one of those declarations.
  ```

  Unconnected models do not collide, which is what makes the timing surprising:
  the same two columns compile fine until the join that relates them is added,
  so the error arrives with a change that touched neither column. Tag every
  dimension with its model — `dimension(name="issue_created_at", ...)` — rather
  than renaming only the collisions already reported. Piecemeal renaming is not
  closed under itself: a tag invented for one collision can land on a real
  column of another model and produce a fresh one (`status` renamed to
  `judgment_status` colliding with an existing `judgment_status`).
- **`join(to, to_column=None, cardinality=None, to_data_product=None)`** —
  `to` is the target model name (positional or keyword). `cardinality`
  defaults to `Cardinality.MANY_TO_ONE` — nearly always leave it unset.
  `to_data_product` is for a cross-DP join edge (mesh only); leave `None` on
  desktop.

  **`to_column` must cover the target model's `primary_key()`**, not merely a
  column whose values happen to be unique:

  ```
  error: join declares MANY_TO_ONE onto 'linear_issues_landed', but key id does
  not cover that model's grain identifier; missing key columns: identifier. A
  lookup row is not uniquely identified, so measures would be double-counted.
  ```

  The usual trap is a landed source key sitting beside the declared one — an
  API's opaque `id` next to the human-readable `identifier` the model actually
  keys on. Join on whichever column the target declares as its key, or declare
  the other one as the key; a composite key needs every one of its columns.

- **`metric(agg, of=None, name=None, description="", boolean=False, extra_dimensions=(), column=None)`**
  — `agg` is an `Agg` value. `of` is a `FieldRef` (from `<model>.field("<col>")`)
  pointing at the base column being aggregated; mutually exclusive with the
  explicit `column=` override — pass one or neither, never both. Use
  `column="*"` only for `COUNT(*)`. For `Agg.EXPRESSION`, omit both `of` and
  `column`; the output port's `expressions={...}` SQL names the fields.
- **`field(dtype, *role_args, roles=None, description=None, label=None, name="")`**
  — builds a base-model column. `role_args` takes `primary_key()` /
  `dimension()` / `join()` positionally; a `metric()` role here **raises** —
  metrics are consume-time-only, declared on a `semantic_view` instead.
- **`metric_field(dtype, metric_role, description=None, name="")`** — the
  `semantic_view` counterpart to `field()`. `metric_role` must be a
  `metric()` result; anything else raises.

**Neither rule is visible offline.** `self_check.py`'s structural phase parses
`models.py` against the surface described here and passes on both; they are the
compiler's own validation, so the workflow-v2 supervisor's returned
`start_requirement` action reports them for an enrolled construction, as
`structure/spec_compile_failed`. `check_data_product` is the compatibility
validator only for an explicitly feature-off/non-enrolled runtime. Do not use
either surface to bypass workflow-v2 capture, review, or admission. Note also
that the compiler prints
`deployment-spec.yaml: OK` / `manifest.yaml: OK` *after* the error line and
still exits non-zero — read the `error:` line, not the tail.

A field may also be written as a bare `dtype` (no role) or a
`(dtype, *rest)` tuple inside `.schema({...})` — see `SemanticModelSpec`
below; `field()`/`metric_field()` are for when you need to attach `label` or an
explicit `name` inline. `description=` is accepted there too, but it is an
attribute description and never reaches the agent — see the next section.

### `description=` — two parameters, only one reaches the agent

`description=` appears on both the **role builders** and the **field wrappers**,
and they land in different places. This is the single easiest thing to get
wrong here:

| Written as | Lands in | Seen by the querying agent? |
|---|---|---|
| `dimension(description=...)`, `metric(description=...)` | the role blob → `Role::Metric.description` / dimension description | **Yes** — this is what `describe_model` shows |
| `field(description=...)`, `metric_field(description=...)` | `AttributeSpec._description` → manifest attribute description | **No** — the roles alone are serialized into the blob; it surfaces only in the structural `data_model` block |

So the human sentence a consumer reads when choosing a measure or a dimension
**must** go inside the role:

```python
# RIGHT — the description reaches describe_model
"total_revenue": metric_field(
    float64(),
    metric(Agg.SUM, of=orders.field("AMOUNT_USD"),
           name="total_revenue",
           description="Gross order amount in USD across ALL statuses."),
)

# WRONG — this string only ever appears in data_model
"total_revenue": metric_field(
    float64(),
    metric(Agg.SUM, of=orders.field("AMOUNT_USD"), name="total_revenue"),
    description="Gross order amount in USD across ALL statuses.",
)
```

Model-level description **does** reach the agent — it is emitted in both
`list_models` and `describe_model`. The chained
`semantic_model(...).description(...)` form is the one verified against the
runtime; the `description=` constructor kwarg pinned in the signature above is
documented and is what the vendored `nextdata-public-examples` corpus uses, but
has not been traced end-to-end. Prefer the chained form; tooling accepts both.

## `Agg` — the closed aggregation vocabulary

`from nxd.spec import Agg` — despite appearing as `TYPE_CHECKING`-only in the
package source, this import works at runtime (a module-level `__getattr__`
lazily resolves it). It's a `str` enum; use it by member name
(`Agg.SUM`, not a bare string). The vocabulary is **closed**:

```
Agg.COUNT, Agg.COUNT_DISTINCT, Agg.SUM, Agg.AVG, Agg.MIN, Agg.MAX, Agg.EXPRESSION
```

`Agg.EXPRESSION` is a custom SQL aggregate slot, not a new metric kind and not
an escape hatch for missing semantics. It still produces a port-level aggregate
expression, so it cannot define row-level dimensions, row generation/removal,
default filters, cross-row transformations, or unsupported statistical
functions. In particular, **median does not exist and never will via this
path** — do not invent a `MEDIAN`/`PERCENTILE` metric kind, do not smuggle
median SQL through `Agg.EXPRESSION`, and do not promise median unless the
product surface gains a first-class supported aggregation.

## `SemanticModelSpec` / `semantic_model()` / `semantic_view()`

- **`semantic_model(name, attributes=None, description=None, sampling=None)`**
  — starts a base model. `attributes` is rarely passed here; the normal
  pattern builds an empty model then calls `.schema({...})`.
- **`.schema(attributes: dict)`** (alias: **`.fields(...)`**, identical) —
  each dict value is one of: a bare `DataType` (no role), an `AttributeSpec`
  (a `field()`/`metric_field()` result), or a tuple `(dtype, *rest)` where
  trailing items are one optional `str` description plus any number of role
  specs. All three forms are equivalent; mix freely across attributes.
- **`.field(name) -> FieldRef`** — a handle to one of this model's columns,
  used as `metric(of=base.field("<col>"))` from a `semantic_view`. Does not
  require the field to already be declared.
- **`.description(text)`** — sets the model's description; chainable.
- **`semantic_view(name, base, attributes=None, description=None)`** — starts
  a view over `base` (a `semantic_model`). A view's fields are built with
  `metric_field(...)`; unlike a base model, a `metric()` role is allowed in
  its `.schema({...})` tuples too. A view with zero attributes raises at
  build time — always give it at least one `metric_field`.

## `spec.py` builders: `data_product`, `script`, `data_product_output`, `storage`

- **`data_product(name=, domain=, version=, infra_profile=, ...)`** — starts
  the spec. `infra_profile` is the string name matching
  `infra-profile.yaml`'s `metadata.name` (`"desktop-local"` on desktop).
  Several other keyword args exist (`versioned`, `defer_provisioning`, etc.)
  — none apply to the desktop closure pattern; leave them unset.
  - **`.transform(function_spec)`** — takes a `UserCodeSpec` (what `script(...)`
    returns), not a bare function.
  - **`.output(output_spec)`** — takes a `DataProductOutputSpec` (what
    `data_product_output()` returns).
  - **No `.semantic_tools(...)`** on desktop — it exists on `DataProductSpec`
    but wires a k8s RPC port; see the Invariants section elsewhere in this
    skill for why it's wrong here.
- **`script(path) -> UserCodeSpec`** — one file, the transform entrypoint.
  - **`.compute(url, alias=None)`** — binds the compute service (`url` is the
    infra-profile service reference, e.g.
    `"/infra-profile/desktop-local#/services/python-compute"`).
  - **`.secrets(urls: list[str])`** — one infra-profile service reference per
    source instance; **replaces** any prior call rather than accumulating —
    always pass the complete list in one call.
- **`data_product_output() -> DataProductOutputSpec`**
  - **`.port(name, port_spec)`** — `port_spec` comes from `storage(...)`;
    raises if `name` is already registered on this output.
  - **`.promise(promise)`** — a `SemanticModelSpec` (or a custom
    `VerificationSpec`); registers a produce-time contract that the model's
    physical table gets written. Never call this for a `semantic_view`.
  - **`.model(model, is_public=True, expressions=None)`** — registers a model
    (base or view) into the output's global model list without a
    produce-time contract; this is how a `semantic_view` reaches the
    compiled catalog. `is_public` defaults `True`. `expressions` is
    **validated and then discarded** here — only the port-level `.model()`
    below persists it, so an expression map passed at this level silently
    does nothing. Use this same registration for a physical model whose
    resource is explicitly allowed to yield zero rows; use `.promise(model)`
    for required physical models so the output contract still verifies their
    table.
- **`storage(url, alias=None) -> OutputPortSpec`** — `url` is the
  infra-profile service reference for the storage backend (e.g.
  `"/infra-profile/desktop-local#/services/duckdb"`).
  - **`.model(model, is_public=True, expressions=None)`** — registers the
    model on this port. This is the **only** surface that persists
    `expressions` (a `dict[str, str]` keyed by metric name, valued with the
    SQL for an `Agg.EXPRESSION` metric). Outside this skill's desktop
    closure pattern — see the `Agg` note above.

## Version pin and drift

Verified against the installed `nxd` package at **`v0.41.139`**
(`nxd/version.py`). The two compiler rules above — registry-wide dimension
names and join-covers-grain — were additionally reproduced against `0.41.172`
by compiling closures that violate each; the rest of this file has not been
re-verified at that version. Treat this the same way the closure pins
`dlt[duckdb]==1.28.2` — if the installed version differs, this file may be
stale; re-derive only the specific signature that produces an unexpected
error, not the whole surface.
