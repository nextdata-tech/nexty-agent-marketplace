"""Self-contained source-materialization and derived-metric checks.

Copy this module's source into a generated transform (or adapt the
source-schema and derived-metric functions) when a closure derives a model from
one or more source exports. ``project_csv_columns`` and ``verify_csv_landing``
are preparation-time helpers: run them while the original source is available,
then retain their evidence. A generated closure must remain self-contained
after it is handed to the supervisor; this file is a recipe, not a runtime
dependency on the installed skill tree.

The checks are deliberately fail-closed:

* a missing or duplicate source header raises before matching (and an
  unexpected header raises when the exact header set is declared);
* file landing is byte-exact by default; a CSV projection must declare its
  allow-list, dropped personal-data columns and reasons, and both file digests;
* coverage is computed from the complete source identity set and rejects
  matches for identities that were not supplied; and
* ratio metrics are derived from additive numerators and denominators rather
  than by summing row-level ratios.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


PathInput = str | Path
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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


def _declared_columns(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise RuntimeError(f"{label} must be a declared list of column names")
    try:
        return _columns(value, label=label)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from None


def _read_csv_bytes(raw: bytes, *, source_name: str, role: str) -> tuple[list[str], list[list[str]]]:
    try:
        text = raw.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        headers = next(reader, None)
        if not headers or any(not header for header in headers):
            raise RuntimeError(f"{source_name}: {role} has an empty CSV header")
        if len(headers) != len(set(headers)):
            raise RuntimeError(f"{source_name}: {role} has duplicate CSV headers")
        rows: list[list[str]] = []
        for row_number, row in enumerate(reader, start=2):
            if len(row) != len(headers):
                raise RuntimeError(
                    f"{source_name}: {role} row {row_number} has {len(row)} values; "
                    f"the declared header has {len(headers)} columns"
                )
            rows.append(row)
        return headers, rows
    except (UnicodeError, csv.Error, StopIteration):
        raise RuntimeError(f"{source_name}: {role} is not a valid UTF-8 CSV") from None


def _safe_file_bytes(path: PathInput, *, source_name: str, role: str) -> bytes:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise RuntimeError(f"{source_name}: {role} is a symlink or not a regular file")
    try:
        return candidate.read_bytes()
    except OSError:
        raise RuntimeError(f"{source_name}: could not read {role}") from None


def _validate_projection_declaration(
    headers: Sequence[str],
    *,
    allow_columns: Any,
    required_columns: Any,
    personal_data_columns: Any,
    drop_reasons: Any,
    derived_columns: Any,
    derivation_keys: Any,
    personal_data_decision: Any,
    reason: Any,
    source_name: str,
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    dict[str, str],
    dict[str, dict[str, Any]],
    dict[str, bytes],
    str | None,
]:
    allow = _declared_columns(allow_columns, label="allow_columns")
    required = _declared_columns(required_columns, label="required_columns")
    personal = _declared_columns(
        personal_data_columns, label="personal_data_columns"
    )
    if not isinstance(drop_reasons, Mapping):
        raise RuntimeError("drop_reasons must map every dropped column to its reason")
    drops: dict[str, str] = {}
    for column, drop_reason in drop_reasons.items():
        if not isinstance(column, str) or not column:
            raise RuntimeError("drop_reasons must use non-empty column names")
        if not isinstance(drop_reason, str) or not drop_reason.strip():
            raise RuntimeError(f"{source_name}: dropped column {column!r} needs a reason")
        drops[column] = drop_reason.strip()
    if not isinstance(reason, str) or not reason.strip():
        raise RuntimeError("projection reason must be a non-empty string")

    if not isinstance(derived_columns, Mapping):
        raise RuntimeError("derived_columns must map output names to derivation declarations")
    if any(not isinstance(name, str) or not name for name in derived_columns):
        raise RuntimeError("derived_columns must use non-empty output names")
    normalized_derived: dict[str, dict[str, Any]] = {}
    for output_column in sorted(derived_columns):
        declaration = derived_columns[output_column]
        if not isinstance(declaration, Mapping) or set(declaration) - {
            "input_columns", "method", "key_ref", "domain"
        } or not {"input_columns", "method", "key_ref", "domain"} <= set(declaration):
            raise RuntimeError(
                f"{source_name}: derived column {output_column!r} needs input_columns, method, key_ref, and domain"
            )
        inputs = _declared_columns(
            declaration.get("input_columns"),
            label=f"derived_columns[{output_column!r}].input_columns",
        )
        method = declaration.get("method")
        key_ref = declaration.get("key_ref")
        domain = declaration.get("domain")
        if method != "hmac-sha256:v1":
            raise RuntimeError(
                f"{source_name}: derived identifiers must use hmac-sha256:v1"
            )
        if not isinstance(key_ref, str) or not key_ref.strip():
            raise RuntimeError(f"{source_name}: derived column {output_column!r} has an invalid key_ref")
        if not isinstance(domain, str) or not domain.strip():
            raise RuntimeError(f"{source_name}: derived column {output_column!r} has an invalid domain")
        normalized_derived[output_column] = {
            "input_columns": list(inputs),
            "method": method,
            "key_ref": key_ref.strip(),
            "domain": domain.strip(),
        }

    source_columns = set(headers)
    if not set(personal) <= source_columns:
        raise RuntimeError(f"{source_name}: a declared personal-data column is absent")
    if set(normalized_derived) & source_columns:
        raise RuntimeError(f"{source_name}: a derived output name collides with a source column")
    derived_names = set(normalized_derived)
    if set(required) - source_columns - derived_names:
        raise RuntimeError(f"{source_name}: a required column is absent from the source and derivations")
    direct_required = set(required) & source_columns
    required_set = set(required)
    personal_set = set(personal)
    personal = tuple(column for column in headers if column in personal_set)
    derived_inputs: set[str] = set()
    for output_column, declaration in normalized_derived.items():
        inputs = set(declaration["input_columns"])
        if not inputs <= source_columns:
            raise RuntimeError(
                f"{source_name}: derived column {output_column!r} names an absent input"
            )
        if not inputs <= personal_set:
            raise RuntimeError(
                f"{source_name}: derived column {output_column!r} may use only declared personal-data inputs"
            )
        if inputs & direct_required:
            raise RuntimeError(
                f"{source_name}: a derived personal-data input must not also be retained"
            )
        derived_inputs.update(inputs)
        if output_column not in required_set:
            raise RuntimeError(
                f"{source_name}: every derived output must be required by the approved blueprint"
            )

    key_refs = {declaration["key_ref"] for declaration in normalized_derived.values()}
    if not isinstance(derivation_keys, Mapping) or set(derivation_keys) != key_refs:
        raise RuntimeError(
            f"{source_name}: derivation_keys must exactly match declared key_ref values"
        )
    for key_ref, key in derivation_keys.items():
        if not isinstance(key_ref, str) or not isinstance(key, bytes) or len(key) < 32:
            raise RuntimeError(f"{source_name}: derivation key is missing or invalid")

    if not drops:
        raise RuntimeError("a column projection must declare at least one dropped column")
    expected_drops = personal_set - direct_required
    if set(drops) != expected_drops:
        raise RuntimeError(
            f"{source_name}: drop_reasons must name every unneeded personal-data "
            "column and no other column"
        )
    if not derived_inputs <= set(drops):
        raise RuntimeError(f"{source_name}: raw derivation inputs must be omitted from the landing")
    expected_allow = tuple(column for column in headers if column not in drops) + tuple(
        sorted(derived_names)
    )
    if allow != expected_allow:
        raise RuntimeError(
            f"{source_name}: allow_columns must retain every other source column in source order "
            "followed by derived outputs in sorted order"
        )
    if not set(required) <= set(allow):
        raise RuntimeError(f"{source_name}: allow_columns omits a required column")

    retained_personal = personal_set & set(allow)
    if retained_personal:
        if not isinstance(personal_data_decision, str) or not personal_data_decision.strip():
            raise RuntimeError(
                f"{source_name}: retaining a personal-data column requires an explicit user decision"
            )
        normalized_decision: str | None = personal_data_decision.strip()
    else:
        if personal_data_decision is not None:
            raise RuntimeError(
                f"{source_name}: personal_data_decision is only valid when retaining personal data"
            )
        normalized_decision = None

    drops = {column: drops[column] for column in headers if column in drops}
    return (
        allow,
        required,
        personal,
        drops,
        normalized_derived,
        dict(derivation_keys),
        normalized_decision,
    )


def hmac_identifier(value: str | Sequence[str], *, key: bytes, domain: str) -> str:
    """Return a keyed, domain-separated identifier without exposing its input.

    Keep ``key`` in the approved secret slot; never place it in the blueprint
    or projection evidence. Reuse the same approved key and domain for stable
    continuity across source files.
    """
    values = [value] if isinstance(value, str) else list(value)
    if not values or any(not isinstance(item, str) or not item for item in values):
        raise ValueError("identifier inputs must be non-empty strings")
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("identifier key must contain at least 32 bytes")
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError("identifier domain must be a non-empty string")
    canonical_values = json.dumps(
        values, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    payload = domain.strip().encode("utf-8") + b"\0" + canonical_values
    return "hmac-sha256:v1:" + hmac.new(key, payload, hashlib.sha256).hexdigest()


def _derive_output_values(
    row: Sequence[str],
    headers: Sequence[str],
    derived: Mapping[str, Mapping[str, Any]],
    derivation_keys: Mapping[str, bytes],
    *,
    row_number: int,
    source_name: str,
) -> dict[str, str]:
    by_name = dict(zip(headers, row))
    values: dict[str, str] = {}
    for output_column in sorted(derived):
        declaration = derived[output_column]
        input_values = [by_name[column] for column in declaration["input_columns"]]
        try:
            value = hmac_identifier(
                input_values,
                key=derivation_keys[declaration["key_ref"]],
                domain=declaration["domain"],
            )
        except Exception:
            raise RuntimeError(
                f"{source_name}: could not derive declared key {output_column!r} at row {row_number}"
            ) from None
        values[output_column] = value
    return values


def project_csv_columns(
    source_path: PathInput,
    landed_path: PathInput,
    *,
    allow_columns: Sequence[str],
    required_columns: Sequence[str],
    personal_data_columns: Sequence[str],
    drop_reasons: Mapping[str, str],
    reason: str,
    source_name: str,
    derived_columns: Mapping[str, Mapping[str, Any]] | None = None,
    derivation_keys: Mapping[str, bytes] | None = None,
    personal_data_decision: str | None = None,
) -> dict[str, Any]:
    """Write a declared CSV projection and return evidence for the blueprint.

    The allow-list must retain all non-dropped columns in their original order.
    Only declared personal-data columns that are absent from the approved
    required-column set may be dropped. A declared derived output may depend
    only on declared personal-data inputs that are omitted from the landing.
    Retained cell strings and row order are preserved; the projected CSV itself
    uses deterministic UTF-8/LF encoding.
    No cell values are included in returned evidence or diagnostics.
    """
    if not isinstance(source_name, str) or not source_name.strip():
        raise ValueError("source_name must be a non-empty string")
    source = Path(source_path)
    destination = Path(landed_path)
    if source.resolve() == destination.resolve():
        raise RuntimeError(f"{source_name}: projection must not overwrite its source")
    if destination.is_symlink():
        raise RuntimeError(f"{source_name}: landed projection must not be a symlink")
    if destination.exists() and destination.samefile(source):
        raise RuntimeError(f"{source_name}: projection must not overwrite its source")

    source_bytes = _safe_file_bytes(source, source_name=source_name, role="source")
    headers, rows = _read_csv_bytes(
        source_bytes, source_name=source_name, role="source"
    )
    derived_columns = derived_columns or {}
    derivation_keys = derivation_keys or {}
    (
        allow,
        required,
        personal,
        drops,
        derived,
        checked_keys,
        normalized_decision,
    ) = _validate_projection_declaration(
        headers,
        allow_columns=allow_columns,
        required_columns=required_columns,
        personal_data_columns=personal_data_columns,
        drop_reasons=drop_reasons,
        derived_columns=derived_columns,
        derivation_keys=derivation_keys,
        personal_data_decision=personal_data_decision,
        reason=reason,
        source_name=source_name,
    )
    indexes = {column: headers.index(column) for column in headers if column in allow}
    output = io.StringIO(newline="")
    writer = csv.writer(output, dialect=csv.excel, lineterminator="\n")
    writer.writerow(allow)
    for row_number, row in enumerate(rows, start=2):
        derived_values = _derive_output_values(
            row,
            headers,
            derived,
            checked_keys,
            row_number=row_number,
            source_name=source_name,
        )
        output_values = {
            **{column: row[index] for column, index in indexes.items()},
            **derived_values,
        }
        writer.writerow([output_values[column] for column in allow])
    landed_bytes = output.getvalue().encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.write_bytes(landed_bytes)
    except OSError:
        raise RuntimeError(f"{source_name}: could not write landed projection") from None

    evidence: dict[str, Any] = {
        "mode": "column_projection",
        "format": "csv",
        "allow_columns": list(allow),
        "required_columns": list(required),
        "personal_data_columns": list(personal),
        "drop_reasons": drops,
        "derived_columns": derived,
        "personal_data_decision": normalized_decision,
        "reason": reason.strip(),
        "row_count": len(rows),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "landed_sha256": hashlib.sha256(landed_bytes).hexdigest(),
    }
    verify_csv_landing(
        source,
        destination,
        declaration=evidence,
        source_name=source_name,
        derivation_keys=checked_keys,
    )
    return evidence


def verify_csv_landing(
    source_path: PathInput,
    landed_path: PathInput,
    *,
    declaration: Mapping[str, Any] | None = None,
    source_name: str,
    derivation_keys: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    """Verify byte-exact default landing or an explicitly declared projection.

    Projection validation checks both recorded SHA-256 values, the exact
    source-header partition, row count/order, and every retained cell value.
    Diagnostics identify columns and row numbers only; they never include cell
    contents.
    """
    if not isinstance(source_name, str) or not source_name.strip():
        raise ValueError("source_name must be a non-empty string")
    source_bytes = _safe_file_bytes(source_path, source_name=source_name, role="source")
    landed_bytes = _safe_file_bytes(
        landed_path, source_name=source_name, role="landed file"
    )
    if declaration is None:
        if source_bytes != landed_bytes:
            raise RuntimeError(
                f"{source_name}: landed file differs from the source without a "
                "declared column projection"
            )
        return {
            "mode": "byte_exact",
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "landed_sha256": hashlib.sha256(landed_bytes).hexdigest(),
        }

    if not isinstance(declaration, Mapping):
        raise RuntimeError(f"{source_name}: projection declaration is invalid")
    expected_keys = {
        "mode", "format", "allow_columns", "required_columns",
        "personal_data_columns", "drop_reasons", "derived_columns",
        "personal_data_decision", "reason", "row_count",
        "source_sha256", "landed_sha256",
    }
    if set(declaration) != expected_keys:
        raise RuntimeError(
            f"{source_name}: projection declaration is incomplete or has unknown fields"
        )
    if declaration.get("mode") != "column_projection" or declaration.get("format") != "csv":
        raise RuntimeError(f"{source_name}: projection must be declared as CSV")
    source_headers, source_rows = _read_csv_bytes(
        source_bytes, source_name=source_name, role="source"
    )
    landed_headers, landed_rows = _read_csv_bytes(
        landed_bytes, source_name=source_name, role="landed projection"
    )
    derivation_keys = derivation_keys or {}
    (
        allow,
        _required,
        _personal,
        _drops,
        derived,
        checked_keys,
        _decision,
    ) = _validate_projection_declaration(
        source_headers,
        allow_columns=declaration.get("allow_columns"),
        required_columns=declaration.get("required_columns"),
        personal_data_columns=declaration.get("personal_data_columns"),
        drop_reasons=declaration.get("drop_reasons"),
        derived_columns=declaration.get("derived_columns"),
        derivation_keys=derivation_keys,
        personal_data_decision=declaration.get("personal_data_decision"),
        reason=declaration.get("reason"),
        source_name=source_name,
    )
    if landed_headers != list(allow):
        raise RuntimeError(
            f"{source_name}: landed header does not equal the declared allow_columns"
        )
    row_count = declaration.get("row_count")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
        raise RuntimeError(f"{source_name}: projection row_count is invalid")
    if row_count != len(source_rows) or len(landed_rows) != len(source_rows):
        raise RuntimeError(f"{source_name}: projection changed the source row count")

    expected_projection = io.StringIO(newline="")
    projection_writer = csv.writer(
        expected_projection, dialect=csv.excel, lineterminator="\n"
    )
    projection_writer.writerow(allow)
    for row_number, (source_row, landed_row) in enumerate(
        zip(source_rows, landed_rows), start=2
    ):
        derived_values = _derive_output_values(
            source_row,
            source_headers,
            derived,
            checked_keys,
            row_number=row_number,
            source_name=source_name,
        )
        source_by_name = dict(zip(source_headers, source_row))
        output_values = {
            **{column: source_by_name[column] for column in allow if column in source_by_name},
            **derived_values,
        }
        projected_row = [output_values[column] for column in allow]
        if landed_row != projected_row:
            raise RuntimeError(
                f"{source_name}: landed row {row_number} differs in an allowed column"
            )
        projection_writer.writerow(projected_row)

    if expected_projection.getvalue().encode("utf-8") != landed_bytes:
        raise RuntimeError(
            f"{source_name}: landed file does not match the deterministic CSV projection"
        )

    source_digest = declaration.get("source_sha256")
    landed_digest = declaration.get("landed_sha256")
    if not isinstance(source_digest, str) or not _SHA256_RE.fullmatch(source_digest):
        raise RuntimeError(f"{source_name}: source_sha256 is missing or invalid")
    if not isinstance(landed_digest, str) or not _SHA256_RE.fullmatch(landed_digest):
        raise RuntimeError(f"{source_name}: landed_sha256 is missing or invalid")
    if source_digest != hashlib.sha256(source_bytes).hexdigest():
        raise RuntimeError(f"{source_name}: source_sha256 does not match the source file")
    if landed_digest != hashlib.sha256(landed_bytes).hexdigest():
        raise RuntimeError(f"{source_name}: landed_sha256 does not match the projection")
    return dict(declaration)


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
