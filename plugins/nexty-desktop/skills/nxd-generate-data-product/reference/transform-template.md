# The `transform/main.py` base-ingest template

Worked code for Step 3 of nxd-generate-data-product — the dlt-through-port ingest that
lands each base model's CSV directory into the local DuckDB output port. The
mandatory contract clauses (the "keep every one" list) live in SKILL.md Step 3;
this file is the shape they produce. For the derived-model additions (the
`@dlt.resource` block that slots between the base-reader loop and
`pipeline.run(...)`), see [derived-models.md](derived-models.md).

## Contents

- [The template](#the-template)
- [The source-checkout shim](#the-source-checkout-shim)

## The template

```python
"""<dp-name>: load the CSV connector export into the local DuckDB output port."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Source-checkout shim: see reference/derived-models.md. Copy it verbatim —
# it keeps an interpreter without the wheel working, and is a no-op otherwise.

import dlt
from dlt.sources.filesystem import filesystem, read_csv

from nxd import data_product
from nxd.core.context import DuckDbOutput

# Landed tables exposed by spec.py; never semantic views.
# Required models are produce-time promises. Optional models are catalog-only
# registrations because a zero-row dlt resource has no physical table.
# BASE_MODELS have data/<name>/; DERIVED_MODELS are computed below.
BASE_MODELS = ("<base_model>",)
DERIVED_MODELS = ()
PHYSICAL_MODELS = BASE_MODELS + DERIVED_MODELS
# A member must be in PHYSICAL_MODELS and may be absent only when its resource
# yields zero rows. Optional physical models are registered with .model(...),
# never .promise(...), in spec.py.
OPTIONAL_EMPTY_MODELS = ()


@data_product.on_transform()
def ingest(duckdb: DuckDbOutput, secrets: dict[str, Any]) -> None:
    """Land each promised model into the DuckDB output port."""
    source_root = Path(secrets["csv_source"])
    # Keep ALL dlt state run-local (next to the staging file) — never ~/.dlt.
    # If this transform also maps fields, pass `run_dir / "run" / "mapper"`
    # to `map_inputs`; never pass this parent directly as the mapper ledger root.
    run_dir = Path(duckdb.path).parent
    pipelines_dir = run_dir / "dlt-pipelines"
    pipelines_dir.mkdir(parents=True, exist_ok=True)
    os.environ["DLT_DATA_DIR"] = str(run_dir / "dlt-data")

    pipeline = dlt.pipeline(
        pipelines_dir=str(pipelines_dir),
        destination=dlt.destinations.duckdb(credentials=duckdb.path),
        dataset_name=duckdb.schema,
    )
    resources = []
    # Base models: one dlt CSV reader per data/<model>/ directory. An optional
    # source directory may be absent before a human supplies its first rows.
    for model in BASE_MODELS:
        if model in OPTIONAL_EMPTY_MODELS and not (source_root / model).is_dir():
            continue
        reader = filesystem(
            bucket_url=str(source_root / model), file_glob="*.csv"
        ) | read_csv()
        resources.append(reader.with_name(duckdb.model_tables[model]))
    # Derived models (Step 3a) append their @dlt.resource here — same list.
    pipeline.run(resources, write_disposition="replace")

    # dlt must write exactly the declared physical tables, except that an
    # explicitly optional zero-row resource has no table to write. Unexpected
    # tables and missing required tables remain failures.
    actual = set(pipeline.default_schema.data_table_names())
    expected = {duckdb.model_tables[model] for model in PHYSICAL_MODELS}
    optional = {duckdb.model_tables[model] for model in OPTIONAL_EMPTY_MODELS}
    missing = expected - actual
    absent_optional = missing & optional
    if actual != expected - absent_optional:
        raise RuntimeError(
            f"dlt produced tables {sorted(actual)!r}, expected required tables "
            f"{sorted(expected - optional)!r}; optional absent tables "
            f"{sorted(absent_optional)!r}; unexpected tables "
            f"{sorted(actual - expected)!r}"
        )
    # Produce-verification marker: the supervisor's readiness gate waits for it.
    (run_dir / ".transform-complete").touch()


if __name__ == "__main__":
    data_product.main()
```

## The source-checkout shim

The `# Source-checkout shim` comment in the template above is where this block
goes — copy it verbatim, directly below the stdlib imports and **above**
`import dlt`. The full shim, and why `reversed(...)` matters, is documented in
[derived-models.md](derived-models.md#the-source-checkout-shim).
