# Semantic authoring guide

## Contents
- Core concepts
- Public role grammar
- Metrics on semantic views
- Cross-model dimensions
- Worked example
- Validation and common mistakes

---

## Core concepts

A semantic registry describes physical models, their entity keys and dimensions,
their N:1 relationships, and the metrics available over each model. Author it
only with the public `nxd.spec` DSL:

| Element | Where it belongs | Public form |
|---|---|---|
| Entity key | Physical `semantic_model` field | `field(type, primary_key(), dimension(name=...), description=...)` — pair the key role with a dimension or the key is not groupable |
| Dimension | Physical `semantic_model` field | `field(type, dimension(...))` |
| Join | Physical `semantic_model` field | `field(type, join(...))` |
| Metric | Query-time `semantic_view` field | `metric_field(type, metric(...))` |
| Model description | The model itself | `semantic_model(name).description(text)` |
| Concept description | On the **field**, once | `field(..., description=...)`, `metric_field(..., description=...)` — the role inherits it |

Every physical model has one or more entity-key fields. Multiple
`primary_key()` fields define a composite key. A semantic view belongs to one
base model and is registered with `.model(view)` on the output; it is never
promised or written by the transform.

**Every field carries semantic information.** A role decides whether the field
is queryable at all — a column with none produces no metric, dimension or join
and is absent from `describe_model` (see `overview.md`). A description decides
whether it is queryable *correctly*: `describe_model` is the entire basis on
which a consuming agent maps a question to a concept, so a dimension that
arrives as a bare name gives it nothing to choose on. Declare a role on every
field **a metric does not already aggregate**, a `description` on every
**dimension and metric** role, and a `.description(...)` on every
`semantic_model`. `primary_key()` and `join()` accept no
`description` — do not try to attach one, and never fall back to putting it on
the enclosing `field()`, which never reaches the agent.

---

## Public role grammar

```python
from nxd.spec import Agg, dimension, field, join, metric, metric_field
from nxd.spec import primary_key, semantic_model, semantic_view
from nxd.spec.data_types import float64, int64, string
```

### Entity key

```python
"ORDER_ID": field(int64(), primary_key(), dimension(name="order_id"), description="Order key.")
```

The key must identify one row of the physical model. Use more than one field when
the source has a validated composite key.

### Dimension

```python
"COUNTRY": field(
    string(),
    dimension(
        name="country",
        pii=False,
    ),
    description="ISO-3166 alpha-2 country of the customer's billing address.",
)
```

`name` is the stable concept used in a semantic query, `description` is what the
consuming agent reads when deciding whether this is the concept the question
meant, and `pii=True` marks governed personal data.

Write the description so it distinguishes this concept from its neighbours and
states anything a consumer would otherwise have to assume — the unit, the
basis, the population, or the ruling that produced it. "Country of the
customer" is not enough when a model also carries a shipping country; name
which one and where it comes from.

> **Write each description once, on the field.** `field(..., description=...)`
> and `metric_field(..., description=...)` reach `describe_model`: a dimension
> or metric declaring none of its own inherits the field's, and the catalog UI
> shows the same sentence. `dimension(description=...)` /
> `metric(description=...)` still win over it but are deprecated and emit a
> `FutureWarning`. For the full generator DSL reference,
> consult `nxd-generate-data-product`'s `reference/nxd-spec-api.md` when that
> companion skill is installed; it is not a DataMesh bundle dependency.

### Join

```python
"CUSTOMER_ID": field(
    int64(),
    join(to="customer_profile", to_column="CUSTOMER_ID"),
)
```

Declare the join on the many-side foreign key. `to` is the one-side model and
`to_column` is its entity key. Only a validated many-to-one relationship is safe
for slicing a fact metric by dimensions from the related model.

---

## Metrics on semantic views

Do not add a metric role to a physical base field. A metric is a query-time view
field that names an aggregation over a base-model column:

```python
order_metrics = semantic_view("order_metrics", orders).schema(
    {
        "order_count": metric_field(
            int64(),
            metric(
                Agg.COUNT,
                of=orders.field("ORDER_ID"),
                name="order_count",
            ),
            description="Number of order rows, including cancelled orders.",
        ),
        "total_revenue": metric_field(
            float64(),
            metric(
                Agg.SUM,
                of=orders.field("AMOUNT_USD"),
                name="total_revenue",
            ),
            description=(
                "Gross order amount in USD across ALL statuses, including "
                "refunded and cancelled. Filter on the order_status "
                "dimension for a net figure."
            ),
        ),
        "unique_customers": metric_field(
            int64(),
            metric(
                Agg.COUNT_DISTINCT,
                of=orders.field("CUSTOMER_ID"),
                name="unique_customers",
            ),
            description="Distinct customers with at least one order.",
        ),
    }
)
```

Every metric carries a `description`. It is the string the consuming agent
matches a question against, so it must say what the number *is* — the unit, and
the population it covers. `total_revenue` above is the pattern for an
aggregation the role grammar cannot qualify: the metric is unconditional, so
the description states that and points at the dimension a caller filters on.

This skill authors metrics with `Agg.COUNT`, `Agg.COUNT_DISTINCT`, `Agg.SUM`,
`Agg.AVG`, `Agg.MIN`, and `Agg.MAX`. Put boolean counts on a `SUM` metric over
the flag field only when the intended result is a count of truthy rows.

A seventh member, `Agg.EXPRESSION`, exists on the API. It is a custom SQL
aggregate slot whose SQL is supplied by the output **port** model's
`expressions={...}` map, keyed by metric name — so it is only usable on a
topology whose port carries that map. It is not a derivation surface: it cannot
define dimensions, generate or remove rows, apply default filters, or add
unsupported statistical kinds such as median. Prefer the six aggregations above
and materialize business rulings as physical columns.

---

## Cross-model dimensions

The compiler derives compatible dimensions from a validated N:1 join. If
`orders.CUSTOMER_ID` joins to `customer_profile.CUSTOMER_ID`, metrics on the
`orders` view can be sliced by non-PII customer dimensions such as `country`.
Do not model a many-to-many relationship as a direct semantic join; it can change
the fact row count and invalidate metric results.

---

## Worked example: people and events

```python
from nxd.spec import Agg, dimension, field, join, metric, metric_field
from nxd.spec import primary_key, semantic_model, semantic_view
from nxd.spec.data_types import int64, string

people = (
    semantic_model("people")
    .description("One row per registered person.")
    .schema(
        {
            "PERSON_ID": field(int64(), primary_key(), dimension(name="person_id"), description="Person key."),
            "COUNTRY_CODE": field(
                string(),
                dimension(name="country"),
                description="ISO-3166 alpha-2 country the person registered from.",
            ),
            "EMAIL": field(
                string(),
                dimension(
                    name="email",
                    pii=True,
                ),
                description="Primary contact email address.",
            ),
        }
    )
)

activity_events = (
    semantic_model("activity_events")
    .description("One row per product activity event emitted by a person.")
    .schema(
        {
            "EVENT_ID": field(int64(), primary_key(), dimension(name="event_id"), description="Event key."),
            "EVENT_TYPE": field(
                string(),
                dimension(name="event_type"),
                description=(
                    "Kind of activity recorded — one of login, view, "
                    "export, share."
                ),
            ),
            "PERSON_ID": field(
                int64(),
                join(to="people", to_column="PERSON_ID"),
            ),
        }
    )
)

event_metrics = semantic_view("event_metrics", activity_events).schema(
    {
        "event_count": metric_field(
            int64(),
            metric(
                Agg.COUNT,
                of=activity_events.field("EVENT_ID"),
                name="event_count",
            ),
            description="Number of activity events recorded.",
        ),
        "unique_actors": metric_field(
            int64(),
            metric(
                Agg.COUNT_DISTINCT,
                of=activity_events.field("PERSON_ID"),
                name="unique_actors",
            ),
            description="Distinct people who emitted at least one event.",
        ),
    }
)
```

Note the primary-key and join fields carry no description — `primary_key()` and
`join()` have no such parameter, and neither is a concept an agent selects.
Every dimension and metric field does.

`event_count` and `unique_actors` can be sliced by `country`; the PII `email`
dimension is not propagated to the related metrics.

---

## Validation and common mistakes

- Every field carries semantic information: either its own role, or it is the
  `of=` target of a declared metric. A measure column an aggregation already
  names needs no dimension of its own — grouping by a continuous amount is not
  a useful slice. Everything else takes a role. A column that is neither roled
  nor aggregated produces no metric, dimension or join and is invisible to
  `describe_model` — that is a decision to make it unqueryable, not a neutral
  default. The **marker model** is the one exception to the *role* rule: it
  exists to satisfy the storage port's produce-verification and is never a
  query target, so its columns stay bare and out of the consumer's catalog. It
  still carries a `.description(...)` like any other `semantic_model` — both
  shipped templates give it one.
- Every dimension and metric carries a `description`, and every
  `semantic_model` a `.description(...)`. A `semantic_view` may carry one too
  (`semantic_view(..., description=)`); the gates do not require it. A concept the agent cannot tell apart from its
  neighbours is as unusable as one that was never declared.
- Put the description **inside** the role builder. `field(description=...)` and
  `metric_field(description=...)` are attribute descriptions and never reach
  the querying agent.
- Use a unique, non-null source key for every physical model. Do not synthesize
  a key just to make the model compile.
- Keep physical names and field names identical to the actual table and source
  columns. The transform must seed the same physical tables.
- Promise every physical base with `.promise(base)`. Register every metric view
  with `.model(view)`.
- Do not put metrics from different base models into one semantic query. Query
  each model separately.
- Do not use a relationship until foreign-key containment and the target key's
  uniqueness have been validated.
