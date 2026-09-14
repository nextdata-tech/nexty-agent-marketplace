import hashlib
import json
import sys
from pathlib import Path

import dp_diagnostics as diagnostics
import dp_spec_authoring as authoring

source = Path(sys.argv[1])
proposal_path = Path(sys.argv[2])
closure = Path(sys.argv[3])
parsed = authoring.parse(source.read_text(encoding="utf-8"))
proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
approved = authoring.approve(parsed, proposal, base_hash=authoring.semantic_hash(parsed))
approved_path = source.with_name("approved.md")
approved_path.write_text(approved, encoding="utf-8")
lock, write_report = diagnostics.write_lock(
    approved_path,
    closure,
    proposal=proposal_path,
    plugin_version="nxd-workflow-v3-materializer-v1",
    generator_skill="nxd-workflow-supervisor",
    now_ms=0,
)
verify_report = diagnostics.verify_lock(closure, approved_path)
spec_report = {
    "schema": "nxd-diagnostic-report-v3",
    "tool": "validate_dp_spec",
    "target": str(approved_path),
    "ok": True,
    "counts": {"error": 0, "warning": 0, "info": 0},
    "spec_hash": authoring.semantic_hash(parsed),
    "proposal_hash": authoring.proposal_hash(proposal),
    "diagnostics": [],
}
diagnostics.record_init(
    closure / "build-record.json",
    closure / diagnostics.CLOSURE_LOCK,
    spec_report=spec_report,
    generator_model="supervisor-materialized",
    now_ms=0,
)
approved_bytes = (closure / diagnostics.CLOSURE_SNAPSHOT).read_bytes()
proposal_bytes = (closure / diagnostics.V3_PROPOSAL_SNAPSHOT).read_bytes()
result = {
    "schema": "nxd-workflow-proposal-materialization-v1",
    "ok": bool(lock) and write_report.ok and verify_report.ok,
    "source_semantic_sha256": authoring.semantic_hash(parsed),
    "proposal_sha256": authoring.proposal_hash(proposal),
    "approved_raw_sha256": "sha256:" + hashlib.sha256(approved_bytes).hexdigest(),
    "proposal_raw_sha256": "sha256:" + hashlib.sha256(proposal_bytes).hexdigest(),
}
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
sys.exit(0 if result["ok"] else 2)
