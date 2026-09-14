#!/usr/bin/env python3
"""Validate a prose-first dp-spec document and its typed proposal.

Version 3 is the user-authoring boundary. Version 2 remains available here for
read-only verification of existing closure artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import dp_spec_v2 as v2  # noqa: E402
import dp_spec_authoring as v3  # noqa: E402


def _issue(issue: v2.ValidationIssue) -> dict[str, str]:
    return {
        "schema": v2.SPEC_DIAGNOSTIC_SCHEMA_ID,
        "code": issue.code,
        "path": issue.path,
        "severity": "error",
        "owner": issue.owner,
        "control": issue.control,
        "stage": "s0_spec",
        "origin": "tool_computed",
        "message": issue.message,
    }


def _version(raw: str) -> int | None:
    match = re.search(r"^dp_spec_version:\s*(\d+)\s*$", raw, flags=re.MULTILINE)
    return int(match.group(1)) if match else None


def _v3_issue(issue: v3.ValidationIssue) -> dict[str, str]:
    return {
        "schema": v3.DIAGNOSTIC_SCHEMA_ID,
        "code": issue.code,
        "path": issue.path,
        "severity": "error",
        "owner": issue.owner,
        "control": issue.control,
        "stage": "s0_spec",
        "origin": "tool_computed",
        "message": issue.message,
    }


def validate(path: Path, proposal_path: Path | None = None) -> dict:
    """Return the stable report envelope used by lock/build-record tooling."""
    try:
        raw = path.read_text(encoding="utf-8")
        if _version(raw) == v3.SPEC_VERSION:
            parsed = v3.parse(raw)
            proposal = None
            if proposal_path is not None:
                try:
                    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, ValueError) as exc:
                    raise v3.ParseError(f"proposal JSON is invalid: {exc}") from exc
            issues = v3.validate(parsed) if proposal is None else v3.validate_proposal(parsed, proposal)
            diagnostics = [_v3_issue(issue) for issue in issues]
            return {
                "schema": "nxd-diagnostic-report-v3",
                "tool": "validate_dp_spec",
                "target": str(path),
                "ok": not diagnostics,
                "spec_hash": v3.semantic_hash(parsed),
                "proposal_hash": v3.proposal_hash(proposal) if isinstance(proposal, dict) else None,
                "counts": {"error": len(diagnostics), "warning": 0, "info": 0},
                "diagnostics": diagnostics,
            }
        parsed = v2.parse(raw)
        if proposal_path is not None:
            # v2 is a read-only verifier for closure evidence written before the
            # v3 cutover; it has no typed proposal to bind. Accepting the flag
            # and ignoring it would report `ok: true` for a spec whose supplied
            # proposal was never looked at — the one outcome an approval flow
            # binding proposal hashes must never see.
            #
            # This runs *after* `v2.parse`, so a source that is unparseable or
            # names an unsupported version reports that instead. A flag-usage
            # complaint must never mask the reason the document cannot be read,
            # and the version named below is only ever one the parser accepted.
            return {
                "schema": "nxd-diagnostic-report-v2",
                "tool": "validate_dp_spec",
                "target": str(path),
                "ok": False,
                "spec_hash": None,
                "counts": {"error": 1, "warning": 0, "info": 0},
                "diagnostics": [{
                    "schema": v2.SPEC_DIAGNOSTIC_SCHEMA_ID,
                    "code": "spec.proposal.unsupported",
                    # `v2:proposal`, matching `write_lock`'s arm: one mistake
                    # gets one address as well as one code, and the address
                    # names the flag at fault rather than the whole document.
                    "path": "v2:proposal",
                    "severity": "error",
                    "owner": "agent",
                    # `none`, not `text`: no field in the v2 document can be
                    # edited to clear this. The repair is to drop the flag, so
                    # offering a text control invites typing into a field the
                    # v2 model does not have.
                    "control": "none",
                    "stage": "s0_spec",
                    "origin": "tool_computed",
                    "message": (
                        "--proposal is a v3 authoring input; this spec declares "
                        f"dp_spec_version {v2.SPEC_VERSION}, which carries no typed proposal"
                    ),
                }],
            }
        issues = v2.validate(parsed)
        diagnostics = [_issue(issue) for issue in issues]
        return {
            "schema": "nxd-diagnostic-report-v2",
            "tool": "validate_dp_spec",
            "target": str(path),
            "ok": not diagnostics,
            "spec_hash": v2.semantic_hash(parsed),
            "counts": {"error": len(diagnostics), "warning": 0, "info": 0},
            "diagnostics": diagnostics,
        }
    except v2.UnsupportedVersionError as exc:
        return {
            "schema": "nxd-diagnostic-report-v2",
            "tool": "validate_dp_spec",
            "target": str(path),
            "ok": False,
            "spec_hash": None,
            "counts": {"error": 1, "warning": 0, "info": 0},
            "diagnostics": [{
                "schema": v2.SPEC_DIAGNOSTIC_SCHEMA_ID,
                "code": "spec.frontmatter.unsupported_version",
                "path": "v2:frontmatter.dp_spec_version",
                "severity": "error",
                "owner": "user",
                "control": "enum",
                "stage": "s0_spec",
                "origin": "tool_computed",
                "message": str(exc),
            }],
        }
    except v3.ParseError as exc:
        return {
            "schema": "nxd-diagnostic-report-v3",
            "tool": "validate_dp_spec",
            "target": str(path),
            "ok": False,
            "spec_hash": None,
            "proposal_hash": None,
            "counts": {"error": 1, "warning": 0, "info": 0},
            "diagnostics": [{
                "schema": v3.DIAGNOSTIC_SCHEMA_ID,
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
    except (OSError, UnicodeError, v2.ParseError) as exc:
        return {
            "schema": "nxd-diagnostic-report-v2",
            "tool": "validate_dp_spec",
            "target": str(path),
            "ok": False,
            "spec_hash": None,
            "counts": {"error": 1, "warning": 0, "info": 0},
            "diagnostics": [{
                "schema": v2.SPEC_DIAGNOSTIC_SCHEMA_ID,
                "code": "spec.parse.invalid",
                "path": "v2:document",
                "severity": "error",
                "owner": "user",
                "control": "text",
                "stage": "s0_spec",
                "origin": "tool_computed",
                "message": str(exc),
            }],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--proposal", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = validate(args.spec, args.proposal)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for diag in report["diagnostics"]:
            print(f"{diag['severity'].upper()} {diag['code']} {diag['path']}: {diag['message']}")
        if report["ok"]:
            print("ok")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
