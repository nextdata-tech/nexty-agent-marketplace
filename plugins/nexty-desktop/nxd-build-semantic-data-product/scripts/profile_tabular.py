#!/usr/bin/env python3
"""Profile local tabular samples for semantic model inference.

Two modes, one output shape:

  File mode:    profile_tabular.py <path.csv|.json|.jsonl|.ndjson|.parquet>
  DuckDB mode:  profile_tabular.py <path.duckdb|.ddb|.db> <table> [<table> ...]

File mode reads up to MAX_ROWS records and infers per-column type, null
presence, and sample values. DuckDB mode profiles MATERIALIZED tables
(``main.<table>``, e.g. ones written by a dlt sample load): it runs ``DESCRIBE``
plus a sampled ``SELECT`` (capped at MAX_ROWS) through the same row-profiling
logic, then enriches every column with exact full-table statistics —
``declared_type`` (from DESCRIBE), ``nullable``, ``null_pct``,
``distinct_count``, and ``cardinality`` (COUNT(DISTINCT col) / COUNT(*)) — all
computed over the FULL table, not the sample. Both modes emit the same
per-column JSON shape; file mode's nullable/null_pct/cardinality are computed
over the sampled rows only.

With ONE table, DuckDB mode prints that table's profile document. With TWO OR
MORE tables it prints one combined document — ``{"path": ..., "format":
"duckdb", "tables": {<table>: <profile>, ...}}`` — suitable for redirecting to
a ``schema.json`` handoff artifact:

  profile_tabular.py source.duckdb customers orders > schema.json

DuckDB mode requires the ``duckdb`` package (not a stdlib dep). If it is not
importable, run via ``uv run --with duckdb python profile_tabular.py ...`` or
``pip install duckdb``.

The script is intentionally read-only: it prints a JSON profile to stdout,
opens DuckDB files read-only, and does not write files.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


MAX_ROWS = 1000
MAX_SAMPLE_VALUES = 5


def infer_scalar(value: Any) -> str:
    if value is None or value == "":
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    text = str(value)
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return "boolean"
    try:
        int(text)
        return "integer"
    except (TypeError, ValueError):
        pass
    try:
        float(text)
        return "number"
    except (TypeError, ValueError):
        pass
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return "date_or_timestamp"
    return "string"


def merge_types(types: Counter[str]) -> str:
    non_null = [name for name in types if name != "null"]
    if not non_null:
        return "string"
    if len(non_null) == 1:
        return non_null[0]
    if set(non_null) <= {"integer", "number"}:
        return "number"
    return "string"


def profile_rows(path: Path, rows: list[dict[str, Any]], total_rows: int | None) -> dict[str, Any]:
    columns: dict[str, dict[str, Any]] = {}
    field_names: list[str] = []
    for row in rows:
        for key in row:
            if key not in field_names:
                field_names.append(key)

    for name in field_names:
        values = [row.get(name) for row in rows]
        types = Counter(infer_scalar(value) for value in values)
        samples: list[Any] = []
        for value in values:
            if value in (None, "") or value in samples:
                continue
            samples.append(value)
            if len(samples) >= MAX_SAMPLE_VALUES:
                break
        non_null = [value for value in values if value not in (None, "")]
        distinct = len({str(value) for value in non_null})
        n = len(values)
        columns[name] = {
            "inferred_type": merge_types(types),
            "nullable": types.get("null", 0) > 0,
            # Over the PROFILED rows (DuckDB mode overwrites these with exact
            # full-table numbers). cardinality ~1.0 => key/grain candidate;
            # low => dimension candidate.
            "null_pct": round(100.0 * (n - len(non_null)) / n, 2) if n else 0.0,
            "distinct_count": distinct,
            "cardinality": round(distinct / n, 4) if n else 0.0,
            "observed_types": dict(types),
            "sample_values": samples,
        }

    partition_hints = [
        name
        for name in field_names
        if name.lower() in {"date", "dt", "day", "month", "year", "timestamp", "created_at", "updated_at"}
        or name.lower().endswith(("_date", "_dt", "_at"))
    ]

    return {
        "path": str(path),
        "format": path.suffix.lower().lstrip(".") or "unknown",
        "rows_profiled": len(rows),
        "total_rows": total_rows,
        "columns": columns,
        "partition_or_freshness_hints": partition_hints,
    }


def read_csv(path: Path) -> tuple[list[dict[str, Any]], int | None]:
    rows: list[dict[str, Any]] = []
    total = 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            total += 1
            if len(rows) < MAX_ROWS:
                rows.append(dict(row))
    return rows, total


def read_json(path: Path) -> tuple[list[dict[str, Any]], int | None]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                data = value
                break
    if isinstance(data, dict):
        return [data], 1
    if not isinstance(data, list):
        raise ValueError("JSON root must be an object or array of objects")
    rows = [row for row in data[:MAX_ROWS] if isinstance(row, dict)]
    return rows, len(data)


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int | None]:
    rows: list[dict[str, Any]] = []
    total = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            total += 1
            if len(rows) < MAX_ROWS:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    return rows, total


def read_parquet(path: Path) -> tuple[list[dict[str, Any]], int | None]:
    try:
        import pandas as pd  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Parquet profiling requires pandas and a parquet engine such as pyarrow") from exc

    frame = pd.read_parquet(path)
    total = len(frame)
    rows = frame.head(MAX_ROWS).where(frame.notna(), None).to_dict(orient="records")
    return rows, total


DUCKDB_SUFFIXES = {".duckdb", ".ddb", ".db"}


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _inferred_from_declared(declared: str) -> str:
    """Map a DuckDB declared type to the row-profiler's inferred_type vocabulary.

    Used when the sampled rows could not establish a type for a column — an
    empty table, or a column DESCRIBE reports that the sample never carried —
    so DuckDB mode always emits the full common column shape."""
    upper = declared.upper()
    if "BOOL" in upper:
        return "boolean"
    if "DATE" in upper or "TIMESTAMP" in upper:
        return "date_or_timestamp"
    if "INTERVAL" in upper:
        return "string"
    if any(t in upper for t in ("TINYINT", "SMALLINT", "INT", "BIGINT", "HUGEINT")):
        return "integer"
    if any(t in upper for t in ("DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL")):
        return "number"
    return "string"


def profile_duckdb(path: Path, table: str) -> dict[str, Any]:
    """Profile a materialized DuckDB table (``main.<table>``).

    DESCRIBE + a sampled SELECT feed the shared row profiler; every column is
    then enriched with exact full-table stats: declared_type, null_pct,
    distinct_count, and cardinality (COUNT(DISTINCT col) / COUNT(*)).
    """
    try:
        import duckdb  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "DuckDB profiling requires the duckdb package: run via "
            "`uv run --with duckdb python profile_tabular.py ...` or "
            "`pip install duckdb`"
        ) from exc

    con = duckdb.connect(str(path), read_only=True)
    try:
        rel = f"main.{_quote_ident(table)}"
        described = con.execute(f"DESCRIBE {rel}").fetchall()
        col_names = [row[0] for row in described]
        declared_types = {row[0]: row[1] for row in described}

        total = con.execute(f"SELECT COUNT(*) FROM {rel}").fetchone()[0]
        sampled = con.execute(f"SELECT * FROM {rel} LIMIT {MAX_ROWS}").fetchall()
        rows = [dict(zip(col_names, values)) for values in sampled]

        profile = profile_rows(path, rows, total)
        profile["format"] = "duckdb"
        profile["table"] = f"main.{table}"

        for name in col_names:
            qi = _quote_ident(name)
            n_non_null, n_distinct = con.execute(
                f"SELECT COUNT({qi}), COUNT(DISTINCT {qi}) FROM {rel}"
            ).fetchone()
            # Full common shape even when the sample established nothing for
            # this column (empty table): default every row-profiler field.
            column = profile["columns"].setdefault(name, {
                "inferred_type": _inferred_from_declared(declared_types[name]),
                "nullable": False,
                "null_pct": 0.0,
                "distinct_count": 0,
                "cardinality": 0.0,
                "observed_types": {},
                "sample_values": [],
            })
            column["declared_type"] = declared_types[name]
            # Exact full-table stats override the sample-derived numbers —
            # including nullable, so a null past the sampled rows can never
            # yield the contradictory `nullable: false` + `null_pct > 0`.
            column["nullable"] = n_non_null < total
            column["null_pct"] = (
                round(100.0 * (total - n_non_null) / total, 2) if total else 0.0
            )
            column["distinct_count"] = n_distinct
            column["cardinality"] = round(n_distinct / total, 4) if total else 0.0

        # Declared DATE/TIMESTAMP columns are freshness hints even when the
        # column name carries no date-ish suffix.
        hints = set(profile["partition_or_freshness_hints"])
        for name, declared in declared_types.items():
            upper = declared.upper()
            if "DATE" in upper or "TIMESTAMP" in upper:
                hints.add(name)
        profile["partition_or_freshness_hints"] = sorted(hints)
        return profile
    finally:
        con.close()


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(
            "usage: profile_tabular.py <csv|json|jsonl|parquet-path>\n"
            "       profile_tabular.py <duckdb-path> <table> [<table> ...]",
            file=sys.stderr,
        )
        return 2

    path = Path(argv[0]).expanduser().resolve()
    if not path.exists():
        print(f"not found: {path}", file=sys.stderr)
        return 2

    suffix = path.suffix.lower()
    if suffix in DUCKDB_SUFFIXES:
        tables = argv[1:]
        if not tables:
            print("duckdb mode requires at least one table name: "
                  f"profile_tabular.py {path} <table> [<table> ...]",
                  file=sys.stderr)
            return 2
        try:
            profiles = {table: profile_duckdb(path, table) for table in tables}
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if len(tables) == 1:
            document: dict[str, Any] = profiles[tables[0]]
        else:
            # Combined multi-table document — the schema.json handoff shape.
            document = {"path": str(path), "format": "duckdb", "tables": profiles}
        print(json.dumps(document, indent=2, default=str))
        return 0

    if len(argv) > 1:
        print(f"table names only apply to DuckDB files, not {suffix}",
              file=sys.stderr)
        return 2

    if suffix == ".csv":
        rows, total = read_csv(path)
    elif suffix == ".json":
        rows, total = read_json(path)
    elif suffix in {".jsonl", ".ndjson"}:
        rows, total = read_jsonl(path)
    elif suffix == ".parquet":
        rows, total = read_parquet(path)
    else:
        print(f"unsupported file type: {suffix}", file=sys.stderr)
        return 2

    print(json.dumps(profile_rows(path, rows, total), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
