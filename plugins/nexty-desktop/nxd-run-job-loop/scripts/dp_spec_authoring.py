#!/usr/bin/env python3
"""Natural-language DP-spec authoring and proposal boundary.

Version 3 deliberately has a smaller deterministic responsibility than the
retired v2 authoring shape:

* this module parses only frontmatter, the required top-level headings, and
  Markdown subheadings/spans;
* an AI or another external author supplies the typed proposal JSON;
* this module validates that proposal, its provenance, echo coverage, local
  terms, derived contract inventory, fixed local delivery, and decision locks.

There is no model call here.  The proposal JSON is an integration boundary,
not a second document that a user is expected to author or inspect.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SPEC_VERSION = 3
AUTHORING_VERSION = "nxd-dp-spec-authoring-v1"
SCHEMA_ID = "nxd-dp-spec-schema-v3"
PROPOSAL_SCHEMA_ID = "nxd-dp-spec-proposal-v3"
DIAGNOSTIC_SCHEMA_ID = "nxd-dp-spec-diagnostic-v3"
CANONICALIZATION = "nxd-dp-spec-canon-v3"
STATUS_VALUES = ("draft", "proposed", "approved")
PROVENANCE_VALUES = ("explicit", "inferred", "platform_fixed")
DECISION_STATUS_VALUES = ("proposed", "locked")
TRANSFORM_OPERATIONS = (
    "filter", "project", "derive", "join", "aggregate", "union",
    "deduplicate", "apply_procedure",
)
FIXED_DELIVERY = {
    "kind": "semantic_query",
    "profile": "desktop-local",
    "port": "duckdb",
    "provenance": "platform_fixed",
}
FIXED_DELIVERY_PROFILE = "desktop-local-duckdb-semantic-query"
SECTIONS = (
    "Intent", "Questions", "Scope", "Terms", "Inputs", "Models",
    "Transform", "Outputs", "Decisions", "Open Questions",
)
SECTION_KEYS = {title: title.lower().replace(" ", "_") for title in SECTIONS}
NONEMPTY_SECTIONS = frozenset({
    "intent", "questions", "scope", "inputs", "models", "transform", "outputs",
})
# All ten headings are part of the stable authoring surface. Terms, Decisions,
# and Open Questions may be present but empty; their headings remain required so
# a form and an AI can rely on stable navigation.
REQUIRED_BODY_SECTIONS = frozenset(SECTION_KEYS.values())
FRONTMATTER_REQUIRED = ("dp_spec_version", "name", "workflow", "status")
FRONTMATTER_OPTIONAL = (
    "approved_content_hash",
    "approved_proposal_hash",
    "prior_approved_proposal_hash",
)
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SECTION_RE = re.compile(r"^## ([A-Za-z][A-Za-z ]*)\s*$")
SUBHEADING_RE = re.compile(r"^### (.+?)\s*$")
H1_RE = re.compile(r"^\s{0,3}#[ \t]+(.+?)\s*$")
SETEXT_H1_RE = re.compile(r"^\s{0,3}=+[ \t]*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
COMMENT_RE = re.compile(r"^\s*<!--.*-->\s*$")
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ParseError(ValueError):
    """The source cannot be represented without dropping authored content."""


class UnsupportedVersionError(ParseError):
    """The source names a version this authoring boundary does not parse."""


class StalePatchError(ValueError):
    """A source edit was based on a different semantic revision."""


@dataclass(frozen=True)
class SourceSpan:
    line_start: int
    line_end: int
    offset_start: int
    offset_end: int

    def to_dict(self) -> dict[str, int]:
        return dataclasses.asdict(self)


@dataclass
class SourceMap:
    spans: dict[str, SourceSpan] = field(default_factory=dict)

    def add(self, path: str, span: SourceSpan) -> None:
        if path in self.spans:
            raise ParseError(f"source map collision at {path}")
        self.spans[path] = span

    def to_dict(self) -> dict[str, dict[str, int]]:
        return {key: self.spans[key].to_dict() for key in sorted(self.spans)}


@dataclass(frozen=True)
class Frontmatter:
    dp_spec_version: int
    name: str
    workflow: str
    status: str
    approved_content_hash: str | None = None
    approved_proposal_hash: str | None = None
    prior_approved_proposal_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class Block:
    """One optional `###` block and its free Markdown body."""

    heading: str | None
    text: str
    path_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"heading": self.heading, "text": self.text, "id": self.path_id}


@dataclass(frozen=True)
class Section:
    title: str
    key: str
    text: str
    blocks: tuple[Block, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "text": self.text,
            "blocks": [block.to_dict() for block in self.blocks],
        }


@dataclass(frozen=True)
class ParsedDocument:
    frontmatter: Frontmatter
    sections: dict[str, Section]
    source_map: SourceMap
    raw: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_ID,
            "authoring_version": AUTHORING_VERSION,
            "frontmatter": {
                "dp_spec_version": self.frontmatter.dp_spec_version,
                "name": self.frontmatter.name,
                "workflow": self.frontmatter.workflow,
            },
            "sections": {
                key: self.sections[key].to_dict()
                for key in SECTION_KEYS.values() if key in self.sections
            },
        }


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    owner: str = "user"
    control: str = "text"

    def to_dict(self) -> dict[str, str]:
        return dataclasses.asdict(self)


def _path(section: str, identity: str | None = None, field_name: str | None = None) -> str:
    base = f"v3:{section}" if identity is None else f"v3:{section}[{identity}]"
    return base if field_name is None else f"{base}.{field_name}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "section"


def _lines_with_offsets(text: str) -> list[tuple[int, str, int]]:
    result: list[tuple[int, str, int]] = []
    offset = 0
    for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
        result.append((line_number, line.rstrip("\r\n"), offset))
        offset += len(line)
    if not result:
        result.append((1, "", 0))
    return result


def _parse_frontmatter(lines: list[tuple[int, str, int]], source_map: SourceMap) -> tuple[Frontmatter, int]:
    if not lines or lines[0][1] != "---":
        raise ParseError("v3 document must start with minimal YAML frontmatter")
    values: dict[str, str] = {}
    close_index: int | None = None
    for index, (line_number, line, offset) in enumerate(lines[1:], start=1):
        if line == "---":
            close_index = index
            break
        match = re.fullmatch(r"([a-z_]+):\s*(.+)", line)
        if not match:
            raise ParseError(f"frontmatter line {line_number} is not a minimal key/value")
        key, value = match.groups()
        if key in values:
            raise ParseError(f"duplicate frontmatter key {key!r}")
        if key not in FRONTMATTER_REQUIRED and key not in FRONTMATTER_OPTIONAL:
            raise ParseError(f"v3 frontmatter does not allow {key!r}")
        values[key] = value.strip().strip("`")
        source_map.add(
            _path("frontmatter", None, key),
            SourceSpan(line_number, line_number, offset + match.start(2), offset + match.end(2)),
        )
    if close_index is None:
        raise ParseError("unterminated v3 frontmatter")
    missing = [key for key in FRONTMATTER_REQUIRED if key not in values]
    if missing:
        raise ParseError("v3 frontmatter missing " + ", ".join(missing))
    try:
        version = int(values["dp_spec_version"])
    except ValueError as exc:
        raise ParseError("dp_spec_version must be an integer") from exc
    if version != SPEC_VERSION:
        raise UnsupportedVersionError(
            f"unsupported_version: dp_spec_version {version!r} is not supported; expected {SPEC_VERSION}"
        )
    return Frontmatter(
        version,
        values["name"],
        values["workflow"],
        values["status"],
        values.get("approved_content_hash") or None,
        values.get("approved_proposal_hash") or None,
        values.get("prior_approved_proposal_hash") or None,
    ), close_index + 1


def _normalized_text(text: str) -> str:
    """Normalize prose while preserving fenced-code bytes line-for-line.

    Markdown outside a fence is semantic prose, so insignificant horizontal
    whitespace and comments are normalized there. A fenced block is authored
    content, however: indentation can change executable behavior and must
    therefore remain part of the semantic hash.
    """
    lines: list[str] = []
    fence_marker: tuple[str, int, bool] | None = None
    for line in text.splitlines():
        marker = _fence_marker(line)
        if marker is not None:
            lines.append(line)
            if fence_marker is None:
                fence_marker = marker
            elif not marker[2] and marker[0] == fence_marker[0] and marker[1] >= fence_marker[1]:
                fence_marker = None
            continue
        if fence_marker is not None:
            lines.append(line)
            continue
        if COMMENT_RE.match(line):
            continue
        lines.append(re.sub(r"[ \t]+", " ", line).rstrip())
    return "\n".join(lines).strip()


def _fence_marker(line: str) -> tuple[str, int, bool] | None:
    match = FENCE_RE.match(line)
    if match is None:
        return None
    marker = match.group(1)
    return marker[0], len(marker), bool(line[match.end():].strip())


def _span_for_lines(entries: list[tuple[int, str, int]]) -> SourceSpan:
    if not entries:
        return SourceSpan(1, 1, 0, 0)
    first = entries[0]
    last = entries[-1]
    return SourceSpan(first[0], last[0], first[2], last[2] + len(last[1]))


def _parse_section(key: str, title: str, entries: list[tuple[int, str, int]], source_map: SourceMap) -> Section:
    nonblank = [entry for entry in entries if entry[1].strip()]
    blocks: list[Block] = []
    current_heading: str | None = None
    current_id: str | None = None
    current_lines: list[tuple[int, str, int]] = []
    seen_ids: set[str] = set()
    fence_marker: tuple[str, int, bool] | None = None

    def finish() -> None:
        nonlocal current_heading, current_id, current_lines
        if current_heading is None:
            return
        text = _normalized_text("\n".join(line for _, line, _ in current_lines))
        blocks.append(Block(current_heading, text, current_id))
        if current_id is not None and current_lines:
            source_map.add(
                _path(key, current_id, "text"),
                _span_for_lines(current_lines),
            )
        current_heading, current_id, current_lines = None, None, []

    for line_number, line, offset in entries:
        marker = _fence_marker(line)
        if marker is not None:
            if current_heading is not None:
                current_lines.append((line_number, line, offset))
            if fence_marker is None:
                fence_marker = marker
            elif not marker[2] and marker[0] == fence_marker[0] and marker[1] >= fence_marker[1]:
                fence_marker = None
            continue
        if fence_marker is not None:
            if current_heading is not None:
                current_lines.append((line_number, line, offset))
            continue
        match = SUBHEADING_RE.match(line)
        if match:
            finish()
            heading = match.group(1).strip()
            identity = _slug(heading)
            if identity in seen_ids:
                raise ParseError(f"duplicate subsection {heading!r} in {title!r}")
            seen_ids.add(identity)
            current_heading, current_id = heading, identity
            source_map.add(_path(key, identity), SourceSpan(line_number, line_number, offset, offset + len(line)))
            continue
        if current_heading is not None:
            current_lines.append((line_number, line, offset))

    finish()
    if fence_marker is not None:
        raise ParseError(f"unclosed fenced code block in {title!r}")
    # Keep prose before the first `###` and prose between subsections in the
    # semantic section text. The blocks are navigation/form metadata; they are
    # not allowed to make user-authored prose disappear from the hash.
    fence_marker = None
    prose_lines: list[str] = []
    for _, line, _ in entries:
        marker = _fence_marker(line)
        if marker is not None:
            prose_lines.append(line)
            if fence_marker is None:
                fence_marker = marker
            elif not marker[2] and marker[0] == fence_marker[0] and marker[1] >= fence_marker[1]:
                fence_marker = None
        elif fence_marker is not None or not SUBHEADING_RE.match(line):
            prose_lines.append(line)
    text = _normalized_text("\n".join(prose_lines))
    if nonblank:
        # A free-prose section can contain subsection headings; its section span
        # covers all authored content for coarse form controls.
        source_map.add(_path(key, None, "text"), _span_for_lines(nonblank))
    return Section(title, key, text, tuple(blocks))


def parse(text: str) -> ParsedDocument:
    """Parse v3 structure and source spans without interpreting business meaning."""
    if "\r" in text:
        raise ParseError("CRLF/CR input is not supported; use LF")
    lines = _lines_with_offsets(text)
    source_map = SourceMap()
    frontmatter, body_start = _parse_frontmatter(lines, source_map)
    sections: dict[str, list[tuple[int, str, int]]] = {}
    titles: dict[str, tuple[str, int, int]] = {}
    current: str | None = None
    fence_marker: tuple[str, int, bool] | None = None
    body_lines = lines[body_start:]
    for index, (line_number, line, offset) in enumerate(body_lines):
        marker = _fence_marker(line)
        if marker is not None:
            if current is None:
                if fence_marker is None and line.strip():
                    raise ParseError(f"content before first v3 section at line {line_number}")
            else:
                sections[current].append((line_number, line, offset))
            if fence_marker is None:
                fence_marker = marker
            elif not marker[2] and marker[0] == fence_marker[0] and marker[1] >= fence_marker[1]:
                fence_marker = None
            continue
        if fence_marker is not None:
            if current is None:
                if line.strip():
                    raise ParseError(f"content before first v3 section at line {line_number}")
            else:
                sections[current].append((line_number, line, offset))
            continue
        match = SECTION_RE.match(line)
        if match:
            title = match.group(1)
            key = SECTION_KEYS.get(title)
            if key is None:
                raise ParseError(f"unknown top-level section {title!r}; v3 headings are fixed")
            if key in sections:
                raise ParseError(f"duplicate section heading {title!r}")
            sections[key] = []
            titles[key] = (title, line_number, offset)
            current = key
            continue
        if H1_RE.match(line) or (
            line.strip()
            and index + 1 < len(body_lines)
            and SETEXT_H1_RE.match(body_lines[index + 1][1])
        ):
            raise ParseError(f"unsupported H1 heading at line {line_number}; use the fixed v3 section headings")
        if current is None:
            if line.strip():
                raise ParseError(f"content before first v3 section at line {line_number}")
            continue
        sections[current].append((line_number, line, offset))

    if fence_marker is not None:
        raise ParseError("unclosed fenced code block")

    parsed_sections: dict[str, Section] = {}
    for key in sections:
        section_title, line_number, offset = titles[key]
        source_map.add(_path(key), SourceSpan(line_number, line_number, offset, offset + len(f"## {section_title}")))
        parsed_sections[key] = _parse_section(key, section_title, sections[key], source_map)
    return ParsedDocument(frontmatter, parsed_sections, source_map, text)


def content_object(value: ParsedDocument | str) -> dict[str, Any]:
    parsed = parse(value) if isinstance(value, str) else value
    return {
        "schema": SCHEMA_ID,
        "authoring_version": AUTHORING_VERSION,
        "frontmatter": {
            "dp_spec_version": parsed.frontmatter.dp_spec_version,
            "name": parsed.frontmatter.name,
            "workflow": parsed.frontmatter.workflow,
        },
        "sections": {
            key: {
                "text": parsed.sections[key].text,
                "blocks": [block.to_dict() for block in parsed.sections[key].blocks],
            }
            for key in SECTION_KEYS.values() if key in parsed.sections
        },
    }


def canonical_object(value: ParsedDocument | str) -> dict[str, Any]:
    return content_object(value)


def canonical_bytes(value: ParsedDocument | str) -> bytes:
    return json.dumps(canonical_object(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def semantic_hash(value: ParsedDocument | str) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _proposal_bytes(proposal: dict[str, Any]) -> bytes:
    payload = {key: proposal[key] for key in proposal if key not in {"hashes", "proposal_hash"}}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def proposal_hash(proposal: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_proposal_bytes(proposal)).hexdigest()


def _issue(issues: list[ValidationIssue], code: str, path: str, message: str, *, owner: str = "user", control: str = "text") -> None:
    issues.append(ValidationIssue(code, path, message, owner, control))


def validate(value: ParsedDocument | str) -> list[ValidationIssue]:
    """Validate only deterministic source structure and lifecycle metadata."""
    parsed = parse(value) if isinstance(value, str) else value
    issues: list[ValidationIssue] = []
    fm = parsed.frontmatter
    if not NAME_RE.match(fm.name):
        _issue(issues, "v3.frontmatter.name", _path("frontmatter", None, "name"), "name must be lowercase snake_case", control="text")
    if not fm.workflow.strip():
        _issue(issues, "v3.frontmatter.workflow", _path("frontmatter", None, "workflow"), "workflow must not be empty")
    if fm.status not in STATUS_VALUES:
        _issue(issues, "v3.frontmatter.status", _path("frontmatter", None, "status"), "status is not supported", control="enum")
    if fm.status == "approved":
        if not fm.approved_content_hash:
            _issue(issues, "v3.approval.binding_missing", _path("frontmatter", None, "approved_content_hash"), "approved documents must bind approved_content_hash")
        elif fm.approved_content_hash != semantic_hash(parsed):
            _issue(issues, "v3.approval.binding_mismatch", _path("frontmatter", None, "approved_content_hash"), "approved_content_hash does not match source semantics")
        if not fm.approved_proposal_hash or not HASH_RE.match(fm.approved_proposal_hash):
            _issue(issues, "v3.approval.proposal_binding_missing", _path("frontmatter", None, "approved_proposal_hash"), "approved documents must bind the typed proposal snapshot")
    else:
        if fm.approved_content_hash:
            _issue(issues, "v3.approval.binding_lifecycle", _path("frontmatter", None, "approved_content_hash"), "only approved documents may carry approved_content_hash")
        if fm.approved_proposal_hash:
            _issue(issues, "v3.approval.proposal_binding_lifecycle", _path("frontmatter", None, "approved_proposal_hash"), "only approved documents may carry approved_proposal_hash")
        if fm.prior_approved_proposal_hash and fm.status != "proposed":
            _issue(issues, "v3.approval.prior_binding_lifecycle", _path("frontmatter", None, "prior_approved_proposal_hash"), "prior_approved_proposal_hash may only be carried by a proposed re-approval")
        elif fm.prior_approved_proposal_hash and not HASH_RE.match(fm.prior_approved_proposal_hash):
            _issue(issues, "v3.approval.prior_binding_invalid", _path("frontmatter", None, "prior_approved_proposal_hash"), "prior_approved_proposal_hash must be sha256:<64 lowercase hex>")
    if fm.status == "approved" and fm.prior_approved_proposal_hash:
        _issue(issues, "v3.approval.prior_binding_lifecycle", _path("frontmatter", None, "prior_approved_proposal_hash"), "approved documents may not carry prior_approved_proposal_hash")
    missing = REQUIRED_BODY_SECTIONS - set(parsed.sections)
    for key in sorted(missing):
        _issue(issues, "v3.section.missing", _path(key), f"missing required v3 section {key!r}")
    present = list(parsed.sections)
    expected = [key for key in SECTION_KEYS.values() if key in present]
    if present != expected:
        _issue(issues, "v3.section.order", "v3:sections", "top-level sections must follow the canonical order")
    for key in sorted(NONEMPTY_SECTIONS & set(parsed.sections)):
        if not parsed.sections[key].text:
            _issue(issues, "v3.section.empty", _path(key), f"{key} must contain prose")
    return sorted(issues, key=lambda item: (item.path, item.code, item.message))


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


PROPOSAL_KEYS = frozenset({
    "schema", "authoring_version", "source_hash", "proposal", "provenance",
    "source_spans", "anchors", "echo",
})


def _shape(
    issues: list[ValidationIssue],
    value: Any,
    path: str,
    *,
    required: set[str],
    allowed: set[str],
    label: str,
) -> dict[str, Any] | None:
    """Validate one closed object and return it only when it is an object."""
    if not isinstance(value, dict):
        _issue(issues, "v3.proposal.item_shape", path, f"{label} must be an object", owner="agent", control="mapping")
        return None
    for key in sorted(set(value) - allowed):
        _issue(issues, "v3.proposal.unknown_key", f"{path}.{key}", f"{label} contains unknown key {key!r}", owner="agent", control="mapping")
    for key in sorted(required - set(value)):
        _issue(issues, "v3.proposal.required_field", f"{path}.{key}", f"{label} requires {key!r}", owner="agent")
    return value


def _list_field(
    issues: list[ValidationIssue],
    value: Any,
    path: str,
    label: str,
) -> list[Any]:
    if not isinstance(value, list):
        _issue(issues, "v3.proposal.list", path, f"{label} must be a list", owner="agent", control="list")
        return []
    return value


def _validate_string_field(issues: list[ValidationIssue], value: Any, path: str, label: str) -> None:
    if not _is_nonempty_string(value):
        _issue(issues, "v3.proposal.required_field", path, f"{label} must be a non-empty string", owner="agent", control="text")


def _validate_string_list(issues: list[ValidationIssue], value: Any, path: str, label: str) -> None:
    if not isinstance(value, list) or any(not _is_nonempty_string(item) for item in value):
        _issue(issues, "v3.proposal.string_list", path, f"{label} must be a list of non-empty strings", owner="agent", control="list")


def _validate_field_list(issues: list[ValidationIssue], value: Any, path: str, label: str) -> None:
    if (
        not isinstance(value, list)
        or not value
        or any(not _is_nonempty_string(item) or not FIELD_NAME_RE.match(item) for item in value)
    ):
        _issue(issues, "v3.proposal.field_list", path, f"{label} must be a non-empty list of lowercase field names", owner="agent", control="list")


def _validate_questions(issues: list[ValidationIssue], value: Any) -> None:
    seen: set[str] = set()
    for index, question in enumerate(_list_field(issues, value, "v3:proposal.proposal.questions", "questions")):
        path = f"v3:questions[{index}]"
        item = _shape(issues, question, path, required={"id", "question"}, allowed={"id", "question"}, label="question")
        if item is None:
            continue
        _validate_string_field(issues, item.get("id"), f"{path}.id", "question id")
        _validate_string_field(issues, item.get("question"), f"{path}.question", "question")
        question_id = item.get("id")
        if isinstance(question_id, str):
            if question_id in seen:
                _issue(issues, "v3.question.duplicate_id", f"{path}.id", "question ids must be unique", owner="agent")
            seen.add(question_id)


def _validate_term(issues: list[ValidationIssue], term: Any, index: int, term_ids: set[str]) -> None:
    path = f"v3:terms[{index}]"
    term = _shape(
        issues,
        term,
        path,
        required={"id", "name", "definition", "priority"},
        allowed={"id", "name", "definition", "synonyms", "related_terms", "examples", "term_values", "tags", "priority"},
        label="term",
    )
    if term is None:
        return
    required = ("id", "name", "definition")
    for key in required:
        if not _is_nonempty_string(term.get(key)):
            _issue(issues, "v3.term.required", f"{path}.{key}", f"term requires non-empty {key!r}")
    term_id = term.get("id")
    if isinstance(term_id, str):
        if term_id in term_ids:
            _issue(issues, "v3.term.duplicate_id", f"{path}.id", "term ids must be unique")
        term_ids.add(term_id)
    if "priority" in term and (
        not isinstance(term["priority"], str)
        or term["priority"] not in {"P1", "P2", "P3", "P4", "P5"}
    ):
        _issue(issues, "v3.term.priority", f"{path}.priority", "priority must be P1 through P5", control="enum")
    for field_name in ("synonyms", "related_terms", "examples", "term_values"):
        if field_name in term:
            _validate_string_list(issues, term[field_name], f"{path}.{field_name}", field_name)
    if "tags" in term:
        tags = term["tags"]
        if not isinstance(tags, dict) or any(
            not _is_nonempty_string(key) or not _is_nonempty_string(value)
            for key, value in tags.items()
        ):
            _issue(issues, "v3.term.tags", f"{path}.tags", "tags must be an NXD glossary string-to-string map", control="mapping")


def _validate_inputs(issues: list[ValidationIssue], value: Any) -> None:
    seen_inputs: set[str] = set()
    for index, input_item in enumerate(_list_field(issues, value, "v3:proposal.proposal.inputs", "inputs")):
        path = f"v3:inputs[{index}]"
        item = _shape(issues, input_item, path, required={"id", "expectations"}, allowed={"id", "expectations"}, label="input")
        if item is None:
            continue
        _validate_string_field(issues, item.get("id"), f"{path}.id", "input id")
        input_id = item.get("id")
        if isinstance(input_id, str):
            if input_id in seen_inputs:
                _issue(issues, "v3.input.duplicate_id", f"{path}.id", "input ids must be unique", owner="agent")
            seen_inputs.add(input_id)
        expectations = _list_field(issues, item.get("expectations"), f"{path}.expectations", "expectations")
        seen_expectations: set[str] = set()
        for expectation_index, expectation in enumerate(expectations):
            expectation_path = f"{path}.expectations[{expectation_index}]"
            row = _shape(
                issues,
                expectation,
                expectation_path,
                required={"id", "model", "guarantee", "rule", "fields"},
                allowed={"id", "model", "guarantee", "rule", "fields"},
                label="input expectation",
            )
            if row is None:
                continue
            for field_name in ("id", "model", "guarantee", "rule"):
                _validate_string_field(issues, row.get(field_name), f"{expectation_path}.{field_name}", f"expectation {field_name}")
            _validate_field_list(issues, row.get("fields"), f"{expectation_path}.fields", "expectation fields")
            expectation_id = row.get("id")
            if isinstance(expectation_id, str):
                if expectation_id in seen_expectations:
                    _issue(issues, "v3.contract.source_duplicate_id", f"{expectation_path}.id", "expectation and promise source ids must be unique", owner="agent")
                seen_expectations.add(expectation_id)


def _validate_models(issues: list[ValidationIssue], value: Any) -> None:
    seen: set[str] = set()
    for index, model in enumerate(_list_field(issues, value, "v3:proposal.proposal.models", "models")):
        path = f"v3:models[{index}]"
        item = _shape(issues, model, path, required={"id", "fields"}, allowed={"id", "fields"}, label="model")
        if item is None:
            continue
        _validate_string_field(issues, item.get("id"), f"{path}.id", "model id")
        _validate_field_list(issues, item.get("fields"), f"{path}.fields", "model fields")
        model_id = item.get("id")
        if isinstance(model_id, str):
            if model_id in seen:
                _issue(issues, "v3.model.duplicate_id", f"{path}.id", "model ids must be unique", owner="agent")
            seen.add(model_id)


def _validate_transform(issues: list[ValidationIssue], value: Any) -> None:
    seen: set[str] = set()
    for index, transform in enumerate(_list_field(issues, value, "v3:proposal.proposal.transform", "transform")):
        path = f"v3:transform[{index}]"
        item = _shape(issues, transform, path, required={"id", "operation"}, allowed={"id", "operation"}, label="transform step")
        if item is None:
            continue
        _validate_string_field(issues, item.get("id"), f"{path}.id", "transform id")
        _validate_string_field(issues, item.get("operation"), f"{path}.operation", "transform operation")
        if item.get("operation") not in TRANSFORM_OPERATIONS:
            _issue(issues, "v3.transform.operation", f"{path}.operation", "transform operation is not supported", owner="agent", control="enum")
        transform_id = item.get("id")
        if isinstance(transform_id, str):
            if transform_id in seen:
                _issue(issues, "v3.transform.duplicate_id", f"{path}.id", "transform ids must be unique", owner="agent")
            seen.add(transform_id)


def _validate_outputs(issues: list[ValidationIssue], value: Any) -> None:
    seen_outputs: set[str] = set()
    for index, output_item in enumerate(_list_field(issues, value, "v3:proposal.proposal.outputs", "outputs")):
        path = f"v3:outputs[{index}]"
        item = _shape(issues, output_item, path, required={"id", "promises"}, allowed={"id", "promises"}, label="output")
        if item is None:
            continue
        _validate_string_field(issues, item.get("id"), f"{path}.id", "output id")
        output_id = item.get("id")
        if isinstance(output_id, str):
            if output_id in seen_outputs:
                _issue(issues, "v3.output.duplicate_id", f"{path}.id", "output ids must be unique", owner="agent")
            seen_outputs.add(output_id)
        promises = _list_field(issues, item.get("promises"), f"{path}.promises", "promises")
        seen_promises: set[str] = set()
        for promise_index, promise in enumerate(promises):
            promise_path = f"{path}.promises[{promise_index}]"
            row = _shape(
                issues,
                promise,
                promise_path,
                required={"id", "model", "guarantee", "rule", "fields"},
                allowed={"id", "model", "guarantee", "rule", "fields"},
                label="output promise",
            )
            if row is None:
                continue
            for field_name in ("id", "model", "guarantee", "rule"):
                _validate_string_field(issues, row.get(field_name), f"{promise_path}.{field_name}", f"promise {field_name}")
            _validate_field_list(issues, row.get("fields"), f"{promise_path}.fields", "promise fields")
            promise_id = row.get("id")
            if isinstance(promise_id, str):
                if promise_id in seen_promises:
                    _issue(issues, "v3.contract.source_duplicate_id", f"{promise_path}.id", "expectation and promise source ids must be unique", owner="agent")
                seen_promises.add(promise_id)


def _validate_decisions(issues: list[ValidationIssue], value: Any, *, require_locked: bool = False) -> None:
    seen: set[str] = set()
    for index, decision in enumerate(_list_field(issues, value, "v3:proposal.proposal.decisions", "decisions")):
        path = f"v3:decisions[{index}]"
        item = _shape(
            issues,
            decision,
            path,
            required={"id", "target", "ruling", "status"},
            allowed={"id", "target", "ruling", "status"},
            label="decision",
        )
        if item is None:
            continue
        for field_name in ("id", "target", "ruling", "status"):
            _validate_string_field(issues, item.get(field_name), f"{path}.{field_name}", f"decision {field_name}")
        decision_id = item.get("id")
        if isinstance(decision_id, str):
            if decision_id in seen:
                _issue(issues, "v3.decision.duplicate_id", f"{path}.id", "decision ids must be unique", owner="agent")
            seen.add(decision_id)
        if not isinstance(item.get("status"), str) or item.get("status") not in DECISION_STATUS_VALUES:
            _issue(issues, "v3.decision.status", f"{path}.status", "decision status must be proposed or locked", control="enum")
        elif require_locked and item["status"] != "locked":
            _issue(issues, "v3.decision.unsettled", f"{path}.status", "approved proposals require every decision to be locked", owner="agent", control="enum")


def _validate_open_questions(issues: list[ValidationIssue], value: Any) -> None:
    seen: set[str] = set()
    for index, question in enumerate(_list_field(issues, value, "v3:proposal.proposal.open_questions", "open_questions")):
        path = f"v3:open_questions[{index}]"
        item = _shape(
            issues,
            question,
            path,
            required={"id", "question", "blocking"},
            allowed={"id", "question", "blocking"},
            label="open question",
        )
        if item is None:
            continue
        _validate_string_field(issues, item.get("id"), f"{path}.id", "open question id")
        _validate_string_field(issues, item.get("question"), f"{path}.question", "open question")
        if not isinstance(item.get("blocking"), bool):
            _issue(issues, "v3.open_question.blocking", f"{path}.blocking", "blocking must be boolean", control="confirm")
        question_id = item.get("id")
        if isinstance(question_id, str):
            if question_id in seen:
                _issue(issues, "v3.open_question.duplicate_id", f"{path}.id", "open question ids must be unique", owner="agent")
            seen.add(question_id)


def _canonical_contract_text(value: str) -> str:
    return _normalized_text(value)


def _validate_contract_inventory(issues: list[ValidationIssue], payload: dict[str, Any]) -> None:
    expected: dict[str, dict[str, Any]] = {}
    source_ids: set[str] = set()
    for item in _as_list(payload.get("inputs")):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        for expectation in _as_list(item.get("expectations")):
            if not isinstance(expectation, dict) or not isinstance(expectation.get("id"), str):
                continue
            source_id = expectation["id"]
            if source_id in source_ids:
                _issue(issues, "v3.contract.source_duplicate_id", f"v3:inputs[{item['id']}].expectations[{source_id}].id", "expectation and promise source ids must be unique", owner="agent")
                continue
            source_ids.add(source_id)
            expected[source_id] = {
                "id": source_id,
                "attachment": f"input:{item['id']}",
                "phase": "pre_transform",
                "model": expectation.get("model"),
                "guarantee": expectation.get("guarantee"),
                "rule": expectation.get("rule"),
                "fields": expectation.get("fields"),
            }
    for item in _as_list(payload.get("outputs")):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        for promise in _as_list(item.get("promises")):
            if not isinstance(promise, dict) or not isinstance(promise.get("id"), str):
                continue
            source_id = promise["id"]
            if source_id in source_ids:
                _issue(issues, "v3.contract.source_duplicate_id", f"v3:outputs[{item['id']}].promises[{source_id}].id", "expectation and promise source ids must be unique", owner="agent")
                continue
            source_ids.add(source_id)
            expected[source_id] = {
                "id": source_id,
                "attachment": f"output:{item['id']}",
                "phase": "post_transform",
                "model": promise.get("model"),
                "guarantee": promise.get("guarantee"),
                "rule": promise.get("rule"),
                "fields": promise.get("fields"),
            }

    models: dict[str, set[str]] = {}
    for item in _as_list(payload.get("models")):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        fields = item.get("fields")
        if isinstance(fields, list) and all(isinstance(field, str) for field in fields):
            models[item["id"]] = set(fields)
    for source_id, source in expected.items():
        model_id = source.get("model")
        if model_id not in models:
            _issue(issues, "v3.contract.model_ref", f"v3:contracts[{source_id}].model", "contract model must reference a declared model", owner="agent")
        elif isinstance(source.get("fields"), list) and all(isinstance(field, str) for field in source["fields"]):
            unknown = sorted(set(source["fields"]) - models[model_id])
            if unknown:
                _issue(issues, "v3.contract.field_ref", f"v3:contracts[{source_id}].fields", f"contract fields are not declared by model {model_id!r}: {unknown}", owner="agent")

    actual: dict[str, dict[str, Any]] = {}
    contracts = _list_field(issues, payload.get("contracts"), "v3:proposal.proposal.contracts", "contracts")
    for index, contract in enumerate(contracts):
        path = f"v3:contracts[{index}]"
        item = _shape(
            issues,
            contract,
            path,
            required={"id", "attachment", "model", "phase", "guarantee", "rule", "fields"},
            allowed={"id", "attachment", "model", "phase", "guarantee", "rule", "fields"},
            label="compiled contract",
        )
        if item is None:
            continue
        for field_name in ("id", "attachment", "model", "phase", "guarantee", "rule"):
            _validate_string_field(issues, item.get(field_name), f"{path}.{field_name}", f"compiled contract {field_name}")
        _validate_field_list(issues, item.get("fields"), f"{path}.fields", "compiled contract fields")
        contract_id = item.get("id")
        if isinstance(contract_id, str):
            if contract_id in actual:
                _issue(issues, "v3.contract.duplicate_id", f"{path}.id", "compiled contract ids must be unique", owner="agent")
            else:
                actual[contract_id] = item

    if set(expected) != set(actual):
        _issue(issues, "v3.contract.inventory_mismatch", "v3:contracts", "contracts must be compiled one-for-one from Input expectations and Output promises", owner="agent")
    for contract_id in sorted(set(expected) & set(actual)):
        expected_item = expected[contract_id]
        actual_item = actual[contract_id]
        for field_name in ("attachment", "model", "phase", "guarantee", "rule", "fields"):
            expected_value = expected_item[field_name]
            actual_value = actual_item[field_name]
            if field_name in {"guarantee", "rule"} and isinstance(expected_value, str) and isinstance(actual_value, str):
                matches = _canonical_contract_text(expected_value) == _canonical_contract_text(actual_value)
            elif field_name == "fields" and isinstance(expected_value, list) and isinstance(actual_value, list) and all(isinstance(field, str) for field in expected_value + actual_value):
                matches = sorted(expected_value) == sorted(actual_value)
            else:
                matches = expected_value == actual_value
            if not matches:
                _issue(issues, "v3.contract.content_mismatch", f"v3:contracts[{contract_id}].{field_name}", f"compiled contract {field_name} does not exactly match its source expectation or promise", owner="agent")


def locked_decision_inventory(proposal: Any) -> tuple[dict[str, Any], ...]:
    """Return the settled decision inventory bound into a v3 closure lock."""
    if not isinstance(proposal, dict):
        return ()
    payload = proposal.get("proposal")
    if not isinstance(payload, dict) or not isinstance(payload.get("decisions"), list):
        return ()
    decisions = [
        {key: item[key] for key in ("id", "target", "ruling", "status")}
        for item in payload["decisions"]
        if isinstance(item, dict)
        and all(key in item for key in ("id", "target", "ruling", "status"))
        and isinstance(item.get("id"), str)
        and item.get("status") == "locked"
    ]
    return tuple(sorted(decisions, key=lambda item: str(item["id"])))


def lock_decisions_for_approval(proposal: Any) -> dict[str, Any]:
    """Project a validated candidate into the post-consent locked snapshot.

    This changes only the typed Decision lifecycle field.  The supervisor
    invokes it after recording subject-bound user consent; it is not an
    authorization operation and must never be used to alter the retained
    pre-consent proposal.
    """
    if not isinstance(proposal, dict):
        raise ValueError("the typed proposal must be a JSON object")
    projected = copy.deepcopy(proposal)
    payload = projected.get("proposal")
    if not isinstance(payload, dict) or not isinstance(payload.get("decisions"), list):
        raise ValueError("the typed proposal must contain a decisions list")
    for index, decision in enumerate(payload["decisions"]):
        if not isinstance(decision, dict):
            raise ValueError(f"decision {index} must be an object")
        status = decision.get("status")
        if status == "proposed":
            decision["status"] = "locked"
        elif status != "locked":
            raise ValueError(f"decision {index} has an unsupported status")
    return projected


def canonical_terms(payload: Any) -> list[dict[str, Any]]:
    """Return the stable Terms inventory used by v3 lock hashes."""
    if not isinstance(payload, dict):
        return []
    terms = [item for item in _as_list(payload.get("terms")) if isinstance(item, dict)]
    return sorted(terms, key=lambda item: str(item.get("id", "")))


def canonical_contract_inventory(payload: Any) -> list[dict[str, Any]]:
    """Return only the closed, compiled contract fields in stable order."""
    if not isinstance(payload, dict):
        return []
    contracts = [
        {
            **{key: item.get(key) for key in ("id", "attachment", "model", "phase", "guarantee", "rule")},
            "fields": (
                sorted(item.get("fields", []))
                if isinstance(item.get("fields"), list) and all(isinstance(field, str) for field in item.get("fields", []))
                else item.get("fields")
            ),
        }
        for item in _as_list(payload.get("contracts"))
        if isinstance(item, dict)
    ]
    return sorted(contracts, key=lambda item: str(item.get("id", "")))


def _validate_locked_decisions(
    issues: list[ValidationIssue],
    proposal: dict[str, Any],
    locked_proposal: dict[str, Any] | None,
    *,
    expected_proposal_hash: str | None = None,
) -> None:
    if locked_proposal is None:
        return
    if not isinstance(locked_proposal, dict):
        _issue(issues, "v3.decision.locked_evidence_invalid", "v3:locked_proposal", "prior approved proposal evidence must be a typed proposal object", owner="agent", control="mapping")
        return
    if set(locked_proposal) != PROPOSAL_KEYS:
        _issue(issues, "v3.decision.locked_evidence_invalid", "v3:locked_proposal", "prior approved proposal evidence must have the exact typed proposal envelope", owner="agent", control="mapping")
        return
    if locked_proposal.get("schema") != PROPOSAL_SCHEMA_ID or locked_proposal.get("authoring_version") != AUTHORING_VERSION:
        _issue(issues, "v3.decision.locked_evidence_invalid", "v3:locked_proposal", "prior approved proposal evidence has the wrong schema or authoring version", owner="agent")
        return
    if not isinstance(locked_proposal.get("source_hash"), str) or not HASH_RE.match(locked_proposal["source_hash"]):
        _issue(issues, "v3.decision.locked_evidence_invalid", "v3:locked_proposal.source_hash", "prior approved proposal evidence must carry a semantic source hash", owner="agent")
        return
    if not isinstance(locked_proposal.get("provenance"), dict) or not isinstance(locked_proposal.get("source_spans"), dict) or not isinstance(locked_proposal.get("anchors"), dict) or not isinstance(locked_proposal.get("echo"), dict):
        _issue(issues, "v3.decision.locked_evidence_invalid", "v3:locked_proposal", "prior approved proposal evidence has malformed envelope values", owner="agent", control="mapping")
        return
    if not isinstance(locked_proposal.get("proposal"), dict):
        _issue(issues, "v3.decision.locked_evidence_invalid", "v3:locked_proposal.proposal", "prior approved proposal evidence must carry a typed payload", owner="agent", control="mapping")
        return
    if expected_proposal_hash is not None and proposal_hash(locked_proposal) != expected_proposal_hash:
        _issue(
            issues,
            "v3.decision.locked_evidence_invalid",
            "v3:locked_proposal",
            "prior approved proposal evidence does not match the document's prior approval hash",
            owner="agent",
            control="mapping",
        )
        return
    old_items = locked_proposal["proposal"].get("decisions")
    new_items = proposal.get("proposal", {}).get("decisions")
    if not isinstance(old_items, list) or not isinstance(new_items, list):
        _issue(issues, "v3.decision.locked_evidence_invalid", "v3:locked_proposal.proposal.decisions", "prior approved proposal evidence must contain a decisions list", owner="agent", control="list")
        return
    old: dict[str, dict[str, Any]] = {}
    for item in old_items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            _issue(issues, "v3.decision.locked_evidence_invalid", "v3:locked_proposal.proposal.decisions", "prior approved decisions must have string ids", owner="agent")
            continue
        if item.get("status") != "locked":
            _issue(issues, "v3.decision.locked_evidence_invalid", f"v3:locked_proposal.proposal.decisions[{item['id']}].status", "prior approved proposal evidence must contain only locked decisions", owner="agent")
        if item["id"] in old:
            _issue(issues, "v3.decision.locked_evidence_invalid", f"v3:locked_proposal.proposal.decisions[{item['id']}].id", "prior approved locked decision ids must be unique", owner="agent")
        old[item["id"]] = item
    new = {item.get("id"): item for item in new_items if isinstance(item, dict) and isinstance(item.get("id"), str)}
    for decision_id in sorted(set(old) | set(new)):
        if new.get(decision_id) != old.get(decision_id):
            _issue(issues, "v3.decision.locked_conflict", f"v3:decisions[{decision_id}]", "a locked decision cannot be overwritten; propose an explicit change")


def validate_proposal(
    parsed: ParsedDocument,
    proposal: dict[str, Any],
    *,
    locked_proposal: dict[str, Any] | None = None,
    require_locked_decisions: bool = False,
) -> list[ValidationIssue]:
    """Validate the external typed proposal without making the Markdown typed."""
    issues = list(validate(parsed))
    if not isinstance(proposal, dict):
        return issues + [ValidationIssue("v3.proposal.shape", "v3:proposal", "proposal must be an object", "agent", "mapping")]

    envelope_keys = {"schema", "authoring_version", "source_hash", "proposal", "provenance", "source_spans", "anchors", "echo"}
    for key in sorted(set(proposal) - envelope_keys):
        _issue(issues, "v3.proposal.unknown_key", f"v3:proposal.{key}", f"proposal envelope contains unknown key {key!r}", owner="agent", control="mapping")
    for key in sorted(envelope_keys - set(proposal)):
        _issue(issues, "v3.proposal.required_field", f"v3:proposal.{key}", f"proposal envelope requires {key!r}", owner="agent")
    if proposal.get("schema") != PROPOSAL_SCHEMA_ID:
        _issue(issues, "v3.proposal.schema", "v3:proposal.schema", f"proposal schema must be {PROPOSAL_SCHEMA_ID!r}", owner="agent")
    if proposal.get("authoring_version") != AUTHORING_VERSION:
        _issue(issues, "v3.proposal.version", "v3:proposal.authoring_version", f"authoring_version must be {AUTHORING_VERSION!r}", owner="agent")
    if proposal.get("source_hash") != semantic_hash(parsed):
        _issue(issues, "v3.proposal.source_mismatch", "v3:proposal.source_hash", "proposal was not extracted from this Markdown revision", owner="agent")
    payload = proposal.get("proposal")
    if not isinstance(payload, dict):
        _issue(issues, "v3.proposal.payload", "v3:proposal.proposal", "proposal payload must be an object", owner="agent", control="mapping")
        return issues

    expected_keys = {"intent", "questions", "scope", "terms", "inputs", "models", "transform", "outputs", "decisions", "open_questions", "delivery", "contracts"}
    for key in sorted(expected_keys - set(payload)):
        _issue(issues, "v3.proposal.payload_missing", f"v3:proposal.proposal.{key}", f"typed proposal is missing {key!r}", owner="agent")
    for key in sorted(set(payload) - expected_keys):
        _issue(issues, "v3.proposal.payload_unknown", f"v3:proposal.proposal.{key}", "typed proposal has no unowned section; put behavior in Transform or Models", owner="agent")
    for key in ("intent", "scope"):
        if key in payload:
            _validate_string_field(issues, payload[key], f"v3:proposal.proposal.{key}", key)

    _validate_questions(issues, payload.get("questions"))
    terms = _list_field(issues, payload.get("terms"), "v3:proposal.proposal.terms", "terms")
    term_ids: set[str] = set()
    for index, term in enumerate(terms):
        _validate_term(issues, term, index, term_ids)
    known_terms = term_ids
    for index, term in enumerate(terms):
        if not isinstance(term, dict):
            continue
        for ref in term.get("related_terms", []) if isinstance(term.get("related_terms"), list) else []:
            if isinstance(ref, str) and ref not in known_terms:
                _issue(issues, "v3.term.related_ref", f"v3:terms[{index}].related_terms", f"related term {ref!r} is not defined inline", control="list")

    _validate_inputs(issues, payload.get("inputs"))
    _validate_models(issues, payload.get("models"))
    _validate_transform(issues, payload.get("transform"))
    _validate_outputs(issues, payload.get("outputs"))
    _validate_decisions(issues, payload.get("decisions"), require_locked=require_locked_decisions)
    _validate_open_questions(issues, payload.get("open_questions"))
    for key in ("questions", "terms", "inputs", "models", "transform", "outputs", "decisions", "open_questions"):
        section = parsed.sections.get(key)
        if section is not None and section.text and not _as_list(payload.get(key)):
            _issue(
                issues,
                "v3.proposal.section_omitted",
                f"v3:proposal.proposal.{key}",
                f"the populated {key} section must produce at least one typed entry",
                owner="agent",
                control="list",
            )
    for key in ("intent", "scope"):
        section = parsed.sections.get(key)
        if section is not None and section.text and not _is_nonempty_string(payload.get(key)):
            _issue(
                issues,
                "v3.proposal.section_omitted",
                f"v3:proposal.proposal.{key}",
                f"the populated {key} section must produce a typed value",
                owner="agent",
                control="text",
            )

    delivery = payload.get("delivery")
    if isinstance(delivery, dict):
        for key in sorted(set(delivery) - set(FIXED_DELIVERY)):
            _issue(issues, "v3.proposal.unknown_key", f"v3:delivery.{key}", f"delivery contains unknown key {key!r}", owner="agent", control="mapping")
        for key in sorted(set(FIXED_DELIVERY) - set(delivery)):
            _issue(issues, "v3.proposal.required_field", f"v3:delivery.{key}", f"delivery requires {key!r}", owner="agent")
    if delivery != FIXED_DELIVERY:
        _issue(issues, "v3.delivery.fixed_profile", "v3:proposal.proposal.delivery", "local DP delivery is fixed to the desktop-local DuckDB semantic-query path; unsupported user delivery is an Open Question", owner="agent", control="mapping")

    _validate_contract_inventory(issues, payload)
    _validate_provenance(issues, parsed, proposal, allow_lifecycle_shift=parsed.frontmatter.status == "approved")
    _validate_default_term_echo(issues, payload, proposal)
    _validate_source_section_coverage(issues, parsed, proposal)
    _validate_locked_decisions(
        issues,
        proposal,
        locked_proposal,
        expected_proposal_hash=(
            parsed.frontmatter.prior_approved_proposal_hash
            or (
                parsed.frontmatter.approved_proposal_hash
                if parsed.frontmatter.status == "approved"
                else None
            )
        ),
    )
    return sorted(issues, key=lambda item: (item.path, item.code, item.message))


def _validate_default_term_echo(
    issues: list[ValidationIssue],
    payload: dict[str, Any],
    proposal: dict[str, Any],
) -> None:
    """Require provenance and echo disclosure for materialized priorities."""
    provenance = proposal.get("provenance")
    echo = proposal.get("echo")
    if not isinstance(provenance, dict) or not isinstance(echo, dict):
        return
    echo_text = echo.get("text")
    coverage = echo.get("coverage")
    for term in _as_list(payload.get("terms")):
        if not isinstance(term, dict):
            continue
        term_id = term.get("id")
        if not isinstance(term_id, str):
            continue
        path = f"v3:terms[{term_id}].priority"
        origin = provenance.get(path)
        if origin not in {"explicit", "inferred", "platform_fixed"}:
            _issue(
                issues,
                "v3.term.priority_provenance",
                path,
                "every materialized term priority must have explicit, inferred, or platform_fixed provenance",
                owner="agent",
                control="enum",
            )
        elif origin == "platform_fixed" and term.get("priority") != "P3":
            _issue(
                issues,
                "v3.term.priority_provenance",
                path,
                "platform_fixed provenance is reserved for the omitted P3 default",
                owner="agent",
                control="mapping",
            )
        if not isinstance(coverage, list) or path not in coverage:
            _issue(
                issues,
                "v3.echo.default_uncovered" if term.get("priority") == "P3" else "v3.echo.priority_uncovered",
                path,
                "the materialized term priority must be explicitly covered by the echo-back",
                owner="agent",
                control="list",
            )
        expected_priority = str(term.get("priority"))
        disclosure_missing = (
            not isinstance(echo_text, str)
            or not re.search(rf"\b{re.escape(expected_priority)}\b", echo_text, re.IGNORECASE)
            or (expected_priority == "P3" and origin == "platform_fixed" and not re.search(r"\bdefault\b", echo_text, re.IGNORECASE))
        )
        if disclosure_missing:
            _issue(
                issues,
                "v3.echo.default_disclosure" if expected_priority == "P3" else "v3.echo.priority_disclosure",
                path,
                "the echo-back must explicitly disclose the materialized term priority",
                owner="agent",
                control="long_text",
            )


def _validate_provenance(
    issues: list[ValidationIssue],
    parsed: ParsedDocument,
    proposal: dict[str, Any],
    *,
    allow_lifecycle_shift: bool = False,
) -> None:
    provenance = proposal.get("provenance")
    spans = proposal.get("source_spans")
    echo = proposal.get("echo")
    if not isinstance(provenance, dict) or not provenance:
        _issue(issues, "v3.provenance.missing", "v3:proposal.provenance", "proposal must identify explicit, inferred, and platform_fixed values", control="mapping")
        return
    if not isinstance(spans, dict):
        _issue(issues, "v3.provenance.spans_missing", "v3:proposal.source_spans", "proposal must carry source spans", control="mapping")
        spans = {}
    anchors = proposal.get("anchors")
    if not isinstance(anchors, dict):
        _issue(issues, "v3.provenance.anchors_shape", "v3:proposal.anchors", "anchors must map source span paths to typed proposal paths", control="mapping")
        anchors = {}
    source_for_target: dict[str, str] = {}
    for source_path, target_path in anchors.items():
        if not isinstance(source_path, str) or not isinstance(target_path, str) or not source_path or not target_path:
            _issue(issues, "v3.provenance.anchors_shape", "v3:proposal.anchors", "anchor paths must be non-empty strings", control="mapping")
            continue
        if source_path not in parsed.source_map.spans:
            _issue(issues, "v3.provenance.anchor_source", f"v3:proposal.anchors[{source_path}]", "anchor source is not present in the Markdown source map", control="mapping")
        if target_path in source_for_target and source_for_target[target_path] != source_path:
            _issue(issues, "v3.provenance.anchor_duplicate", f"v3:proposal.anchors[{source_path}]", "a typed proposal path may have only one source anchor", control="mapping")
        source_for_target[target_path] = source_path
    for path, origin in provenance.items():
        if origin == "platform_fixed" or path in source_for_target:
            continue
        match = re.fullmatch(r"v3:terms\[([^\]]+)\]\.priority", str(path))
        if match:
            source_path = f"v3:terms[{match.group(1)}].text"
            if source_path in parsed.source_map.spans:
                source_for_target[path] = source_path
    for path, supplied in spans.items():
        span_path = f"v3:proposal.source_spans[{path}]"
        if path not in provenance:
            _issue(issues, "v3.provenance.span_unbound", span_path, "source spans must correspond to a provenance entry", owner="agent", control="mapping")
            continue
        if provenance.get(path) == "platform_fixed":
            _issue(issues, "v3.provenance.span_unbound", span_path, "platform_fixed values must not carry source spans", owner="agent", control="mapping")
            continue
        actual = parsed.source_map.spans.get(source_for_target.get(path, path))
        if actual is None:
            _issue(issues, "v3.provenance.span_unknown", span_path, "source span is not present in the Markdown source map", owner="agent", control="mapping")
            continue
        if not isinstance(supplied, dict) or set(supplied) != {"line_start", "line_end", "offset_start", "offset_end"} or any(
            not isinstance(supplied.get(key), int) or isinstance(supplied.get(key), bool)
            for key in ("line_start", "line_end", "offset_start", "offset_end")
        ):
            _issue(issues, "v3.provenance.span_shape", span_path, "source spans require exactly four integer offsets", owner="agent", control="mapping")

    def span_shape_matches(supplied: Any, actual: SourceSpan) -> bool:
        if not isinstance(supplied, dict):
            return False
        keys = ("line_start", "line_end", "offset_start", "offset_end")
        if any(not isinstance(supplied.get(key), int) or isinstance(supplied.get(key), bool) for key in keys):
            return False
        return (
            supplied["line_end"] - supplied["line_start"] == actual.line_end - actual.line_start
            and supplied["offset_end"] - supplied["offset_start"] == actual.offset_end - actual.offset_start
        )

    for path, origin in provenance.items():
        if not isinstance(origin, str) or origin not in PROVENANCE_VALUES:
            _issue(issues, "v3.provenance.value", str(path), f"provenance must be one of {PROVENANCE_VALUES}", control="enum")
            continue
        if origin != "platform_fixed":
            actual = parsed.source_map.spans.get(source_for_target.get(path, path))
            supplied = spans.get(path)
            if actual is None:
                _issue(issues, "v3.provenance.path", str(path), "provenance path is not a source span in the Markdown")
            elif supplied != actual.to_dict() and not (allow_lifecycle_shift and span_shape_matches(supplied, actual)):
                _issue(issues, "v3.provenance.span_mismatch", str(path), "proposal source span does not match the parsed Markdown span")
    if isinstance(echo, dict):
        for key in sorted(set(echo) - {"text", "coverage"}):
            _issue(issues, "v3.proposal.unknown_key", f"v3:proposal.echo.{key}", f"echo contains unknown key {key!r}", owner="agent", control="mapping")
        for key in ("text", "coverage"):
            if key not in echo:
                _issue(issues, "v3.proposal.required_field", f"v3:proposal.echo.{key}", f"echo requires {key!r}", owner="agent")
    if not isinstance(echo, dict) or not _is_nonempty_string(echo.get("text")):
        _issue(issues, "v3.echo.missing", "v3:proposal.echo", "proposal must include a natural-language echo-back", control="long_text")
        return
    coverage = echo.get("coverage")
    if not isinstance(coverage, list) or any(not isinstance(item, str) for item in coverage):
        _issue(issues, "v3.echo.coverage", "v3:proposal.echo.coverage", "echo coverage must be a list of source paths", control="list")
        return
    missing = sorted(set(provenance) - set(coverage))
    for path in missing:
        _issue(issues, "v3.echo.uncovered", path, "the echo-back does not cover this interpreted value")
    for index, path in enumerate(coverage):
        if path not in provenance:
            _issue(issues, "v3.echo.coverage_path", f"v3:proposal.echo.coverage[{index}]", "echo coverage must name a provenance entry", owner="agent", control="list")


def _validate_source_section_coverage(
    issues: list[ValidationIssue],
    parsed: ParsedDocument,
    proposal: dict[str, Any],
) -> None:
    """Reject a typed proposal that silently drops populated source sections."""
    provenance = proposal.get("provenance")
    if not isinstance(provenance, dict):
        return
    anchors = proposal.get("anchors")
    if not isinstance(anchors, dict):
        anchors = {}
    for key, section in parsed.sections.items():
        if not section.text:
            continue
        prefix = f"v3:{key}"
        covered = any(
            origin != "platform_fixed"
            and isinstance(path, str)
            and (path == f"{prefix}.text" or path.startswith(f"{prefix}["))
            for path, origin in provenance.items()
        )
        if not covered:
            _issue(
                issues,
                "v3.provenance.section_uncovered",
                f"v3:{key}",
                "a populated Markdown section must contribute a provenance entry to the typed proposal",
                owner="agent",
                control="mapping",
            )
    seen_block_targets: dict[str, str] = {}
    for path in parsed.source_map.spans:
        if not path.endswith(".text"):
            continue
        if "[" in path:
            target_path = anchors.get(path, path)
            covered = target_path in provenance
            target_match = re.fullmatch(r"v3:(questions|terms|inputs|models|transform|outputs|decisions|open_questions)\[[^\]]+\]\.text", target_path)
            if target_match:
                previous_source = seen_block_targets.get(target_path)
                if previous_source is not None and previous_source != path:
                    _issue(
                        issues,
                        "v3.provenance.anchor_duplicate",
                        path,
                        f"multiple Markdown subsections map to the typed proposal path {target_path!r}",
                        owner="agent",
                        control="mapping",
                    )
                seen_block_targets[target_path] = path
            if not covered:
                _issue(
                    issues,
                    "v3.provenance.source_block_uncovered",
                    path,
                    "every populated Markdown subsection must contribute a provenance entry to the typed proposal",
                    owner="agent",
                    control="mapping",
                )
            match = re.fullmatch(r"v3:([a-z_]+)\[([^\]]+)\]\.text", target_path)
            if match and covered:
                key, item_id = match.groups()
                payload = proposal.get("proposal")
                items = _as_list(payload.get(key) if isinstance(payload, dict) else None)
                if not any(isinstance(item, dict) and item.get("id") == item_id for item in items):
                    _issue(
                        issues,
                        "v3.provenance.source_block_unmatched",
                        path,
                        "a populated Markdown subsection must map through anchors to a typed entry with the same stable id",
                        owner="agent",
                        control="mapping",
                    )


def _rewrite_lifecycle(
    raw: str,
    *,
    status: str,
    content_hash: str | None = None,
    proposal_hash_value: str | None = None,
    prior_proposal_hash: str | None = None,
) -> str:
    match = re.match(r"\A---\n(?P<body>.*?)\n---(?P<tail>.*)\Z", raw, flags=re.DOTALL)
    if not match:
        raise ParseError("cannot rewrite lifecycle metadata without v3 frontmatter")
    lines = []
    for line in match.group("body").split("\n"):
        if line.startswith("status:"):
            lines.append(f"status: {status}")
        elif (
            line.startswith("approved_content_hash:")
            or line.startswith("approved_proposal_hash:")
            or line.startswith("prior_approved_proposal_hash:")
        ):
            continue
        else:
            lines.append(line)
    if content_hash is not None:
        lines.append(f"approved_content_hash: {content_hash}")
    if proposal_hash_value is not None:
        lines.append(f"approved_proposal_hash: {proposal_hash_value}")
    if prior_proposal_hash is not None:
        lines.append(f"prior_approved_proposal_hash: {prior_proposal_hash}")
    return "---\n" + "\n".join(lines) + "\n---" + match.group("tail")


def approve(
    parsed: ParsedDocument,
    proposal: dict[str, Any],
    *,
    base_hash: str,
    locked_proposal: dict[str, Any] | None = None,
) -> str:
    """Approve the user's interpretation, binding source and proposal hashes."""
    if parsed.frontmatter.status == "approved" and locked_proposal is None:
        raise ValueError("cannot re-approve an approved v3 document without prior locked proposal evidence")
    if parsed.frontmatter.prior_approved_proposal_hash:
        if locked_proposal is None:
            raise ValueError("cannot re-approve a patched v3 document without prior locked proposal evidence")
        if proposal_hash(locked_proposal) != parsed.frontmatter.prior_approved_proposal_hash:
            raise ValueError("prior locked proposal evidence does not match the document's prior approval")
    actual = semantic_hash(parsed)
    if base_hash != actual:
        raise StalePatchError(f"stale approval base hash: expected {base_hash}, current {actual}")
    issues = validate_proposal(
        parsed,
        proposal,
        locked_proposal=locked_proposal,
        require_locked_decisions=True,
    )
    blocking = list(issues)
    payload = proposal.get("proposal") if isinstance(proposal, dict) else None
    for question in _as_list(payload.get("open_questions") if isinstance(payload, dict) else None):
        if isinstance(question, dict) and question.get("blocking") is True:
            blocking.append(ValidationIssue("v3.approval.blocked", f"v3:open_questions[{question.get('id', '?')}].blocking", "blocking open questions prevent approval"))
    if blocking:
        detail = "; ".join(f"{issue.path}: {issue.code}" for issue in blocking)
        raise ValueError(f"cannot approve invalid or blocked v3 document: {detail}")
    candidate = parse(_rewrite_lifecycle(parsed.raw, status="approved", content_hash=actual, proposal_hash_value=proposal_hash(proposal)))
    return candidate.raw


def _span_value(parsed: ParsedDocument, path: str) -> str | None:
    span = parsed.source_map.spans.get(path)
    if span is None:
        return None
    return parsed.raw[span.offset_start:span.offset_end]


def targeted_patch(parsed: ParsedDocument, *, base_hash: str, path: str, value: str) -> str:
    """Replace a prose span while preserving all unrelated Markdown bytes."""
    actual = semantic_hash(parsed)
    if base_hash != actual:
        raise StalePatchError(f"stale patch base hash: expected {base_hash}, current {actual}")
    span = parsed.source_map.spans.get(path)
    if span is None or not path.endswith(".text"):
        raise ValueError(f"path {path!r} is not a patchable prose span")
    updated = parsed.raw[:span.offset_start] + value + parsed.raw[span.offset_end:]
    if parsed.frontmatter.status == "approved":
        updated = _rewrite_lifecycle(
            updated,
            status="proposed",
            prior_proposal_hash=parsed.frontmatter.approved_proposal_hash,
        )
    parse(updated)
    return updated


def validate_approval(parsed: ParsedDocument, proposal: dict[str, Any]) -> list[ValidationIssue]:
    issues = validate_proposal(parsed, proposal, require_locked_decisions=True)
    if parsed.frontmatter.status == "approved" and isinstance(proposal, dict):
        if parsed.frontmatter.approved_proposal_hash != proposal_hash(proposal):
            _issue(issues, "v3.approval.proposal_binding_mismatch", _path("frontmatter", None, "approved_proposal_hash"), "approved proposal hash does not match the supplied typed snapshot", owner="agent")
    return sorted(issues, key=lambda item: (item.path, item.code, item.message))


def emit_review(proposal: dict[str, Any]) -> str:
    """Return the external agent's natural-language echo, never Markdown AST."""
    echo = proposal.get("echo") if isinstance(proposal, dict) else None
    if not isinstance(echo, dict) or not isinstance(echo.get("text"), str):
        raise ValueError("proposal has no natural-language echo")
    return echo["text"]


def _issue_json(issue: ValidationIssue) -> dict[str, str]:
    return {
        "schema": DIAGNOSTIC_SCHEMA_ID,
        "code": issue.code,
        "path": issue.path,
        "severity": "error",
        "owner": issue.owner,
        "control": issue.control,
        "stage": "s0_spec",
        "origin": "tool_computed",
        "message": issue.message,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_validate = sub.add_parser("validate")
    p_validate.add_argument("spec", type=Path)
    p_validate.add_argument("--proposal", type=Path, default=None)
    p_validate.add_argument("--json", action="store_true")
    p_schema = sub.add_parser("schema")
    p_schema.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "schema":
        print(json.dumps(PROPOSAL_SCHEMA, indent=2, ensure_ascii=False))
        return 0
    proposal: Any = None
    try:
        parsed = parse(args.spec.read_text(encoding="utf-8"))
        issues = validate(parsed)
        if args.proposal:
            proposal = json.loads(args.proposal.read_text(encoding="utf-8"))
            issues = validate_proposal(parsed, proposal)
        report = {
            "schema": "nxd-diagnostic-report-v3",
            "tool": "dp_spec_authoring",
            "target": str(args.spec),
            "ok": not issues,
            "spec_hash": semantic_hash(parsed),
            "proposal_hash": proposal_hash(proposal) if isinstance(proposal, dict) else None,
            "counts": {"error": len(issues), "warning": 0, "info": 0},
            "diagnostics": [_issue_json(issue) for issue in issues],
        }
    except (OSError, UnicodeError, ValueError) as exc:
        report = {
            "schema": "nxd-diagnostic-report-v3",
            "tool": "dp_spec_authoring",
            "target": str(args.spec),
            "ok": False,
            "spec_hash": None,
            "proposal_hash": None,
            "counts": {"error": 1, "warning": 0, "info": 0},
            "diagnostics": [{
                "schema": DIAGNOSTIC_SCHEMA_ID,
                "code": "v3.parse.invalid",
                "path": "v3:document",
                "severity": "error",
                "owner": "user",
                "control": "text",
                "stage": "s0_spec",
                "origin": "tool_computed",
                "message": str(exc),
            }],
        }
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for diagnostic in report["diagnostics"]:
            print(f"ERROR {diagnostic['code']} {diagnostic['path']}: {diagnostic['message']}")
        if report["ok"]:
            print("ok")
    return 0 if report["ok"] else 1


_NONEMPTY_STRING_SCHEMA = {"type": "string", "minLength": 1}
_CONTRACT_SOURCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "model", "guarantee", "rule", "fields"],
    "properties": {
        "id": _NONEMPTY_STRING_SCHEMA,
        "model": _NONEMPTY_STRING_SCHEMA,
        "guarantee": _NONEMPTY_STRING_SCHEMA,
        "rule": _NONEMPTY_STRING_SCHEMA,
        "fields": {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"}},
    },
}

PROPOSAL_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": PROPOSAL_SCHEMA_ID,
    "title": "NXD natural-language DP-spec typed proposal",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "authoring_version", "source_hash", "proposal", "provenance", "source_spans", "anchors", "echo"],
    "properties": {
        "schema": {"const": PROPOSAL_SCHEMA_ID},
        "authoring_version": {"const": AUTHORING_VERSION},
        "source_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "proposal": {
            "type": "object",
            "additionalProperties": False,
            "required": ["intent", "questions", "scope", "terms", "inputs", "models", "transform", "outputs", "decisions", "open_questions", "delivery", "contracts"],
            "properties": {
                "intent": _NONEMPTY_STRING_SCHEMA,
                "scope": _NONEMPTY_STRING_SCHEMA,
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "question"],
                        "properties": {"id": _NONEMPTY_STRING_SCHEMA, "question": _NONEMPTY_STRING_SCHEMA},
                    },
                },
                "terms": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "name", "definition", "priority"],
                        "properties": {
                            "id": _NONEMPTY_STRING_SCHEMA,
                            "name": _NONEMPTY_STRING_SCHEMA,
                            "definition": _NONEMPTY_STRING_SCHEMA,
                            "synonyms": {"type": "array", "items": _NONEMPTY_STRING_SCHEMA},
                            "related_terms": {"type": "array", "items": _NONEMPTY_STRING_SCHEMA},
                            "examples": {"type": "array", "items": _NONEMPTY_STRING_SCHEMA},
                            "term_values": {"type": "array", "items": _NONEMPTY_STRING_SCHEMA},
                            "tags": {"type": "object", "additionalProperties": _NONEMPTY_STRING_SCHEMA},
                            "priority": {"enum": ["P1", "P2", "P3", "P4", "P5"]},
                        },
                    },
                },
                "inputs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "expectations"],
                        "properties": {"id": _NONEMPTY_STRING_SCHEMA, "expectations": {"type": "array", "items": _CONTRACT_SOURCE_SCHEMA}},
                    },
                },
                "models": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": False, "required": ["id", "fields"], "properties": {"id": _NONEMPTY_STRING_SCHEMA, "fields": {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"}}}},
                },
                "transform": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": False, "required": ["id", "operation"], "properties": {"id": _NONEMPTY_STRING_SCHEMA, "operation": {"enum": list(TRANSFORM_OPERATIONS)}}},
                },
                "outputs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "promises"],
                        "properties": {"id": _NONEMPTY_STRING_SCHEMA, "promises": {"type": "array", "items": _CONTRACT_SOURCE_SCHEMA}},
                    },
                },
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "target", "ruling", "status"],
                        "properties": {
                            "id": _NONEMPTY_STRING_SCHEMA,
                            "target": _NONEMPTY_STRING_SCHEMA,
                            "ruling": _NONEMPTY_STRING_SCHEMA,
                            "status": {"enum": list(DECISION_STATUS_VALUES)},
                        },
                    },
                },
                "open_questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "question", "blocking"],
                        "properties": {"id": _NONEMPTY_STRING_SCHEMA, "question": _NONEMPTY_STRING_SCHEMA, "blocking": {"type": "boolean"}},
                    },
                },
                "delivery": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(FIXED_DELIVERY),
                    "properties": {key: {"const": value} for key, value in FIXED_DELIVERY.items()},
                },
                "contracts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "attachment", "model", "phase", "guarantee", "rule", "fields"],
                        "properties": {
                            "id": _NONEMPTY_STRING_SCHEMA,
                            "attachment": _NONEMPTY_STRING_SCHEMA,
                            "model": _NONEMPTY_STRING_SCHEMA,
                            "phase": _NONEMPTY_STRING_SCHEMA,
                            "guarantee": _NONEMPTY_STRING_SCHEMA,
                            "rule": _NONEMPTY_STRING_SCHEMA,
                            "fields": {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"}},
                        },
                    },
                },
            },
        },
        "provenance": {"type": "object", "minProperties": 1, "additionalProperties": {"enum": list(PROVENANCE_VALUES)}},
        "source_spans": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": False,
                "required": ["line_start", "line_end", "offset_start", "offset_end"],
                "properties": {key: {"type": "integer"} for key in ("line_start", "line_end", "offset_start", "offset_end")},
            },
        },
        "anchors": {
            "type": "object",
            "additionalProperties": {"type": "string", "minLength": 1},
        },
        "echo": {
            "type": "object",
            "additionalProperties": False,
            "required": ["text", "coverage"],
            "properties": {"text": _NONEMPTY_STRING_SCHEMA, "coverage": {"type": "array", "items": _NONEMPTY_STRING_SCHEMA}},
        },
    },
}


if __name__ == "__main__":
    raise SystemExit(main())
