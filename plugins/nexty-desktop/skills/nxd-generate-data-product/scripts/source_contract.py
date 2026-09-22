"""Self-contained source and derived-metric checks for generated transforms.

Copy this module's source into a generated transform (or adapt the functions)
when a closure derives a model from one or more source exports.  A generated
closure must remain self-contained after it is handed to the supervisor; this
file is a recipe, not a runtime dependency on the installed skill tree.

The checks are deliberately fail-closed:

* a missing or duplicate source header raises before matching (and an
  unexpected header raises when the exact header set is declared);
* coverage is computed from the complete source identity set and rejects
  matches for identities that were not supplied; and
* ratio metrics are derived from additive numerators and denominators rather
  than by summing row-level ratios.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


PathInput = str | Path


def _csv_paths(paths: Iterable[PathInput] | PathInput) -> list[Path]:
    if isinstance(paths, (str, Path)):
        candidate = Path(paths)
        if candidate.is_dir():
            return sorted(candidate.glob("*.csv"))
        return [candidate]
    return sorted(Path(path) for path in paths)


def _columns(columns: Sequence[str], *, label: str) -> tuple[str, ...]:
    values = tuple(columns)
    if not values:
        raise ValueError(f"{label} must not be empty")
    if any(not isinstance(column, str) or not column for column in values):
        raise ValueError(f"{label} must contain non-empty string names")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicate names")
    return values


def read_csv_rows(
    paths: Iterable[PathInput] | PathInput,
    *,
    required_columns: Sequence[str],
    source_name: str,
    expected_columns: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    """Read source rows only after validating every file's header.

    ``required_columns`` permits approved source extensions.  Pass
    ``expected_columns`` when the approved source contract is exact and extra
    columns must fail as well.  Files are read in sorted order so output does
    not depend on filesystem enumeration order.
    """
    if not isinstance(source_name, str) or not source_name.strip():
        raise ValueError("source_name must be a non-empty string")
    required = _columns(required_columns, label="required_columns")
    expected = (
        _columns(expected_columns, label="expected_columns")
        if expected_columns is not None
        else None
    )
    if expected is not None and not set(required) <= set(expected):
        raise ValueError("required_columns must be a subset of expected_columns")

    csv_paths = _csv_paths(paths)
    if not csv_paths:
        raise RuntimeError(f"{source_name}: no CSV files were supplied")

    rows: list[dict[str, str]] = []
    for path in csv_paths:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(
                f"{source_name}: source file is a symlink or not regular: {path}"
            )
        try:
            with path.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                headers = reader.fieldnames or []
                if any(header is None or not header for header in headers):
                    raise RuntimeError(f"{source_name}: {path} has an empty header")
                if len(headers) != len(set(headers)):
                    raise RuntimeError(f"{source_name}: {path} has duplicate headers")
                actual = set(headers)
                missing = sorted(set(required) - actual)
                if missing:
                    raise RuntimeError(
                        f"{source_name}: {path} is missing required columns {missing}"
                    )
                if expected is not None and actual != set(expected):
                    raise RuntimeError(
                        f"{source_name}: {path} columns {sorted(actual)} do not match "
                        f"the approved header set {sorted(expected)}"
                    )
                for row_number, row in enumerate(reader, start=2):
                    if None in row:
                        raise RuntimeError(
                            f"{source_name}: {path}:{row_number} has more values "
                            "than the declared header"
                        )
                    missing_values = [
                        column
                        for column in required
                        if row.get(column) is None or not str(row[column]).strip()
                    ]
                    if missing_values:
                        raise RuntimeError(
                            f"{source_name}: {path}:{row_number} has missing or blank "
                            f"values for required columns {missing_values}"
                        )
                    rows.append(dict(row))
        except (OSError, UnicodeError) as exc:
            raise RuntimeError(
                f"{source_name}: could not read or decode {path}: {exc}"
            ) from None
    return rows


def coverage_summary(
    rows: Sequence[Mapping[str, Any]],
    matched_ids: Sequence[str],
    *,
    id_column: str,
    source_name: str,
) -> dict[str, Any]:
    """Return an auditable coverage row for one complete source side."""
    if not rows:
        raise RuntimeError(f"{source_name}: coverage cannot be computed from zero rows")
    if len(matched_ids) != len(set(matched_ids)):
        raise RuntimeError(f"{source_name}: a source identity was matched more than once")

    source_ids: list[str] = []
    for index, row in enumerate(rows, start=1):
        value = row.get(id_column)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(
                f"{source_name}: row {index} has no non-empty {id_column!r}"
            )
        source_ids.append(value)
    if len(source_ids) != len(set(source_ids)):
        raise RuntimeError(f"{source_name}: {id_column!r} is not unique")

    source_set = set(source_ids)
    matched_set = set(matched_ids)
    unknown = sorted(matched_set - source_set)
    if unknown:
        raise RuntimeError(
            f"{source_name}: matched identities are not in the source: {unknown}"
        )
    matched_count = len(matched_set)
    rate_bps = int(
        (Decimal(matched_count) * Decimal(10000) / Decimal(len(source_ids))).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    return {
        "source": source_name,
        "row_count": len(source_ids),
        "matched_count": matched_count,
        "coverage_bps": rate_bps,
        "unmatched_ids": sorted(source_set - matched_set),
    }


def ratio_value(
    numerator: Any,
    denominator: Any,
    *,
    metric_name: str,
    quantum: Any = "1",
) -> Decimal:
    """Compute a positive-denominator ratio rounded to the approved quantum."""
    try:
        number = Decimal(str(numerator))
        divisor = Decimal(str(denominator))
        rounding_quantum = Decimal(str(quantum))
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError(f"{metric_name}: ratio inputs must be numeric") from exc
    if (
        not number.is_finite()
        or not divisor.is_finite()
        or not rounding_quantum.is_finite()
        or divisor <= 0
        or rounding_quantum <= 0
    ):
        raise RuntimeError(
            f"{metric_name}: ratio requires finite values, a positive denominator, "
            "and a positive quantum"
        )
    return (number / divisor).quantize(rounding_quantum, rounding=ROUND_HALF_UP)


def ratio_cents(numerator: Any, denominator: Any, *, metric_name: str) -> int:
    """Compute a ratio whose inputs and output are already expressed in cents."""
    return int(ratio_value(numerator, denominator, metric_name=metric_name))


def assert_aggregate_ratio(
    source_rows: Sequence[Mapping[str, Any]],
    output_row: Mapping[str, Any],
    *,
    numerator_column: str,
    denominator_column: str,
    ratio_column: str,
    metric_name: str,
    quantum: Any = "1",
) -> None:
    """Reject a summed row ratio and enforce the approved output quantum."""
    try:
        numerator = sum(Decimal(str(row[numerator_column])) for row in source_rows)
        denominator = sum(Decimal(str(row[denominator_column])) for row in source_rows)
        actual = Decimal(str(output_row[ratio_column]))
    except (KeyError, InvalidOperation, ValueError) as exc:
        raise RuntimeError(
            f"{metric_name}: ratio fields are missing or non-numeric"
        ) from exc
    expected = ratio_value(
        numerator, denominator, metric_name=metric_name, quantum=quantum
    )
    if actual != expected:
        raise RuntimeError(
            f"{metric_name}: {ratio_column}={actual} does not equal "
            f"aggregate {numerator}/{denominator}={expected}; do not sum row-level ratios"
        )


def assert_row_ratios(
    rows: Sequence[Mapping[str, Any]],
    *,
    numerator_column: str,
    denominator_column: str,
    ratio_column: str,
    metric_name: str,
    quantum: Any = "1",
) -> None:
    """Validate a ratio column at its declared row grain and output quantum."""
    for index, row in enumerate(rows, start=1):
        try:
            expected = ratio_value(
                row[numerator_column],
                row[denominator_column],
                metric_name=metric_name,
                quantum=quantum,
            )
            actual = Decimal(str(row[ratio_column]))
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise RuntimeError(
                f"{metric_name}: row {index} ratio is missing or non-numeric"
            ) from exc
        if actual != expected:
            raise RuntimeError(
                f"{metric_name}: row {index} {ratio_column}={actual} does not equal "
                f"{expected} from the additive inputs"
            )
