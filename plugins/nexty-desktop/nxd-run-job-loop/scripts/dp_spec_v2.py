#!/usr/bin/env python3
"""Deterministic tooling for the canonical dp-spec v2 Markdown contract.

The v2 source of truth is human-authored Markdown; this module parses it into a
typed canonical object, retains a source map for narrow edits, and never
rewrites a live document wholesale.  ``emit`` and ``new`` therefore create
proposals only, while ``patch`` changes one mapped scalar after checking the
caller's semantic base hash.  There is deliberately no parser fallback for an
older contract: an old version is an explicit ``unsupported_version`` error.

The body grammar is deliberately small and Markdown-native:

    ## Models
    ### Model `orders`
    - Kind: `base`
    - Input: `orders_csv`
    - Description: One row per order.
    - Grain: one row per order
    - Key: `order_id`

This is not a YAML document split across headings.  Entity ids, field labels,
and operation fields are closed so the validator can be deterministic.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


SPEC_VERSION = 2
SCHEMA_ID = "nxd-dp-spec-schema-v2"
SPEC_DIAGNOSTIC_SCHEMA_ID = "nxd-dp-spec-diagnostic-v2"
CANONICALIZATION = "nxd-dp-spec-canon-v2"
STATUS_VALUES = ("draft", "proposed", "approved")
REQUIRED_FRONTMATTER = ("dp_spec_version", "name", "workflow", "status")
OPTIONAL_FRONTMATTER = ("approved_content_hash",)
SECTION_TITLES = (
    "Intent", "Questions", "Scope", "Inputs", "Models", "Transform",
    "Outputs", "Delivery", "Contracts", "Decisions", "Open Questions",
)
SECTION_KEYS = {title: title.lower().replace(" ", "_") for title in SECTION_TITLES}
ENTITY_KINDS = {
    "questions": "Question",
    "inputs": "Input",
    "models": "Model",
    "transform": "Step",
    "outputs": "Output",
    "delivery": "Delivery",
    "contracts": "Contract",
    "decisions": "Decision",
    "open_questions": "Question",
}
MODEL_KINDS = ("base", "reference", "derived", "view")
INPUT_TYPES = ("csv", "file", "database", "api")
DELIVERY_KINDS = ("semantic_port", "static_artifact")
DECISION_STATUSES = ("confirmed", "proposed", "blocked")
DECISION_PROVENANCE = ("user_confirmed", "agent_authored", "source_derived", "deferred")
JOIN_TYPES = ("inner", "left", "right", "full")
JOIN_CARDINALITIES = ("one_to_one", "one_to_many", "many_to_one", "many_to_many")
UNMATCHED_BEHAVIORS = ("drop", "keep_left", "keep_right", "keep_both")
JOIN_NULL_BEHAVIORS = ("drop", "unmatched")
AGGREGATE_FUNCTIONS = ("sum", "avg", "min", "max", "count")
NULL_HANDLING = ("ignore", "zero", "error")
UNION_ALIGNMENTS = ("by_name", "by_position")
MISSING_FIELD_BEHAVIORS = ("error", "null")
DEDUP_WINNERS = ("first", "last", "error")
OPERATION_KINDS = (
    "filter", "project", "derive", "join", "aggregate", "union",
    "deduplicate", "apply_procedure",
)
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
PROCEDURE_RE = re.compile(r"^[a-z][a-z0-9_-]*@[A-Za-z0-9][A-Za-z0-9._-]*$")
VIEW_EXPRESSION_RE = re.compile(r"^(?:[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)(?:,\s*(?:[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*))*$")
SECTION_RE = re.compile(r"^## ([A-Za-z][A-Za-z ]*)\s*$")
ENTITY_RE = re.compile(r"^### ([A-Za-z]+) `([a-z][a-z0-9_]*)`\s*$")
FIELD_RE = re.compile(r"^- ([A-Z][A-Za-z ]*):\s*(.*?)\s*$")
YAMLISH_RE = re.compile(r"^\s*(?:-\s+)?[a-z][A-Za-z0-9_-]*\s*:\s*.*$")
COMMENT_RE = re.compile(r"^\s*<!--.*-->\s*$")

LIST_FIELDS = frozenset({
    "inputs", "questions", "projection", "order_by", "delivery_refs",
    "group_by", "measures", "expressions", "on", "keys", "fields",
})
REQUIRED_SCALAR_SECTIONS = frozenset({"intent", "scope"})
ENTITY_FIELDS = {
    "inputs": frozenset({"type", "location", "description"}),
    "models": frozenset({
        "kind", "input", "description", "grain", "key", "produced_by",
        "view_expression", "procedure", "fields",
    }),
    "transform": frozenset({
        "operation", "inputs", "output", "predicate", "fields", "expressions",
        "join_type", "on", "cardinality", "unmatched", "group_by", "measures",
        "null_keys", "null_handling", "keys", "order_by", "winner", "alignment", "missing_fields", "procedure",
    }),
    "outputs": frozenset({"model", "questions", "projection", "order_by", "delivery_refs"}),
    "delivery": frozenset({"kind", "target", "description"}),
    "contracts": frozenset({"name", "attachment", "model", "phase", "guarantee", "rule", "fields"}),
    "decisions": frozenset({"target", "status", "provenance", "ruling"}),
    "open_questions": frozenset({"blocking", "target"}),
    "questions": frozenset(),
}
OPERATION_FIELDS = {
    "filter": frozenset({"operation", "inputs", "output", "predicate"}),
    "project": frozenset({"operation", "inputs", "output", "fields"}),
    "derive": frozenset({"operation", "inputs", "output", "expressions"}),
    "join": frozenset({"operation", "inputs", "output", "join_type", "on", "cardinality", "unmatched", "null_keys"}),
    "aggregate": frozenset({"operation", "inputs", "output", "group_by", "measures", "null_handling"}),
    "union": frozenset({"operation", "inputs", "output", "alignment", "missing_fields"}),
    "deduplicate": frozenset({"operation", "inputs", "output", "keys", "order_by", "winner"}),
    "apply_procedure": frozenset({"operation", "inputs", "output", "procedure", "fields"}),
}


class ParseError(ValueError):
    """The Markdown cannot be represented without dropping or guessing data."""


class StalePatchError(ValueError):
    """A targeted edit was based on a different semantic revision."""


class UnsupportedVersionError(ParseError):
    """The document names a contract version this tool no longer supports."""


@dataclass(frozen=True)
class SourceSpan:
    """One half-open source range; lines are one-based, offsets zero-based."""

    line_start: int
    line_end: int
    offset_start: int
    offset_end: int

    def to_dict(self) -> dict[str, int]:
        return dataclasses.asdict(self)


@dataclass
class SourceMap:
    spans: dict[str, SourceSpan] = field(default_factory=dict)

    def add(self, path: str, line: int, start: int, end: int, line_end: int | None = None) -> None:
        if path in self.spans:
            raise ParseError(f"source map collision at {path}")
        self.spans[path] = SourceSpan(line, line if line_end is None else line_end, start, end)

    def to_dict(self) -> dict[str, dict[str, int]]:
        return {path: self.spans[path].to_dict() for path in sorted(self.spans)}


@dataclass(frozen=True)
class Frontmatter:
    dp_spec_version: int
    name: str
    workflow: str
    status: str
    approved_content_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class Entity:
    id: str
    fields: dict[str, Any]
    prose: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = {"id": self.id, **{key: self.fields[key] for key in sorted(self.fields)}}
        if self.prose:
            out["prose"] = self.prose
        return out


@dataclass(frozen=True)
class CanonicalDocument:
    frontmatter: Frontmatter
    intent: str
    questions: tuple[Entity, ...]
    scope: str
    inputs: tuple[Entity, ...]
    models: tuple[Entity, ...]
    transform: tuple[Entity, ...]
    outputs: tuple[Entity, ...]
    delivery: tuple[Entity, ...]
    decisions: tuple[Entity, ...]
    open_questions: tuple[Entity, ...]
    preamble: str = ""
    unknown_sections: tuple[str, ...] = ()
    unknown_section_bodies: dict[str, str] = field(default_factory=dict)
    present_sections: tuple[str, ...] = ()
    contracts: tuple[Entity, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        def entities(values: tuple[Entity, ...]) -> dict[str, Any]:
            return {entry.id: entry.to_dict() for entry in sorted(values, key=lambda item: item.id)}

        out = {
            "schema": SCHEMA_ID,
            "canonicalization": CANONICALIZATION,
            "frontmatter": self.frontmatter.to_dict(),
            "intent": self.intent,
            "questions": entities(self.questions),
            "scope": self.scope,
            "inputs": entities(self.inputs),
            "models": entities(self.models),
            "transform": entities(self.transform),
            "outputs": entities(self.outputs),
            "delivery": entities(self.delivery),
            "decisions": entities(self.decisions),
            "open_questions": entities(self.open_questions),
            "sections_present": list(sorted(self.present_sections)),
        }
        if self.contracts or "contracts" in self.present_sections:
            out["contracts"] = entities(self.contracts)
        # These values are not semantic, but retain their existence so callers
        # can diagnose an unsafe document instead of a parser silently dropping it.
        if self.preamble:
            out["preamble"] = self.preamble
        if self.unknown_sections:
            out["unknown_sections"] = list(self.unknown_sections)
        if self.unknown_section_bodies:
            out["unknown_section_bodies"] = {
                title: self.unknown_section_bodies[title] for title in sorted(self.unknown_section_bodies)
            }
        return out


@dataclass(frozen=True)
class ParsedDocument:
    document: CanonicalDocument
    source_map: SourceMap
    raw: str


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    owner: str = "user"
    control: str = "text"

    def to_dict(self) -> dict[str, str]:
        return dataclasses.asdict(self)


def _path(section: str, entity_id: str | None = None, field_name: str | None = None) -> str:
    base = f"v2:{section}" if entity_id is None else f"v2:{section}[{entity_id}]"
    return base if field_name is None else f"{base}.{field_name}"


def _clean_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
        value = value[1:-1]
    if not value:
        raise ParseError("empty labelled Markdown value")
    return value


def _list_value(value: str) -> list[str]:
    # Accept both `` `a, b` `` and `` `a`, `b` `` without leaving formatting
    # delimiters in the semantic value.  Split only after deciding whether the
    # complete value is one code span; otherwise clean each item independently.
    raw = value.strip()
    if len(raw) >= 2 and raw.startswith("`") and raw.endswith("`") and raw.count("`") == 2:
        raw = raw[1:-1]
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts:
        raise ParseError("empty list value")
    return [_clean_scalar(part) for part in parts]


def _field_key(label: str) -> str:
    return label.lower().replace(" ", "_")


def _lines_with_offsets(text: str) -> list[tuple[int, str, int]]:
    out: list[tuple[int, str, int]] = []
    offset = 0
    for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
        raw = line.rstrip("\r\n")
        out.append((line_number, raw, offset))
        offset += len(line)
    if not out:
        out.append((1, "", 0))
    return out


def _parse_frontmatter(lines: list[tuple[int, str, int]], source_map: SourceMap) -> tuple[Frontmatter, int]:
    if not lines or lines[0][1] != "---":
        raise ParseError("v2 document must start with minimal YAML frontmatter")
    values: dict[str, str] = {}
    close_index: int | None = None
    for index, (line_number, line, offset) in enumerate(lines[1:], start=1):
        if line == "---":
            close_index = index
            break
        match = re.fullmatch(r"([a-z_]+):\s*(.+)", line)
        if not match:
            raise ParseError(f"frontmatter line {line_number} is not a minimal key/value")
        key, raw_value = match.groups()
        if key in values:
            raise ParseError(f"duplicate frontmatter key {key!r}")
        if key not in REQUIRED_FRONTMATTER and key not in OPTIONAL_FRONTMATTER:
            raise ParseError(f"v2 frontmatter does not allow {key!r}")
        value_start = offset + match.start(2)
        source_map.add(_path("frontmatter", None, key), line_number, value_start, offset + match.end(2))
        if key == "approved_content_hash" and raw_value.strip().lower() in {"null", "none", "~"}:
            values[key] = ""
        else:
            values[key] = _clean_scalar(raw_value)
    if close_index is None:
        raise ParseError("unterminated v2 frontmatter")
    missing = [key for key in REQUIRED_FRONTMATTER if key not in values]
    if missing:
        raise ParseError("v2 frontmatter missing " + ", ".join(missing))
    if values["dp_spec_version"] != str(SPEC_VERSION):
        raise UnsupportedVersionError(
            f"unsupported_version: dp_spec_version {values['dp_spec_version']!r} is not supported; expected {SPEC_VERSION}"
        )
    approved_hash = values.get("approved_content_hash") or None
    return Frontmatter(SPEC_VERSION, values["name"], values["workflow"], values["status"], approved_hash), close_index + 1


def parse(text: str) -> ParsedDocument:
    """Parse v2 Markdown without normalizing, dropping, or repairing source text."""
    if "\r" in text:
        raise ParseError("CRLF/CR input is not supported for v2 source-preserving patches; use LF")
    lines = _lines_with_offsets(text)
    source_map = SourceMap()
    frontmatter, body_start = _parse_frontmatter(lines, source_map)

    sections: dict[str, list[tuple[int, str, int]]] = {}
    section_lines: dict[str, tuple[int, int]] = {}
    unknown_sections: list[str] = []
    unknown_section_bodies: dict[str, str] = {}
    preamble_lines: list[str] = []
    current: str | None = None
    for line_number, line, offset in lines[body_start:]:
        heading = SECTION_RE.match(line)
        if heading:
            title = heading.group(1)
            key = SECTION_KEYS.get(title)
            if key is None:
                unknown_sections.append(title)
                key = "__unknown_" + title
            if key in sections:
                raise ParseError(f"duplicate section heading {title!r} at line {line_number}")
            sections[key] = []
            section_lines[key] = (line_number, offset)
            current = key
            continue
        if current is None:
            preamble_lines.append(line)
        else:
            sections[current].append((line_number, line, offset))

    preamble = "\n".join(preamble_lines).strip()
    for key in SECTION_KEYS.values():
        if key not in sections:
            continue
        line_number, offset = section_lines[key]
        end = offset + len("## " + next(title for title, candidate in SECTION_KEYS.items() if candidate == key))
        source_map.add(_path(key), line_number, offset, end)

    scalar_values: dict[str, str] = {}
    entity_values: dict[str, tuple[Entity, ...]] = {}
    for key in SECTION_KEYS.values():
        contents = sections.get(key)
        if contents is None:
            continue
        if key in REQUIRED_SCALAR_SECTIONS:
            scalar_values[key] = _parse_scalar_section(key, contents, source_map)
        else:
            entity_values[key] = _parse_entities(key, contents, source_map)
    for key, contents in sections.items():
        if key.startswith("__unknown_"):
            title = key[len("__unknown_"):]
            unknown_section_bodies[title] = "\n".join(line for _, line, _ in contents)

    return ParsedDocument(
        CanonicalDocument(
            frontmatter=frontmatter,
            intent=scalar_values.get("intent", ""),
            questions=entity_values.get("questions", ()),
            scope=scalar_values.get("scope", ""),
            inputs=entity_values.get("inputs", ()),
            models=entity_values.get("models", ()),
            transform=entity_values.get("transform", ()),
            outputs=entity_values.get("outputs", ()),
            delivery=entity_values.get("delivery", ()),
            contracts=entity_values.get("contracts", ()),
            decisions=entity_values.get("decisions", ()),
            open_questions=entity_values.get("open_questions", ()),
            preamble=preamble,
            unknown_sections=tuple(unknown_sections),
            unknown_section_bodies=unknown_section_bodies,
            present_sections=tuple(key for key in SECTION_KEYS.values() if key in sections),
        ),
        source_map,
        text,
    )


def _parse_scalar_section(section: str, lines: list[tuple[int, str, int]], source_map: SourceMap) -> str:
    values = [line for _, line, _ in lines]
    for line_number, line, _ in lines:
        if YAMLISH_RE.match(line):
            raise ParseError(f"YAML-ish body at line {line_number}; use prose Markdown")
    value = "\n".join(line for line in values if not COMMENT_RE.match(line)).strip()
    if value:
        first = next((entry for entry in lines if entry[1].strip()), lines[0])
        last = next((entry for entry in reversed(lines) if entry[1].strip()), lines[-1])
        source_map.add(_path(section, None, "text"), first[0], first[2], last[2] + len(last[1]))
    return value


def _parse_entities(section: str, lines: list[tuple[int, str, int]], source_map: SourceMap) -> tuple[Entity, ...]:
    expected_kind = ENTITY_KINDS[section]
    entities: list[Entity] = []
    current_id: str | None = None
    current_line: tuple[int, str, int] | None = None
    fields: dict[str, Any] = {}
    prose: list[str] = []
    prose_entries: list[tuple[int, str, int]] = []
    seen_ids: set[str] = set()

    def finish() -> None:
        nonlocal fields, prose, prose_entries, current_id, current_line
        if current_id is None:
            return
        if current_id in seen_ids:
            raise ParseError(f"duplicate {section} id {current_id!r}")
        seen_ids.add(current_id)
        entity_prose = "\n".join(prose).strip()
        if entity_prose:
            first = prose_entries[0]
            last = prose_entries[-1]
            source_map.add(
                _path(section, current_id, "prose"), first[0], first[2],
                last[2] + len(last[1]), line_end=last[0],
            )
        entities.append(Entity(current_id, _typed_entity_fields(section, fields), entity_prose))
        fields, prose, prose_entries = {}, [], []
        current_id, current_line = None, None

    for line_number, line, offset in lines:
        entity_match = ENTITY_RE.match(line)
        if entity_match:
            finish()
            kind, entity_id = entity_match.groups()
            if kind != expected_kind:
                raise ParseError(
                    f"section {section!r} needs headings of the form "
                    f"### {expected_kind} `id` (line {line_number})"
                )
            current_id = entity_id
            current_line = (line_number, line, offset)
            source_map.add(_path(section, entity_id), line_number, offset, offset + len(line))
            continue
        if current_id is None:
            if line.strip():
                if YAMLISH_RE.match(line):
                    raise ParseError(f"YAML-ish body at line {line_number}; use Markdown headings")
                raise ParseError(f"content before first {expected_kind} heading at line {line_number}")
            continue
        field_match = FIELD_RE.match(line)
        if field_match:
            label, raw_value = field_match.groups()
            key = _field_key(label)
            if key in fields:
                raise ParseError(f"duplicate field {label!r} for {current_id!r}")
            value = _list_value(raw_value) if key in LIST_FIELDS else _clean_scalar(raw_value)
            fields[key] = value
            value_start = offset + field_match.start(2)
            value_end = offset + field_match.end(2)
            # A scalar written as inline code has syntax delimiters that are
            # formatting, not value. Keep them outside the editable span.
            if key not in LIST_FIELDS and raw_value.startswith("`") and raw_value.endswith("`"):
                value_start += 1
                value_end -= 1
            source_map.add(_path(section, current_id, key), line_number, value_start, value_end)
            continue
        if YAMLISH_RE.match(line):
            raise ParseError(f"YAML-ish body at line {line_number}; field labels must be Markdown bullets")
        if COMMENT_RE.match(line):
            # Comments are source-only annotations. They remain in ``raw`` and
            # survive a targeted patch, but cannot change the semantic hash.
            continue
        prose.append(line)
        prose_entries.append((line_number, line, offset))
    finish()
    return tuple(entities)


def _typed_entity_fields(section: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Turn closed Markdown operation notation into typed canonical values.

    Invalid notation is retained as an ``invalid`` typed value so validation can
    point at its stable field path rather than a parser silently accepting it as
    opaque text.
    """
    out = dict(fields)
    if section == "outputs" and isinstance(out.get("order_by"), list):
        out["order_by"] = [_order_term(item) for item in out["order_by"]]
    if section != "transform":
        return out
    if isinstance(out.get("predicate"), str):
        out["predicate"] = _predicate(out["predicate"])
    if isinstance(out.get("expressions"), list):
        out["expressions"] = [_expression(item) for item in out["expressions"]]
    if isinstance(out.get("on"), list):
        out["on"] = [_join_key(item) for item in out["on"]]
    if isinstance(out.get("measures"), list):
        out["measures"] = [_measure(item) for item in out["measures"]]
    if isinstance(out.get("order_by"), list):
        out["order_by"] = [_order_term(item) for item in out["order_by"]]
    return out


def _invalid(raw: str) -> dict[str, str]:
    return {"invalid": raw}


def _predicate(raw: str) -> dict[str, str]:
    match = re.fullmatch(r"([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)\s*(=|!=|<=|>=|<|>)\s*([A-Za-z0-9_.-]+)", raw)
    return {
        "model": match.group(1), "field": match.group(2),
        "operator": match.group(3), "value": match.group(4),
    } if match else _invalid(raw)


def _expression(raw: str) -> dict[str, Any]:
    match = re.fullmatch(
        r"([a-z][a-z0-9_]*)\s*=\s*((?:[a-z][a-z0-9_]*\.)[a-z][a-z0-9_]*|"
        r"(?:lower|upper|abs|round)\([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\)|"
        r"coalesce\([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*,\s*[A-Za-z0-9_.-]+\))",
        raw,
    )
    if not match:
        return _invalid(raw)
    target, expression = match.groups()
    refs = re.findall(r"([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)", expression)
    return {
        "field": target, "expression": expression,
        "references": [f"{model}.{field}" for model, field in refs],
    }


def _join_key(raw: str) -> dict[str, str]:
    match = re.fullmatch(r"([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)\s*=\s*([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)", raw)
    if not match:
        return _invalid(raw)
    left_model, left_field, right_model, right_field = match.groups()
    return {"left_model": left_model, "left_field": left_field, "right_model": right_model, "right_field": right_field}


def _measure(raw: str) -> dict[str, str]:
    match = re.fullmatch(r"([a-z][a-z0-9_]*)\s*=\s*(sum|avg|min|max|count)\(([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*|\*)\)", raw)
    if not match:
        return _invalid(raw)
    field_name, function, model, source = match.groups()
    return {"field": field_name, "function": function, "source_model": model, "source": source}


def _order_term(raw: str) -> dict[str, str]:
    match = re.fullmatch(r"((?:[a-z][a-z0-9_]*\.)?[a-z][a-z0-9_]*)\s+(asc|desc)", raw)
    return {"field": match.group(1), "direction": match.group(2)} if match else _invalid(raw)


def content_object(document: CanonicalDocument) -> dict[str, Any]:
    """Return semantic content only; lifecycle/approval metadata is excluded."""
    value = document.to_dict()
    def normalize_prose(text: str) -> str:
        return "\n".join(re.sub(r"[ \t]+", " ", line).rstrip() for line in text.splitlines()).strip()
    value["intent"] = normalize_prose(value["intent"])
    value["scope"] = normalize_prose(value["scope"])
    for section in ("questions", "inputs", "models", "transform", "outputs", "delivery", "contracts", "decisions", "open_questions"):
        if section not in value:
            continue
        for item in value[section].values():
            if isinstance(item, dict) and isinstance(item.get("prose"), str):
                item["prose"] = normalize_prose(item["prose"])
    value["frontmatter"] = {
        "dp_spec_version": document.frontmatter.dp_spec_version,
        "name": document.frontmatter.name,
        "workflow": document.frontmatter.workflow,
    }
    return value


def canonical_object(value: ParsedDocument | CanonicalDocument | str) -> dict[str, Any]:
    document = parse(value).document if isinstance(value, str) else value.document if isinstance(value, ParsedDocument) else value
    return content_object(document)


def canonical_bytes(value: ParsedDocument | CanonicalDocument | str) -> bytes:
    return json.dumps(canonical_object(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def semantic_hash(value: ParsedDocument | CanonicalDocument | str) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _from_canonical(payload: dict[str, Any]) -> CanonicalDocument:
    if payload.get("schema") != SCHEMA_ID:
        raise ParseError(f"canonical object schema must be {SCHEMA_ID}")
    fm = payload.get("frontmatter")
    if not isinstance(fm, dict):
        raise ParseError("canonical object needs frontmatter")
    frontmatter = Frontmatter(
        int(fm.get("dp_spec_version", 0)), str(fm.get("name", "")),
        str(fm.get("workflow", "")), str(fm.get("status") or "proposed"),
        (str(fm["approved_content_hash"]) if fm.get("approved_content_hash") else None),
    )
    def entities(name: str) -> tuple[Entity, ...]:
        raw = payload.get(name, {})
        if not isinstance(raw, dict):
            raise ParseError(f"canonical {name} must be an object keyed by stable id")
        values: list[Entity] = []
        for entity_id, item in raw.items():
            if not isinstance(item, dict):
                raise ParseError(f"canonical {name}[{entity_id}] must be an object")
            fields = {key: value for key, value in item.items() if key not in {"id", "prose"}}
            values.append(Entity(str(entity_id), fields, str(item.get("prose", ""))))
        return tuple(values)
    return CanonicalDocument(
        frontmatter, str(payload.get("intent", "")), entities("questions"), str(payload.get("scope", "")),
        entities("inputs"), entities("models"), entities("transform"), entities("outputs"),
        entities("delivery"), entities("decisions"), entities("open_questions"),
        preamble=str(payload.get("preamble", "")),
        unknown_sections=tuple(str(item) for item in payload.get("unknown_sections", [])),
        unknown_section_bodies={str(key): str(value) for key, value in (payload.get("unknown_section_bodies") or {}).items()},
        present_sections=tuple(str(item) for item in payload.get("sections_present", SECTION_KEYS.values())),
        contracts=entities("contracts") if "contracts" in payload else (),
    )


def emit(document: CanonicalDocument | dict[str, Any]) -> str:
    """Render a deterministic proposal. Callers must not overwrite live Markdown with it."""
    if isinstance(document, dict):
        document = _from_canonical(document)
    fm = document.frontmatter
    out = ["---", f"dp_spec_version: {fm.dp_spec_version}", f"name: {fm.name}", f"workflow: {fm.workflow}", f"status: {fm.status}"]
    if fm.approved_content_hash:
        out.append(f"approved_content_hash: {fm.approved_content_hash}")
    out.append("---")
    if document.preamble:
        out.extend(["", document.preamble])
    scalar_sections = {"Intent": document.intent, "Scope": document.scope}
    entity_sections = {
        "Questions": document.questions, "Inputs": document.inputs, "Models": document.models,
        "Transform": document.transform, "Outputs": document.outputs, "Delivery": document.delivery,
        "Contracts": document.contracts,
        "Decisions": document.decisions, "Open Questions": document.open_questions,
    }
    for title in SECTION_TITLES:
        out.extend(["", f"## {title}", ""])
        key = SECTION_KEYS[title]
        if title in scalar_sections:
            if scalar_sections[title]:
                out.append(scalar_sections[title])
            continue
        kind = ENTITY_KINDS[key]
        for entity in sorted(entity_sections[title], key=lambda item: item.id):
            out.append(f"### {kind} `{entity.id}`")
            for field_name in sorted(entity.fields):
                label = field_name.replace("_", " ").title()
                value = entity.fields[field_name]
                rendered = _render_field_value(field_name, value)
                out.append(f"- {label}: {rendered}")
            if entity.prose:
                out.extend(["", entity.prose])
            out.append("")
    for title in sorted(document.unknown_section_bodies):
        out.extend(["", f"## {title}", "", document.unknown_section_bodies[title]])
    return "\n".join(out).rstrip() + "\n"


def _render_field_value(field_name: str, value: Any) -> str:
    if field_name == "predicate" and isinstance(value, dict) and "field" in value:
        return f"`{value['model']}.{value['field']} {value['operator']} {value['value']}`"
    if field_name == "expressions" and isinstance(value, list):
        return ", ".join(f"`{item['field']} = {item['expression']}`" if isinstance(item, dict) and "field" in item else f"`{item.get('invalid', '')}`" for item in value)
    if field_name == "on" and isinstance(value, list):
        return ", ".join(
            f"`{item['left_model']}.{item['left_field']} = {item['right_model']}.{item['right_field']}`"
            if isinstance(item, dict) and "left_model" in item else f"`{item.get('invalid', '')}`" for item in value
        )
    if field_name == "measures" and isinstance(value, list):
        return ", ".join(f"`{item['field']}={item['function']}({item['source_model']}.{item['source']})`" if isinstance(item, dict) and "field" in item else f"`{item.get('invalid', '')}`" for item in value)
    if field_name == "order_by" and isinstance(value, list):
        return ", ".join(f"`{item['field']} {item['direction']}`" if isinstance(item, dict) and "field" in item else f"`{item.get('invalid', '')}`" for item in value)
    if isinstance(value, list):
        return ", ".join(f"`{item}`" for item in value)
    return f"`{value}`"


def new_document(name: str, workflow: str, status: str = "draft") -> str:
    """Return an intentionally incomplete, reviewable v2 proposal."""
    document = CanonicalDocument(
        Frontmatter(SPEC_VERSION, name, workflow, status), "Describe the user outcome.", (),
        "Describe the boundary and exclusions.", (), (), (), (), (), (), (),
    )
    return emit(document)


def _by_id(items: Iterable[Entity]) -> dict[str, Entity]:
    return {item.id: item for item in items}


def _issue(issues: list[ValidationIssue], code: str, path: str, message: str) -> None:
    agent_owned = code.startswith((
        "v2.document.", "v2.section.", "v2.field.", "v2.transform.",
        "v2.model.", "v2.output.", "v2.delivery.", "v2.question.",
        "v2.contract.", "v2.procedure.", "v2.target.",
    ))
    enum_fields = {
        "dp_spec_version", "status", "type", "kind", "operation", "join_type",
        "cardinality", "unmatched", "null_keys", "null_handling", "winner",
        "alignment", "missing_fields", "phase", "blocking", "provenance",
        "direction",
    }
    list_fields = {
        "fields", "inputs", "questions", "projection", "delivery_refs", "group_by",
        "measures", "expressions", "on", "keys", "order_by",
    }
    field_name = path.rsplit(".", 1)[-1]
    if field_name in enum_fields or code.endswith((".kind", ".status", ".type", ".phase", ".provenance")):
        control = "enum"
    elif field_name in list_fields or "output_shape" in code or "collision" in code:
        control = "list" if field_name in list_fields else "table"
    elif code.endswith((".prose", ".empty")):
        control = "long_text"
    else:
        control = "text"
    issues.append(ValidationIssue(code, path, message, "agent" if agent_owned else "user", control))


def _require_fields(issues: list[ValidationIssue], section: str, entry: Entity, required: Iterable[str]) -> None:
    for name in required:
        if name not in entry.fields:
            _issue(issues, "v2.field.missing", _path(section, entry.id, name), f"missing required field {name!r}")


def _known_fields(issues: list[ValidationIssue], section: str, entry: Entity) -> None:
    for name in entry.fields:
        if name not in ENTITY_FIELDS[section]:
            _issue(issues, "v2.field.unknown", _path(section, entry.id, name), f"unknown v2 field {name!r}")


def _as_list(value: Any) -> list[str]:
    return value if isinstance(value, list) else []


def _valid_ref(value: Any, choices: dict[str, Entity]) -> bool:
    return isinstance(value, str) and value in choices


def _model_fields(issues: list[ValidationIssue], model: Entity) -> set[str]:
    values = model.fields.get("fields")
    if not isinstance(values, list) or not values:
        _issue(issues, "v2.model.fields", _path("models", model.id, "fields"), "model must declare non-empty Fields")
        return set()
    seen: set[str] = set()
    for field_name in values:
        if not isinstance(field_name, str) or not NAME_RE.match(field_name) or field_name in seen:
            _issue(issues, "v2.model.fields", _path("models", model.id, "fields"), "Fields must be unique lowercase snake_case names")
        seen.add(str(field_name))
    return seen


def _nonempty_list(issues: list[ValidationIssue], section: str, entry: Entity, field_name: str) -> list[Any]:
    value = entry.fields.get(field_name)
    if not isinstance(value, list) or not value:
        _issue(issues, "v2.field.nonempty_list", _path(section, entry.id, field_name), f"{field_name} must be a non-empty list")
        return []
    return value


def validate(value: ParsedDocument | CanonicalDocument | str) -> list[ValidationIssue]:
    """Return deterministic, stable-path v2 semantic validation findings."""
    parsed = parse(value) if isinstance(value, str) else value
    document = parsed.document if isinstance(parsed, ParsedDocument) else parsed
    issues: list[ValidationIssue] = []
    fm = document.frontmatter
    if fm.dp_spec_version != SPEC_VERSION:
        _issue(issues, "v2.frontmatter.version", _path("frontmatter", None, "dp_spec_version"), "dp_spec_version must be 2")
    if not NAME_RE.match(fm.name):
        _issue(issues, "v2.frontmatter.name", _path("frontmatter", None, "name"), "name must be lowercase snake_case")
    if not fm.workflow:
        _issue(issues, "v2.frontmatter.workflow", _path("frontmatter", None, "workflow"), "workflow must not be empty")
    if fm.status not in STATUS_VALUES:
        _issue(issues, "v2.frontmatter.status", _path("frontmatter", None, "status"), "status is not supported")
    if fm.status == "approved":
        if not fm.approved_content_hash:
            _issue(issues, "v2.approval.binding_missing", _path("frontmatter", None, "approved_content_hash"), "approved documents must bind an approved_content_hash")
        elif fm.approved_content_hash != semantic_hash(document):
            _issue(issues, "v2.approval.binding_mismatch", _path("frontmatter", None, "approved_content_hash"), "approved_content_hash does not match semantic content")
    elif fm.approved_content_hash:
        _issue(issues, "v2.approval.binding_lifecycle", _path("frontmatter", None, "approved_content_hash"), "only approved documents may carry approved_content_hash")
    if document.preamble:
        _issue(issues, "v2.document.preamble", "v2:preamble", "preamble is preserved but not allowed in a v2 contract")
    for title in document.unknown_sections:
        code = "v2.section.policy" if title.lower() == "policy" else "v2.section.unknown"
        _issue(issues, code, "v2:sections", f"section {title!r} is not part of v2")
    if not document.intent:
        _issue(issues, "v2.section.empty", _path("intent"), "Intent must contain prose")
    if not document.scope:
        _issue(issues, "v2.section.empty", _path("scope"), "Scope must contain prose")
    for section in (key for key in SECTION_KEYS.values() if key != "contracts"):
        if section not in document.present_sections:
            _issue(issues, "v2.section.missing", _path(section), f"missing required v2 section {section!r}")
    for section, items, require_items in (
        ("questions", document.questions, True), ("inputs", document.inputs, True),
        ("models", document.models, True), ("transform", document.transform, True),
        ("outputs", document.outputs, True), ("delivery", document.delivery, True),
        ("contracts", document.contracts, False),
        ("decisions", document.decisions, False), ("open_questions", document.open_questions, False),
    ):
        if require_items and not items:
            _issue(issues, "v2.section.empty", _path(section), f"{section} needs at least one entry")
        for item in items:
            _known_fields(issues, section, item)

    inputs = _by_id(document.inputs)
    models = _by_id(document.models)
    steps = _by_id(document.transform)
    questions = _by_id(document.questions)
    deliveries = _by_id(document.delivery)
    outputs = _by_id(document.outputs)
    contracts = _by_id(document.contracts)

    for entry in document.questions:
        if not entry.prose:
            _issue(issues, "v2.question.text", _path("questions", entry.id), "question must contain prose")
    for entry in document.inputs:
        _require_fields(issues, "inputs", entry, ("type", "location", "description"))
        if entry.fields.get("type") not in INPUT_TYPES:
            _issue(issues, "v2.input.type", _path("inputs", entry.id, "type"), "input type is not in the v2 vocabulary")
    for entry in document.delivery:
        _require_fields(issues, "delivery", entry, ("kind", "target"))
        if entry.fields.get("kind") not in DELIVERY_KINDS:
            _issue(issues, "v2.delivery.kind", _path("delivery", entry.id, "kind"), "delivery kind is not in the v2 vocabulary")

    producers: dict[str, list[Entity]] = {}
    for step in document.transform:
        _require_fields(issues, "transform", step, ("operation", "inputs", "output"))
        operation = step.fields.get("operation")
        if operation not in OPERATION_KINDS:
            _issue(issues, "v2.transform.operation", _path("transform", step.id, "operation"), "operation must be a closed v2 operation kind")
            continue
        step_inputs = _as_list(step.fields.get("inputs"))
        output = step.fields.get("output")
        if not isinstance(output, str) or not NAME_RE.match(output):
            _issue(issues, "v2.transform.output", _path("transform", step.id, "output"), "a step writes exactly one declared model id")
        else:
            producers.setdefault(output, []).append(step)
            target = models.get(output)
            if target is None:
                _issue(issues, "v2.transform.temporary_relation", _path("transform", step.id, "output"), "step output is not a declared model")
            elif target.fields.get("kind") != "derived":
                _issue(issues, "v2.transform.output_kind", _path("transform", step.id, "output"), "step output must be a declared derived model")
        for model_id in step_inputs:
            if model_id not in models:
                _issue(issues, "v2.transform.input_ref", _path("transform", step.id, "inputs"), f"unknown input model {model_id!r}")
        _validate_operation(issues, step, operation, step_inputs, models)

    for entry in document.models:
        _require_fields(issues, "models", entry, ("kind", "description", "grain", "key", "fields"))
        _model_fields(issues, entry)
        kind = entry.fields.get("kind")
        if kind not in MODEL_KINDS:
            _issue(issues, "v2.model.kind", _path("models", entry.id, "kind"), "model kind is not supported")
            continue
        if kind in {"base", "reference"}:
            _require_fields(issues, "models", entry, ("input",))
            source = entry.fields.get("input")
            if not _valid_ref(source, inputs):
                _issue(issues, "v2.model.origin", _path("models", entry.id, "input"), "base/reference model must originate in a declared Input")
            for forbidden in ("produced_by", "view_expression"):
                if forbidden in entry.fields:
                    _issue(issues, "v2.model.origin_shape", _path("models", entry.id, forbidden), "base/reference models cannot declare another origin")
        if kind == "derived":
            _require_fields(issues, "models", entry, ("produced_by",))
            produced_by = entry.fields.get("produced_by")
            if not _valid_ref(produced_by, steps):
                _issue(issues, "v2.model.origin", _path("models", entry.id, "produced_by"), "derived model must name its transform step")
            model_producers = producers.get(entry.id, [])
            if len(model_producers) != 1:
                _issue(issues, "v2.model.producer_count", _path("models", entry.id, "produced_by"), "derived model needs exactly one transform producer")
            elif produced_by != model_producers[0].id:
                _issue(issues, "v2.model.origin", _path("models", entry.id, "produced_by"), "Produced by must agree with the transform step that writes this model")
            for forbidden in ("input", "view_expression"):
                if forbidden in entry.fields:
                    _issue(issues, "v2.model.origin_shape", _path("models", entry.id, forbidden), "derived models cannot declare another origin")
        if kind == "view":
            _require_fields(issues, "models", entry, ("view_expression",))
            if entry.id in producers:
                _issue(issues, "v2.model.view_producer", _path("models", entry.id), "view must come from its declared view expression, not a transform step")
            expression = entry.fields.get("view_expression")
            if not isinstance(expression, str) or not VIEW_EXPRESSION_RE.match(expression):
                _issue(issues, "v2.model.view_expression", _path("models", entry.id, "view_expression"), "view expression must be a comma-separated list of qualified model.field references")
            else:
                for ref in expression.split(","):
                    model_id, field_name = ref.strip().split(".", 1)
                    source_model = models.get(model_id)
                    if source_model is None or field_name not in _model_fields(issues, source_model):
                        _issue(issues, "v2.model.view_expression", _path("models", entry.id, "view_expression"), f"view expression references unknown field {ref.strip()!r}")
            for forbidden in ("input", "produced_by"):
                if forbidden in entry.fields:
                    _issue(issues, "v2.model.origin_shape", _path("models", entry.id, forbidden), "views cannot declare another origin")
        procedure = entry.fields.get("procedure")
        if procedure is not None and (not isinstance(procedure, str) or not PROCEDURE_RE.match(procedure)):
            _issue(issues, "v2.procedure.version", _path("models", entry.id, "procedure"), "procedure must carry id@version")
        elif procedure is not None and kind != "reference":
            _issue(issues, "v2.procedure.model_kind", _path("models", entry.id, "procedure"), "procedure-bearing models must be reference models")

    reached_questions: set[str] = set()
    used_deliveries: set[str] = set()
    for output in document.outputs:
        _require_fields(issues, "outputs", output, ("model", "questions", "projection", "order_by", "delivery_refs"))
        target_model = models.get(output.fields.get("model")) if isinstance(output.fields.get("model"), str) else None
        if target_model is None:
            _issue(issues, "v2.output.model_ref", _path("outputs", output.id, "model"), "output must reference a declared model")
        for question_id in _nonempty_list(issues, "outputs", output, "questions"):
            if isinstance(question_id, str):
                reached_questions.add(question_id)
            if question_id not in questions:
                _issue(issues, "v2.output.question_ref", _path("outputs", output.id, "questions"), f"unknown question {question_id!r}")
        projection = _nonempty_list(issues, "outputs", output, "projection")
        order_by = _nonempty_list(issues, "outputs", output, "order_by")
        for field_name in projection:
            if target_model is not None and field_name not in _model_fields(issues, target_model):
                _issue(issues, "v2.output.field_ref", _path("outputs", output.id, "projection"), f"projection references undeclared model field {field_name!r}")
        for term in order_by:
            if not isinstance(term, dict) or set(term) != {"field", "direction"} or term.get("direction") not in {"asc", "desc"}:
                _issue(issues, "v2.output.order", _path("outputs", output.id, "order_by"), "Order by must use field asc|desc terms")
            elif target_model is not None and term["field"] not in _model_fields(issues, target_model):
                _issue(issues, "v2.output.field_ref", _path("outputs", output.id, "order_by"), f"order references undeclared model field {term['field']!r}")
        for delivery_id in _nonempty_list(issues, "outputs", output, "delivery_refs"):
            if isinstance(delivery_id, str):
                used_deliveries.add(delivery_id)
            if delivery_id not in deliveries:
                _issue(issues, "v2.output.delivery_ref", _path("outputs", output.id, "delivery_refs"), f"unknown delivery {delivery_id!r}")
    for question_id in sorted(set(questions) - reached_questions):
        _issue(issues, "v2.question.unserved", _path("questions", question_id), "every question must be answered by at least one explicit output")
    for delivery_id in sorted(set(deliveries) - used_deliveries):
        _issue(issues, "v2.delivery.unused", _path("delivery", delivery_id), "every delivery must be referenced by an explicit output")
    contract_names: set[str] = set()
    for contract in document.contracts:
        _require_fields(issues, "contracts", contract, ("name", "attachment", "model", "phase", "guarantee", "rule", "fields"))
        contract_name = contract.fields.get("name")
        if not isinstance(contract_name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", contract_name):
            _issue(issues, "v2.contract.name", _path("contracts", contract.id, "name"), "contract name must be lowercase hyphenated")
        elif contract_name in contract_names:
            _issue(issues, "v2.contract.duplicate_name", _path("contracts", contract.id, "name"), "contract names must be unique")
        else:
            contract_names.add(contract_name)
        attachment = contract.fields.get("attachment")
        if not isinstance(attachment, str) or ":" not in attachment:
            _issue(issues, "v2.contract.attachment", _path("contracts", contract.id, "attachment"), "attachment must be input:id or output:id")
        else:
            attachment_kind, attachment_id = attachment.split(":", 1)
            choices = {"input": inputs, "output": outputs}
            if attachment_kind not in choices or attachment_id not in choices[attachment_kind]:
                _issue(issues, "v2.contract.attachment", _path("contracts", contract.id, "attachment"), f"unknown contract attachment {attachment!r}")
            phase = contract.fields.get("phase")
            expected_phase = {"input": "pre_transform", "output": "post_transform"}.get(attachment_kind)
            if phase != expected_phase:
                _issue(issues, "v2.contract.phase", _path("contracts", contract.id, "phase"), f"{attachment_kind} contracts must use phase {expected_phase!r}")
            declared_contract_model = contract.fields.get("model")
            if attachment_kind == "output" and attachment_id in outputs:
                expected_model = outputs[attachment_id].fields.get("model")
                if declared_contract_model != expected_model:
                    _issue(issues, "v2.contract.model", _path("contracts", contract.id, "model"), "an output contract must name the model exposed by its attached output")
            elif attachment_kind == "input" and attachment_id in inputs:
                input_models = {
                    model.id for model in document.models
                    if model.fields.get("kind") in {"base", "reference"}
                    and model.fields.get("input") == attachment_id
                }
                if declared_contract_model not in input_models:
                    _issue(issues, "v2.contract.model", _path("contracts", contract.id, "model"), "an input contract must name a base/reference model originating from its attached input")
        if contract.fields.get("model") not in models:
            _issue(issues, "v2.contract.model", _path("contracts", contract.id, "model"), "contract model must reference a declared model")
        if contract.fields.get("phase") not in {"pre_transform", "post_transform"}:
            _issue(issues, "v2.contract.phase", _path("contracts", contract.id, "phase"), "contract phase must be pre_transform or post_transform")
        contract_model = models.get(contract.fields.get("model"))
        declared_model_fields = _model_fields(issues, contract_model) if contract_model else set()
        for field_name in _nonempty_list(issues, "contracts", contract, "fields"):
            if not isinstance(field_name, str) or not NAME_RE.match(field_name):
                _issue(issues, "v2.contract.fields", _path("contracts", contract.id, "fields"), "contract fields must be lowercase snake_case")
            elif field_name not in declared_model_fields:
                _issue(issues, "v2.contract.fields", _path("contracts", contract.id, "fields"), f"contract field {field_name!r} is not declared by model {contract.fields.get('model')!r}")
        for field_name in ("guarantee", "rule"):
            if not isinstance(contract.fields.get(field_name), str) or not contract.fields[field_name].strip():
                _issue(issues, "v2.contract.prose", _path("contracts", contract.id, field_name), f"contract {field_name} must not be empty")
    for decision in document.decisions:
        _require_fields(issues, "decisions", decision, ("target", "status", "provenance", "ruling"))
        _validate_target(issues, "decisions", decision, models, steps, outputs, inputs)
        if decision.fields.get("status") not in DECISION_STATUSES:
            _issue(issues, "v2.decision.status", _path("decisions", decision.id, "status"), "decision status is not in the v2 vocabulary")
        if decision.fields.get("provenance") not in DECISION_PROVENANCE:
            _issue(issues, "v2.decision.provenance", _path("decisions", decision.id, "provenance"), "decision provenance is not in the v2 vocabulary")
    blocking_questions: list[Entity] = []
    for question in document.open_questions:
        _require_fields(issues, "open_questions", question, ("blocking", "target"))
        if not question.prose:
            _issue(issues, "v2.open_question.text", _path("open_questions", question.id), "open question must contain prose")
        _validate_target(issues, "open_questions", question, models, steps, outputs, inputs)
        if question.fields.get("blocking") == "yes":
            blocking_questions.append(question)
        elif question.fields.get("blocking") not in {"yes", "no"}:
            _issue(issues, "v2.open_question.blocking", _path("open_questions", question.id, "blocking"), "Blocking must be yes or no")
    if fm.status == "approved":
        for question in blocking_questions:
            _issue(issues, "v2.approval.blocked", _path("open_questions", question.id, "blocking"), "blocking open questions prevent approval/materialization")
    for cycle in graph_cycles(document):
        _issue(issues, "v2.transform.cycle", _path("transform", cycle[0]), "transform graph cycle: " + " -> ".join(cycle))
    return sorted(issues, key=lambda item: (item.path, item.code, item.message))


def _validate_operation(
    issues: list[ValidationIssue], step: Entity, operation: str, inputs: list[str], models: dict[str, Entity],
) -> None:
    path = lambda field_name: _path("transform", step.id, field_name)
    output = models.get(step.fields.get("output")) if isinstance(step.fields.get("output"), str) else None
    input_models = [models[item] for item in inputs if item in models]

    def require_model_fields(model: Entity | None, field_names: Iterable[str], field_path: str) -> None:
        if model is None:
            return
        available = _model_fields(issues, model)
        for field_name in field_names:
            if field_name not in available:
                _issue(issues, "v2.transform.field_ref", field_path, f"field {field_name!r} is not declared by model {model.id!r}")

    def structure(value: Any, keys: set[str], field_name: str) -> bool:
        if not isinstance(value, dict) or set(value) != keys:
            _issue(issues, "v2.transform.structure", path(field_name), f"{field_name} does not match its closed v2 structure")
            return False
        return True

    def qualified(value: Any, model: Entity | None, field_path: str) -> str | None:
        if not isinstance(value, str) or "." not in value:
            _issue(issues, "v2.transform.qualified_ref", field_path, "transform field references must be qualified as model.field")
            return None
        model_id, field_name = value.split(".", 1)
        if model is not None and model_id != model.id:
            _issue(issues, "v2.transform.qualified_ref", field_path, f"field reference must use input model {model.id!r}")
        require_model_fields(model, [field_name], field_path)
        return field_name

    for field_name in step.fields:
        if field_name not in OPERATION_FIELDS.get(operation, frozenset()):
            _issue(issues, "v2.transform.field.unknown", path(field_name), f"{field_name} is not allowed for operation {operation!r}")

    def output_shape(expected: set[str], operation_note: str) -> None:
        if output is None:
            return
        actual = _model_fields(issues, output)
        if actual != expected:
            _issue(
                issues,
                "v2.transform.output_shape",
                path("output"),
                f"{operation_note}: output fields {sorted(actual)} must equal {sorted(expected)}",
            )

    def ordered_output_shape(expected: list[str], operation_note: str) -> None:
        if output is None:
            return
        actual = output.fields.get("fields")
        if actual != expected:
            _issue(
                issues,
                "v2.transform.output_shape",
                path("output"),
                f"{operation_note}: output fields {actual!r} must equal {expected!r} in declared order",
            )

    one_input = {"filter", "project", "derive", "aggregate", "deduplicate", "apply_procedure"}
    if operation in one_input and len(inputs) != 1:
        _issue(issues, "v2.transform.input_arity", path("inputs"), f"{operation} needs exactly one input")
    if operation == "join":
        if len(inputs) != 2:
            _issue(issues, "v2.transform.input_arity", path("inputs"), "join needs exactly two inputs")
        _require_fields(issues, "transform", step, ("join_type", "on", "cardinality", "unmatched", "null_keys"))
        if step.fields.get("join_type") not in JOIN_TYPES:
            _issue(issues, "v2.transform.join_type", path("join_type"), "join type is not in the v2 vocabulary")
        if step.fields.get("cardinality") not in JOIN_CARDINALITIES:
            _issue(issues, "v2.transform.cardinality", path("cardinality"), "join cardinality is not in the v2 vocabulary")
        if step.fields.get("unmatched") not in UNMATCHED_BEHAVIORS:
            _issue(issues, "v2.transform.unmatched", path("unmatched"), "join unmatched behavior is not in the v2 vocabulary")
        if step.fields.get("null_keys") not in JOIN_NULL_BEHAVIORS:
            _issue(issues, "v2.transform.null_keys", path("null_keys"), "join null-key behavior is not in the v2 vocabulary")
        if step.fields.get("unmatched") not in {
            "inner": {"drop"}, "left": {"drop", "keep_left"},
            "right": {"drop", "keep_right"}, "full": {"drop", "keep_both"},
        }.get(step.fields.get("join_type"), set()):
            _issue(issues, "v2.transform.join_behavior", path("unmatched"), "join unmatched behavior is incompatible with join type")
        for key in _nonempty_list(issues, "transform", step, "on"):
            if not structure(key, {"left_model", "left_field", "right_model", "right_field"}, "on"):
                continue
            if len(input_models) == 2:
                left, right = input_models
                if key["left_model"] != left.id or key["right_model"] != right.id:
                    _issue(issues, "v2.transform.join_key", path("on"), "join keys must qualify fields with the two declared input model ids in input order")
                require_model_fields(left, [key["left_field"]], path("on"))
                require_model_fields(right, [key["right_field"]], path("on"))
        if len(input_models) == 2:
            left_fields = _model_fields(issues, input_models[0])
            right_fields = _model_fields(issues, input_models[1])
            collision = left_fields & right_fields
            if collision:
                _issue(issues, "v2.transform.join_collision", path("output"), f"join inputs share field names {sorted(collision)}; qualify or rename them before joining")
            output_shape(left_fields | right_fields, "join")
    elif operation == "aggregate":
        _require_fields(issues, "transform", step, ("group_by", "measures", "null_handling"))
        if step.fields.get("null_handling") not in NULL_HANDLING:
            _issue(issues, "v2.transform.null_handling", path("null_handling"), "aggregate null handling is not in the v2 vocabulary")
        source = input_models[0] if len(input_models) == 1 else None
        group_by = _nonempty_list(issues, "transform", step, "group_by")
        output_group_by: list[str] = []
        for ref in group_by:
            field_name = qualified(ref, source, path("group_by"))
            if field_name:
                output_group_by.append(field_name)
        if output is not None:
            require_model_fields(output, output_group_by, path("group_by"))
        output_measures: set[str] = set()
        for measure in _nonempty_list(issues, "transform", step, "measures"):
            if not structure(measure, {"field", "function", "source_model", "source"}, "measures"):
                continue
            if measure["function"] not in AGGREGATE_FUNCTIONS:
                _issue(issues, "v2.transform.measure", path("measures"), "measure function is not in the v2 vocabulary")
            if measure["source_model"] != (source.id if source is not None else None):
                _issue(issues, "v2.transform.qualified_ref", path("measures"), "measure source must use the declared input model")
            if measure["source"] != "*":
                require_model_fields(source, [measure["source"]], path("measures"))
            require_model_fields(output, [measure["field"]], path("measures"))
            output_measures.add(measure["field"])
        output_shape(set(output_group_by) | output_measures, "aggregate")
    elif operation == "filter":
        _require_fields(issues, "transform", step, ("predicate",))
        predicate = step.fields.get("predicate")
        if structure(predicate, {"model", "field", "operator", "value"}, "predicate"):
            source = input_models[0] if len(input_models) == 1 else None
            if source is not None and predicate["model"] != source.id:
                _issue(issues, "v2.transform.qualified_ref", path("predicate"), "predicate must use the declared input model")
            require_model_fields(source, [predicate["field"]], path("predicate"))
        if input_models:
            output_shape(_model_fields(issues, input_models[0]), "filter")
    elif operation == "project":
        _require_fields(issues, "transform", step, ("fields",))
        field_names = _nonempty_list(issues, "transform", step, "fields")
        source = input_models[0] if len(input_models) == 1 else None
        output_fields: list[str] = []
        for ref in field_names:
            field_name = qualified(ref, source, path("fields"))
            if field_name:
                output_fields.append(field_name)
        if output is not None:
            require_model_fields(output, output_fields, path("fields"))
        output_shape(set(output_fields), "project")
    elif operation == "derive":
        _require_fields(issues, "transform", step, ("expressions",))
        source = input_models[0] if len(input_models) == 1 else None
        expression_fields: set[str] = set()
        for expression in _nonempty_list(issues, "transform", step, "expressions"):
            if not structure(expression, {"field", "expression", "references"}, "expressions"):
                continue
            for ref in expression["references"]:
                qualified(ref, source, path("expressions"))
            require_model_fields(output, [expression["field"]], path("expressions"))
            expression_fields.add(expression["field"])
        if source is not None:
            output_shape(_model_fields(issues, source) | expression_fields, "derive")
    elif operation == "deduplicate":
        _require_fields(issues, "transform", step, ("keys", "order_by", "winner"))
        if step.fields.get("winner") not in DEDUP_WINNERS:
            _issue(issues, "v2.transform.winner", path("winner"), "deduplicate winner is not in the v2 vocabulary")
        source = input_models[0] if len(input_models) == 1 else None
        for ref in _nonempty_list(issues, "transform", step, "keys"):
            qualified(ref, source, path("keys"))
        for term in _nonempty_list(issues, "transform", step, "order_by"):
            if structure(term, {"field", "direction"}, "order_by"):
                if term["direction"] not in {"asc", "desc"}:
                    _issue(issues, "v2.transform.order", path("order_by"), "order direction must be asc or desc")
                qualified(term["field"], source, path("order_by"))
        if source is not None:
            output_shape(_model_fields(issues, source), "deduplicate")
    elif operation == "union" and len(inputs) < 2:
        _issue(issues, "v2.transform.input_arity", path("inputs"), "union needs at least two inputs")
    elif operation == "union":
        _require_fields(issues, "transform", step, ("alignment", "missing_fields"))
        if step.fields.get("alignment") not in UNION_ALIGNMENTS:
            _issue(issues, "v2.transform.alignment", path("alignment"), "union alignment is not in the v2 vocabulary")
        if step.fields.get("missing_fields") not in MISSING_FIELD_BEHAVIORS:
            _issue(issues, "v2.transform.missing_fields", path("missing_fields"), "union missing-field behavior is not in the v2 vocabulary")
        if output is not None:
            target_fields = _model_fields(issues, output)
            if step.fields.get("alignment") == "by_position":
                source_field_lists = [list(source.fields.get("fields") or []) for source in input_models]
                if any(len(fields) != len(source_field_lists[0]) for fields in source_field_lists[1:]):
                    _issue(issues, "v2.transform.union_alignment", path("inputs"), "union by_position requires every input to have the same field count")
                if step.fields.get("missing_fields") != "error":
                    _issue(issues, "v2.transform.union_alignment", path("missing_fields"), "union by_position requires missing_fields=error")
                ordered_output_shape(source_field_lists[0] if source_field_lists else [], "union by_position")
            else:
                output_shape(set().union(*(_model_fields(issues, source) for source in input_models)), "union by_name")
                for source in input_models:
                    source_fields = _model_fields(issues, source)
                    if step.fields.get("missing_fields") == "error" and source_fields != target_fields:
                        _issue(issues, "v2.transform.union_alignment", path("inputs"), "union by_name with missing_fields=error requires every input to match output fields")
    elif operation == "apply_procedure":
        _require_fields(issues, "transform", step, ("procedure", "fields"))
        procedure = step.fields.get("procedure")
        if not isinstance(procedure, str) or not PROCEDURE_RE.match(procedure):
            _issue(issues, "v2.procedure.version", path("procedure"), "procedure must carry id@version")
        result_fields = _nonempty_list(issues, "transform", step, "fields")
        if output is not None:
            require_model_fields(output, result_fields, path("fields"))
        output_shape(set(result_fields), "apply_procedure")


def _validate_target(
    issues: list[ValidationIssue], section: str, entry: Entity, models: dict[str, Entity], steps: dict[str, Entity],
    outputs: dict[str, Entity], inputs: dict[str, Entity],
) -> None:
    target = entry.fields.get("target")
    if not isinstance(target, str) or ":" not in target:
        _issue(issues, "v2.target.ref", _path(section, entry.id, "target"), "target must be kind:id")
        return
    kind, entity_id = target.split(":", 1)
    choices = {"model": models, "step": steps, "output": outputs, "input": inputs}
    if kind not in choices or entity_id not in choices[kind]:
        _issue(issues, "v2.target.ref", _path(section, entry.id, "target"), f"unknown stable target {target!r}")


def _step_graph(document: CanonicalDocument) -> dict[str, set[str]]:
    models = _by_id(document.models)
    producers = {step.fields.get("output"): step.id for step in document.transform if isinstance(step.fields.get("output"), str)}
    graph: dict[str, set[str]] = {step.id: set() for step in document.transform}
    for step in document.transform:
        for model_id in _as_list(step.fields.get("inputs")):
            model = models.get(model_id)
            producer = producers.get(model_id)
            if model is not None and model.fields.get("kind") == "derived" and producer is not None:
                graph[step.id].add(producer)
    return graph


def graph_cycles(document: CanonicalDocument) -> list[list[str]]:
    graph = _step_graph(document)
    state: dict[str, int] = {node: 0 for node in graph}
    stack: list[str] = []
    cycles: list[list[str]] = []
    def visit(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for child in sorted(graph[node]):
            if state[child] == 0:
                visit(child)
            elif state[child] == 1:
                cycles.append(stack[stack.index(child):] + [child])
        stack.pop()
        state[node] = 2
    for node in sorted(graph):
        if state[node] == 0:
            visit(node)
    return cycles


def validate_with_graph(value: ParsedDocument | CanonicalDocument | str) -> list[ValidationIssue]:
    """Backward-compatible spelling for callers that adopted the first draft API."""
    return validate(value)


def semantic_diff(before: ParsedDocument | CanonicalDocument | str, after: ParsedDocument | CanonicalDocument | str) -> list[dict[str, Any]]:
    before_doc = parse(before).document if isinstance(before, str) else before.document if isinstance(before, ParsedDocument) else before
    after_doc = parse(after).document if isinstance(after, str) else after.document if isinstance(after, ParsedDocument) else after
    differences: list[dict[str, Any]] = []
    def add(path: str, left: Any, right: Any) -> None:
        if left != right:
            differences.append({"path": path, "before": left, "after": right})
    left_frontmatter = before_doc.frontmatter.to_dict()
    right_frontmatter = after_doc.frontmatter.to_dict()
    for field_name in sorted(set(left_frontmatter) | set(right_frontmatter)):
        # Lifecycle is deliberately outside semantic identity.  Approval and
        # proposal transitions are reported by the caller's lifecycle state,
        # never as content changes that would suggest regeneration.
        if field_name in {"status", "approved_content_hash"}:
            continue
        add(_path("frontmatter", None, field_name), left_frontmatter.get(field_name), right_frontmatter.get(field_name))
    add(_path("intent", None, "text"), before_doc.intent, after_doc.intent)
    add(_path("scope", None, "text"), before_doc.scope, after_doc.scope)
    for section in ("questions", "inputs", "models", "transform", "outputs", "delivery", "decisions", "open_questions"):
        before_items = _by_id(getattr(before_doc, section))
        after_items = _by_id(getattr(after_doc, section))
        for entity_id in sorted(set(before_items) | set(after_items)):
            left = before_items.get(entity_id)
            right = after_items.get(entity_id)
            if left is None or right is None:
                add(_path(section, entity_id), left.to_dict() if left else None, right.to_dict() if right else None)
                continue
            for field_name in sorted(set(left.fields) | set(right.fields)):
                add(_path(section, entity_id, field_name), left.fields.get(field_name), right.fields.get(field_name))
            add(_path(section, entity_id, "prose"), left.prose, right.prose)
    for title in sorted(set(before_doc.unknown_section_bodies) | set(after_doc.unknown_section_bodies)):
        add(f"v2:unknown_sections[{title}].text", before_doc.unknown_section_bodies.get(title), after_doc.unknown_section_bodies.get(title))
    return differences


def _replace_spans(raw: str, replacements: list[tuple[SourceSpan, str]]) -> str:
    """Apply non-overlapping replacements from the end, preserving all other bytes."""
    updated = raw
    for span, value in sorted(replacements, key=lambda item: item[0].offset_start, reverse=True):
        updated = updated[:span.offset_start] + value + updated[span.offset_end:]
    return updated


def _rewrite_lifecycle(raw: str, *, status: str | None = None, approved_hash: str | None = None) -> str:
    """Rewrite only lifecycle lines in frontmatter, preserving the body bytes."""
    match = re.match(r"\A---\n(?P<body>.*?)\n---(?P<tail>.*)\Z", raw, flags=re.DOTALL)
    if not match:
        raise ParseError("cannot rewrite lifecycle metadata without v2 frontmatter")
    lines = match.group("body").split("\n")
    rewritten: list[str] = []
    for line in lines:
        if line.startswith("status:") and status is not None:
            rewritten.append(f"status: {status}")
        elif line.startswith("approved_content_hash:"):
            continue
        else:
            rewritten.append(line)
    if approved_hash is not None:
        rewritten.append(f"approved_content_hash: {approved_hash}")
    return "---\n" + "\n".join(rewritten) + "\n---" + match.group("tail")


def targeted_patch(parsed: ParsedDocument, *, base_hash: str, path: str, value: str) -> str:
    """Patch a mapped scalar or entity prose span; list/entity edits need a proposal.

    Any semantic edit to an approved contract automatically changes only its
    mapped status value to ``proposed``.  Approval is deliberately a separate
    checked operation so a UI cannot smuggle it through a field patch.
    """
    actual = semantic_hash(parsed)
    if base_hash != actual:
        raise StalePatchError(f"stale patch base hash: expected {base_hash}, current {actual}")
    span = parsed.source_map.spans.get(path)
    if span is None:
        raise ValueError(f"path {path!r} is not a patchable scalar or prose source field")
    if "\n" in value or "\r" in value:
        raise ValueError("targeted patches cannot introduce multiline values")
    if _path_value(parsed.document, path) is _LIST:
        raise ValueError("targeted patches edit one scalar or prose span; replace list/entity structure through a reviewed proposal")
    if path == _path("frontmatter", None, "status"):
        raise ValueError("status changes require approve(); targeted_patch cannot approve or bypass approval")
    updated = _replace_spans(parsed.raw, [(span, value)])
    if parsed.document.frontmatter.status == "approved":
        updated = _rewrite_lifecycle(updated, status="proposed")
    # Parse now: a caller never receives a source-preserving edit that no longer
    # represents a v2 document.
    parse(updated)
    return updated


def approve(parsed: ParsedDocument, *, base_hash: str) -> str:
    """Set status to approved only for a fully valid, unblocked v2 document."""
    actual = semantic_hash(parsed)
    if base_hash != actual:
        raise StalePatchError(f"stale approval base hash: expected {base_hash}, current {actual}")
    status_span = parsed.source_map.spans[_path("frontmatter", None, "status")]
    candidate = parse(_rewrite_lifecycle(_replace_spans(parsed.raw, [(status_span, "approved")]), status="approved"))
    binding = semantic_hash(candidate)
    candidate = parse(_rewrite_lifecycle(candidate.raw, status="approved", approved_hash=binding))
    issues = validate(candidate)
    if issues:
        detail = "; ".join(f"{issue.path}: {issue.code}" for issue in issues)
        raise ValueError(f"cannot approve invalid or blocked v2 document: {detail}")
    return candidate.raw


_LIST = object()


def _path_value(document: CanonicalDocument, path: str) -> Any:
    """Return a scalar field value, or a sentinel for list-valued source paths."""
    frontmatter_match = re.fullmatch(r"v2:frontmatter\.([a-z_]+)", path)
    if frontmatter_match:
        return getattr(document.frontmatter, frontmatter_match.group(1), None)
    match = re.fullmatch(r"v2:([a-z_]+)\[([a-z][a-z0-9_]*)\]\.([a-z_]+)", path)
    if not match:
        return None
    section, entity_id, field_name = match.groups()
    entries = _by_id(getattr(document, section, ()))
    entity = entries.get(entity_id, Entity("", {}))
    if field_name == "prose":
        return entity.prose
    value = entity.fields.get(field_name)
    return _LIST if isinstance(value, list) else value


SCHEMA: dict[str, Any] = {
    "schema": SCHEMA_ID,
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": SCHEMA_ID,
    "title": "NXD dp-spec v2 canonical AST",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "canonicalization", "frontmatter", "sections_present", *(key for key in SECTION_KEYS.values() if key != "contracts")],
    "properties": {
        "schema": {"const": SCHEMA_ID}, "canonicalization": {"const": CANONICALIZATION},
        "frontmatter": {
            "type": "object", "additionalProperties": False,
            # Canonical content excludes lifecycle fields; the Markdown parser
            # still requires status before approval can be evaluated.
            "required": ["dp_spec_version", "name", "workflow"],
            "properties": {
                "dp_spec_version": {"const": SPEC_VERSION},
                "name": {"type": "string", "pattern": NAME_RE.pattern},
                "workflow": {"type": "string", "minLength": 1},
            },
        },
        "intent": {"type": "string"}, "scope": {"type": "string"},
        "sections_present": {"type": "array", "items": {"enum": list(SECTION_KEYS.values())}, "uniqueItems": True},
        "preamble": {"type": "string"},
        "unknown_sections": {"type": "array", "items": {"type": "string"}},
        "unknown_section_bodies": {"type": "object", "additionalProperties": {"type": "string"}},
        "questions": {"$ref": "#/$defs/entities"}, "inputs": {"$ref": "#/$defs/entities"},
        "models": {"$ref": "#/$defs/entities"}, "transform": {"$ref": "#/$defs/entities"},
        "outputs": {"$ref": "#/$defs/entities"}, "delivery": {"$ref": "#/$defs/entities"},
        "contracts": {"$ref": "#/$defs/entities"},
        "decisions": {"$ref": "#/$defs/entities"}, "open_questions": {"$ref": "#/$defs/entities"},
    },
    "$defs": {
        "entities": {
            "type": "object", "propertyNames": {"pattern": NAME_RE.pattern},
            "additionalProperties": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}, "prose": {"type": "string"}}},
        },
        "operation": {"enum": list(OPERATION_KINDS)},
        "model_kind": {"enum": list(MODEL_KINDS)},
        "operation_required_fields": {
            "filter": ["inputs", "output", "predicate"], "project": ["inputs", "output", "fields"],
            "derive": ["inputs", "output", "expressions"], "join": ["inputs", "output", "join_type", "on", "null_keys"],
            "aggregate": ["inputs", "output", "group_by", "measures"], "union": ["inputs", "output"],
            "deduplicate": ["inputs", "output", "keys"], "apply_procedure": ["inputs", "output", "procedure", "fields"],
        },
    },
    "x_nxd_v2_contract": {
        "body_sections": list(SECTION_TITLES),
        "stable_identity_path": "v2:<section>[<id>].<field>",
        "outputs": {"required": ["model", "questions", "projection", "order_by", "delivery_refs"]},
        "contracts": {"required": ["name", "attachment", "model", "phase", "guarantee", "rule", "fields"]},
        "model_origins": {
            "base": ["input"], "reference": ["input"], "derived": ["produced_by"], "view": ["view_expression"],
        },
        "transform": {"operations": list(OPERATION_KINDS), "one_declared_output_per_step": True, "acyclic": True},
        "procedures": {"version_location": ["transform.procedure", "models.procedure"], "frontmatter_forbidden": True},
        "approval": {"blocking_open_questions_forbidden_when": "status=approved"},
    },
}


def _install_section_schemas() -> None:
    """Replace the generic entity placeholder with closed UI-facing shapes."""
    string = {"type": "string", "minLength": 1}
    string_list = {"type": "array", "items": string, "minItems": 1, "uniqueItems": True}
    field_list = {"type": "array", "items": {"type": "string", "pattern": NAME_RE.pattern}, "minItems": 1, "uniqueItems": True}
    predicate = {
        "type": "object", "additionalProperties": False,
        "required": ["model", "field", "operator", "value"],
        "properties": {
            "model": {"type": "string", "pattern": NAME_RE.pattern},
            "field": {"type": "string", "pattern": NAME_RE.pattern},
            "operator": {"enum": ["=", "!=", "<", "<=", ">", ">="]},
            "value": {"type": "string", "minLength": 1},
        },
    }
    expression = {
        "type": "object", "additionalProperties": False,
        "required": ["field", "expression", "references"],
        "properties": {
            "field": {"type": "string", "pattern": NAME_RE.pattern},
            "expression": {"type": "string", "minLength": 1},
            "references": {"type": "array", "items": {"type": "string", "pattern": r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"}, "minItems": 1},
        },
    }
    join_key = {
        "type": "object", "additionalProperties": False,
        "required": ["left_model", "left_field", "right_model", "right_field"],
        "properties": {key: {"type": "string", "pattern": NAME_RE.pattern} for key in ("left_model", "left_field", "right_model", "right_field")},
    }
    measure = {
        "type": "object", "additionalProperties": False,
        "required": ["field", "function", "source_model", "source"],
        "properties": {
            "field": {"type": "string", "pattern": NAME_RE.pattern},
            "function": {"enum": list(AGGREGATE_FUNCTIONS)},
            "source_model": {"type": "string", "pattern": NAME_RE.pattern},
            "source": {"type": "string", "pattern": r"^(\*|[a-z][a-z0-9_]*)$"},
        },
    }
    order_term = {
        "type": "object", "additionalProperties": False,
        "required": ["field", "direction"],
        "properties": {
            "field": {"type": "string", "pattern": r"^(?:[a-z][a-z0-9_]*\.)?[a-z][a-z0-9_]*$"},
            "direction": {"enum": ["asc", "desc"]},
        },
    }
    specs: dict[str, tuple[dict[str, Any], tuple[str, ...]]] = {
        "questions": ({}, ()),
        "inputs": ({"type": {"enum": list(INPUT_TYPES)}, "location": string, "description": string}, ("type", "location", "description")),
        "models": ({
            "kind": {"enum": list(MODEL_KINDS)}, "input": string, "description": string,
            "grain": string, "key": string, "fields": field_list, "produced_by": string,
            "view_expression": {"type": "string", "pattern": r"^(?:[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)(?:,\s*(?:[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*))*$"}, "procedure": {"type": "string", "pattern": PROCEDURE_RE.pattern},
        }, ("kind", "description", "grain", "key", "fields")),
        "transform": ({
            "operation": {"enum": list(OPERATION_KINDS)}, "inputs": string_list, "output": string,
            "predicate": predicate,
            "fields": string_list, "expressions": {"type": "array", "items": expression, "minItems": 1}, "join_type": {"enum": list(JOIN_TYPES)},
            "on": {"type": "array", "items": join_key, "minItems": 1}, "cardinality": {"enum": list(JOIN_CARDINALITIES)},
            "null_keys": {"enum": list(JOIN_NULL_BEHAVIORS)},
            "unmatched": {"enum": list(UNMATCHED_BEHAVIORS)}, "group_by": string_list,
            "measures": {"type": "array", "items": measure, "minItems": 1}, "null_handling": {"enum": list(NULL_HANDLING)},
            "keys": string_list, "order_by": {"type": "array", "items": order_term, "minItems": 1}, "winner": {"enum": list(DEDUP_WINNERS)},
            "alignment": {"enum": list(UNION_ALIGNMENTS)}, "missing_fields": {"enum": list(MISSING_FIELD_BEHAVIORS)},
            "procedure": {"type": "string", "pattern": PROCEDURE_RE.pattern},
        }, ("operation", "inputs", "output")),
        "outputs": ({"model": string, "questions": string_list, "projection": field_list, "order_by": {"type": "array", "items": order_term, "minItems": 1}, "delivery_refs": string_list}, ("model", "questions", "projection", "order_by", "delivery_refs")),
        "delivery": ({"kind": {"enum": list(DELIVERY_KINDS)}, "target": string, "description": string}, ("kind", "target")),
        "contracts": ({"name": {"type": "string", "pattern": r"^[a-z][a-z0-9-]*$"}, "attachment": string, "model": string, "phase": {"enum": ["pre_transform", "post_transform"]}, "guarantee": string, "rule": string, "fields": field_list}, ("name", "attachment", "model", "phase", "guarantee", "rule", "fields")),
        "decisions": ({"target": string, "status": {"enum": list(DECISION_STATUSES)}, "provenance": {"enum": list(DECISION_PROVENANCE)}, "ruling": string}, ("target", "status", "provenance", "ruling")),
        "open_questions": ({"blocking": {"enum": ["yes", "no"]}, "target": string}, ("blocking", "target")),
    }
    for section, (fields, required) in specs.items():
        properties = {"id": {"type": "string", "pattern": NAME_RE.pattern}, "prose": {"type": "string"}, **fields}
        if section == "transform":
            alternatives = []
            for operation, operation_fields in OPERATION_FIELDS.items():
                operation_properties = dict(properties)
                operation_properties["operation"] = {"const": operation}
                alternatives.append({
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", *sorted(operation_fields)],
                    "properties": {
                        key: value for key, value in operation_properties.items()
                        if key in {"id", "prose"} or key in operation_fields
                    },
                })
            SCHEMA["properties"][section] = {
                "type": "object", "propertyNames": {"pattern": NAME_RE.pattern},
                "additionalProperties": {"oneOf": alternatives},
            }
            continue
        SCHEMA["properties"][section] = {
            "type": "object", "propertyNames": {"pattern": NAME_RE.pattern},
            "additionalProperties": {"type": "object", "additionalProperties": False, "required": ["id", *required], "properties": properties},
        }


_install_section_schemas()


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("parse", "hash", "validate"):
        item = sub.add_parser(command)
        item.add_argument("spec")
    sub.add_parser("schema")
    new = sub.add_parser("new")
    new.add_argument("--name", required=True)
    new.add_argument("--workflow", required=True)
    new.add_argument("--status", default="draft")
    emit_parser = sub.add_parser("emit")
    emit_parser.add_argument("canonical")
    diff_parser = sub.add_parser("diff")
    diff_parser.add_argument("before")
    diff_parser.add_argument("after")
    patch = sub.add_parser("patch")
    patch.add_argument("spec")
    patch.add_argument("--base-hash", required=True)
    patch.add_argument("--set", dest="assignment", required=True)
    approve_parser = sub.add_parser("approve")
    approve_parser.add_argument("spec")
    approve_parser.add_argument("--base-hash", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            print(json.dumps(SCHEMA, indent=2, ensure_ascii=False))
        elif args.command == "new":
            print(new_document(args.name, args.workflow, args.status), end="")
        elif args.command == "parse":
            parsed = parse(_read(args.spec))
            print(json.dumps({"canonical": parsed.document.to_dict(), "source_map": parsed.source_map.to_dict(), "semantic_hash": semantic_hash(parsed)}, indent=2, ensure_ascii=False))
        elif args.command == "hash":
            print(semantic_hash(_read(args.spec)))
        elif args.command == "validate":
            parsed = parse(_read(args.spec))
            issues = validate_with_graph(parsed)
            print(json.dumps({"ok": not issues, "semantic_hash": semantic_hash(parsed), "issues": [issue.to_dict() for issue in issues]}, indent=2, ensure_ascii=False))
            return 0 if not issues else 1
        elif args.command == "emit":
            payload = json.loads(_read(args.canonical))
            print(emit(payload), end="")
        elif args.command == "diff":
            print(json.dumps(semantic_diff(_read(args.before), _read(args.after)), indent=2, ensure_ascii=False))
        elif args.command == "patch":
            if "=" not in args.assignment:
                raise ValueError("--set needs stable-path=value")
            path, replacement = args.assignment.split("=", 1)
            print(targeted_patch(parse(_read(args.spec)), base_hash=args.base_hash, path=path, value=replacement), end="")
        elif args.command == "approve":
            parsed = parse(_read(args.spec))
            print(approve(parsed, base_hash=args.base_hash), end="")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"dp-spec-v2: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
