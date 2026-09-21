# `models.py` and `spec.py` worked examples

## Contents

- [`models.py`](#modelspy)
- [`spec.py`](#specpy)

## `models.py`

Matches the proven closure referenced by `SKILL.md` Step 2 — a full
`models.py` showing base models with placed roles plus one semantic view with
a metric. Use it as the concrete shape to match; do not deviate from the
`primary_key()` / `dimension(...)` / `join(...)` / `metric_field(metric(...))`
DSL calls shown here.

```python
"""Base models and query-time metrics for the desktop DP."""

from nxd.spec import Agg, semantic_model, semantic_view
from nxd.spec.data_types import number, string
from nxd.spec import dimension, field, join, metric, metric_field, primary_key

customers = (
    semantic_model("customers")
    .description("One row per customer account.")
    .schema(
        {
            # Type IDs from observed values, not habit: numeric-looking is not
            # numeric, and these keys are "C0417"-style strings.
            # primary_key() AND dimension(): a key with only the key role is
            # not groupable, so `describe_models` offers no way to ask WHICH
            # customer a row belongs to — every answer comes back as a count
            # with no identity. Roles compose; keys nearly always need both.
            "customer_id": field(
                string(),
                primary_key(),
                dimension(name="customer_id"),
                description="Account key, e.g. C0417. Group by this to name a customer.",
            ),
            "country_id": field(
                string(),
                dimension(name="customer_country"),
                # The description goes on the field. The dimension inherits it,
                # so describe_models and the catalog UI show the same sentence.
                description="ISO-3166 alpha-2 country of the billing address.",
            ),
            # No question named this column, and it is annotated anyway: the
            # questions decide the ROLE, not whether to annotate. Bare-typing
            # it would drop it from describe_models entirely.
            "country": field(
                string(),
                dimension(name="customer_country_name"),
                description=(
                    "Country display name. Duplicates country_id — prefer "
                    "customer_country for grouping."
                ),
            ),
            "email": field(
                string(),
                dimension(
                    name="customer_email",
                    # Flagged from the DATA — the samples are addresses — not
                    # because a question asked for it.
                    pii=True,
                ),
                description="Primary contact email.",
            ),
        }
    )
)

orders = (
    semantic_model("orders")
    .description("One row per placed order.")
    .schema(
        {
            # order_id is number() here because its observed values are numeric —
            # the two ID shapes sit side by side deliberately.
            # Same pairing as customers.customer_id above, and for the same
            # reason: a key carrying only primary_key() cannot be grouped by,
            # so "which orders..." has no answerable form.
            "order_id": field(
                number(),
                primary_key(),
                dimension(name="order_id"),
                description="Order key. Group by this to name an order.",
            ),
            "customer_id": field(
                # Matches the customers.customer_id type; join endpoints must agree.
                string(),
                # join() has no description parameter — a join is not a concept
                # an agent selects.
                join(to="customers", to_column="customer_id"),
            ),
            # Bare is correct HERE and only here: a declared metric aggregates
            # this column, so its meaning travels on total_order_amount. A
            # numeric NO metric names would take a number dimension instead.
            "amount": number(),
        }
    )
)

order_metrics = semantic_view("order_metrics", orders).schema(
    {
        "total_order_amount": metric_field(
            number(),
            metric(
                Agg.SUM,
                of=orders.field("amount"),
                name="total_order_amount",
            ),
            description=(
                "Gross order amount across ALL statuses. The role grammar "
                "has no filtered metrics, so this is unconditional — "
                "consumers filter at query time."
            ),
        ),
    }
)
```

## Two ways a model is landed but unanswerable

Both are silent: no error, no failed assert, no missing table — only questions
that quietly have no answer. They are the join- and view-shaped siblings of the
bare `primary_key()` already shown above.

**A promised model backing no `semantic_view`.** `run_semantic_query` requires at
least one measure, so a model no metric reaches cannot be selected at all. Its
rows are then reachable only through another model's metric across a join — and
there a filter on the joined model scopes that model's *aggregate*, not this
model's spine, so the query returns every row of the model you were trying to
narrow and looks like it worked. A `COUNT` of the key is enough:

```python
ticket_signals_metrics = semantic_view("ticket_signals_metrics", ticket_signals).schema(
    {
        "evidence_count": metric_field(
            number(),
            metric(
                Agg.COUNT,
                of=ticket_signals.field("evidence_id"),
                name="evidence_count",
            ),
            description="Number of cited evidence spans.",
        ),
    }
)
```

This does not contradict "metrics stay question-driven". That rule decides *what
to aggregate*; this one decides *whether the model can be reached at all*.

**A field carrying only `join(...)`.** A join is a traversal edge and nothing
else — it never reaches `describe_models`, so the model cannot be filtered or
grouped by the entity it points at. Compose it with a dimension exactly as a key
is composed:

```python
"identifier": field(
    string(),
    join(to="open_pocket_tickets", to_column="identifier"),
    dimension(name="evidence_ticket"),
    description="Ticket this evidence was cited for. Filter on this to read one ticket's citations.",
),
```

`struct.model_not_queryable` and `struct.key_not_groupable` report both.

## `spec.py`

The paired `spec.py` for SKILL.md Step 4 — the same closure, promising base and
derived models alike on the `duckdb` port and registering the metric view. The
contract facts that must survive any edit to this shape are listed in Step 4;
this is the shape they produce.

```python
"""A desktop data product authored entirely in Python. The supervisor compiles
this to the kernel definition YAML at create time."""

from nxd.spec import data_product, data_product_output, script, storage

from models import customers, orders, monthly_spend, order_metrics

_csv = "/infra-profile/desktop-local#/services/csv-source"
_compute = "/infra-profile/desktop-local#/services/python-compute"
_duckdb = "/infra-profile/desktop-local#/services/duckdb"

_output = (
    data_product_output()
    .promise(customers)
    .promise(orders)
    .promise(monthly_spend)      # derived — promised identically to a base model
    .model(order_metrics)
    .port("duckdb", storage(_duckdb))
)

spec = (
    data_product(
        name="<dp-name>",
        domain="desktop.local",
        version="0.0.1",
        infra_profile="desktop-local",
    )
    .transform(script("transform/main.py").compute(_compute).secrets([_csv]))
    .output(_output)
)
```
