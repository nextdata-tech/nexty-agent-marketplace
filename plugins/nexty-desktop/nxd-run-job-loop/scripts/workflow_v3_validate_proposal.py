import json
import sys
from pathlib import Path

import dp_spec_authoring as authoring

source = Path(sys.argv[1])
proposal_path = Path(sys.argv[2])
parsed = authoring.parse(source.read_text(encoding="utf-8"))
proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
issues = authoring.validate_proposal(parsed, proposal, require_locked_decisions=False)
payload = proposal.get("proposal") if isinstance(proposal, dict) else None
questions = payload.get("open_questions", []) if isinstance(payload, dict) else []
blocking = any(isinstance(item, dict) and item.get("blocking") is True for item in questions)
primary = min(issues, key=lambda item: (item.path, item.code, item.message), default=None)


def expected_source_span(issue):
    # Coordinates are safe structured remediation for a span mismatch.  Do not
    # return source text or arbitrary validator output across the supervisor
    # boundary.
    if issue is None or issue.code != "v3.provenance.span_mismatch":
        return None
    span = parsed.source_map.spans.get(issue.path)
    return None if span is None else span.to_dict()


def bounded(value, limit):
    clean = "".join(" " if character.isspace() else character for character in str(value))
    return clean.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


result = {
    "schema": "nxd-workflow-proposal-validation-v1",
    "ok": not issues and not blocking and parsed.frontmatter.status == "proposed",
    "source_semantic_sha256": authoring.semantic_hash(parsed),
    "proposal_sha256": authoring.proposal_hash(proposal) if isinstance(proposal, dict) else None,
    "issue": None if primary is None else {
        "code": bounded(primary.code, 128),
        "path": bounded(primary.path, 256),
        "message": bounded(primary.message, 512),
        **({"expected_source_span": expected_source_span(primary)} if expected_source_span(primary) is not None else {}),
    },
}
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
sys.exit(0 if result["ok"] else 2)
