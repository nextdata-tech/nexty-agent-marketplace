#!/usr/bin/env python3
"""Scaffold a semantic-layer data product (.semantic_tools() pattern).

Writes placeholder `models.py`, `transform.py`, and `requirements.txt` as flat
siblings at the DP root, then prints the `spec.py` wiring block.

The four governed MCP tools (`list_models`, `semantic_model`, `describe_model`,
and `run_semantic_query`) are auto-generated at pod boot by
`.semantic_tools(service=...)`. Author the vocabulary with the public `nxd.spec`
DSL: physical bases use `field(..., primary_key()|dimension()|join())`, while
metrics use `metric_field(metric(...))` on a query-time `semantic_view(...)`.
The platform compiles these roles into the runtime payloads; author no tool code.

Usage:
    uv run python scaffold_semantic_dp.py <target_dir>

`<target_dir>` is the root of the data product directory, which will contain
`spec.py`. Fill the placeholders from either a supplied schema or a profiled
source and the user's questions. Validate every entity key and N:1 join against
the full source before declaring it.

Wiring constraints:
1. Promise every physical base model on the storage port. Register each metric
   view with `.model(view)`, never `.promise(view)`.
2. `.semantic_tools(service=...)` creates the RPC output and all four tools. Do
   not add `data_product_rpc_output()`.
3. The transform seeds physical base tables and writes a marker row for storage
   produce-verification. It creates no semantic-view DDL.
"""

from __future__ import annotations

import sys
from pathlib import Path


REQUIREMENTS_LINES = [
    "nxd.data_product[spec]",
    "nxd.drivers[rpc]",
    "snowflake-connector-python[pandas]",
    "pandas",
    # Supports the split-pod fallback that reconstructs the compiled semantic
    # payload when the MCP pod has no kernel-delivered payload.
    "pyyaml>=6.0.2",
]

_MODELS_STUB = '''\
"""Physical semantic models and query-time metrics.

Use public `nxd.spec` role builders only. Physical bases carry entity keys,
dimensions, and joins; metrics belong on `semantic_view(...)`. The platform
compiles these public roles into the semantic catalog served by `.semantic_tools()`.
"""

from nxd.spec import Agg, dimension, field, join, metric, metric_field
from nxd.spec import primary_key, semantic_model, semantic_view
from nxd.spec.data_types import float64, int64, string


# ── <model_a> — one row per <entity_a> ──────────────────────────────────────
your_model = (
    semantic_model("<model_a>")
    .description("One row per <entity_a>.")
    .schema(
        {
            # primary_key() alone is not groupable; pair it with a dimension.
            "<KEY_A>": field(int64(), primary_key(), dimension(name="<key_a>", description="<Entity> key.")),
            "<DIM_COL>": field(
                string(),
                dimension(
                    name="<dim_name>",
                    description="<what this dimension means to a consumer>",
                ),
            ),
            "<METRIC_COL>": float64(),
        }
    )
)

# Metrics are query-time fields. Add a separate metric view for each physical
# base that has metrics; do not attach a metric to the base field itself.
your_model_metrics = semantic_view("<model_a>_metrics", your_model).schema(
    {
        "<metric_name>": metric_field(
            float64(),
            metric(
                Agg.SUM,
                of=your_model.field("<METRIC_COL>"),
                name="<metric_name>",
                description="<what this number is: unit, and the population it covers>",
            ),
        ),
        # COUNT example:
        # "<entity_count>": metric_field(
        #     int64(),
        #     metric(Agg.COUNT, of=your_model.field("<KEY_A>"),
        #            name="<entity_count>",
        #            description="<what this counts>"),
        # ),
    }
)

# N:1 join example for a separate physical base:
# "<FOREIGN_KEY>": field(
#     int64(),
#     join(to="<other_model>", to_column="<OTHER_KEY>"),
# )

# ── marker model — satisfies storage produce-verification ────────────────────
provision_marker = (
    semantic_model("<dp_name>_marker")
    .description("Marker table written by the seeding transform.")
    .schema({"MARKER_ID": int64(), "VIEW_NAME": string()})
)
'''

_TRANSFORM_STUB = '''\
"""Transform — seeds the physical tables the semantic MCP tools query.

The transform creates idempotent base tables and writes a marker row. Query-time
semantic views need no warehouse DDL: `run_semantic_query` compiles SQL against
the physical base tables.
"""

from nxd.data_product.context import Snowflake


def transform(snowflake: Snowflake) -> None:
    import pandas as pd
    from snowflake import connector
    from snowflake.connector.pandas_tools import write_pandas

    if snowflake is None or not snowflake.schema:
        print("transform skipped — no Snowflake schema in context")
        return

    fqn = (
        f"{snowflake.database}.{snowflake.schema}."
        if snowflake.database
        else f"{snowflake.schema}."
    )

    # TODO: replace with real source or seed data.
    rows = pd.DataFrame(
        [
            {"<KEY_A>": 1, "<DIM_COL>": "value-1", "<METRIC_COL>": 100.0},
            {"<KEY_A>": 2, "<DIM_COL>": "value-2", "<METRIC_COL>": 250.0},
        ]
    )

    conn = connector.connect(
        user=snowflake.user,
        account=snowflake.account,
        warehouse=snowflake.warehouse,
        role=snowflake.role,
        database=snowflake.database,
        schema=snowflake.schema,
        ocsp_fail_open=True,
        **snowflake.connector_params(),
    )
    try:
        cur = conn.cursor()
        try:
            # The unquoted name must match semantic_model("<model_a>").
            cur.execute(
                f"CREATE OR REPLACE TABLE {fqn}<model_a> "
                "(<KEY_A> NUMBER, <DIM_COL> VARCHAR, <METRIC_COL> FLOAT)"
            )
            write_pandas(
                conn, rows, "<MODEL_A>",
                database=snowflake.database, schema=snowflake.schema,
            )
            print(f"seeded {fqn}<model_a> rows={len(rows)}")

            managed = snowflake.full_table_name("<dp_name>_marker")
            cur.execute(f"TRUNCATE TABLE IF EXISTS {managed}")
            write_pandas(
                conn,
                pd.DataFrame([{"MARKER_ID": 1, "VIEW_NAME": "n/a"}]),
                managed.split(".")[-1].strip('"'),
                database=snowflake.database, schema=snowflake.schema,
            )
            print(f"marker written to {managed}")
        finally:
            cur.close()
    finally:
        conn.close()
'''

SPEC_SNIPPET = """\\
# ── spec.py — .semantic_tools() auto-wires the four MCP tools ───────────────
from nxd.spec import code, data_product, data_product_output, storage
from transform import transform
from models import provision_marker, your_model, your_model_metrics

INFRA_PROFILE = "<infra-profile>"
SNOWFLAKE_SERVICE = "<snowflake-service>"
MCP_SERVICE = "mcp-api-service-k8s"

# Promise physical bases. Register query-time metric views separately.
_storage = (
    data_product_output()
    .promise(provision_marker)
    .promise(your_model)
    .model(your_model_metrics)
    .port("snowflake", storage(f"/infra-profile/{INFRA_PROFILE}#/services/{SNOWFLAKE_SERVICE}"))
)

spec = (
    data_product(
        name="<your-dp-name>",
        domain="<domain>",
        version="0.1.0",
        infra_profile=INFRA_PROFILE,
    )
    .transform(
        code(transform).compute(f"/infra-profile/{INFRA_PROFILE}#/services/k8s-compute")
    )
    .output(_storage)
    .semantic_tools(service=MCP_SERVICE)
)
"""


def _write_stub(path: Path, content: str, label: str) -> None:
    if path.exists():
        print(f"  [skip] {path} already exists — not overwriting.")
        return
    path.write_text(content, encoding="utf-8")
    print(f"  [write] {path} ({label})")


def scaffold(target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    print(f"  [ok] DP root ready: {target_dir}")

    _write_stub(
        target_dir / "models.py",
        _MODELS_STUB,
        "models with public semantic roles — adapt to your schema",
    )
    _write_stub(
        target_dir / "transform.py",
        _TRANSFORM_STUB,
        "self-seeding transform stub — adapt to your rows",
    )

    requirements = target_dir / "requirements.txt"
    if not requirements.exists():
        requirements.write_text("\n".join(REQUIREMENTS_LINES) + "\n", encoding="utf-8")
        print(f"  [write] {requirements} (dependencies)")
    else:
        print(f"  [skip] {requirements} already exists — add these lines if missing:")
        for line in REQUIREMENTS_LINES:
            print(f"            {line}")

    print()
    print("All modules are flat at the DP root (no transform/ subdir package).")
    print()
    print("spec.py wiring (.semantic_tools() auto-generates all four MCP tools):")
    print()
    print(SPEC_SNIPPET)
    print(
        "See reference/scripts/templates/semantic_dp.py.tmpl for the complete "
        "models.py + transform.py + spec.py example.\n"
        "See SKILL.md for the authoritative recipe these stubs mirror."
    )


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: uv run python {sys.argv[0]} <target_dp_dir>", file=sys.stderr)
        return 1
    target = Path(sys.argv[1]).resolve()
    print(f"Scaffolding semantic-layer DP in: {target}")
    scaffold(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
