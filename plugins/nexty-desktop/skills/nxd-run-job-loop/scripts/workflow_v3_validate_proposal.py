import json
import sys
from pathlib import Path

import dp_spec_authoring as authoring

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


def expected_source_span(parsed, proposal, issue):
    # Coordinates are safe structured remediation for a span mismatch.  Do not
    # return source text or arbitrary validator output across the supervisor
    # boundary.
    if issue is None or issue.code != "v3.provenance.span_mismatch":
        return None
    source_path = _source_path_for_target(parsed, proposal, issue.path)
    if source_path is None:
        return None
    span = parsed.source_map.spans.get(source_path)
    return None if span is None else span.to_dict()


def bounded(value, limit):
    clean = "".join(" " if character.isspace() else character for character in str(value))
    return clean.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def main(argv):
    source = Path(argv[1])
    proposal_path = Path(argv[2])
    parsed = authoring.parse(source.read_text(encoding="utf-8"))
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    # Preparation validates and binds the candidate before consent. The trusted
    # materializer performs the proposed-to-locked projection only after the
    # supervisor has recorded the subject-bound session_decision.
    issues = authoring.validate_proposal(parsed, proposal, require_locked_decisions=False)
    payload = proposal.get("proposal") if isinstance(proposal, dict) else None
    questions = payload.get("open_questions", []) if isinstance(payload, dict) else []
    blocking = any(isinstance(item, dict) and item.get("blocking") is True for item in questions)
    primary = min(issues, key=lambda item: (item.path, item.code, item.message), default=None)
    expected = expected_source_span(parsed, proposal, primary)

    result = {
        "schema": "nxd-workflow-proposal-validation-v1",
        "ok": not issues and not blocking and parsed.frontmatter.status == "proposed",
        "source_semantic_sha256": authoring.semantic_hash(parsed),
        "proposal_sha256": authoring.proposal_hash(proposal) if isinstance(proposal, dict) else None,
        "issue": None if primary is None else {
            "code": bounded(primary.code, 128),
            "path": bounded(primary.path, 256),
            "message": bounded(primary.message, 512),
            **({"expected_source_span": expected} if expected is not None else {}),
        },
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
