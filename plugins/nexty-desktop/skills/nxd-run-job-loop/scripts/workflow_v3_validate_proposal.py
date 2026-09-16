import argparse
import json
import re
import sys
from pathlib import Path

import dp_spec_authoring as authoring


VALIDATION_SCHEMA_V1 = "nxd-workflow-proposal-validation-v1"
VALIDATION_SCHEMA_V2 = "nxd-workflow-proposal-validation-v2"
SOURCE_MAP_UNAVAILABLE_CODE = "v3.provenance.source_map_unavailable"
SOURCE_MAP_OVERSIZED_CODE = "v3.provenance.source_map_oversized"

# These are deliberately limits on the structural recovery hint, not on the
# blueprint parser.  The map contains only parser paths and coordinates; it
# must never become a second channel for source text or typed proposal values.
SOURCE_MAP_MAX_PATH_BYTES = 256
SOURCE_MAP_MAX_ENTRIES = 256
SOURCE_MAP_MAX_SERIALIZED_BYTES = 32 * 1024

_SOURCE_PATH_RE = re.compile(
    r"v3:(?:"
    r"frontmatter\.(?:dp_spec_version|name|workflow|status|approved_content_hash|"
    r"approved_proposal_hash|prior_approved_proposal_hash)|"
    r"(?:intent|questions|scope|terms|inputs|models|transform|outputs|decisions|open_questions)"
    r"(?:\.text|\[[a-z0-9]+(?:_[a-z0-9]+)*\](?:\.text)?)?"
    r")"
)
_SPAN_FIELDS = ("line_start", "line_end", "offset_start", "offset_end")


def _source_path_for_target(parsed, proposal, target_path):
    """Resolve a typed target to one trusted parser source path, or fail closed."""

    anchors = proposal.get("anchors") if isinstance(proposal, dict) else None
    if anchors is None:
        anchors = {}
    if not isinstance(anchors, dict):
        return None

    source_for_target = {}
    for source_path, anchored_target in anchors.items():
        if not all(isinstance(value, str) and value for value in (source_path, anchored_target)):
            return None
        if source_path not in parsed.source_map.spans:
            return None
        previous = source_for_target.get(anchored_target)
        if previous is not None and previous != source_path:
            return None
        source_for_target[anchored_target] = source_path

    source_path = source_for_target.get(target_path, target_path)
    if source_path not in parsed.source_map.spans:
        return None
    return source_path


def _span_as_dict(span):
    """Project only the four parser coordinates from a source-map span."""

    values = {field: getattr(span, field, None) for field in _SPAN_FIELDS}
    if any(not isinstance(value, int) or isinstance(value, bool) for value in values.values()):
        return None
    return values


def _source_line_count(source):
    lines = source.splitlines(keepends=True)
    return max(1, len(lines))


def _valid_span(span, *, source_length, line_count):
    values = _span_as_dict(span)
    if values is None:
        return None
    if not (1 <= values["line_start"] <= values["line_end"] <= line_count):
        return None
    if not (0 <= values["offset_start"] <= values["offset_end"] <= source_length):
        return None
    return values


def structural_source_map(parsed):
    """Return the same-parse structural map, or a stable failure code.

    Parser paths are intentionally restricted to the v3 grammar.  Normalized
    identifiers inside those paths are structural keys, not source prose or
    typed values.  Every returned value is exactly four bounded integers.
    """

    spans = getattr(getattr(parsed, "source_map", None), "spans", None)
    if not isinstance(spans, dict) or not spans:
        return None, SOURCE_MAP_UNAVAILABLE_CODE
    if len(spans) > SOURCE_MAP_MAX_ENTRIES:
        return None, SOURCE_MAP_OVERSIZED_CODE

    source = getattr(parsed, "raw", None)
    if not isinstance(source, str):
        return None, SOURCE_MAP_UNAVAILABLE_CODE
    source_length = len(source)
    line_count = _source_line_count(source)
    if any(not isinstance(path, str) for path in spans):
        return None, SOURCE_MAP_UNAVAILABLE_CODE

    result = {}
    for path in sorted(spans):
        if not isinstance(path, str):
            return None, SOURCE_MAP_UNAVAILABLE_CODE
        try:
            path_bytes = path.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            return None, SOURCE_MAP_UNAVAILABLE_CODE
        if len(path_bytes) > SOURCE_MAP_MAX_PATH_BYTES:
            return None, SOURCE_MAP_OVERSIZED_CODE
        if _SOURCE_PATH_RE.fullmatch(path) is None:
            return None, SOURCE_MAP_UNAVAILABLE_CODE
        span = _valid_span(spans[path], source_length=source_length, line_count=line_count)
        if span is None:
            return None, SOURCE_MAP_UNAVAILABLE_CODE
        result[path] = span

    serialized = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(serialized.encode("utf-8")) > SOURCE_MAP_MAX_SERIALIZED_BYTES:
        return None, SOURCE_MAP_OVERSIZED_CODE
    return result, None


def expected_source_span(parsed, proposal, issue):
    # Coordinates are safe structured remediation for a span mismatch.  Do not
    # return source text or arbitrary validator output across the supervisor
    # boundary.  Apply the complete-map bounds here too so the v1 compatibility
    # field never bypasses the v2 fail-closed policy.
    if issue is None or issue.code != "v3.provenance.span_mismatch":
        return None
    source_map, _ = structural_source_map(parsed)
    if source_map is None:
        return None
    source_path = _source_path_for_target(parsed, proposal, issue.path)
    if source_path is None:
        return None
    return source_map.get(source_path)


def _issue_payload(parsed, proposal, primary, *, protocol):
    if primary is None:
        return None
    issue = {
        "code": bounded(primary.code, 128),
        "path": bounded(primary.path, 256),
        "message": bounded(primary.message, 512),
    }
    if primary.code != "v3.provenance.span_mismatch":
        return issue

    source_map, map_code = structural_source_map(parsed)
    source_path = _source_path_for_target(parsed, proposal, primary.path)
    if source_map is not None:
        # Keep the v1 hint for existing consumers when the primary issue maps
        # cleanly.  Protocol v2 still returns the complete map for every safe
        # mismatch, including an issue whose typed target has no direct anchor,
        # so recovery can regenerate the complete proposal from one parse.
        if source_path is not None and source_path in source_map:
            issue["expected_source_span"] = source_map[source_path]
        if protocol == 2:
            issue["source_spans"] = source_map
    elif protocol == 2:
        # Preserve the original mismatch code and put the stable recovery
        # status alongside it.  A v1 consumer can continue to use code/path/
        # message without learning about the optional v2 recovery channel.
        issue["source_map_code"] = map_code or SOURCE_MAP_UNAVAILABLE_CODE
    return issue


def validation_result(parsed, proposal, *, protocol=2):
    """Build a v2 result while retaining the v1 issue semantics.

    ``protocol=1`` is an explicit compatibility mode for older callers.  Both
    modes validate the same proposal and retain the same ``ok`` and primary
    issue code/path/message semantics; v2 only adds the optional recovery map.
    """

    if protocol in ("v1", 1):
        protocol = 1
    elif protocol in ("v2", 2):
        protocol = 2
    else:
        raise ValueError("protocol must be v1 or v2")

    issues = authoring.validate_proposal(parsed, proposal, require_locked_decisions=False)
    payload = proposal.get("proposal") if isinstance(proposal, dict) else None
    questions = payload.get("open_questions", []) if isinstance(payload, dict) else []
    blocking = any(isinstance(item, dict) and item.get("blocking") is True for item in questions)
    primary = min(issues, key=lambda item: (item.path, item.code, item.message), default=None)

    return {
        "schema": VALIDATION_SCHEMA_V1 if protocol == 1 else VALIDATION_SCHEMA_V2,
        "ok": not issues and not blocking and parsed.frontmatter.status == "proposed",
        "source_semantic_sha256": authoring.semantic_hash(parsed),
        "proposal_sha256": authoring.proposal_hash(proposal) if isinstance(proposal, dict) else None,
        "issue": _issue_payload(parsed, proposal, primary, protocol=protocol),
    }


def bounded(value, limit):
    clean = "".join(" " if character.isspace() else character for character in str(value))
    return clean.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("proposal", type=Path)
    parser.add_argument("--protocol", choices=("v1", "v2"), default="v2")
    args = parser.parse_args(argv[1:])

    parsed = authoring.parse(args.source.read_text(encoding="utf-8"))
    proposal = json.loads(args.proposal.read_text(encoding="utf-8"))
    # Preparation validates and binds the candidate before consent. The trusted
    # materializer performs the proposed-to-locked projection only after the
    # supervisor has recorded the subject-bound session_decision.
    result = validation_result(parsed, proposal, protocol=args.protocol)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
