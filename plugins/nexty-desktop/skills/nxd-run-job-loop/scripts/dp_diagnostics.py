#!/usr/bin/env python3
"""The shared diagnostic core for the nxd job loop.

Pipeline producers emit the closed `nxd-diagnostic-v2` shape. The
user-facing `validate_dp_spec.py` report uses the v2 or v3 field-addressed
diagnostic shape so a form can bind stable paths and controls; record ingestion
adapts those findings to the pipeline diagnostic envelope.

What lives here:

* the v2 closure schema and v3 authoring/lock dispatch — v2 remains for old
  closure evidence, while v3 owns new prose-first plans;
* `CODES` — the closed registry. A code carries its stage, severity, owner,
  form control and a summary. `owner` comes from here and ONLY from here;
* `Diagnostic` / `Report` — the `nxd-diagnostic-v2` record and its envelope;
* `canonicalize()` / `spec_hash()` / `emit()` — `nxd-dp-spec-canon-v2`, the
  semantic hash of a spec. Whitespace, comments and key order do not move it;
  a semantic value or list order does; lifecycle status is excluded from the
  approval binding.
* the lock writer/verifier (`dp-blueprint.lock.json`) and the build record
  (`build-record.json`), including INVARIANT-D2 and the materialization
  predicate.

Two rules worth stating up front, because everything else follows from them:

1. A compiler does not edit your source to make the build pass. The self-heal
   loop may change generated code; it may never change the IR. INVARIANT-D2
   below is that rule made mechanical.
2. Classification fails closed. When the evidence does not settle whether a
   failure is environmental, it is not environmental. Misclassifying a real bug
   as "the environment" is what ships a broken data product flagged green.

CONSTRAINT: the installed `self_check.py` is copied INTO a closure and run
there. It can never import this module. It inlines a literal vocabulary instead, and
`evals/tests/test_self_check_diagnostic_vocab.py` asserts that vocabulary is a
subset of `CODES`.

Usage:
    python3 scripts/dp_diagnostics.py hash         <spec.md>
    python3 scripts/dp_diagnostics.py canonicalize <spec.md>
    python3 scripts/dp_diagnostics.py emit         <canonical.json>
    python3 scripts/dp_diagnostics.py schema       [--json|--diagnostic|--record|--lock]
    python3 scripts/dp_diagnostics.py lock write   <spec.md> <closure-dir> [--proposal <proposal.json>]
    python3 scripts/dp_diagnostics.py lock verify  <closure-dir> [--spec <spec.md>]
    python3 scripts/dp_diagnostics.py record init   --record <path> --lock <path>
    python3 scripts/dp_diagnostics.py record append --record <path> --stage <id> --from <report.json>
    python3 scripts/dp_diagnostics.py record query  --record <path> [filters]
    python3 scripts/dp_diagnostics.py materialized  --record <path> [--lock <path>] [--spec <path>]

Exit codes:
    0  ok
    1  findings — the thing asked about is not clean
    2  could not read, parse, or satisfy a precondition
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import dp_spec_authoring as _v3
import dp_spec_v2 as _v2


# ---------------------------------------------------------------------------
# Spec vocabularies — one definition, two consumers (§8.2)
# ---------------------------------------------------------------------------

SPEC_VERSION = 2
SUPPORTED_SPEC_VERSIONS = (2, 3)

REQUIRED_FRONTMATTER = ("dp_spec_version", "name", "workflow", "status")
STATUS_VALUES = ("draft", "proposed", "approved")

REQUIRED_SECTIONS = (
    "intent", "questions", "scope", "inputs", "models", "transform",
    "outputs", "delivery", "decisions", "open_questions",
)
KNOWN_SECTIONS = REQUIRED_SECTIONS

DISPOSITIONS = ("blocked", "deferred", "answered")

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# A credential VALUE smuggled into a shareable file. Key names are fine and
# expected; anything that looks like a populated secret is not. Every producer
# runs `message` and `evidence` through this before emitting — a check that
# prints the secret it found turns a contained file leak into a transcript leak.
CREDENTIAL_VALUE_RE = re.compile(
    r"\b(password|passwd|secret|api[_-]?key|token|bearer|private[_-]?key)\b\s*[:=]\s*\S+",
    re.IGNORECASE,
)
CREDENTIAL_URL_RE = re.compile(
    r"\b([a-z][a-z0-9+.-]*://[^:\s@/]+:)([^@\s]+)(@)",
    re.IGNORECASE,
)
CREDENTIAL_KEY_RE = re.compile(
    r"(?:^|[_-])(?:password|passwd|secret|api[_-]?key|token|bearer|private[_-]?key)$",
    re.IGNORECASE,
)
CREDENTIAL_PLACEHOLDERS = frozenset(
    {"null", "none", "~", "<redacted>", "redacted", "[]", "{}", "''", '""', "..."}
)

# ---------------------------------------------------------------------------
# Diagnostic vocabularies (§1)
# ---------------------------------------------------------------------------

DIAGNOSTIC_SCHEMA_ID = "nxd-diagnostic-v2"
REPORT_SCHEMA_ID = "nxd-diagnostic-report-v2"
V3_REPORT_SCHEMA_ID = "nxd-diagnostic-report-v3"
BUILD_RECORD_SCHEMA_ID = "nxd-build-record-v2"
LOCK_SCHEMA_ID = "nxd-dp-spec-lock-v2"
CANONICALIZATION = "nxd-dp-spec-canon-v2"
SPEC_SCHEMA_ID = "nxd-dp-spec-schema-v2"
SPEC_DIAGNOSTIC_SCHEMA_ID = _v2.SPEC_DIAGNOSTIC_SCHEMA_ID
V3_LOCK_SCHEMA_ID = "nxd-dp-spec-lock-v3"

# Ordered. The `s<N>_` prefix makes the ordinal recoverable by int(stage[1])
# and makes a lexicographic sort equal pipeline order.
STAGES = (
    "s0_spec",
    "s1_structure",
    "s2_transform",
    "s3_closure",
    "s4_pin",
    "s5_serve",
    "s6_run",
    "s7_publish",
    "s8_answer",
)

# Stages 0-3 touch no kernel, no network and no supervisor. A failure there is
# NEVER environmental. This is not a heuristic.
OFFLINE_STAGES = frozenset({"s0_spec", "s1_structure", "s2_transform", "s3_closure"})

SEVERITIES = ("error", "warning", "info")
_SEVERITY_RANK = {"error": 2, "warning": 1, "info": 0}

OWNERS = ("agent", "user", "environment")

ORIGINS = (
    "agent_observed",
    "supervisor_reported",
    "tool_computed",
    "llm_authored",
    "unbound",
)

CONTROLS = (
    "text",
    "long_text",
    "number",
    "enum",
    "list",
    "mapping",
    "table",
    "confirm",
    "none",
)

STAGE_STATUSES = (
    "passed",
    "passed_with_warnings",
    "failed",
    "not_reached",
    "skipped",
)
# `skipped` is only ever legal for these two — nothing else may be skipped.
SKIPPABLE_STAGES = ("s7_publish", "s8_answer")

ATTEMPT_KINDS = ("generate", "regenerate", "remap", "heal", "retry")
ATTEMPT_EXITS = (
    "healed",
    "healed_with_concessions",
    "caps_exhausted",
    "blocked",
    "retry_environmental",
)
REVIEW_STATUSES = ("complete", "timed_out", "needs_user")
REVIEW_DISPOSITIONS = ("accepted", "rejected", "out_of_scope")
REVIEW_CLASSIFICATIONS = ("behavior_affecting", "structural_note")
REVIEW_FINDING_STATES = ("not_applied", "needs_user", "applied")
# The kinds INVARIANT-D2 binds: a heal that moved the spec hash edited the IR
# to make the build pass. `regenerate` is the one kind allowed to move it.
HASH_FROZEN_KINDS = ("heal", "retry", "remap")

CODE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,2}$")

# Which stages a report of each `tool` may carry (§1.8). `loop` is the agent,
# hand-constructing a report from tool results — visibly weaker evidence than a
# script's, and named honestly so a reader can tell.
REPORT_TOOLS = {
    "validate_dp_spec": ("s0_spec",),
    "self_check": ("s1_structure", "s2_transform", "s3_closure"),
    "dp_diagnostics": STAGES,
    "loop": ("s4_pin", "s5_serve", "s6_run", "s7_publish", "s8_answer"),
}

DEFAULT_CAPS = {
    "remap_per_question": 2,
    "regenerate_total": 3,
    "retry_environmental_total": 3,
}

CLOSURE_SNAPSHOT = "dp-blueprint.approved.md"
CLOSURE_LOCK = "dp-blueprint.lock.json"
CLOSURE_BUILD_RECORD = "build-record.json"
CLOSURE_README = "README.md"

# The pre-0.38.0 spelling, when these artifacts were named after the dp-spec
# rather than the blueprint. Only the LOCK needs a legacy name here: the
# snapshot and proposal filenames travel inside the lock, as `snapshot` and
# `proposal_snapshot`, so once the lock is found a legacy closure resolves the
# rest of itself from its own contents. `source_basename` is not that mechanism
# — it is write-only provenance and is never dereferenced.
LEGACY_CLOSURE_LOCK = "dp-spec.lock.json"


LEGACY_CLOSURE_ARTIFACTS = (
    "dp-spec.approved.md",
    "dp-spec.lock.json",
    "dp-spec.proposal.approved.json",
)


def _drop_superseded_legacy_artifacts(closure: Path) -> list[str]:
    """Remove the pre-v0.38.0 pair a fresh lock write has just superseded.

    Writing into an existing legacy closure otherwise leaves TWO approved plans
    with different hashes and two locks. Every reader prefers the current names,
    so the stale pair is never looked at again and never reported — but a cold
    reader opening the closure finds two answers to "what was this supposed to
    build", which is the single failure mode the byte-copy discipline exists to
    remove. These are generated artifacts and the copy that replaces them was
    just written from the same approved spec.
    """
    dropped = []
    for name in LEGACY_CLOSURE_ARTIFACTS:
        stale = closure / name
        if stale.is_file():
            stale.unlink()
            dropped.append(name)
    return dropped


def resolve_lock_path(lock_path: Path) -> Path:
    """A caller-supplied --lock path, with the same legacy fallback.

    The resume playbook hands the agent a literal
    `<closure>/dp-blueprint.lock.json`, so an explicit path has to tolerate a
    pre-v0.38.0 closure exactly as the directory form does. Only substitutes
    when the given name is the current one and is absent; any other path is
    left alone so a genuine typo still reports itself.
    """
    if lock_path.is_file() or lock_path.name != CLOSURE_LOCK:
        return lock_path
    legacy = lock_path.with_name(LEGACY_CLOSURE_LOCK)
    return legacy if legacy.is_file() else lock_path


def resolve_closure_lock(closure: Path) -> Path:
    """The closure's lock, preferring the current name over the legacy one.

    Returns the CURRENT-name path when neither exists, so a closure that is
    simply missing its lock still reports the name a fresh closure should have
    rather than advertising the retired one.
    """
    current = closure / CLOSURE_LOCK
    if current.is_file():
        return current
    legacy = closure / LEGACY_CLOSURE_LOCK
    return legacy if legacy.is_file() else current


# ---------------------------------------------------------------------------
# The code registry (§1.5)
# ---------------------------------------------------------------------------

CODES: dict[str, dict] = {}


def _register(
    code: str,
    *,
    stage: str,
    severity: str,
    owner: str,
    control: str = "none",
    agent_fillable: bool = False,
    summary: str = "",
    stages: tuple[str, ...] | None = None,
) -> None:
    """Add one code to the registry.

    `stage` is where the registry files the code — the earliest stage that may
    legitimately produce it. `stages` lists every stage that may, for the codes
    that genuinely span the ladder (`env.`, `blocker.`, `heal.`, `meta.`).
    """
    if code in CODES:
        raise ValueError(f"duplicate code in registry: {code}")
    if not CODE_RE.match(code):
        raise ValueError(f"code {code!r} does not match the grammar")
    if stage not in STAGES:
        raise ValueError(f"code {code!r} has unknown stage {stage!r}")
    if severity not in SEVERITIES:
        raise ValueError(f"code {code!r} has unknown severity {severity!r}")
    if owner not in OWNERS:
        raise ValueError(f"code {code!r} has unknown owner {owner!r}")
    if control not in CONTROLS:
        raise ValueError(f"code {code!r} has unknown control {control!r}")
    CODES[code] = {
        "stage": stage,
        "severity": severity,
        "owner": owner,
        "control": control,
        "agent_fillable": bool(agent_fillable),
        "summary": summary,
        "stages": tuple(stages) if stages else (stage,),
    }


def _register_table(stage: str, rows: Iterable[tuple], **defaults) -> None:
    for row in rows:
        code, severity, owner, control, fillable, summary = row
        _register(
            code,
            stage=stage,
            severity=severity,
            owner=owner,
            control=control,
            agent_fillable=fillable,
            summary=summary,
            **defaults,
        )


# --- domain `spec.` — stage s0_spec, produced by validate_dp_spec.py ---------
# Every code here is produced by at least one check in validate_dp_spec.py, and
# every `spec.*` code that file can emit appears here. The scope is deliberate:
# that file also emits `v2.*` and `v3.*` codes, and no code in either vocabulary
# belongs in this registry. Those are self-describing — a `ValidationIssue`
# carries its own `owner` and `control` — so a consumer reads them off the
# diagnostic rather than resolving them here.
#
# The second direction is the one that had rotted. A comment here used to claim
# `test_validator_code_coverage.py` enforced both; it exercises the `v2.*`
# field-addressed vocabulary instead, so nothing noticed that
# `spec.parse.invalid` and `spec.frontmatter.unsupported_version` — the two most
# reachable outcomes of the validator — shipped unregistered. `Report.error`
# rejects an unknown code, so they only escaped because `validate_dp_spec.py`
# hand-builds its envelopes and bypasses that check; a consumer resolving
# `owner`/`control`/`summary` from the registry hit a `KeyError` on the common
# failure. Both are registered now, and
# `test_validate_dp_spec_emits_only_registered_codes` enforces the direction the
# stale comment claimed. `spec.encoding.not_utf8` is an exemption the other way:
# it is raised at the file-read boundary, before a Report exists.
_register_table(
    "s0_spec",
    (
        ("spec.encoding.not_utf8", "error", "agent", "none", False,
         "the file is not valid UTF-8"),
        ("spec.parse.invalid", "error", "user", "text", False,
         "the document could not be read or split into frontmatter and body"),
        ("spec.frontmatter.unsupported_version", "error", "user", "enum", False,
         "dp_spec_version names a generation this boundary does not parse"),
        ("spec.proposal.unsupported", "error", "agent", "none", False,
         "--proposal is a v3 input; the source is not a v3 spec"),
        ("spec.v2.invalid", "error", "agent", "none", False,
         "the v2 validator found a field-addressed spec error"),
        ("spec.frontmatter.unparseable", "error", "agent", "none", True,
         "frontmatter missing, unterminated, not parseable as YAML, or not a "
         "mapping"),
        ("spec.frontmatter.missing_key", "error", "agent", "text", True,
         "a required frontmatter key is absent"),
        ("spec.frontmatter.bad_version", "error", "agent", "number", True,
         "dp_spec_version is not the supported version"),
        ("spec.frontmatter.bad_name", "error", "agent", "text", True,
         "name is not lowercase snake_case"),
        ("spec.frontmatter.bad_status", "error", "user", "enum", False,
         "status is outside draft|proposed|approved"),
        ("spec.frontmatter.approved_with_errors", "error", "user", "confirm", False,
         "status is approved while the spec has errors"),
        ("spec.frontmatter.rubric_version_missing", "error", "user", "text", False,
         "judgments are present but rubric_version is unset"),
        ("spec.section.missing", "error", "agent", "none", True,
         "a required '## section' is absent"),
        ("spec.section.empty", "error", "agent", "none", True,
         "a required '## section' is empty"),
        ("spec.section.unknown", "warning", "agent", "none", False,
         "an unknown '## heading' compiles to nothing"),
        ("spec.section.unparseable", "error", "agent", "long_text", False,
         "a section body is not parseable as YAML"),
        ("spec.source.no_entries", "error", "agent", "table", True,
         "no source entries"),
        ("spec.source.not_mapping", "error", "agent", "mapping", True,
         "a source entry is not a mapping"),
        ("spec.source.bad_type", "error", "agent", "enum", True,
         "source type is outside the vocabulary"),
        ("spec.source.no_location", "error", "user", "text", False,
         "a source has no location"),
        ("spec.source.no_scope", "error", "user", "long_text", False,
         "a source has no scope — the population filter must be stated"),
        ("spec.source.credential_value", "error", "user", "none", False,
         "a credential VALUE is in the file"),
        ("spec.source.credential_key_mapping", "error", "user", "none", False,
         "credential_keys carries a mapping, so a value is in the file"),
        ("spec.source.label_missing", "error", "agent", "text", True,
         "2+ sources and a source has no label"),
        ("spec.source.label_duplicate", "error", "agent", "text", True,
         "two sources share a label"),
        ("spec.population.not_mapping", "error", "agent", "mapping", True,
         "population is not a mapping"),
        ("spec.population.prose", "warning", "agent", "mapping", True,
         "population is prose, so the sample rule cannot land as a decision"),
        ("spec.population.missing", "error", "user", "long_text", False,
         "no population: the full row set is unstated"),
        ("spec.model.no_entries", "error", "agent", "table", True,
         "no model entries"),
        ("spec.model.no_name", "error", "agent", "text", True,
         "a model has no name"),
        ("spec.model.bad_name", "error", "agent", "text", True,
         "a model name is not lowercase snake_case"),
        ("spec.model.bad_kind", "error", "agent", "enum", True,
         "model kind is outside the vocabulary"),
        ("spec.model.no_description", "error", "agent", "long_text", True,
         "a model has no description"),
        ("spec.model.no_grain", "error", "user", "long_text", False,
         "a promised model has no grain"),
        ("spec.model.no_key", "error", "user", "list", False,
         "a promised model has no key"),
        ("spec.model.duplicate_name", "error", "agent", "text", True,
         "two models share a name"),
        ("spec.model.unmotivated", "warning", "agent", "list", True,
         "a derived model names no question in answers"),
        ("spec.contract.not_mapping", "error", "agent", "mapping", True,
         "a contract entry is not a mapping"),
        ("spec.contract.no_name", "error", "agent", "text", True,
         "a contract has no name"),
        ("spec.contract.bad_name", "error", "agent", "text", True,
         "a contract name is not lowercase-hyphenated"),
        ("spec.contract.duplicate_name", "error", "agent", "text", True,
         "two contracts share a name — the name selects the verifier file"),
        ("spec.contract.no_authority", "error", "agent", "enum", True,
         "a contract does not say whether the user stated it or the agent "
         "inferred it"),
        ("spec.contract.bad_authority", "error", "agent", "enum", True,
         "contract authority is outside the vocabulary"),
        ("spec.contract.inferred_in_spec", "error", "agent", "enum", True,
         "an inferred constraint is claimed as a spec contract — it belongs in "
         "models.py and an ordinary .promise(model)"),
        ("spec.contract.no_guarantee", "error", "user", "long_text", False,
         "a contract has no guarantee in the user's own words"),
        ("spec.contract.no_rule", "error", "user", "long_text", False,
         "a contract has no executable rule"),
        ("spec.contract.no_model", "error", "agent", "text", True,
         "a contract names no model"),
        ("spec.contract.unknown_model", "error", "agent", "text", True,
         "a contract names a model no models entry declares"),
        ("spec.contract.bad_phase", "error", "agent", "enum", True,
         "contract phase is outside the vocabulary"),
        ("spec.contract.wrong_phase", "error", "agent", "enum", True,
         "an expectation is not pre_transform, or a promise is not "
         "post_transform"),
        ("spec.contract.unbound_threshold", "error", "user", "text", False,
         "a contract rule names a threshold the spec never fixes"),
        ("spec.gate.not_mapping", "error", "agent", "mapping", True,
         "a gate entry is not a mapping"),
        ("spec.gate.no_rule", "error", "user", "long_text", False,
         "a gate has no rule"),
        ("spec.gate.no_unknown", "error", "user", "enum", False,
         "a gate has no unknown-handling rule"),
        ("spec.gate.unknown_is_fail", "error", "user", "enum", False,
         "a gate maps an absent input to FAIL — absence is never a judgement"),
        ("spec.criteria.no_entries", "error", "agent", "table", True,
         "criteria section present with no entries"),
        ("spec.criteria.no_weight", "error", "user", "number", False,
         "a criterion has no weight"),
        ("spec.criteria.bad_weight", "error", "user", "number", False,
         "a criterion weight is not a number"),
        ("spec.criteria.weights_unbalanced", "error", "user", "table", False,
         "criterion weights do not sum to 1.0"),
        ("spec.criteria.no_scale", "error", "user", "mapping", False,
         "a criterion has no scale with min and max"),
        ("spec.criteria.bad_scale", "error", "user", "mapping", False,
         "a criterion scale is non-integer or min is not below max"),
        ("spec.criteria.no_anchors", "error", "agent", "mapping", True,
         "a criterion scale has no anchors"),
        ("spec.criteria.incomplete_scale", "error", "agent", "mapping", True,
         "a scale level has no anchor — reads as complete, is unexecutable"),
        ("spec.criteria.anchor_out_of_range", "error", "agent", "mapping", True,
         "an anchor sits outside the scale"),
        ("spec.criteria.bad_provenance", "error", "agent", "enum", True,
         "criterion provenance is outside the vocabulary"),
        ("spec.verdict.missing", "error", "agent", "table", True,
         "criteria are present and verdicts are absent"),
        ("spec.verdict.not_mapping", "error", "agent", "mapping", True,
         "verdicts is not a mapping"),
        ("spec.verdict.no_values", "error", "user", "list", False,
         "verdicts has no values — the verdict vocabulary"),
        ("spec.verdict.band_no_verdict", "error", "agent", "text", True,
         "a band names no verdict"),
        ("spec.verdict.band_unknown_verdict", "error", "agent", "enum", True,
         "a band names a verdict outside the declared values"),
        ("spec.verdict.band_unreachable", "error", "user", "mapping", False,
         "a band has neither min_score nor rule"),
        ("spec.verdict.value_unreached", "error", "user", "table", False,
         "a declared verdict no band reaches"),
        ("spec.verdict.no_precedence", "error", "user", "long_text", False,
         "gates and bands both in play with no precedence rule"),
        ("spec.judgment.no_entries", "error", "agent", "table", True,
         "judgments section present with no entries"),
        ("spec.judgment.no_model", "error", "agent", "text", True,
         "a judgment names no model"),
        ("spec.judgment.bad_produced_by", "error", "agent", "enum", True,
         "produced_by is outside the vocabulary"),
        ("spec.judgment.no_generator_model", "error", "agent", "text", True,
         "an agent-produced judgment names no generator_model"),
        ("spec.judgment.no_rubric_version", "error", "agent", "text", True,
         "a judgment has no rubric_version"),
        ("spec.judgment.bad_reruns", "error", "agent", "enum", True,
         "reruns is outside the vocabulary"),
        ("spec.judgment.evidence_disabled", "error", "user", "confirm", False,
         "evidence_required: false — an uncited judgement is unreviewable"),
        ("spec.schedule.not_mapping", "error", "agent", "mapping", True,
         "schedule is not a mapping"),
        ("spec.schedule.bad_trigger", "error", "agent", "enum", True,
         "schedule trigger is outside the vocabulary"),
        ("spec.schedule.no_cron", "error", "user", "text", False,
         "trigger is cron with no cron expression"),
        ("spec.schedule.no_cursor_field", "error", "user", "text", False,
         "incremental with no cursor_field — the duplicate-rows bug"),
        ("spec.schedule.regrain_not_append_safe", "warning", "user", "confirm", False,
         "incremental over a model that reads as an aggregate or regrain"),
        ("spec.output.not_mapping", "error", "agent", "mapping", True,
         "an output entry is not a mapping"),
        ("spec.output.no_name", "error", "agent", "text", True,
         "an output has no name"),
        ("spec.output.unknown_model", "error", "agent", "enum", True,
         "an output names an undeclared model"),
        ("spec.output.bad_kind", "error", "agent", "enum", True,
         "output kind is outside the vocabulary"),
        ("spec.decision.no_id", "error", "agent", "text", True,
         "a decision row has no decision_id"),
        ("spec.decision.bad_status", "error", "agent", "enum", True,
         "decision status is outside the vocabulary"),
        ("spec.decision.bad_provenance", "error", "agent", "enum", True,
         "decision provenance is outside the vocabulary"),
        ("spec.decision.no_ruling", "error", "user", "long_text", False,
         "a decision row has no ruling"),
        ("spec.decision.blocked_with_applies_to", "error", "agent", "none", True,
         "a blocked ruling sets applies_to but materializes nothing"),
        ("spec.decision.no_applies_to", "error", "agent", "list", True,
         "a decision row names no models or columns it materializes in"),
        ("spec.decision.duplicate_id", "error", "agent", "text", True,
         "two decision rows share a decision_id"),
        ("spec.decision.missing_for_ruling", "error", "agent", "table", True,
         "the spec encodes rulings and carries no ledger"),
        ("spec.decision.ruling_uncovered", "error", "agent", "table", True,
         "no ledger row mentions a ruling-bearing section"),
        ("spec.decision.sample_rule_unrecorded", "error", "agent", "table", True,
         "the population is sampled and no ledger row records the rule"),
        ("spec.open_question.not_mapping", "error", "agent", "mapping", True,
         "an open_questions entry is not a mapping"),
        ("spec.open_question.no_question", "error", "agent", "long_text", True,
         "an open_questions entry has no question"),
        ("spec.open_question.bad_disposition", "error", "agent", "enum", True,
         "disposition is outside blocked|deferred|answered"),
        ("spec.open_question.answered_without_decision", "warning", "agent", "table", True,
         "an answered question left no ruling behind"),
        ("spec.question.unanswered", "warning", "agent", "list", True,
         "a question no model answers"),
        ("spec.approval.agent_authored_at_approved", "warning", "user", "confirm", False,
         "agent_authored criteria at status: approved"),
        ("spec.prefill.empty_required_field", "warning", "agent", "none", True,
         "a required field is present but blank — pre-fill it or carry it as an "
         "open question; a blank is illegal in a rendered form"),
    ),
)

# The historical registry table above is retained only as a source-compatible
# fixture during the v2 cutover. Runtime support is deliberately narrowed to
# diagnostics emitted by the v2 parser/lock boundary; the removed source,
# criteria, contract, verdict, judgment, schedule, and population codes are
# not valid v2 diagnostics.
_V2_PIPELINE_SPEC_CODES = frozenset({
    "spec.encoding.not_utf8",
    "spec.frontmatter.unsupported_version",
    "spec.parse.invalid",
    "spec.proposal.unsupported",
    "spec.v2.invalid",
    "spec.frontmatter.unparseable",
    "spec.frontmatter.missing_key",
    "spec.frontmatter.bad_version",
    "spec.frontmatter.bad_name",
    "spec.frontmatter.bad_status",
    "spec.section.missing",
    "spec.section.empty",
    "spec.section.unknown",
    "spec.section.unparseable",
})
for _code in tuple(CODES):
    if _code.startswith("spec.") and _code not in _V2_PIPELINE_SPEC_CODES:
        del CODES[_code]

# --- domain `struct.` — stage s1_structure (Phase A) -------------------------
for _code in (
    "struct.import_not_public_dsl",
    "struct.model_name_not_literal",
    "struct.model_no_description",
    "struct.view_empty_schema",
    "struct.view_field_not_metric_field",
    "struct.unknown_dtype",
    "struct.unknown_agg",
    "struct.agg_expression_forbidden",
    "struct.metric_in_model",
    "struct.metric_first_arg_not_agg",
    "struct.bad_kwarg",
    "struct.join_to_model_kwarg",
    "struct.primary_key_takes_no_args",
    "struct.metric_of_and_column",
    "struct.description_unreachable",
    "struct.role_no_description",
    "struct.join_target_missing",
    "struct.no_primary_key",
    "struct.semantic_tools_forbidden",
    "struct.bad_infra_profile",
    "struct.bad_script_path",
    "struct.missing_call",
    "struct.port_not_duckdb",
    "struct.port_no_storage",
    "struct.promise_of_view",
    "struct.malformed_service_ref",
    "struct.naming_invariant_promised_vs_models",
    "struct.naming_invariant_promised_vs_physical",
    "struct.base_models_vs_data_dirs",
    "struct.optional_models_invalid",
    "struct.optional_model_promised",
    "struct.optional_model_not_registered",
):
    _register(
        _code,
        stage="s1_structure",
        severity="error",
        owner="agent",
        summary="Phase A structural check failed",
    )

# Phase A's own declared blind spot, one diagnostic per `unverified:` line. It
# is `info` because it is data, not judgement — but it belongs in the record
# rather than in a scrollback.
_register(
    "struct.unverified",
    stage="s1_structure",
    severity="info",
    owner="agent",
    summary="Phase A could not verify a dynamic construct — its declared blind spot",
)
_register(
    "struct.key_not_groupable",
    stage="s1_structure",
    severity="warning",
    owner="agent",
    summary="A key field carries only primary_key() or only join() — not groupable, so no query can name the entity",
)
_register(
    "struct.model_not_queryable",
    stage="s1_structure",
    severity="warning",
    owner="agent",
    summary="A promised model backs no semantic_view, so no metric reaches it and it cannot be selected",
)

# --- domain `reach.` — stage s1_structure (Phase E) --------------------------
# The reach gate: a transform never imports a provider SDK, and reaches a model
# only through the sanctioned seam. Filed under s1_structure because it is
# static, offline and must DECIDE before Phase B imports the transform — a
# verdict delivered after the socket is already open is a post-mortem, not a
# gate.
#
# owner: agent on all four. Every one is fixed by editing the closure — drop the
# import, or declare the service the import implies. None is a question for the
# user and none is environmental.
_register_table(
    "s1_structure",
    (
        ("reach.model_sdk_import", "error", "agent", "none", False,
         "transform/main.py or a contracts/ verifier imports a model-provider SDK directly — a packaged "
         "closure infers through nxd.experimental.field_mapper under a consent grant, not a raw SDK"),
        ("reach.undeclared_transport", "error", "agent", "none", False,
         "transform/main.py imports raw network transport but spec.py declares "
         "no network-shaped connector"),
        ("reach.connector_shape_mismatch", "error", "agent", "none", False,
         "an import contradicts the connector type spec.py declares — the "
         "closure reads from a source its own declaration does not name"),
    ),
)

# Not an error: an unreadable connector declaration is "this gate could not read
# the declaration", which is a different claim from "the declaration says no
# network". Denying every transport on the strength of a parse failure is a
# verdict the gate has not earned, so it warns, and the SDK denial — waived by
# nothing — still applies.
_register(
    "reach.connector_undeclared",
    stage="s1_structure",
    severity="warning",
    owner="agent",
    summary="Phase E could not read a service reference from spec.py, so the "
            "transport check was skipped rather than guessed",
)

# --- domain `grant.` — stage s1_structure (Phase G) --------------------------
# The consent gate: a closure that vendors the field-mapper harness maps only
# under a grant binding each mapper spec found in the closure. Statically it
# binds the specs on disk, never the path the transform hands `map_inputs` —
# see "What Phase G cannot see". Filed under s1_structure for
# Phase E's reason plus one of its own: Phase B EXECUTES the transform, and a
# mapper transform with an environment-resolvable key makes live model calls
# there. A consent verdict delivered after that is a report about consent
# already spent.
#
# The owner split is the unusual part. Three codes are owner: user, because
# consent is not a code defect — the agent cannot author a grant on the user's
# behalf, cannot decide that a drifted rubric is still acceptable, and cannot
# extend an expiry. The remaining four are agent-fixable closure defects.
_register_table(
    "s1_structure",
    (
        ("grant.missing", "error", "user", "confirm", False,
         "a transform module imports the field-mapper harness and a spec is present, "
         "but contracts/ carries no consent grant"),
        ("grant.spec_mismatch", "error", "user", "confirm", False,
         "no grant binds the hash of a spec found under contracts/, or the "
         "bound grant names different models — the rubric changed since "
         "consent was given"),
        ("grant.expired", "error", "user", "confirm", False,
         "the grant binding this spec is past its expires_at — consent lapsed"),
        ("grant.invalid", "error", "agent", "none", False,
         "a grant does not parse, is rejected by the harness, or carries the "
         "'<derived>' fixture placeholder instead of a real spec hash"),
        ("grant.vendored_harness", "error", "agent", "none", False,
         "a module imports a copy of the field-mapper harness vendored into "
         "the closure root — the pre-package contract, which no longer runs on "
         "the platform and answers for its own spec hash"),
        ("grant.spec_unreadable", "error", "agent", "none", False,
         "the transform maps but no spec JSON was found under contracts/, or "
         "the harness could not compute its bound mapper_spec_id"),
        ("grant.ungated_map", "error", "agent", "none", False,
         "the transform imports the harness but never references map_inputs — "
         "the only entry point that checks the grant before reading source "
         "content or resolving a key"),
        ("grant.verifier_maps", "error", "agent", "none", False,
         "a contracts/ verifier imports the field-mapper harness — a verifier "
         "that re-decides pass/fail via a model is not a check, and no grant "
         "authorizes mapping there"),
    ),
)

# Not an error: a grant binding no spec in this closure authorizes nothing and
# fails nothing. It warns because a stale consent artifact left on disk is what
# a later reader mistakes for coverage it does not provide.
_register(
    "grant.unbound",
    stage="s1_structure",
    severity="warning",
    owner="agent",
    summary="a consent grant in contracts/ binds no spec in this closure — "
            "stale consent left behind",
)

# --- domain `runtime.` — stages s2_transform, s6_run -------------------------
_register_table(
    "s2_transform",
    (
        ("runtime.import_failed", "error", "agent", "none", False,
         "the transform module could not be imported"),
        ("runtime.transform_raised", "error", "agent", "none", False,
         "the transform raised while executing for real"),
        ("runtime.assert_failed", "error", "agent", "none", False,
         "a transform assert fired"),
        ("runtime.base_models_mismatch", "error", "agent", "none", False,
         "BASE_MODELS does not match what the transform yields"),
        ("runtime.transform_incomplete", "error", "agent", "none", False,
         "the transform did not run to completion"),
        ("runtime.model_table_missing", "error", "agent", "none", False,
         "a promised model produced no table"),
        ("runtime.state_unserializable", "error", "agent", "none", False,
         "transform_state could not cross the strict JSON persistence boundary"),
        ("runtime.state_not_persisted", "error", "agent", "none", False,
         "a stateful transform landed rows without persisting a cursor"),
        ("runtime.state_flat_write", "error", "agent", "none", False,
         "a multi-model transform wrote state through the unbound flat handle"),
        ("runtime.rerun_row_count_changed", "error", "agent", "none", False,
         "an unchanged stateful rerun changed table materialization or row counts"),
        ("runtime.row_count", "info", "agent", "none", False,
         "row count for one model in the Phase B scratch database"),
        ("runtime.dry_run_not_runnable", "info", "agent", "none", False,
         "the offline dry run could not execute because the closure reads a "
         "credential from secrets; phase B reports nothing about the transform"),
    ),
)
_register_table(
    "s6_run",
    (
        ("runtime.remote_assert_failed", "error", "agent", "none", False,
         "an assert fired on the supervisor"),
        ("runtime.remote_traceback", "error", "agent", "none", False,
         "the transform raised on the supervisor"),
    ),
)

# --- domains `closure.` and `policy.` — stage s3_closure (Phases C, D) -------
_register_table(
    "s3_closure",
    (
        ("closure.spec_snapshot_missing", "error", "agent", "none", False,
         "dp-blueprint.approved.md is missing from the closure root"),
        ("closure.lock_missing", "error", "agent", "none", False,
         "dp-blueprint.lock.json is missing"),
        ("closure.lock_unparseable", "error", "agent", "none", False,
         "dp-blueprint.lock.json does not parse or carries the wrong schema"),
        ("closure.lock_snapshot_byte_mismatch", "error", "agent", "none", False,
         "the snapshot's bytes do not match lock.snapshot_sha256 — edited after copy"),
        ("closure.spec_hash_mismatch", "error", "agent", "none", False,
         "the snapshot's canonical hash does not match lock.spec_hash"),
        ("closure.terms_hash_mismatch", "error", "agent", "none", False,
         "the v3 Terms inventory does not match lock.terms_hash"),
        ("closure.contract_inventory_hash_mismatch", "error", "agent", "none", False,
         "the v3 compiled contract inventory does not match lock.contract_inventory_hash"),
        ("closure.decision_inventory_mismatch", "error", "agent", "none", False,
         "the v3 proposal does not carry the settled locked-decision inventory bound by the proposal hash"),
        ("closure.live_spec_diverged", "error", "agent", "none", False,
         "the live IR's canonical hash has moved away from lock.spec_hash"),
        ("closure.live_spec_unparseable", "error", "agent", "none", False,
         "the live IR cannot be canonicalized, so no hash comparison is possible"),
        ("closure.lock_status_not_approved", "error", "user", "confirm", False,
         "the snapshot was copied from a spec that was not approved"),
        ("closure.contract_phase_unsupported", "error", "user", "confirm", False,
         "approved pre_transform input expectations cannot execute: this "
         "runtime runs them only for a declared CSV source-aligned input"),
        ("closure.build_record_missing", "error", "agent", "none", False,
         "build-record.json is missing"),
        ("closure.build_record_invalid", "error", "agent", "none", False,
         "build-record.json does not parse or does not validate"),
        ("closure.build_record_hash_mismatch", "error", "agent", "none", False,
         "build-record.compiled_from does not match lock.spec_hash"),
        ("closure.build_record_merge_failed", "error", "agent", "none", False,
         "self-check could not merge build-record.json"),
        ("closure.readme_missing", "error", "agent", "none", False,
         "README.md — the reopen recipe — is missing"),
        ("closure.escaping_reference", "error", "agent", "none", False,
         "a closure file points outside the closure"),
        ("closure.gitignore_missing", "error", "agent", "none", False,
         ".gitignore is missing while a source carries live credentials"),
        ("closure.sensitive_missing", "error", "agent", "none", False,
         "SENSITIVE is missing while a source carries live credentials"),
        ("closure.gitignore_not_naming_profile", "error", "agent", "none", False,
         ".gitignore does not name infra-profile.yaml"),
        ("closure.canonical_hash_deferred", "info", "agent", "none", False,
         "the byte check ran here; the canonical hash check is `lock verify`"),
        ("closure.legacy_artifact_superseded", "info", "agent", "none", False,
         "a pre-v0.38.0 dp-spec.* artifact was replaced by its dp-blueprint.* "
         "equivalent and removed"),
        ("closure.contract_not_wired", "error", "agent", "none", False,
         "a custom contract is not attached to an input or output declaration"),
        ("closure.contract_verifier_missing", "error", "agent", "none", False,
         "a custom contract's verifier file does not exist"),
        ("closure.contract_verifier_malformed", "error", "agent", "none", False,
         "a verifier cannot be read as UTF-8, does not parse, is async, or has "
         "no on_verify/main guard"),
        ("closure.contract_verifier_inert", "error", "agent", "none", False,
         "a verifier can never fail — decorative, not executable"),
        ("closure.contract_verifier_unreferenced", "error", "agent", "none", False,
         "a file under contracts/ is not referenced by spec.py"),
        ("closure.contract_verifier_secret", "error", "agent", "none", False,
         "a verifier carries a literal secret assignment"),
        ("closure.contract_duplicate_name", "error", "agent", "none", False,
         "two custom contracts share a name — they race for one verifier file"),
        ("closure.contract_inventory_mismatch", "error", "agent", "none", False,
         "wired custom contracts do not match the approved v2 contract inventory"),
        # The desktop runtime-binding faults below are NOT contract-wiring
        # faults. They were folded into closure.contract_not_wired at first,
        # which made a harness render a contract-shaped repair control for an
        # infra-profile driver typo. The code is what selects that control, so
        # each error class gets its own.
        ("closure.profile_service_missing", "error", "agent", "none", False,
         "infra-profile.yaml omits a service spec.py references"),
        ("closure.profile_driver_mismatch", "error", "agent", "none", False,
         "an infra-profile service is bound to the wrong driver"),
        ("closure.profile_name_mismatch", "error", "agent", "none", False,
         "infra-profile.yaml metadata.name is not desktop-local"),
        ("closure.port_storage_mismatch", "error", "agent", "none", False,
         "the DuckDB output port is bound to the wrong storage service"),
        ("closure.input_service_mismatch", "error", "agent", "none", False,
         "a source-aligned input is not bound to the unlabeled csv-source"),
        ("closure.csv_root_invalid", "error", "agent", "none", False,
         "csv-source-path is missing, absolute, or escapes the closure"),
        ("closure.model_path_unresolved", "error", "agent", "none", False,
         "a model_paths entry is unsafe or resolves to no CSV"),
        ("policy.decisions_not_base_model", "error", "agent", "none", False,
         "nxd_decisions is not a base model"),
        ("policy.decisions_csv_missing", "error", "agent", "none", False,
         "the nxd_decisions CSV is missing"),
        ("policy.decisions_column_missing", "error", "agent", "none", False,
         "an nxd_decisions column is missing"),
        ("policy.decisions_value_out_of_vocab", "error", "agent", "none", False,
         "an nxd_decisions value is outside the vocabulary"),
        ("policy.literal_duplicates_landed_value", "error", "agent", "none", False,
         "a policy literal duplicates a landed value"),
    ),
)

# --- domain `semantic.` — the stage-8 tells ---------------------------------
# The read-back runs against Phase B's scratch database, so it is emitted at
# s2_transform. It is nonetheless the stage-8 predictor: a uniform
# classification column is the tell that a green build will answer wrongly. It
# stays non-gating. Recording it is the change; failing on it is not.
_register_table(
    "s2_transform",
    (
        ("semantic.distribution", "info", "agent", "none", False,
         "distribution read-back for one classification column"),
        ("semantic.uniform_column", "warning", "agent", "none", False,
         "a classification column landed one value for every row"),
        ("semantic.absent_vocabulary", "warning", "agent", "none", False,
         "a declared vocabulary value never appears in the data"),
    ),
)
_register_table(
    "s8_answer",
    (
        ("semantic.query_error", "error", "agent", "none", False,
         "a query against the published product errored"),
        ("semantic.empty_result", "warning", "agent", "none", False,
         "a query returned nothing"),
        ("semantic.truncated", "info", "agent", "none", False,
         "a result was truncated"),
        ("semantic.wrong_answer", "warning", "agent", "none", False,
         "a green build answered wrongly"),
    ),
)

# --- domain `pin.` — stage s4_pin -------------------------------------------
# `pin.build_failed` is owner: agent BY CONSTRUCTION, not by evidence. Phase A
# cannot execute supervisor admission, so a closure can pass it in full and
# still fail when the supervisor pins it. Stage 4 masquerades as environment.
_register_table(
    "s4_pin",
    (
        ("pin.build_failed", "error", "agent", "none", False,
         "workflow admission returned an error — a code fault Phase A cannot see"),
        ("pin.spec_compile_error", "error", "agent", "none", False,
         "the supervisor could not compile the spec (origin unbound — inspect the "
         "workflow operation for the bounded diagnostic)"),
        ("pin.no_endpoint", "error", "agent", "none", False,
         "workflow admission returned no endpoint"),
    ),
)

# --- domain `env.` — stages s5_serve, s6_run, s7_publish --------------------
_ENV_STAGES = ("s5_serve", "s6_run", "s7_publish")
for _code, _summary in (
    ("env.supervisor_busy", "the supervisor is busy"),
    ("env.provision_failed", "provisioning failed"),
    ("env.dependency_install_failed", "a dependency would not install"),
    ("env.kernel_unavailable", "no kernel is available"),
    ("env.connection_refused", "the connection was refused"),
    ("env.not_ready_timeout", "the endpoint did not become ready"),
    ("env.remote_unreachable", "the remote is unreachable"),
):
    _register(
        _code,
        stage="s5_serve",
        stages=_ENV_STAGES,
        severity="error",
        owner="environment",
        summary=_summary,
    )

# The remedy is "supply a credential", so the USER must act and the user hears
# it. That makes it owner: user, not environment.
_register(
    "env.credential_missing",
    stage="s5_serve",
    stages=_ENV_STAGES,
    severity="error",
    owner="user",
    control="text",
    summary="a credential the run needs is not present",
)

# --- domain `publish.` — stage s7_publish -----------------------------------
_register_table(
    "s7_publish",
    (
        ("publish.artifact_unavailable", "error", "environment", "none", False,
         "the artifact is unavailable and this is not retryable"),
        ("publish.release_unreadable", "error", "environment", "none", False,
         "the release could not be read"),
        ("publish.workflow_not_found", "error", "environment", "none", False,
         "the workflow was not found"),
        ("publish.superseded", "warning", "environment", "none", False,
         "the publish was superseded and redirected"),
        ("publish.trust_not_verified", "error", "agent", "none", False,
         "verified.json does not report artifact_verified"),
        ("publish.resource_not_found", "error", "environment", "none", False,
         "a published resource was not found"),
    ),
)

# --- domain `blocker.` — always error / user --------------------------------
# The forbidden-invariant escalations and the user-input queue. A build-time
# blocker is an open_questions entry DISCOVERED LATE — there is no second
# mechanism, and writing it back un-approves the spec by design.
for _code, _summary in (
    ("blocker.forbidden_handwritten_yaml",
     "a generated YAML file was about to be hand-written"),
    ("blocker.forbidden_manual_watermark",
     "a durable watermark was about to be hand-rolled instead of transform_state"),
    ("blocker.forbidden_replace_disposition",
     "write_disposition=replace while yielding a delta"),
    ("blocker.forbidden_assert_restates_arithmetic",
     "an assert was about to be loosened into restating its own arithmetic"),
    ("blocker.spec_edit_required",
     "green is only reachable by changing the plan — that is a spec edit, not a heal"),
    ("blocker.open_question", "an open question blocks the build"),
    ("blocker.caps_exhausted", "the bound was reached; what was tried is reported"),
    ("blocker.credential_required", "the user must supply a credential"),
):
    _register(
        _code,
        stage="s0_spec",
        stages=STAGES,
        severity="error",
        owner="user",
        control="long_text",
        summary=_summary,
    )

# --- domain `concession.` — always warning / agent --------------------------
# A concession is green reached by doing something the skills discourage. There
# is no `forbidden` concession: a forbidden invariant is never taken, it is
# escalated as a blocker.
for _code, _summary in (
    ("concession.assert_weakened", "an assert was weakened"),
    ("concession.type_coerced", "a type was coerced"),
    ("concession.incidental_column_dropped", "an incidental column was dropped"),
    ("concession.sample_capped", "the sample was capped"),
    ("concession.dependency_repinned", "a dependency was repinned"),
    ("concession.derived_model_left_inert", "a derived model was left inert"),
    ("concession.unverified_construct_accepted",
     "a construct Phase A could not verify was accepted"),
    ("concession.readback_uniform_unexplained",
     "a uniform read-back column was left unexplained"),
    ("concession.retry_reduced_scope", "a retry reduced scope"),
    ("concession.other",
     "something the skills discourage, with no code for it — silence is never "
     "the fallback"),
):
    _register(
        _code,
        stage="s2_transform",
        stages=STAGES,
        severity="warning",
        owner="agent",
        summary=_summary,
    )

# --- domains `heal.` and `meta.` — bookkeeping, always info -----------------
for _code, _summary in (
    ("heal.attempt_started", "an attempt started"),
    ("heal.healed", "the re-run passed with no concession"),
    ("heal.healed_with_concessions", "the re-run passed and concessions[] grew"),
    ("heal.caps_exhausted", "a cap was reached"),
    ("heal.blocked", "a blocker was hit and the user is being asked"),
    ("heal.retry_environmental", "an environmental retry was taken"),
    ("meta.stage_not_reached", "a stage produced no signal at all"),
    ("meta.classification_unsettled",
     "the evidence did not settle whether the failure was environmental, so it "
     "is treated as not environmental"),
):
    _register(
        _code,
        stage="s0_spec",
        stages=STAGES,
        severity="info",
        owner="agent",
        summary=_summary,
    )


# ---------------------------------------------------------------------------
# Redaction (§1.7)
# ---------------------------------------------------------------------------

def redact(value: Any) -> Any:
    """Replace anything that looks like a populated secret with <redacted>.

    Applied to every `message` and every `evidence` value before a diagnostic
    leaves its producer. A check that prints the secret it found turns a
    contained file leak into a transcript leak.
    """
    if isinstance(value, str):
        redacted = CREDENTIAL_VALUE_RE.sub(_redact_match, value)
        return CREDENTIAL_URL_RE.sub(r"\1<redacted>\3", redacted)
    if isinstance(value, dict):
        return {
            k: _redact_credential_value(v) if _is_credential_key(k) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _is_credential_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    normalized = normalized.replace("-", "_").lower()
    return normalized == "authorization" or CREDENTIAL_KEY_RE.search(normalized) is not None


def _redact_credential_value(value: Any) -> Any:
    """Preserve known empty placeholders, but never a populated credential value."""
    if value is None or value in ("", [], {}):
        return value
    if isinstance(value, str) and value.strip().lower() in CREDENTIAL_PLACEHOLDERS:
        return value
    return "<redacted>"


def _redact_match(match: re.Match) -> str:
    whole = match.group(0)
    tail = whole.split(":", 1)[-1].split("=", 1)[-1].strip()
    if tail.lower() in CREDENTIAL_PLACEHOLDERS:
        return whole
    sep = ":" if ":" in whole else "="
    head = whole.split(sep, 1)[0]
    return f"{head}{sep} <redacted>"

# ---------------------------------------------------------------------------
# The diagnostic record (§1.1) and its envelope (§1.8)
# ---------------------------------------------------------------------------

class DiagnosticError(ValueError):
    """A producer bug: a diagnostic that cannot legally exist."""


class SpecReadError(Exception):
    """The spec could not be read, decoded or split. Exit 2, never exit 1.

    `reason` is the closed discriminator enum for `spec.frontmatter.unparseable`,
    which has more than one call site: `missing` | `unterminated` |
    `unparseable` | `not_mapping`. It lives on the exception rather than beside
    it so that the one `split_frontmatter` both the canonicalizer and the
    validator call can carry it — the validator turns it into
    `evidence.reason`. `None` for every other SpecReadError.
    """

    def __init__(
        self,
        message: str,
        code: str = "spec.frontmatter.unparseable",
        reason: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.reason = reason


# Everything a "could not read the spec" CLI path may legally raise, so it exits
# 2 with a message instead of a traceback. The v2 parser is stdlib-only.
_READ_FAILURES: tuple[type[BaseException], ...] = (SpecReadError, OSError, ValueError)


@dataclass(frozen=True)
class Diagnostic:
    """One `nxd-diagnostic-v2` finding.

    Build these with `diagnostic()` rather than by hand — the factory is what
    pins `owner` to the registry. `owner` decides whether the human hears about
    a finding at all, so a producer may never set it.
    """

    stage: str
    code: str
    severity: str
    owner: str
    origin: str
    path: str
    message: str
    evidence: dict = field(default_factory=dict)
    fix: str | None = None

    def to_dict(self) -> dict:
        out = {
            "schema": DIAGNOSTIC_SCHEMA_ID,
            "stage": self.stage,
            "code": self.code,
            "severity": self.severity,
            "owner": self.owner,
            "origin": self.origin,
            "path": self.path,
            "message": self.message,
            "evidence": self.evidence,
        }
        if self.fix is not None:
            out["fix"] = self.fix
        return out

    @classmethod
    def from_dict(cls, obj: dict) -> "Diagnostic":
        problems = validate_diagnostic(obj)
        if problems:
            raise DiagnosticError("; ".join(problems))
        return cls(
            stage=obj["stage"],
            code=obj["code"],
            severity=obj["severity"],
            owner=obj["owner"],
            origin=obj["origin"],
            path=obj["path"],
            message=obj["message"],
            evidence=obj.get("evidence") or {},
            fix=obj.get("fix"),
        )


def diagnostic(
    code: str,
    *,
    message: str,
    path: str = "",
    evidence: dict | None = None,
    fix: str | None = None,
    origin: str = "tool_computed",
    severity: str | None = None,
    stage: str | None = None,
) -> Diagnostic:
    """Construct a diagnostic from the registry.

    `severity` may be relaxed downward (an `error` reported as a `warning`),
    never raised. `owner` is not a parameter at all: it comes from the registry
    and only from the registry. A diagnostic whose ownership changes is
    re-emitted under a NEW code, never mutated in place — a producer-relaxed
    severity costs a warning, a producer-demoted owner silences a blocker.
    """
    entry = CODES.get(code)
    if entry is None:
        raise DiagnosticError(f"unknown diagnostic code {code!r}")

    use_stage = stage or entry["stage"]
    if use_stage not in entry["stages"]:
        raise DiagnosticError(
            f"code {code!r} may not be emitted at stage {use_stage!r} "
            f"(registry allows {', '.join(entry['stages'])})"
        )

    use_severity = severity or entry["severity"]
    if use_severity not in SEVERITIES:
        raise DiagnosticError(f"unknown severity {use_severity!r}")
    if _SEVERITY_RANK[use_severity] > _SEVERITY_RANK[entry["severity"]]:
        raise DiagnosticError(
            f"code {code!r} is registry severity {entry['severity']!r}; a "
            f"producer may relax downward, never raise to {use_severity!r}"
        )

    if origin not in ORIGINS:
        raise DiagnosticError(f"unknown origin {origin!r}")

    diag = Diagnostic(
        stage=use_stage,
        code=code,
        severity=use_severity,
        owner=entry["owner"],
        origin=origin,
        path=path or "",
        message=redact(str(message)),
        evidence=redact(dict(evidence or {})),
        fix=redact(fix) if fix is not None else None,
    )
    problems = validate_diagnostic(diag.to_dict())
    if problems:
        raise DiagnosticError("; ".join(problems))
    return diag


def validate_diagnostic(obj: Any) -> list[str]:
    """Return every reason `obj` is not a legal `nxd-diagnostic-v2` record."""
    problems: list[str] = []
    if not isinstance(obj, dict):
        return ["diagnostic is not an object"]

    required = (
        "schema",
        "stage",
        "code",
        "severity",
        "owner",
        "origin",
        "path",
        "message",
    )
    for key in required:
        if key not in obj:
            problems.append(f"missing required key {key!r}")
    allowed = set(required) | {"evidence", "fix"}
    for key in obj:
        if key not in allowed:
            problems.append(f"unknown key {key!r} — an unknown key is a producer bug")
    if problems:
        return problems

    if obj["schema"] != DIAGNOSTIC_SCHEMA_ID:
        problems.append(f"schema must be {DIAGNOSTIC_SCHEMA_ID!r}")
    if obj["stage"] not in STAGES:
        problems.append(f"unknown stage {obj['stage']!r}")
    if obj["severity"] not in SEVERITIES:
        problems.append(f"unknown severity {obj['severity']!r}")
    if obj["owner"] not in OWNERS:
        problems.append(f"unknown owner {obj['owner']!r}")
    if obj["origin"] not in ORIGINS:
        problems.append(f"unknown origin {obj['origin']!r}")
    if not isinstance(obj["path"], str):
        problems.append("path must be a string")
    if not isinstance(obj["message"], str) or not obj["message"]:
        problems.append("message must be a non-empty string")
    if "evidence" in obj and not isinstance(obj["evidence"], dict):
        problems.append("evidence must be an object")
    if obj.get("fix") is not None and not isinstance(obj.get("fix"), str):
        problems.append("fix must be a string or null")

    entry = CODES.get(obj["code"])
    if entry is None:
        problems.append(f"unknown code {obj['code']!r} — codes are a closed registry")
        return problems

    if obj["owner"] != entry["owner"]:
        problems.append(
            f"code {obj['code']!r} carries owner {obj['owner']!r} but the "
            f"registry says {entry['owner']!r} — owner is never producer-overridable"
        )
    if obj["stage"] not in entry["stages"]:
        problems.append(
            f"code {obj['code']!r} may not be emitted at stage {obj['stage']!r}"
        )
    if _SEVERITY_RANK.get(obj["severity"], 0) > _SEVERITY_RANK[entry["severity"]]:
        problems.append(
            f"code {obj['code']!r} was raised to {obj['severity']!r} above its "
            f"registry severity {entry['severity']!r}"
        )

    # The §1.8.1 relay criterion. `origin` records who AUTHORED the claim, not
    # who wrote the file: a traceback the supervisor emitted stays
    # supervisor-authored however it reached the record, and an agent's
    # inference stays agent-authored however confidently it is phrased.
    if obj["origin"] == "supervisor_reported":
        detail = (obj.get("evidence") or {}).get("supervisor_detail")
        if not detail:
            problems.append(
                "origin is supervisor_reported with no evidence.supervisor_detail "
                "— an unbacked claim is agent_observed, and fails closed"
            )
        if not obj["path"].startswith("tool:"):
            problems.append(
                "origin is supervisor_reported but path does not name the "
                "producing tool (tool:<toolname>[.<field>])"
            )
    return problems


class Report:
    """A buffer of diagnostics that serializes as `nxd-diagnostic-report-v2`.

    `validate_dp_spec.py` fills one of these; so does `lock verify`. The
    envelope is the same either way, which is the point — one shape, every
    stage, differing only in which stage produced it.
    """

    def __init__(
        self,
        tool: str,
        target: str | None = None,
        spec_hash: str | None = None,
    ) -> None:
        if tool not in REPORT_TOOLS:
            raise DiagnosticError(f"unknown report tool {tool!r}")
        self.tool = tool
        self.target = target
        self.spec_hash = spec_hash
        self.diagnostics: list[Diagnostic] = []

    def add(self, diag: Diagnostic) -> Diagnostic:
        if diag.stage not in REPORT_TOOLS[self.tool]:
            raise DiagnosticError(
                f"tool {self.tool!r} may not carry stage {diag.stage!r}"
            )
        self.diagnostics.append(diag)
        return diag

    def error(self, message: str, *, code: str, **kwargs) -> Diagnostic:
        return self.add(diagnostic(code, message=message, **kwargs))

    def warn(self, message: str, *, code: str, **kwargs) -> Diagnostic:
        kwargs.setdefault("severity", "warning")
        return self.add(diagnostic(code, message=message, **kwargs))

    def info(self, message: str, *, code: str, **kwargs) -> Diagnostic:
        kwargs.setdefault("severity", "info")
        return self.add(diagnostic(code, message=message, **kwargs))

    def _by_severity(self, severity: str) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == severity]

    @property
    def errors(self) -> list[Diagnostic]:
        return self._by_severity("error")

    @property
    def warnings(self) -> list[Diagnostic]:
        return self._by_severity("warning")

    @property
    def infos(self) -> list[Diagnostic]:
        return self._by_severity("info")

    @property
    def ok(self) -> bool:
        return not self.errors

    def counts(self) -> dict:
        return {sev: len(self._by_severity(sev)) for sev in SEVERITIES}

    def to_dict(self) -> dict:
        return {
            "schema": REPORT_SCHEMA_ID,
            "tool": self.tool,
            "target": self.target,
            "ok": self.ok,
            "counts": self.counts(),
            "spec_hash": self.spec_hash,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }


def validate_spec_diagnostic(obj: Any) -> list[str]:
    """Validate the form-facing, field-addressed v2 spec finding shape."""
    problems: list[str] = []
    if not isinstance(obj, dict):
        return ["spec diagnostic is not an object"]
    required = (
        "schema", "code", "path", "severity", "owner", "control",
        "stage", "origin", "message",
    )
    for key in required:
        if key not in obj:
            problems.append(f"missing required key {key!r}")
    for key in obj:
        if key not in required:
            problems.append(f"unknown key {key!r} — an unknown key is a producer bug")
    if problems:
        return problems
    if obj["schema"] != SPEC_DIAGNOSTIC_SCHEMA_ID:
        problems.append(f"schema must be {SPEC_DIAGNOSTIC_SCHEMA_ID!r}")
    if not isinstance(obj["code"], str) or not obj["code"]:
        problems.append("code must be a non-empty string")
    if not isinstance(obj["path"], str) or not obj["path"]:
        problems.append("path must be a non-empty string")
    if obj["severity"] != "error":
        problems.append("severity must be 'error'")
    if obj["owner"] not in ("agent", "user"):
        problems.append("owner must be 'agent' or 'user'")
    if obj["control"] not in CONTROLS:
        problems.append(f"unknown control {obj['control']!r}")
    if obj["stage"] != "s0_spec":
        problems.append("stage must be 's0_spec'")
    if obj["origin"] != "tool_computed":
        problems.append("origin must be 'tool_computed'")
    if not isinstance(obj["message"], str) or not obj["message"]:
        problems.append("message must be a non-empty string")
    return problems


def validate_v3_spec_diagnostic(obj: Any) -> list[str]:
    """Validate the v3 field-addressed spec finding shape."""
    problems = [
        problem
        for problem in validate_spec_diagnostic(obj)
        if not problem.startswith("schema must be")
    ]
    if not isinstance(obj, dict):
        return problems
    if obj.get("schema") != _v3.DIAGNOSTIC_SCHEMA_ID:
        problems.append(f"schema must be {_v3.DIAGNOSTIC_SCHEMA_ID!r}")
    return problems


def validate_report(obj: Any) -> list[str]:
    """Return every reason ``obj`` is not a legal diagnostic report."""
    problems: list[str] = []
    if not isinstance(obj, dict):
        return ["report is not an object"]
    report_schema = obj.get("schema")
    is_v3_report = report_schema == V3_REPORT_SCHEMA_ID
    if report_schema not in {REPORT_SCHEMA_ID, V3_REPORT_SCHEMA_ID}:
        problems.append(f"schema must be {REPORT_SCHEMA_ID!r} or {V3_REPORT_SCHEMA_ID!r}")
    required = {"schema", "tool", "target", "ok", "counts", "spec_hash", "diagnostics"}
    if is_v3_report:
        required.add("proposal_hash")
    problems.extend(f"missing required key {key!r}" for key in sorted(required - set(obj)))
    problems.extend(f"unknown key {key!r}" for key in sorted(set(obj) - required))
    if problems:
        return problems
    tool = obj.get("tool")
    if not isinstance(obj.get("target"), (str, type(None))):
        problems.append("target must be a string or null")
    if not isinstance(obj.get("ok"), bool):
        problems.append("ok must be a boolean")
    if not isinstance(obj.get("spec_hash"), (str, type(None))):
        problems.append("spec_hash must be a string or null")
    if is_v3_report and (
        not isinstance(obj.get("proposal_hash"), (str, type(None)))
        or (isinstance(obj.get("proposal_hash"), str) and not re.fullmatch(r"sha256:[0-9a-f]{64}", obj["proposal_hash"]))
    ):
        problems.append("proposal_hash must be sha256:<64 lowercase hex> or null")
    counts = obj.get("counts")
    if not isinstance(counts, dict):
        problems.append("counts must be an object")
        counts = {}
    else:
        if set(counts) != set(SEVERITIES):
            problems.append("counts must contain exactly error, warning, and info")
        if any(
            key not in SEVERITIES or not isinstance(value, int)
            or isinstance(value, bool) or value < 0
            for key, value in counts.items()
        ):
            problems.append("counts values must be non-negative integers")
    if not isinstance(obj.get("diagnostics"), list):
        problems.append("diagnostics must be a list")
        return problems
    diags = obj["diagnostics"]
    actual_counts = {severity: 0 for severity in SEVERITIES}
    if tool == "validate_dp_spec":
        for i, diag in enumerate(diags):
            if not isinstance(diag, dict):
                problems.append(f"diagnostics[{i}] must be an object")
                continue
            if diag.get("schema") in {SPEC_DIAGNOSTIC_SCHEMA_ID, _v3.DIAGNOSTIC_SCHEMA_ID}:
                problems.extend(
                    f"diagnostics[{i}]: {problem}"
                    for problem in (
                        validate_spec_diagnostic(diag)
                        if diag.get("schema") == SPEC_DIAGNOSTIC_SCHEMA_ID
                        else validate_v3_spec_diagnostic(diag)
                    )
                )
                if diag.get("severity") in actual_counts:
                    actual_counts[diag["severity"]] += 1
                continue
            if diag.get("schema") == DIAGNOSTIC_SCHEMA_ID:
                problems.extend(f"diagnostics[{i}]: {problem}" for problem in validate_diagnostic(diag))
                if diag.get("severity") in actual_counts:
                    actual_counts[diag["severity"]] += 1
                continue
            problems.append(
                f"diagnostics[{i}]: schema must be {SPEC_DIAGNOSTIC_SCHEMA_ID!r}, "
                f"{_v3.DIAGNOSTIC_SCHEMA_ID!r} or {DIAGNOSTIC_SCHEMA_ID!r}"
            )
    else:
        if tool not in REPORT_TOOLS:
            problems.append(
                f"tool {tool!r} is outside the closed enum "
                f"({', '.join(sorted(REPORT_TOOLS))})"
            )
        for i, diag in enumerate(diags):
            for problem in validate_diagnostic(diag):
                problems.append(f"diagnostics[{i}]: {problem}")
            if isinstance(diag, dict) and diag.get("severity") in actual_counts:
                actual_counts[diag["severity"]] += 1
            if tool in REPORT_TOOLS and isinstance(diag, dict):
                if diag.get("stage") not in REPORT_TOOLS[tool]:
                    problems.append(
                        f"diagnostics[{i}]: tool {tool!r} may not carry stage "
                        f"{diag.get('stage')!r}"
                    )
    if isinstance(counts, dict) and counts != actual_counts:
        problems.append(
            f"counts do not match diagnostics: expected {actual_counts}, got {counts}"
        )
    if isinstance(obj.get("ok"), bool) and isinstance(counts, dict):
        expected_ok = counts.get("error") == 0
        if obj["ok"] != expected_ok:
            problems.append(f"ok must equal (counts.error == 0), expected {expected_ok}")
    return problems


# ---------------------------------------------------------------------------
# Paths (§1.6)
# ---------------------------------------------------------------------------

def entry_identity(entry: Any, index: int) -> str:
    """The key a UI hangs a control on.

    Precedence: `id`, `name`, `decision_id`, `model`; only when none is present,
    `#<0-based index>`. Never a bare index for an entry that has an identity —
    array indices move when a list is edited, and a moving path is a UI that
    highlights the wrong field.
    """
    if isinstance(entry, dict):
        for key in ("id", "name", "decision_id", "model"):
            value = entry.get(key)
            if value not in (None, "", [], {}):
                return str(value)
    return f"#{index}"


def spec_path(section: str, identity: str | None = None, *rest: str) -> str:
    """Build a stable v2 field path: `v2:models[orders].fields`."""
    head = section if identity is None else f"{section}[{identity}]"
    return "v2:" + ".".join([head, *[r for r in rest if r]])


# ---------------------------------------------------------------------------
# nxd-dp-spec-canon-v2 — canonicalization, hash, emitter
# ---------------------------------------------------------------------------

def split_frontmatter(text: str) -> tuple[dict, str]:
    """Return v2 frontmatter and the body; old versions fail closed."""
    try:
        parsed = _v2.parse(text)
    except _v2.UnsupportedVersionError as exc:
        raise SpecReadError(str(exc), reason="unsupported_version") from exc
    except _v2.ParseError as exc:
        raise SpecReadError(str(exc), reason="unparseable") from exc
    document = parsed.document
    marker = "\n---"
    close = text.find(marker, text.find("---") + 3)
    body = text[close + len(marker):] if close >= 0 else ""
    return document.frontmatter.to_dict(), body


def _spec_version(text: str) -> int | None:
    match = re.search(r"^dp_spec_version:\s*(\d+)\s*$", text, flags=re.MULTILINE)
    return int(match.group(1)) if match else None


def split_sections(body: str) -> dict[str, str]:
    """Split v2 body headings for legacy callers without interpreting YAML."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = line[3:].strip().lower().replace(" ", "_")
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def canonical_object(raw: bytes) -> dict:
    """Return the canonical object for either active authoring generation."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SpecReadError(f"the spec is not valid UTF-8: {exc}", code="spec.encoding.not_utf8") from exc
    try:
        if _spec_version(text) == _v3.SPEC_VERSION:
            return _v3.canonical_object(text)
        return _v2.canonical_object(text)
    except _v2.UnsupportedVersionError as exc:
        raise SpecReadError(str(exc), reason="unsupported_version") from exc
    except _v2.ParseError as exc:
        raise SpecReadError(str(exc), reason="unparseable") from exc
    except _v3.UnsupportedVersionError as exc:
        raise SpecReadError(str(exc), reason="unsupported_version") from exc
    except _v3.ParseError as exc:
        # The v3 arm is normalized for the same reason the v2 arms are:
        # `SpecReadError` is this function's single failure type, and a caller
        # catching it should not also have to know that the version dispatch
        # above can surface the authoring module's own `ValueError`. Without
        # this clause the next caller that reasonably catches `SpecReadError`
        # reintroduces the escape this PR fixes at two sites.
        raise SpecReadError(str(exc), reason="unparseable") from exc


def canonical_bytes(raw: bytes) -> bytes:
    """The exact v2 semantic bytes the hash is taken over."""
    return json.dumps(canonical_object(raw), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonicalize(raw: bytes) -> bytes:
    """Alias for `canonical_bytes` — the name §3.4 uses."""
    return canonical_bytes(raw)


def spec_hash(raw: bytes) -> str:
    """`sha256:<hex>` over semantic content, excluding lifecycle metadata."""
    return "sha256:" + hashlib.sha256(canonical_bytes(raw)).hexdigest()


def raw_sha256(raw: bytes) -> str:
    """Bare hex sha256 of raw bytes — the snapshot's tamper check."""
    return hashlib.sha256(raw).hexdigest()


def spec_hash_of(path: Path) -> str:
    return spec_hash(path.read_bytes())


def emit(obj: dict) -> str:
    """Render a v2 canonical object as a reviewed proposal."""
    try:
        return _v2.emit(obj)
    except (TypeError, ValueError) as exc:
        raise SpecReadError(str(exc)) from exc


# ---------------------------------------------------------------------------
# The lock file (§3.2, §3.5, §3.6)
# ---------------------------------------------------------------------------

PACKAGED_VERSION_STAMP = ".nexty-plugin-version.json"
V3_PROPOSAL_SNAPSHOT = "dp-blueprint.proposal.approved.json"


def _json_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _write_v3_lock(
    spec: Path,
    closure: Path,
    proposal_path: Path | None,
    *,
    plugin_version: str | None = None,
    generator_skill: str = "nxd-generate-data-product",
    now_ms: int | None = None,
) -> tuple[dict, Report]:
    report = Report("dp_diagnostics", target=str(closure))
    try:
        raw = spec.read_text(encoding="utf-8")
        parsed = _v3.parse(raw)
    except (_v3.ParseError, OSError, UnicodeError) as exc:
        report.error(str(exc), code="pin.spec_compile_error", path="spec", stage="s4_pin")
        return {}, report
    if parsed.frontmatter.status != "approved":
        report.error(
            f"the spec status is {parsed.frontmatter.status!r}, not 'approved' — approve the plan before snapshotting it into a closure",
            code="closure.lock_status_not_approved",
            path="v3:frontmatter.status",
            evidence={"expected": "approved", "found": parsed.frontmatter.status},
            stage="s3_closure",
        )
        return {}, report
    if proposal_path is None:
        report.error(
            "a v3 lock requires the exact typed proposal snapshot used for approval",
            code="pin.spec_compile_error",
            path="v3:proposal",
            stage="s4_pin",
        )
        return {}, report
    try:
        proposal_raw = proposal_path.read_bytes()
        proposal = json.loads(proposal_raw.decode("utf-8"))
        if not isinstance(proposal, dict):
            raise ValueError("the typed proposal must be a JSON object")
    except (OSError, UnicodeError, ValueError) as exc:
        report.error(str(exc), code="pin.spec_compile_error", path="v3:proposal", stage="s4_pin")
        return {}, report
    issues = _v3.validate_approval(parsed, proposal)
    if issues:
        detail = "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        report.error(f"v3 proposal validation failed: {detail}", code="pin.spec_compile_error", path="spec", stage="s4_pin")
        return {}, report

    closure.mkdir(parents=True, exist_ok=True)
    snapshot = closure / CLOSURE_SNAPSHOT
    proposal_snapshot = closure / V3_PROPOSAL_SNAPSHOT
    shutil.copyfile(spec, snapshot)
    proposal_snapshot.write_bytes(proposal_raw)
    payload = proposal.get("proposal", {})
    lock = {
        "schema": V3_LOCK_SCHEMA_ID,
        "spec_hash": _v3.semantic_hash(parsed),
        "proposal_hash": _v3.proposal_hash(proposal),
        "canonicalization": _v3.CANONICALIZATION,
        "snapshot": CLOSURE_SNAPSHOT,
        "snapshot_sha256": raw_sha256(snapshot.read_bytes()),
        "proposal_snapshot": V3_PROPOSAL_SNAPSHOT,
        "proposal_snapshot_sha256": raw_sha256(proposal_raw),
        "spec_status_at_copy": parsed.frontmatter.status,
        "dp_spec_version": parsed.frontmatter.dp_spec_version,
        "name": parsed.frontmatter.name,
        "workflow": parsed.frontmatter.workflow,
        "terms_hash": _json_sha256(_v3.canonical_terms(payload)),
        "contract_inventory_hash": _json_sha256(_v3.canonical_contract_inventory(payload)),
        "locked_decisions_hash": _json_sha256(_v3.locked_decision_inventory(proposal)),
        "delivery_profile": _v3.FIXED_DELIVERY_PROFILE,
        "source_basename": spec.name,
        "compiler_version": {
            "plugin": plugin_version or _plugin_version(),
            "generator_skill": generator_skill,
            "self_check": "nxd-self-check-v3",
        },
        "copied_at_unix_ms": now_ms if now_ms is not None else int(time.time() * 1000),
    }
    (closure / CLOSURE_LOCK).write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for name in _drop_superseded_legacy_artifacts(closure):
        report.info(
            f"removed the superseded pre-v0.38.0 artifact {name}",
            code="closure.legacy_artifact_superseded",
            path=f"closure:{name}",
            stage="s3_closure",
        )
    return lock, report


def _validate_v3_lock(lock: Any) -> list[str]:
    if not isinstance(lock, dict):
        return ["lock is not an object"]
    required = {
        "schema", "spec_hash", "proposal_hash", "canonicalization", "snapshot", "snapshot_sha256",
        "proposal_snapshot", "proposal_snapshot_sha256", "spec_status_at_copy", "dp_spec_version",
        "name", "workflow", "terms_hash", "contract_inventory_hash", "locked_decisions_hash", "delivery_profile",
        "source_basename", "compiler_version", "copied_at_unix_ms",
    }
    problems = [f"missing required key {key!r}" for key in sorted(required - set(lock))]
    problems.extend(f"unknown key {key!r}" for key in sorted(set(lock) - required))
    if problems:
        return problems
    if lock.get("schema") != V3_LOCK_SCHEMA_ID:
        problems.append(f"schema must be {V3_LOCK_SCHEMA_ID!r}")
    if not isinstance(lock.get("spec_hash"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", lock["spec_hash"]):
        problems.append("spec_hash must be sha256:<64 lowercase hex>")
    if not isinstance(lock.get("proposal_hash"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", lock["proposal_hash"]):
        problems.append("proposal_hash must be sha256:<64 lowercase hex>")
    if lock.get("canonicalization") != _v3.CANONICALIZATION:
        problems.append(f"canonicalization must be {_v3.CANONICALIZATION!r}")
    if lock.get("delivery_profile") != _v3.FIXED_DELIVERY_PROFILE:
        problems.append(f"delivery_profile must be {_v3.FIXED_DELIVERY_PROFILE!r}")
    for key in ("snapshot", "proposal_snapshot", "source_basename", "name", "workflow", "delivery_profile"):
        if not isinstance(lock.get(key), str) or not lock[key]:
            problems.append(f"{key} must be a non-empty string")
    for key in ("snapshot_sha256", "proposal_snapshot_sha256", "terms_hash", "contract_inventory_hash", "locked_decisions_hash"):
        if not isinstance(lock.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", lock[key]):
            problems.append(f"{key} must be 64 lowercase hex")
    if lock.get("spec_status_at_copy") != "approved":
        problems.append("spec_status_at_copy must be 'approved'")
    if lock.get("dp_spec_version") != _v3.SPEC_VERSION:
        problems.append(f"dp_spec_version must be {_v3.SPEC_VERSION}")
    compiler = lock.get("compiler_version")
    if not isinstance(compiler, dict) or set(compiler) != {"plugin", "generator_skill", "self_check"}:
        problems.append("compiler_version must contain exactly plugin, generator_skill, self_check")
    elif any(not isinstance(compiler[key], str) or not compiler[key] for key in compiler):
        problems.append("compiler_version values must be non-empty strings")
    elif compiler.get("self_check") != "nxd-self-check-v3":
        problems.append("compiler_version.self_check must be 'nxd-self-check-v3'")
    if not isinstance(lock.get("copied_at_unix_ms"), int) or isinstance(lock["copied_at_unix_ms"], bool):
        problems.append("copied_at_unix_ms must be an integer")
    return problems


def _verify_v3_lock(closure: Path, spec: Path | None = None) -> Report:
    report = Report("dp_diagnostics", target=str(closure))
    lock_path = resolve_closure_lock(closure)
    lock_name = lock_path.name
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.error(str(exc), code="closure.lock_unparseable", path=f"closure:{lock_name}", stage="s3_closure")
        return report
    problems = _validate_v3_lock(lock)
    if problems:
        report.error(
            f"{lock_name} is not a complete v3 lock: {'; '.join(problems)}",
            code="closure.lock_unparseable",
            path=f"closure:{lock_name}",
            stage="s3_closure",
        )
        return report
    for field in ("snapshot", "proposal_snapshot"):
        reference = Path(str(lock[field]))
        if reference.is_absolute() or ".." in reference.parts or not _stays_within_closure(closure, closure / reference):
            report.error(
                f"{field} {str(reference)!r} points outside the closure",
                code="closure.escaping_reference",
                path=f"closure:{lock_name}:{field}",
                stage="s3_closure",
            )
    if report.errors:
        return report
    snapshot = closure / str(lock["snapshot"])
    proposal_snapshot = closure / str(lock["proposal_snapshot"])
    # Report against the names the LOCK declares, not the current constants: on
    # a legacy closure the two differ, and an error naming a file that is not
    # there sends the reader hunting for the wrong fault.
    snapshot_name = snapshot.name
    proposal_name = proposal_snapshot.name
    if not snapshot.is_file() or not proposal_snapshot.is_file():
        report.error("the v3 approved snapshots are incomplete", code="closure.spec_snapshot_missing", path=f"closure:{snapshot_name}", stage="s3_closure")
        return report
    raw = snapshot.read_bytes()
    proposal_raw = proposal_snapshot.read_bytes()
    if raw_sha256(raw) != lock["snapshot_sha256"]:
        report.error("the approved Markdown snapshot bytes do not match the lock", code="closure.lock_snapshot_byte_mismatch", path=f"closure:{snapshot_name}", stage="s3_closure")
    if raw_sha256(proposal_raw) != lock["proposal_snapshot_sha256"]:
        report.error("the typed proposal snapshot bytes do not match the lock", code="closure.lock_snapshot_byte_mismatch", path=f"closure:{proposal_name}", stage="s3_closure")
    try:
        parsed = _v3.parse(raw.decode("utf-8"))
        proposal = json.loads(proposal_raw.decode("utf-8"))
        if not isinstance(proposal, dict):
            raise ValueError("the typed proposal snapshot is not a JSON object")
        if _v3.semantic_hash(parsed) != lock["spec_hash"] or _v3.proposal_hash(proposal) != lock["proposal_hash"]:
            report.error("the v3 snapshot hash does not match the lock", code="closure.spec_hash_mismatch", path=f"closure:{snapshot_name}", stage="s3_closure")
        if parsed.frontmatter.name != lock["name"] or parsed.frontmatter.workflow != lock["workflow"]:
            report.error("the v3 snapshot metadata does not match the lock", code="closure.spec_hash_mismatch", path=f"closure:{lock_name}", stage="s3_closure")
        payload = proposal.get("proposal")
        if not isinstance(payload, dict):
            raise ValueError("the typed proposal payload is not an object")
        actual_terms_hash = _json_sha256(_v3.canonical_terms(payload))
        if actual_terms_hash != lock["terms_hash"]:
            report.error(
                "the v3 Terms inventory does not match the lock",
                code="closure.terms_hash_mismatch",
                path=f"closure:{lock_name}:terms_hash",
                stage="s3_closure",
            )
        actual_contract_hash = _json_sha256(_v3.canonical_contract_inventory(payload))
        if actual_contract_hash != lock["contract_inventory_hash"]:
            report.error(
                "the v3 compiled contract inventory does not match the lock",
                code="closure.contract_inventory_hash_mismatch",
                path=f"closure:{lock_name}:contract_inventory_hash",
                stage="s3_closure",
            )
        decisions = payload.get("decisions")
        locked_decisions = _v3.locked_decision_inventory(proposal)
        actual_decisions_hash = _json_sha256(locked_decisions)
        if (
            not isinstance(decisions, list)
            or len(locked_decisions) != len(decisions)
            or actual_decisions_hash != lock["locked_decisions_hash"]
        ):
            report.error(
                "the v3 locked-decision inventory does not match the lock",
                code="closure.decision_inventory_mismatch",
                path=(
                    f"closure:{lock_name}:locked_decisions_hash"
                    if actual_decisions_hash != lock["locked_decisions_hash"]
                    else f"closure:{lock_name}:proposal_hash"
                ),
                stage="s3_closure",
            )
        # proposal_hash covers the complete typed envelope, including every
        # locked decision's id, target, ruling, and status. The explicit
        # inventory check above prevents an approved snapshot from silently
        # regressing to proposed decisions while this hash check binds the
        # complete inventory to the lock.
        issues = _v3.validate_approval(parsed, proposal)
        if issues:
            report.error("the approved v3 proposal no longer validates", code="closure.spec_hash_mismatch", path=f"closure:{snapshot_name}", stage="s3_closure")
    except (OSError, UnicodeError, ValueError) as exc:
        report.error(str(exc), code="closure.lock_unparseable", path=f"closure:{snapshot_name}", stage="s3_closure")
    if spec is not None:
        try:
            live = spec_hash(spec.read_bytes())
        except _READ_FAILURES as exc:
            # Same failure, same code as the v2 verifier's arm: the live IR
            # will not canonicalize. `closure.spec_snapshot_missing` said the
            # closure snapshot was absent, which is a different fault in a
            # different file, and it disagreed with the v2 generation besides.
            report.error(
                f"the live IR {spec} could not be canonicalized: {exc}",
                code="closure.live_spec_unparseable",
                path="spec",
                stage="s3_closure",
            )
        else:
            report.spec_hash = live
            if live != lock["spec_hash"]:
                report.error("the live spec has moved away from the approved v3 plan", code="closure.live_spec_diverged", path=f"closure:{lock_name}:spec_hash", stage="s3_closure")
    else:
        report.spec_hash = lock["spec_hash"]
    return report


def _plugin_version(root: Path | None = None) -> str:
    """Read the nearest trusted package version. Never write it."""
    start = (root or Path(__file__)).resolve()
    if start.is_file():
        start = start.parent
    for candidate in (start, *start.parents):
        for metadata in (
            candidate / PACKAGED_VERSION_STAMP,
            candidate / ".claude-plugin" / "plugin.json",
        ):
            try:
                payload = json.loads(metadata.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or payload.get("name") != "nexty-agent-skills":
                continue
            version = payload.get("version")
            if isinstance(version, str) and version:
                return version
    return "unknown"


def write_lock(
    spec: Path,
    closure: Path,
    *,
    proposal: Path | None = None,
    plugin_version: str | None = None,
    generator_skill: str = "nxd-generate-data-product",
    now_ms: int | None = None,
) -> tuple[dict, Report]:
    """Snapshot an approved spec into a closure and write the lock.

    The copy is byte-identical — `shutil.copyfile`, no rewriting, no
    reformatting. The snapshot is evidence, and evidence that was reformatted on
    the way in cannot be compared.
    """
    try:
        version = _spec_version(spec.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        version = None
    if version == _v3.SPEC_VERSION:
        return _write_v3_lock(
            spec,
            closure,
            proposal,
            plugin_version=plugin_version,
            generator_skill=generator_skill,
            now_ms=now_ms,
        )

    report = Report("dp_diagnostics", target=str(closure))
    raw = spec.read_bytes()
    try:
        parsed_v2 = _v2.parse(raw.decode("utf-8"))
    except _v2.UnsupportedVersionError as exc:
        report.error(str(exc), code="spec.frontmatter.bad_version", path="v2:frontmatter.dp_spec_version", stage="s0_spec")
        return {}, report
    except (_v2.ParseError, UnicodeDecodeError) as exc:
        report.error(str(exc), code="spec.frontmatter.unparseable", path="spec", stage="s0_spec")
        return {}, report
    if proposal is not None:
        # The mirror of `_write_v3_lock`'s "a v3 lock requires the typed
        # proposal" guard above. A v2 lock has no proposal to bind, and this is
        # the command that writes the binding: accepting the path and never
        # opening it would pin a closure the caller believes carries a proposal
        # snapshot it does not have.
        report.error(
            "a v2 lock binds no typed proposal; --proposal is a v3 input",
            code="spec.proposal.unsupported",
            path="v2:proposal",
            stage="s0_spec",
        )
        return {}, report
    fm = parsed_v2.document.frontmatter.to_dict()
    semantic_issues = _v2.validate(parsed_v2)
    if semantic_issues:
        # ``Report`` is the pipeline envelope with a deliberately closed
        # diagnostic registry. The v2 validator's field-addressed findings
        # are carried by validate_dp_spec; lock write only needs to reject an
        # invalid source without pretending every v2 code is a pipeline code.
        detail = "; ".join(f"{issue.path}: {issue.message}" for issue in semantic_issues)
        report.error(
            f"v2 spec validation failed: {detail}",
            code="pin.spec_compile_error",
            path="spec",
            stage="s4_pin",
        )
        return {}, report
    if fm.get("status") != "approved":
        report.error(
            f"the spec status is {fm.get('status')!r}, not 'approved' — approve "
            "the plan before snapshotting it into a closure",
            code="closure.lock_status_not_approved",
            path="v2:frontmatter.status",
            evidence={"expected": "approved", "found": fm.get("status")},
            stage="s3_closure",
        )
        return {}, report

    closure.mkdir(parents=True, exist_ok=True)
    snapshot = closure / CLOSURE_SNAPSHOT

    # Idempotent: re-running `lock write` over an unchanged spec rewrites the
    # same bytes and the same hash.
    shutil.copyfile(spec, snapshot)
    lock = {
        "schema": LOCK_SCHEMA_ID,
        "spec_hash": spec_hash(raw),
        "canonicalization": CANONICALIZATION,
        "snapshot": CLOSURE_SNAPSHOT,
        "snapshot_sha256": raw_sha256(snapshot.read_bytes()),
        "spec_status_at_copy": str(fm.get("status", "")),
        "dp_spec_version": fm.get("dp_spec_version"),
        "name": fm.get("name"),
        "workflow": fm.get("workflow"),
        "contract_names": sorted(
            str(item.get("name") or entity_id)
            for entity_id, item in _v2.canonical_object(parsed_v2).get("contracts", {}).items()
        ),
        # A basename, never a path: storing a `../`-shaped string inside the
        # closure is exactly the pointer this design removes.
        "source_basename": spec.name,
        "compiler_version": {
            "plugin": plugin_version or _plugin_version(),
            "generator_skill": generator_skill,
            "self_check": "nxd-self-check-v2",
        },
        "copied_at_unix_ms": now_ms if now_ms is not None else int(time.time() * 1000),
    }
    (closure / CLOSURE_LOCK).write_text(
        json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for name in _drop_superseded_legacy_artifacts(closure):
        report.info(
            f"removed the superseded pre-v0.38.0 artifact {name}",
            code="closure.legacy_artifact_superseded",
            path=f"closure:{name}",
            stage="s3_closure",
        )
    return lock, report


def read_lock(closure: Path) -> dict:
    return json.loads(resolve_closure_lock(closure).read_text(encoding="utf-8"))


def validate_lock(lock: Any) -> list[str]:
    """Validate the complete v2 lock envelope before any artifact is trusted."""
    if isinstance(lock, dict) and lock.get("schema") == V3_LOCK_SCHEMA_ID:
        return _validate_v3_lock(lock)
    if not isinstance(lock, dict):
        return ["lock is not an object"]
    required = {
        "schema", "spec_hash", "canonicalization", "snapshot", "snapshot_sha256",
        "spec_status_at_copy", "dp_spec_version", "name", "workflow",
        "source_basename", "contract_names", "compiler_version", "copied_at_unix_ms",
    }
    problems = [f"missing required key {key!r}" for key in sorted(required - set(lock))]
    problems.extend(f"unknown key {key!r}" for key in sorted(set(lock) - required))
    if problems:
        return problems
    if lock.get("schema") != LOCK_SCHEMA_ID:
        problems.append(f"schema must be {LOCK_SCHEMA_ID!r}")
    if not isinstance(lock.get("spec_hash"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", lock["spec_hash"]):
        problems.append("spec_hash must be sha256:<64 lowercase hex>")
    if lock.get("canonicalization") != CANONICALIZATION:
        problems.append(f"canonicalization must be {CANONICALIZATION!r}")
    if not isinstance(lock.get("snapshot"), str) or not lock["snapshot"]:
        problems.append("snapshot must be a non-empty string")
    if not isinstance(lock.get("snapshot_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", lock["snapshot_sha256"]):
        problems.append("snapshot_sha256 must be 64 lowercase hex")
    if lock.get("spec_status_at_copy") not in STATUS_VALUES:
        problems.append("spec_status_at_copy is outside the v2 status vocabulary")
    if lock.get("dp_spec_version") != SPEC_VERSION:
        problems.append(f"dp_spec_version must be {SPEC_VERSION}")
    for key in ("name", "workflow", "source_basename"):
        if not isinstance(lock.get(key), str) or not lock[key]:
            problems.append(f"{key} must be a non-empty string")
    if not isinstance(lock.get("contract_names"), list) or any(
        not isinstance(item, str) or not item for item in lock["contract_names"]
    ) or len(set(lock["contract_names"])) != len(lock["contract_names"]):
        problems.append("contract_names must be a unique list of non-empty strings")
    compiler = lock.get("compiler_version")
    if not isinstance(compiler, dict) or set(compiler) != {"plugin", "generator_skill", "self_check"}:
        problems.append("compiler_version must contain exactly plugin, generator_skill, self_check")
    elif any(not isinstance(compiler[key], str) or not compiler[key] for key in compiler):
        problems.append("compiler_version values must be non-empty strings")
    if not isinstance(lock.get("copied_at_unix_ms"), int) or isinstance(lock["copied_at_unix_ms"], bool):
        problems.append("copied_at_unix_ms must be an integer")
    return problems


def _stays_within_closure(closure: Path, target: Path) -> bool:
    """Whether a target's canonical path remains under the closure root."""
    try:
        target.resolve().relative_to(closure.resolve())
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def verify_lock(closure: Path, spec: Path | None = None) -> Report:
    """The canonical half of the verification split (§3.5).

    Phase C does the byte check inside the closure with `hashlib` alone; this
    compares the v2 semantic hash against the LIVE IR outside the closure.
    """
    lock_path = resolve_closure_lock(closure)
    lock_name = lock_path.name
    try:
        lock_probe = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lock_probe = None
    if isinstance(lock_probe, dict) and lock_probe.get("schema") == V3_LOCK_SCHEMA_ID:
        return _verify_v3_lock(closure, spec)

    report = Report("dp_diagnostics", target=str(closure))
    if not lock_path.is_file():
        report.error(
            f"{lock_name} is missing from {closure}",
            code="closure.lock_missing",
            path=f"closure:{lock_name}",
            stage="s3_closure",
        )
        return report
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.error(
            f"{lock_name} does not parse: {exc}",
            code="closure.lock_unparseable",
            path=f"closure:{lock_name}",
            stage="s3_closure",
        )
        return report
    lock_problems = validate_lock(lock)
    if lock_problems:
        report.error(
            f"{lock_name} is not a complete v2 lock: {'; '.join(lock_problems)}",
            code="closure.lock_unparseable",
            path=f"closure:{lock_name}",
            stage="s3_closure",
        )
        return report

    snapshot_ref = str(lock.get("snapshot") or CLOSURE_SNAPSHOT)
    if Path(snapshot_ref).is_absolute() or ".." in Path(snapshot_ref).parts:
        report.error(
            f"snapshot {snapshot_ref!r} points outside the closure",
            code="closure.escaping_reference",
            path=f"closure:{lock_name}:snapshot",
            evidence={"found": snapshot_ref},
            stage="s3_closure",
        )
        return report

    snapshot = closure / snapshot_ref
    if not _stays_within_closure(closure, snapshot):
        report.error(
            f"snapshot {snapshot_ref!r} resolves outside the closure",
            code="closure.escaping_reference",
            path=f"closure:{lock_name}:snapshot",
            evidence={"found": snapshot_ref},
            stage="s3_closure",
        )
        return report
    if not snapshot.is_file():
        report.error(
            f"{snapshot.name} is missing — the approved plan is not in the closure",
            code="closure.spec_snapshot_missing",
            path=f"closure:{snapshot.name}",
            stage="s3_closure",
        )
        return report

    raw = snapshot.read_bytes()
    if raw_sha256(raw) != lock.get("snapshot_sha256"):
        report.error(
            "the snapshot's bytes do not match snapshot_sha256 — it was edited "
            "after it was written",
            code="closure.lock_snapshot_byte_mismatch",
            path=f"closure:{snapshot.name}",
            evidence={"expected": lock.get("snapshot_sha256"), "actual": raw_sha256(raw)},
            stage="s3_closure",
        )
    if lock.get("spec_status_at_copy") != "approved":
        report.error(
            f"the snapshot was copied at status {lock.get('spec_status_at_copy')!r}, "
            "not 'approved'",
            code="closure.lock_status_not_approved",
            path=f"closure:{lock_name}:spec_status_at_copy",
            stage="s3_closure",
        )

    try:
        snapshot_hash = spec_hash(raw)
    except _READ_FAILURES as exc:
        # Catching only `SpecReadError` let a v3-shaped snapshot under a v2 lock
        # escape as an unhandled exception — no report, no diagnostic for a form
        # to render — because `canonical_object` dispatches on the sniffed
        # version and its v3 arm raised `_v3.ParseError` unnormalized. That arm
        # is normalized now, so `SpecReadError` alone would suffice today;
        # `_READ_FAILURES` stays because it is what every sibling site in this
        # module uses, and because the next dispatch arm added upstream should
        # not be able to reopen this hole by forgetting to normalize.
        report.error(
            f"the snapshot could not be canonicalized: {exc}",
            code="closure.lock_unparseable",
            path=f"closure:{snapshot.name}",
            stage="s3_closure",
        )
        snapshot_hash = None
    if snapshot_hash and snapshot_hash != lock.get("spec_hash"):
        report.error(
            "the snapshot's canonical hash does not match the lock — the plan in "
            "the closure is not the plan that was approved",
            code="closure.spec_hash_mismatch",
            path=f"closure:{snapshot.name}",
            evidence={"expected": lock.get("spec_hash"), "actual": snapshot_hash},
            stage="s3_closure",
        )

    if spec is not None:
        # No `is_file()` pre-check: the read itself raises `FileNotFoundError`,
        # which `_READ_FAILURES` catches, so a missing live spec and an
        # uncanonicalizable one report the same code at the same path as they
        # do in `_verify_v3_lock`. The pre-check that stood here reported
        # `closure.spec_snapshot_missing` against the closure snapshot — a file
        # that is present and fine — for a fault in the live spec.
        try:
            live = spec_hash(spec.read_bytes())
        except _READ_FAILURES as exc:
            report.error(
                f"the live IR {spec} could not be canonicalized: {exc}",
                code="closure.live_spec_unparseable",
                path="spec",
                stage="s3_closure",
            )
            return report
        else:
            report.spec_hash = live
            if live != lock.get("spec_hash"):
                report.error(
                    "the live spec has moved away from the plan this closure was "
                    "built from — regenerate, do not heal",
                    code="closure.live_spec_diverged",
                    path=f"closure:{lock_name}:spec_hash",
                    evidence={"expected": lock.get("spec_hash"), "actual": live},
                    stage="s3_closure",
                )
    else:
        report.spec_hash = lock.get("spec_hash")
    return report


# ---------------------------------------------------------------------------
# The build record (§2)
# ---------------------------------------------------------------------------

def _now_ms() -> int:
    return int(time.time() * 1000)


def status_for(diagnostics: Iterable[dict]) -> str:
    """No errors -> passed; no errors and >=1 warning -> passed_with_warnings."""
    diags = list(diagnostics)
    if any(d.get("severity") == "error" for d in diags):
        return "failed"
    if any(d.get("severity") == "warning" for d in diags):
        return "passed_with_warnings"
    return "passed"


def new_build_record(
    lock: dict,
    closure_path: Path,
    *,
    generator_model: str = "unknown",
    now_ms: int | None = None,
) -> dict:
    """The envelope, with every stage `not_reached`.

    `not_reached` is a distinct status from `passed` and from `failed`, exactly
    because `self_check.py` exits on the first failing phase and later phases
    produce no signal at all. A record whose later stages are `not_reached` is
    normal and honest.
    """
    lock_problems = validate_lock(lock)
    if lock_problems:
        raise ValueError("cannot create a build record from an invalid v2 lock: " + "; ".join(lock_problems))
    stamp = now_ms if now_ms is not None else _now_ms()
    return {
        "schema": BUILD_RECORD_SCHEMA_ID,
        "workflow": lock.get("workflow"),
        "data_product": lock.get("name"),
        "closure_path": str(closure_path),
        "compiled_from": lock.get("spec_hash"),
        "compiler_version": {
            "plugin": (lock.get("compiler_version") or {}).get("plugin", "unknown"),
            "generator_skill": (lock.get("compiler_version") or {}).get(
                "generator_skill", "nxd-generate-data-product"
            ),
            "dp_spec_version": lock.get("dp_spec_version"),
            "canonicalization": lock.get("canonicalization"),
        },
        "generated_at_unix_ms": stamp,
        "generator_model": generator_model,
        "stages": {
            stage: {
                "status": "not_reached",
                "ordinal": i,
                "at_unix_ms": None,
                "origin": "unbound",
                "diagnostics": [],
                "detail": {},
            }
            for i, stage in enumerate(STAGES)
        },
        "attempts": [],
        "review_rounds": [],
        "concessions": [],
        "blockers": [],
        "readback": {"distribution": [], "absent": []},
        "evidence": {},
        "caps": dict(
            DEFAULT_CAPS,
            regenerates_used=0,
            remaps_used={},
            retries_used=0,
            exhausted=[],
        ),
        "narrative": {
            "origin": "llm_authored",
            "disclaimer": (
                "The fields below are the generator's ANALYSIS. They are neither "
                "the approved plan nor a mechanical fact."
            ),
            "incomplete_extraction": "",
            "summary": "",
        },
    }


def merge_report(
    record: dict,
    report: dict,
    *,
    stage: str | None = None,
    status: str | None = None,
    now_ms: int | None = None,
) -> list[str]:
    """Merge an `nxd-diagnostic-report-v2` into a record's `stages`.

    Every stage the report carries diagnostics for is filled; `stage` (when
    given) is additionally recorded even if it produced no diagnostics, because
    a clean stage is a result and `not_reached` would be a lie about it.
    """
    problems = validate_report(report)
    if problems:
        return problems
    stamp = now_ms if now_ms is not None else _now_ms()

    diagnostics = [redact(diag) for diag in report.get("diagnostics") or []]
    touched: dict[str, list[dict]] = {}
    for diag in diagnostics:
        touched.setdefault(diag["stage"], []).append(diag)
    if stage:
        touched.setdefault(stage, [])

    for name, diags in touched.items():
        if name == "s0_spec" and stage != "s0_spec":
            # record init is the only writer of s0_spec.
            continue
        entry = record["stages"][name]
        entry["diagnostics"] = list(entry.get("diagnostics") or []) + diags
        entry["status"] = status_for(entry["diagnostics"])
        entry["at_unix_ms"] = stamp
        entry["origin"] = _report_origin(report, diags)

    if status:
        if status not in STAGE_STATUSES:
            return [f"unknown stage status {status!r}"]
        if status == "skipped" and stage not in SKIPPABLE_STAGES:
            return [
                f"{stage!r} may never be skipped — only "
                f"{' and '.join(SKIPPABLE_STAGES)} can be"
            ]
        record["stages"][stage]["status"] = status
        record["stages"][stage]["at_unix_ms"] = stamp

    _merge_readback(record, diagnostics)
    return []


def _report_origin(report: dict, diags: list[dict]) -> str:
    """A stage is only as strong as the weakest claim in it."""
    if report.get("tool") in ("validate_dp_spec", "self_check", "dp_diagnostics"):
        return "tool_computed"
    origins = {d.get("origin") for d in diags}
    if origins == {"supervisor_reported"}:
        return "supervisor_reported"
    return "agent_observed"


def _merge_readback(record: dict, diagnostics: list[dict]) -> None:
    """The distribution read-back as DATA, not printed prose (§2.6)."""
    readback = record.setdefault("readback", {"distribution": [], "absent": []})
    for diag in diagnostics:
        ev = diag.get("evidence") or {}
        if diag.get("code") in ("semantic.distribution", "semantic.uniform_column"):
            readback.setdefault("distribution", []).append(
                {
                    "model": ev.get("model"),
                    "column": ev.get("column"),
                    "values": ev.get("values") or [],
                    "uniform": bool(ev.get("uniform"))
                    or diag.get("code") == "semantic.uniform_column",
                    "origin": diag.get("origin", "tool_computed"),
                }
            )
        elif diag.get("code") == "semantic.absent_vocabulary":
            readback.setdefault("absent", []).append(
                {
                    "source": ev.get("source") or ev.get("table"),
                    "column": ev.get("column"),
                    "declared_missing": ev.get("declared_missing") or [],
                    "origin": diag.get("origin", "tool_computed"),
                }
            )


def append_attempt(record: dict, attempt: dict) -> list[str]:
    """Append one attempt, enforcing INVARIANT-D2.

    For every attempt with kind in (heal, retry, remap),
    `spec_hash_before == spec_hash_after`. A heal that moved the hash edited the
    IR to make the build pass. That is a spec edit requiring re-approval, and
    this is the single place where "a compiler does not edit your source" stops
    being advice: the attempt is forced to `exit: "blocked"`, a
    `blocker.spec_edit_required` is filed, and the user is asked.

    ORDERING NOTE: the legitimate open_questions write-back happens AFTER the
    attempt is recorded with `exit: "blocked"`, so both hashes record the
    pre-write-back value and are equal — truthfully, because the attempt itself
    never edited the IR. Divergence of the LIVE IR from the lock is
    `plan_moved`, reported by `materialized`, and never an accusation here.
    """
    problems = validate_attempt(attempt)
    if problems:
        return problems

    entry = dict(attempt)
    entry.setdefault("attempt", len(record.get("attempts") or []) + 1)
    entry.setdefault("started_at_unix_ms", _now_ms())
    entry.setdefault("origin", "agent_observed")
    entry.setdefault("changed", [])
    entry.setdefault("concessions", [])

    if entry["kind"] in HASH_FROZEN_KINDS and entry.get(
        "spec_hash_before"
    ) != entry.get("spec_hash_after"):
        entry["exit"] = "blocked"
        record.setdefault("attempts", []).append(entry)
        record.setdefault("blockers", []).append(
            {
                "code": "blocker.spec_edit_required",
                "open_question_id": None,
                "question": (
                    "This build only reaches green by changing the plan. That is "
                    "a spec edit and needs your re-approval, not a fix on my side."
                ),
                "blocks": [],
                "disposition": "blocked",
                "stage": entry.get("stage", "s0_spec"),
                "path": "v2:frontmatter.status",
                "discovered": "build_time",
                "written_back": False,
                "at_unix_ms": _now_ms(),
                "origin": "agent_observed",
            }
        )
        recompute_caps(record)
        return [
            "INVARIANT-D2: a "
            f"{entry['kind']!r} attempt moved the spec hash — the IR was edited to "
            "make the build pass. Recorded as blocked and routed to the user."
        ]

    record.setdefault("attempts", []).append(entry)
    recompute_caps(record)
    return []


def validate_attempt(attempt: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(attempt, dict):
        return ["attempt is not an object"]
    if attempt.get("kind") not in ATTEMPT_KINDS:
        problems.append(f"attempt kind {attempt.get('kind')!r} is outside the vocabulary")
    if attempt.get("stage") not in STAGES:
        problems.append(f"attempt stage {attempt.get('stage')!r} is outside the ladder")
    if attempt.get("exit") not in ATTEMPT_EXITS:
        problems.append(f"attempt exit {attempt.get('exit')!r} is outside the vocabulary")
    for key in ("spec_hash_before", "spec_hash_after"):
        if not isinstance(attempt.get(key), str) or not attempt.get(key):
            problems.append(f"attempt has no {key}")
    diagnosis = attempt.get("diagnosis")
    if not isinstance(diagnosis, dict) or not diagnosis.get("code"):
        problems.append("attempt has no diagnosis.code")
    elif diagnosis["code"] not in CODES:
        problems.append(f"attempt diagnosis code {diagnosis['code']!r} is not registered")
    return problems


def append_concession(record: dict, concession: dict) -> list[str]:
    """Append a DISCOURAGED concession.

    There is no `forbidden` concession: a forbidden invariant is never taken, it
    is escalated as a `blocker.forbidden_*`. A green run carrying an undisclosed
    concession is the worst state in the design, because it reads as
    materialized — so `disclosed` starts false and the materialization predicate
    is false until it flips.
    """
    if not isinstance(concession, dict):
        return ["concession is not an object"]
    code = concession.get("code")
    if code not in CODES or not str(code).startswith("concession."):
        return [f"concession code {code!r} is not a registered concession code"]
    if not concession.get("what"):
        return ["a concession must say what was done"]
    if code == "concession.other" and not concession.get("alternative_rejected"):
        return [
            "concession.other must name the alternative it rejected — silence is "
            "never the fallback"
        ]
    entry = dict(concession)
    entry.setdefault("class", "discouraged")
    entry.setdefault("stage", CODES[code]["stage"])
    entry.setdefault("path", "")
    entry.setdefault("why", "")
    entry.setdefault("alternative_rejected", "")
    entry.setdefault("attempt", len(record.get("attempts") or []))
    entry.setdefault("disclosed", False)
    entry.setdefault("at_unix_ms", _now_ms())
    entry.setdefault("origin", "llm_authored")
    entry["what"] = redact(entry["what"])
    if entry["class"] != "discouraged":
        return ["a concession is always 'discouraged'; a forbidden act is a blocker"]
    record.setdefault("concessions", []).append(entry)
    return []


def append_blocker(record: dict, blocker: dict) -> list[str]:
    """Append a blocker — an `open_questions` entry discovered late.

    There is no second mechanism. Writing it back into the live spec's
    `## open_questions` un-approves the spec, which is the design: the
    elicitation contract is a loop, not a pre-build-only gate.
    """
    if not isinstance(blocker, dict):
        return ["blocker is not an object"]
    code = blocker.get("code")
    if code not in CODES or CODES[code]["owner"] != "user":
        return [f"blocker code {code!r} is not a registered user-owned code"]
    if not blocker.get("question"):
        return ["a blocker must carry the question it is asking"]
    disposition = blocker.get("disposition", "blocked")
    if disposition not in DISPOSITIONS:
        return [f"blocker disposition {disposition!r} is outside the vocabulary"]
    entry = dict(blocker)
    entry["disposition"] = disposition
    entry.setdefault("open_question_id", None)
    entry.setdefault("blocks", [])
    entry.setdefault("stage", CODES[code]["stage"])
    entry.setdefault("path", "")
    entry.setdefault("discovered", "build_time")
    entry.setdefault("written_back", False)
    entry.setdefault("at_unix_ms", _now_ms())
    entry.setdefault("origin", "agent_observed")
    entry["question"] = redact(entry["question"])
    record.setdefault("blockers", []).append(entry)
    return []


def recompute_caps(record: dict) -> dict:
    """Counted from `attempts[]`, never estimated.

    This is what turns the prose caps in the skills into something the agent can
    check rather than self-police.
    """
    caps = record.setdefault("caps", dict(DEFAULT_CAPS))
    for key, value in DEFAULT_CAPS.items():
        caps.setdefault(key, value)
    attempts = record.get("attempts") or []
    caps["regenerates_used"] = sum(1 for a in attempts if a.get("kind") == "regenerate")
    remaps: dict[str, int] = {}
    for a in attempts:
        if a.get("kind") == "remap":
            qid = str(a.get("question") or "unattributed")
            remaps[qid] = remaps.get(qid, 0) + 1
    caps["remaps_used"] = remaps
    caps["retries_used"] = sum(
        1
        for a in attempts
        if a.get("kind") == "retry" and a.get("exit") == "retry_environmental"
    )
    exhausted: list[str] = []
    if caps["regenerates_used"] >= caps["regenerate_total"]:
        exhausted.append("regenerate_total")
    if any(v >= caps["remap_per_question"] for v in remaps.values()):
        exhausted.append("remap_per_question")
    if caps["retries_used"] >= caps["retry_environmental_total"]:
        # §1.4: an unfixable environment is a thing the user must hear about; a
        # retryable one is not. This is the counter that re-emission fires on.
        exhausted.append("retry_environmental")
    caps["exhausted"] = exhausted
    return caps


def validate_build_record(record: Any) -> list[str]:
    """Structural validation of `nxd-build-record-v2`.

    Hand-written rather than JSON-Schema-driven because this repo's scripts are
    stdlib-only and `jsonschema` is not a dependency. `BUILD_RECORD_SCHEMA` is
    the published shape; this is the enforcement of it.
    """
    problems: list[str] = []
    if not isinstance(record, dict):
        return ["build record is not an object"]
    if record.get("schema") != BUILD_RECORD_SCHEMA_ID:
        problems.append(f"schema must be {BUILD_RECORD_SCHEMA_ID!r}")

    required = (
        "schema",
        "workflow",
        "data_product",
        "closure_path",
        "compiled_from",
        "compiler_version",
        "generated_at_unix_ms",
        "generator_model",
        "stages",
        "attempts",
        "review_rounds",
        "concessions",
        "blockers",
        "readback",
        "evidence",
        "caps",
        "narrative",
    )
    for key in required:
        if key not in record:
            problems.append(f"missing required key {key!r}")
    for key in record:
        if key not in required:
            problems.append(f"unknown key {key!r}")

    compiler = record.get("compiler_version")
    if not isinstance(compiler, dict):
        problems.append("compiler_version must be an object")
    else:
        if set(compiler) != {"plugin", "generator_skill", "dp_spec_version", "canonicalization"}:
            problems.append("compiler_version must contain exactly plugin, generator_skill, dp_spec_version, canonicalization")
        version = compiler.get("dp_spec_version")
        expected_canonicalization = {
            2: CANONICALIZATION,
            3: _v3.CANONICALIZATION,
        }.get(version)
        if expected_canonicalization is None:
            problems.append("compiler_version.dp_spec_version must be 2 or 3")
        elif compiler.get("canonicalization") != expected_canonicalization:
            problems.append(f"compiler_version.canonicalization must be {expected_canonicalization!r}")
        for key in ("plugin", "generator_skill"):
            if not isinstance(compiler.get(key), str) or not compiler[key]:
                problems.append(f"compiler_version.{key} must be a non-empty string")

    stages = record.get("stages")
    if not isinstance(stages, dict):
        problems.append("stages must be an object keyed by the nine stage ids")
    else:
        for stage in STAGES:
            entry = stages.get(stage)
            if not isinstance(entry, dict):
                problems.append(f"stages.{stage} is missing")
                continue
            if entry.get("status") not in STAGE_STATUSES:
                problems.append(f"stages.{stage}.status {entry.get('status')!r} is unknown")
            if entry.get("status") == "skipped" and stage not in SKIPPABLE_STAGES:
                problems.append(
                    f"stages.{stage} is 'skipped', which only "
                    f"{' and '.join(SKIPPABLE_STAGES)} may be"
                )
            if entry.get("ordinal") != STAGES.index(stage):
                problems.append(f"stages.{stage}.ordinal is wrong")
            if entry.get("origin") not in ORIGINS:
                problems.append(f"stages.{stage}.origin {entry.get('origin')!r} is unknown")
            for i, diag in enumerate(entry.get("diagnostics") or []):
                for problem in validate_diagnostic(diag):
                    problems.append(f"stages.{stage}.diagnostics[{i}]: {problem}")
                if isinstance(diag, dict) and diag.get("stage") != stage:
                    problems.append(
                        f"stages.{stage}.diagnostics[{i}] carries stage "
                        f"{diag.get('stage')!r}"
                    )
        for key in stages:
            if key not in STAGES:
                problems.append(f"stages.{key} is not a stage id")

    for i, attempt in enumerate(record.get("attempts") or []):
        for problem in validate_attempt(attempt):
            problems.append(f"attempts[{i}]: {problem}")
        if isinstance(attempt, dict) and attempt.get("kind") in HASH_FROZEN_KINDS:
            if attempt.get("spec_hash_before") != attempt.get("spec_hash_after"):
                problems.append(
                    f"attempts[{i}]: INVARIANT-D2 — a {attempt.get('kind')!r} "
                    "attempt moved the spec hash; the IR was edited to make the "
                    "build pass"
                )

    review_rounds = record.get("review_rounds")
    if not isinstance(review_rounds, list):
        problems.append("review_rounds must be an array")
    else:
        for i, review_round in enumerate(review_rounds):
            for problem in validate_review_round(review_round):
                problems.append(f"review_rounds[{i}]: {problem}")

    for i, c in enumerate(record.get("concessions") or []):
        if not isinstance(c, dict):
            problems.append(f"concessions[{i}] is not an object")
            continue
        if c.get("code") not in CODES:
            problems.append(f"concessions[{i}]: unknown code {c.get('code')!r}")
        if c.get("class") != "discouraged":
            problems.append(
                f"concessions[{i}]: class must be 'discouraged' — a forbidden act "
                "is escalated as a blocker, never recorded as a concession"
            )
        if not isinstance(c.get("disclosed"), bool):
            problems.append(f"concessions[{i}]: disclosed must be a boolean")

    for i, b in enumerate(record.get("blockers") or []):
        if not isinstance(b, dict):
            problems.append(f"blockers[{i}] is not an object")
            continue
        if b.get("code") not in CODES:
            problems.append(f"blockers[{i}]: unknown code {b.get('code')!r}")
        elif CODES[b["code"]]["owner"] != "user":
            problems.append(f"blockers[{i}]: {b['code']!r} is not user-owned")
        if b.get("disposition") not in DISPOSITIONS:
            problems.append(f"blockers[{i}]: unknown disposition {b.get('disposition')!r}")
        if not isinstance(b.get("written_back"), bool):
            problems.append(f"blockers[{i}]: written_back must be a boolean")

    narrative = record.get("narrative")
    if isinstance(narrative, dict) and narrative.get("origin") != "llm_authored":
        problems.append(
            "narrative.origin must be 'llm_authored' — the narrative is the "
            "generator's analysis, not a measurement"
        )
    return problems


def validate_review_round(review_round: Any) -> list[str]:
    """Validate one bounded adversarial-review audit entry.

    A review produces claims, not executable instructions.  The record keeps
    those claims, their evidence, and one adjudication for every claim so a
    later run cannot silently treat a review as either accepted or ignored.
    The deadline is a time budget, deliberately not a finding-count budget.
    """
    if not isinstance(review_round, dict):
        return ["must be an object"]

    required = {
        "status",
        "started_at_unix_ms",
        "ended_at_unix_ms",
        "budget_ms",
        "findings",
        "adjudications",
        "user_decision",
        "deferred_finding_ids",
    }
    problems: list[str] = []
    for key in required - set(review_round):
        problems.append(f"missing required key {key!r}")
    for key in set(review_round) - required:
        problems.append(f"unknown key {key!r}")

    status = review_round.get("status")
    if not isinstance(status, str) or status not in REVIEW_STATUSES:
        problems.append(f"status {status!r} is unknown")

    def is_integer(value: object) -> bool:
        """Match JSON integer semantics rather than Python's bool subtype."""

        return isinstance(value, int) and not isinstance(value, bool)

    started = review_round.get("started_at_unix_ms")
    ended = review_round.get("ended_at_unix_ms")
    budget = review_round.get("budget_ms")
    if not is_integer(started):
        problems.append("started_at_unix_ms must be an integer")
    if not is_integer(ended):
        problems.append("ended_at_unix_ms must be an integer")
    if not is_integer(budget) or budget <= 0:
        problems.append("budget_ms must be a positive integer")
    if is_integer(started) and is_integer(ended) and ended < started:
        problems.append("ended_at_unix_ms must not precede started_at_unix_ms")
    if is_integer(started) and is_integer(ended) and is_integer(budget) and budget > 0:
        elapsed = ended - started
        if status == "timed_out" and elapsed < budget:
            problems.append("timed_out review ended before budget_ms")
        elif status in ("complete", "needs_user") and elapsed > budget:
            problems.append(f"{status} review exceeded budget_ms")

    findings = review_round.get("findings")
    finding_ids: set[str] = set()
    if not isinstance(findings, list):
        problems.append("findings must be an array")
    else:
        for i, finding in enumerate(findings):
            prefix = f"findings[{i}]"
            if not isinstance(finding, dict):
                problems.append(f"{prefix} must be an object")
                continue
            finding_required = {
                "id",
                "claim",
                "evidence",
                "classification",
                "proposed_effect",
                "applied_files",
                "state",
            }
            for key in finding_required - set(finding):
                problems.append(f"{prefix} missing required key {key!r}")
            for key in set(finding) - finding_required:
                problems.append(f"{prefix} unknown key {key!r}")
            finding_id = finding.get("id")
            if not isinstance(finding_id, str) or not finding_id:
                problems.append(f"{prefix}.id must be a non-empty string")
            elif finding_id in finding_ids:
                problems.append(f"{prefix}.id {finding_id!r} is duplicated")
            else:
                finding_ids.add(finding_id)
            if not isinstance(finding.get("claim"), str) or not finding.get("claim"):
                problems.append(f"{prefix}.claim must be a non-empty string")
            evidence = finding.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                problems.append(f"{prefix}.evidence must be a non-empty array")
            elif any(not isinstance(citation, str) or not citation for citation in evidence):
                problems.append(f"{prefix}.evidence entries must be non-empty strings")
            classification = finding.get("classification")
            if (
                not isinstance(classification, str)
                or classification not in REVIEW_CLASSIFICATIONS
            ):
                problems.append(
                    f"{prefix}.classification {finding.get('classification')!r} is unknown"
                )
            if not isinstance(finding.get("proposed_effect"), str) or not finding.get("proposed_effect"):
                problems.append(f"{prefix}.proposed_effect must be a non-empty string")
            applied_files = finding.get("applied_files")
            if not isinstance(applied_files, list):
                problems.append(f"{prefix}.applied_files must be an array")
            elif any(not isinstance(path, str) or not path for path in applied_files):
                problems.append(f"{prefix}.applied_files entries must be non-empty strings")
            finding_state = finding.get("state")
            if (
                not isinstance(finding_state, str)
                or finding_state not in REVIEW_FINDING_STATES
            ):
                problems.append(f"{prefix}.state {finding_state!r} is unknown")
            elif finding_state == "applied" and not applied_files:
                problems.append(f"{prefix}.applied state requires applied_files")
            elif finding_state != "applied" and applied_files:
                problems.append(f"{prefix}.applied_files must be empty unless state is applied")

    adjudications = review_round.get("adjudications")
    adjudicated_ids: set[str] = set()
    if not isinstance(adjudications, list):
        problems.append("adjudications must be an array")
    else:
        for i, adjudication in enumerate(adjudications):
            prefix = f"adjudications[{i}]"
            if not isinstance(adjudication, dict):
                problems.append(f"{prefix} must be an object")
                continue
            adjudication_required = {"finding_id", "disposition", "citation"}
            for key in adjudication_required - set(adjudication):
                problems.append(f"{prefix} missing required key {key!r}")
            for key in set(adjudication) - adjudication_required:
                problems.append(f"{prefix} unknown key {key!r}")
            finding_id = adjudication.get("finding_id")
            if not isinstance(finding_id, str) or not finding_id:
                problems.append(f"{prefix}.finding_id must be a non-empty string")
            elif finding_id in adjudicated_ids:
                problems.append(f"{prefix}.finding_id {finding_id!r} is duplicated")
            else:
                adjudicated_ids.add(finding_id)
            disposition = adjudication.get("disposition")
            if (
                not isinstance(disposition, str)
                or disposition not in REVIEW_DISPOSITIONS
            ):
                problems.append(f"{prefix}.disposition {disposition!r} is unknown")
            citation = adjudication.get("citation")
            if citation is not None and (not isinstance(citation, str) or not citation):
                problems.append(f"{prefix}.citation must be a non-empty string or null")
            if disposition == "rejected" and not citation:
                problems.append(f"{prefix}.rejected findings require a citation")

    if isinstance(findings, list) and isinstance(adjudications, list):
        missing = finding_ids - adjudicated_ids
        unknown = adjudicated_ids - finding_ids
        if missing:
            problems.append("findings without an adjudication: " + ", ".join(sorted(missing)))
        if unknown:
            problems.append("adjudications reference unknown finding ids: " + ", ".join(sorted(unknown)))

    user_decision = review_round.get("user_decision")
    if user_decision is not None:
        if not isinstance(user_decision, dict):
            problems.append("user_decision must be an object or null")
        else:
            decision_required = {"approved_at_unix_ms", "citation", "approved_finding_ids"}
            for key in decision_required - set(user_decision):
                problems.append(f"user_decision missing required key {key!r}")
            for key in set(user_decision) - decision_required:
                problems.append(f"user_decision unknown key {key!r}")
            if not is_integer(user_decision.get("approved_at_unix_ms")):
                problems.append("user_decision.approved_at_unix_ms must be an integer")
            if not isinstance(user_decision.get("citation"), str) or not user_decision.get("citation"):
                problems.append("user_decision.citation must be a non-empty string")
            approved_ids = user_decision.get("approved_finding_ids")
            if not isinstance(approved_ids, list) or any(
                not isinstance(finding_id, str) or not finding_id
                for finding_id in approved_ids
            ):
                problems.append("user_decision.approved_finding_ids must be an array of non-empty strings")
            elif len(approved_ids) != len(set(approved_ids)):
                problems.append("user_decision.approved_finding_ids must not contain duplicates")
            elif not set(approved_ids).issubset(finding_ids):
                problems.append("user_decision.approved_finding_ids reference unknown finding ids")
    deferred = review_round.get("deferred_finding_ids")
    deferred_ids: set[str] = set()
    if not isinstance(deferred, list) or any(
        not isinstance(finding_id, str) or not finding_id
        for finding_id in deferred
    ):
        problems.append("deferred_finding_ids must be an array of non-empty strings")
    else:
        deferred_ids = set(deferred)
        if len(deferred) != len(deferred_ids):
            problems.append("deferred_finding_ids must not contain duplicates")
        if not deferred_ids.issubset(finding_ids):
            problems.append("deferred_finding_ids reference unknown finding ids")
        if deferred_ids and user_decision is None:
            problems.append("deferred_finding_ids require an auditable user_decision")

    if isinstance(findings, list):
        states_by_id = {
            finding.get("id"): finding.get("state")
            for finding in findings
            if isinstance(finding, dict) and isinstance(finding.get("id"), str)
        }
        needs_user_ids = {
            finding_id
            for finding_id, finding_state in states_by_id.items()
            if finding_state == "needs_user"
        }
        applied_behavior_ids = {
            finding.get("id")
            for finding in findings
            if isinstance(finding, dict)
            and finding.get("classification") == "behavior_affecting"
            and finding.get("state") == "applied"
            and isinstance(finding.get("id"), str)
        }
        if status == "needs_user" and user_decision is None and not needs_user_ids:
            problems.append("needs_user review must carry a finding in needs_user state")
        if status != "needs_user" and needs_user_ids:
            problems.append("only a needs_user review may carry a finding in needs_user state")
        decision_data = user_decision if isinstance(user_decision, dict) else {}
        approved_ids = set(decision_data.get("approved_finding_ids") or [])
        if applied_behavior_ids - approved_ids:
            problems.append(
                "behavior_affecting applied findings require explicit user approval: "
                + ", ".join(sorted(applied_behavior_ids - approved_ids))
            )
        accepted_behavior_ids = _accepted_behavior_finding_ids(review_round)
        if deferred_ids - accepted_behavior_ids:
            problems.append(
                "deferred findings must be accepted behavior-affecting findings: "
                + ", ".join(sorted(deferred_ids - accepted_behavior_ids))
            )
        deferred_applied_ids = {
            finding_id
            for finding_id in deferred_ids
            if states_by_id.get(finding_id) == "applied"
        }
        if deferred_applied_ids:
            problems.append(
                "deferred findings must remain unapplied: "
                + ", ".join(sorted(deferred_applied_ids))
            )
        if isinstance(user_decision, dict):
            resolved_ids = applied_behavior_ids | deferred_ids
            unresolved_ids = accepted_behavior_ids - resolved_ids
            if unresolved_ids:
                problems.append(
                    "accepted behavior-affecting findings must be applied or explicitly deferred: "
                    + ", ".join(sorted(unresolved_ids))
                )
            unexpected_ids = resolved_ids - accepted_behavior_ids
            if unexpected_ids:
                problems.append(
                    "user decision may only resolve accepted behavior-affecting findings: "
                    + ", ".join(sorted(unexpected_ids))
                )
    return problems


# ---------------------------------------------------------------------------
# Materialization (§5)
# ---------------------------------------------------------------------------

def _accepted_behavior_finding_ids(review_round: dict) -> set[str]:
    """Return behavior-affecting claims the lead verified as real.

    They require a user choice even if no mutation has happened.  The reviewer
    cannot turn an accepted logical claim into permission merely by leaving it
    ``not_applied`` in a superficially complete round.
    """
    accepted_ids = {
        adjudication.get("finding_id")
        for adjudication in review_round.get("adjudications") or []
        if isinstance(adjudication, dict)
        and adjudication.get("disposition") == "accepted"
        and isinstance(adjudication.get("finding_id"), str)
    }
    return {
        finding.get("id")
        for finding in review_round.get("findings") or []
        if isinstance(finding, dict)
        and finding.get("id") in accepted_ids
        and finding.get("classification") == "behavior_affecting"
        and isinstance(finding.get("id"), str)
    }


def _review_user_decision_blockers(review_round: object) -> list[str]:
    """Why one review round keeps materialization fail-closed."""
    if not isinstance(review_round, dict):
        return ["malformed review round"]
    status = review_round.get("status")
    decision = review_round.get("user_decision")
    blockers: list[str] = []
    if status == "needs_user" and decision is None:
        blockers.append("review round is marked needs_user")
    if status == "timed_out" and decision is None:
        blockers.append("timed_out review has no auditable user decision to continue")
    accepted_behavior_ids = _accepted_behavior_finding_ids(review_round)
    if accepted_behavior_ids and decision is None:
        blockers.append(
            "accepted behavior-affecting finding(s) need a user decision: "
            + ", ".join(sorted(accepted_behavior_ids))
        )
    if isinstance(decision, dict):
        approved_ids = set(decision.get("approved_finding_ids") or [])
        deferred_ids = set(review_round.get("deferred_finding_ids") or [])
        applied_ids = {
            finding.get("id")
            for finding in review_round.get("findings") or []
            if isinstance(finding, dict)
            and finding.get("classification") == "behavior_affecting"
            and finding.get("state") == "applied"
            and isinstance(finding.get("id"), str)
        }
        unapproved_applied = applied_ids - approved_ids
        if unapproved_applied:
            blockers.append(
                "applied behavior-affecting finding(s) lack user approval: "
                + ", ".join(sorted(unapproved_applied))
            )
        unresolved_ids = accepted_behavior_ids - applied_ids - deferred_ids
        if unresolved_ids:
            blockers.append(
                "accepted behavior-affecting finding(s) were neither applied nor deferred: "
                + ", ".join(sorted(unresolved_ids))
            )
    return blockers

_REQUIRED_GREEN = (
    "s0_spec",
    "s1_structure",
    "s2_transform",
    "s3_closure",
    "s4_pin",
    "s5_serve",
    "s6_run",
)
_GREEN = ("passed", "passed_with_warnings")


def materialized(record: dict, lock: dict | None = None, live_spec_hash: str | None = None) -> bool:
    """Was the approved plan compiled, did the artifact run, did it publish?

    Call it `materialized`, NEVER `correct`. A green run proves the closure is
    structurally sound and the transform ran, nothing more. It says nothing
    about whether the numbers are right.

    `evidence.source_state` never appears here. Source-data staleness is a
    separate axis; merging it would make a correctly-built product read as
    broken because its input is a day old.
    """
    stages = record.get("stages") or {}
    if lock is not None and record.get("compiled_from") != lock.get("spec_hash"):
        return False
    if live_spec_hash is not None:
        reference = (lock or {}).get("spec_hash", record.get("compiled_from"))
        if live_spec_hash != reference:
            return False
    for stage in _REQUIRED_GREEN:
        if (stages.get(stage) or {}).get("status") not in _GREEN:
            return False
    if (stages.get("s7_publish") or {}).get("status") not in _GREEN + ("skipped",):
        return False
    if (stages.get("s8_answer") or {}).get("status") == "failed":
        return False
    if not all(c.get("disclosed") for c in record.get("concessions") or []):
        return False
    if any(b.get("disposition") == "blocked" for b in record.get("blockers") or []):
        return False
    if any(
        _review_user_decision_blockers(review_round)
        for review_round in record.get("review_rounds") or []
    ):
        return False
    for attempt in record.get("attempts") or []:
        if attempt.get("kind") in HASH_FROZEN_KINDS:
            if attempt.get("spec_hash_before") != attempt.get("spec_hash_after"):
                return False
    return True


def _failing_stages(record: dict) -> list[str]:
    stages = record.get("stages") or {}
    return [s for s in STAGES if (stages.get(s) or {}).get("status") == "failed"]


def _stage_errors(record: dict, stage: str) -> list[dict]:
    entry = (record.get("stages") or {}).get(stage) or {}
    return [d for d in entry.get("diagnostics") or [] if d.get("severity") == "error"]


def _stage_is_environmental(record: dict, stage: str) -> bool:
    """Whether ONE failing stage carries its own supervisor-authored evidence.

    A stage with no error diagnostics returns False: silence is not evidence,
    and a stage nobody can vouch for must never read as environmental. This is
    the per-stage half of the §1.8.1 relay criterion — the per-diagnostic half
    lives in `validate_diagnostic`.
    """
    errors = _stage_errors(record, stage)
    if not errors:
        return False
    return all(
        d.get("owner") == "environment"
        and d.get("origin") == "supervisor_reported"
        and (d.get("evidence") or {}).get("supervisor_detail")
        and STAGES.index(d.get("stage", "s4_pin")) >= STAGES.index("s5_serve")
        for d in errors
    )


def materialization_state(
    record: dict, lock: dict | None = None, live_spec_hash: str | None = None
) -> dict:
    """The distinguishable not-materialized states (§5.1).

    Order of evaluation is the table's order and the first match is reported.
    `needs_user` outranks `plan_moved` deliberately: a build-time blocker
    written back into `open_questions` moves the live hash AS A CONSEQUENCE of
    the blocker, and reporting `plan_moved` first would tell the agent to
    regenerate against a spec that still carries the unanswered question. `why`
    lists every matching condition, so nothing is hidden — only deprioritized.
    """
    why: list[str] = []
    stages = record.get("stages") or {}
    reference = (lock or {}).get("spec_hash", record.get("compiled_from"))

    hash_ok = True
    if lock is not None and record.get("compiled_from") != lock.get("spec_hash"):
        hash_ok = False
        why.append("build record compiled_from does not match the lock's spec_hash")
    plan_moved = live_spec_hash is not None and live_spec_hash != reference
    if plan_moved:
        why.append("the live spec's canonical hash has moved away from the lock")

    blocked = [b for b in record.get("blockers") or [] if b.get("disposition") == "blocked"]
    if blocked:
        why.append(f"{len(blocked)} blocker(s) are waiting on the user")

    review_blocks = [
        blocker
        for review_round in record.get("review_rounds") or []
        for blocker in _review_user_decision_blockers(review_round)
    ]
    if review_blocks:
        why.extend(review_blocks)

    failing = _failing_stages(record)
    offline_failing = [s for s in failing if s in OFFLINE_STAGES]
    # s8_answer is deliberately NOT in this set. A stage-8 failure is a fully
    # green build that answered wrongly — `awaiting_answer`, whose remedy is to
    # refine the question, not to heal or retry the closure. Folding it in with
    # s4-s7 would make `awaiting_answer` unreachable.
    late_failing = [
        s for s in failing if s not in OFFLINE_STAGES and s != "s8_answer"
    ]
    if "s8_answer" in failing:
        why.append("the build is green and the answer came back wrong")
    if offline_failing:
        why.append(
            "offline stage(s) failed: " + ", ".join(offline_failing) + " — never environmental"
        )
    if late_failing:
        why.append("stage(s) failed after the offline ladder: " + ", ".join(late_failing))

    undisclosed = [
        c for c in record.get("concessions") or [] if not c.get("disclosed")
    ]
    if undisclosed:
        why.append(f"{len(undisclosed)} concession(s) have not been disclosed")

    not_reached = [
        s for s in _REQUIRED_GREEN if (stages.get(s) or {}).get("status") == "not_reached"
    ]
    if not_reached:
        why.append("stage(s) not reached: " + ", ".join(not_reached))

    for attempt in record.get("attempts") or []:
        if attempt.get("kind") in HASH_FROZEN_KINDS and attempt.get(
            "spec_hash_before"
        ) != attempt.get("spec_hash_after"):
            why.append(
                f"attempt {attempt.get('attempt')} ({attempt.get('kind')}) moved the "
                "spec hash — INVARIANT-D2"
            )

    if materialized(record, lock, live_spec_hash):
        return {"materialized": True, "state": "materialized", "why": why}

    if blocked or review_blocks:
        state = "needs_user"
    elif plan_moved:
        state = "plan_moved"
    elif offline_failing or not hash_ok:
        state = "code_wrong"
    elif late_failing:
        # Fail closed, PER STAGE. Reaching `environment_suspect` needs every
        # failing stage to CONTRIBUTE supervisor-authored evidence — not merely
        # to fail to contradict another stage's. Flattening the diagnostics
        # across stages first would let a stage that failed with an EMPTY
        # diagnostics list ride on a different stage's payload: `all()` never
        # sees it, so a stage nobody can vouch for reads as environmental and
        # the agent is told to retry against what may be a pure code bug.
        # A real supervisor produces exactly that shape (a pin failure with no
        # per-stage diagnostics attached), so this is not a hypothetical.
        environmental = bool(late_failing) and all(
            _stage_is_environmental(record, s) for s in late_failing
        )
        state = "environment_suspect" if environmental else "unsettled"
    elif undisclosed:
        state = "undisclosed_concession"
    elif (stages.get("s8_answer") or {}).get("status") == "failed":
        state = "awaiting_answer"
    else:
        state = "in_progress"
    return {"materialized": False, "state": state, "why": why}


# ---------------------------------------------------------------------------
# Schemas (§1.9, §2.12, §8.2)
# ---------------------------------------------------------------------------

DIAGNOSTIC_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": DIAGNOSTIC_SCHEMA_ID,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema",
        "stage",
        "code",
        "severity",
        "owner",
        "origin",
        "path",
        "message",
    ],
    "properties": {
        "schema": {"const": DIAGNOSTIC_SCHEMA_ID},
        "stage": {"enum": list(STAGES)},
        "code": {
            "type": "string",
            "pattern": r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,2}$",
        },
        "severity": {"enum": list(SEVERITIES)},
        "owner": {"enum": list(OWNERS)},
        "origin": {"enum": list(ORIGINS)},
        "path": {"type": "string"},
        "message": {"type": "string", "minLength": 1},
        "evidence": {"type": "object", "default": {}},
        "fix": {"type": ["string", "null"]},
    },
}

SPEC_DIAGNOSTIC_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": SPEC_DIAGNOSTIC_SCHEMA_ID,
    "title": "Field-addressed v2 dp-spec diagnostic",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema", "code", "path", "severity", "owner", "control",
        "stage", "origin", "message",
    ],
    "properties": {
        "schema": {"const": SPEC_DIAGNOSTIC_SCHEMA_ID},
        "code": {"type": "string", "minLength": 1},
        "path": {"type": "string", "minLength": 1},
        "severity": {"const": "error"},
        "owner": {"enum": ["agent", "user"]},
        "control": {"enum": list(CONTROLS)},
        "stage": {"const": "s0_spec"},
        "origin": {"const": "tool_computed"},
        "message": {"type": "string", "minLength": 1},
    },
}

REPORT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": REPORT_SCHEMA_ID,
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "tool", "target", "ok", "counts", "spec_hash", "diagnostics"],
    "properties": {
        "schema": {"const": REPORT_SCHEMA_ID},
        "tool": {"enum": sorted(REPORT_TOOLS)},
        "target": {"type": ["string", "null"]},
        "ok": {"type": "boolean"},
        "counts": {
            "type": "object",
            "additionalProperties": False,
            "required": list(SEVERITIES),
            "properties": {sev: {"type": "integer", "minimum": 0} for sev in SEVERITIES},
        },
        "spec_hash": {"type": ["string", "null"]},
        "diagnostics": {
            "type": "array",
            "items": {
                "anyOf": [
                    {"$ref": DIAGNOSTIC_SCHEMA_ID},
                    {"$ref": "#/$defs/spec_diagnostic"},
                ]
            },
        },
    },
    "$defs": {"spec_diagnostic": SPEC_DIAGNOSTIC_SCHEMA},
}

LOCK_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": LOCK_SCHEMA_ID,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema",
        "spec_hash",
        "canonicalization",
        "snapshot",
        "snapshot_sha256",
        "spec_status_at_copy",
        "dp_spec_version",
        "name",
        "workflow",
        "source_basename",
        "contract_names",
        "compiler_version",
        "copied_at_unix_ms",
    ],
    "properties": {
        "schema": {"const": LOCK_SCHEMA_ID},
        "spec_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "canonicalization": {"const": CANONICALIZATION},
        "snapshot": {"type": "string", "minLength": 1},
        "snapshot_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "spec_status_at_copy": {"enum": list(STATUS_VALUES)},
        "dp_spec_version": {"const": SPEC_VERSION},
        "name": {"type": "string"},
        "workflow": {"type": "string"},
        "source_basename": {"type": "string"},
        "contract_names": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "compiler_version": {
            "type": "object",
            "additionalProperties": False,
            "required": ["plugin", "generator_skill", "self_check"],
            "properties": {
                "plugin": {"type": "string"},
                "generator_skill": {"type": "string"},
                "self_check": {"type": "string"},
            },
        },
        "copied_at_unix_ms": {"type": "integer"},
    },
}

_ORIGIN_ENUM = {"enum": list(ORIGINS)}

BUILD_RECORD_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": BUILD_RECORD_SCHEMA_ID,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema",
        "workflow",
        "data_product",
        "closure_path",
        "compiled_from",
        "compiler_version",
        "generated_at_unix_ms",
        "generator_model",
        "stages",
        "attempts",
        "review_rounds",
        "concessions",
        "blockers",
        "readback",
        "evidence",
        "caps",
        "narrative",
    ],
    "properties": {
        "schema": {"const": BUILD_RECORD_SCHEMA_ID},
        "workflow": {"type": "string"},
        "data_product": {"type": "string"},
        "closure_path": {"type": "string"},
        "compiled_from": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "compiler_version": {
            "type": "object",
            "additionalProperties": False,
            "required": ["plugin", "generator_skill", "dp_spec_version", "canonicalization"],
            "properties": {
                "plugin": {"type": "string"},
                "generator_skill": {"type": "string"},
                "dp_spec_version": {"enum": [2, _v3.SPEC_VERSION]},
                "canonicalization": {"enum": [CANONICALIZATION, _v3.CANONICALIZATION]},
            },
        },
        "generated_at_unix_ms": {"type": "integer"},
        "generator_model": {"type": "string"},
        "stages": {
            "type": "object",
            "additionalProperties": False,
            "required": list(STAGES),
            "properties": {
                stage: {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "status",
                        "ordinal",
                        "at_unix_ms",
                        "origin",
                        "diagnostics",
                        "detail",
                    ],
                    "properties": {
                        "status": {
                            "enum": list(STAGE_STATUSES)
                            if stage in SKIPPABLE_STAGES
                            else [s for s in STAGE_STATUSES if s != "skipped"]
                        },
                        "ordinal": {"const": i},
                        "at_unix_ms": {"type": ["integer", "null"]},
                        "origin": _ORIGIN_ENUM,
                        "diagnostics": {
                            "type": "array",
                            "items": {"$ref": DIAGNOSTIC_SCHEMA_ID},
                        },
                        "detail": {"type": "object"},
                    },
                }
                for i, stage in enumerate(STAGES)
            },
        },
        "attempts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "attempt",
                    "kind",
                    "stage",
                    "started_at_unix_ms",
                    "origin",
                    "diagnosis",
                    "changed",
                    "spec_hash_before",
                    "spec_hash_after",
                    "exit",
                ],
                "properties": {
                    "attempt": {"type": "integer"},
                    "kind": {"enum": list(ATTEMPT_KINDS)},
                    "stage": {"enum": list(STAGES)},
                    "started_at_unix_ms": {"type": "integer"},
                    "origin": _ORIGIN_ENUM,
                    "question": {"type": ["string", "null"]},
                    "diagnosis": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["code", "path", "summary"],
                        "properties": {
                            "code": {"type": "string"},
                            "path": {"type": "string"},
                            # LLM prose: the agent's account of what it thought
                            # was wrong. Not evidence.
                            "summary": {"type": "string"},
                        },
                    },
                    "changed": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["file", "what"],
                            "properties": {
                                "file": {"type": "string"},
                                # LLM prose, as above.
                                "what": {"type": "string"},
                            },
                        },
                    },
                    "spec_hash_before": {"type": "string"},
                    "spec_hash_after": {"type": "string"},
                    "rerun": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["stage", "status"],
                        "properties": {
                            "stage": {"enum": list(STAGES)},
                            "status": {"enum": list(STAGE_STATUSES)},
                            "diagnostics_cleared": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "diagnostics_new": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                    "exit": {"enum": list(ATTEMPT_EXITS)},
                    "concessions": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "review_rounds": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "status",
                    "started_at_unix_ms",
                    "ended_at_unix_ms",
                    "budget_ms",
                    "findings",
                    "adjudications",
                    "deferred_finding_ids",
                    "user_decision",
                ],
                "properties": {
                    "status": {"enum": list(REVIEW_STATUSES)},
                    "started_at_unix_ms": {"type": "integer"},
                    "ended_at_unix_ms": {"type": "integer"},
                    "budget_ms": {"type": "integer", "minimum": 1},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "id",
                                "claim",
                                "evidence",
                                "classification",
                                "proposed_effect",
                                "applied_files",
                                "state",
                            ],
                            "properties": {
                                "id": {"type": "string", "minLength": 1},
                                "claim": {"type": "string", "minLength": 1},
                                "evidence": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {"type": "string", "minLength": 1},
                                },
                                "classification": {"enum": list(REVIEW_CLASSIFICATIONS)},
                                "proposed_effect": {"type": "string", "minLength": 1},
                                "applied_files": {
                                    "type": "array",
                                    "items": {"type": "string", "minLength": 1},
                                },
                                "state": {"enum": list(REVIEW_FINDING_STATES)},
                            },
                        },
                    },
                    "adjudications": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["finding_id", "disposition", "citation"],
                            "properties": {
                                "finding_id": {"type": "string", "minLength": 1},
                                "disposition": {"enum": list(REVIEW_DISPOSITIONS)},
                                "citation": {"type": ["string", "null"], "minLength": 1},
                            },
                            "allOf": [
                                {
                                    "if": {"properties": {"disposition": {"const": "rejected"}}},
                                    "then": {
                                        "properties": {"citation": {"type": "string", "minLength": 1}}
                                    },
                                }
                            ],
                        },
                    },
                    "deferred_finding_ids": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "user_decision": {
                        "type": ["object", "null"],
                        "additionalProperties": False,
                        "required": [
                            "approved_at_unix_ms",
                            "citation",
                            "approved_finding_ids",
                        ],
                        "properties": {
                            "approved_at_unix_ms": {"type": "integer"},
                            "citation": {"type": "string", "minLength": 1},
                            "approved_finding_ids": {
                                "type": "array",
                                "uniqueItems": True,
                                "items": {"type": "string", "minLength": 1},
                            },
                        },
                    },
                },
            },
        },
        "concessions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "code",
                    "class",
                    "stage",
                    "path",
                    "what",
                    "why",
                    "alternative_rejected",
                    "attempt",
                    "disclosed",
                    "at_unix_ms",
                    "origin",
                ],
                "properties": {
                    "code": {"type": "string"},
                    "class": {"const": "discouraged"},
                    "stage": {"enum": list(STAGES)},
                    "path": {"type": "string"},
                    "what": {"type": "string"},
                    "why": {"type": "string"},
                    "alternative_rejected": {"type": "string"},
                    "attempt": {"type": "integer"},
                    "disclosed": {"type": "boolean"},
                    "at_unix_ms": {"type": "integer"},
                    "origin": _ORIGIN_ENUM,
                },
            },
        },
        "blockers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "code",
                    "open_question_id",
                    "question",
                    "blocks",
                    "disposition",
                    "stage",
                    "path",
                    "discovered",
                    "written_back",
                    "at_unix_ms",
                    "origin",
                ],
                "properties": {
                    "code": {"type": "string"},
                    "open_question_id": {"type": ["string", "null"]},
                    "question": {"type": "string"},
                    "blocks": {"type": "array", "items": {"type": "string"}},
                    "disposition": {"enum": list(DISPOSITIONS)},
                    "stage": {"enum": list(STAGES)},
                    "path": {"type": "string"},
                    "discovered": {"enum": ["pre_build", "build_time"]},
                    "written_back": {"type": "boolean"},
                    "at_unix_ms": {"type": "integer"},
                    "origin": _ORIGIN_ENUM,
                },
            },
        },
        "readback": {
            "type": "object",
            "additionalProperties": False,
            "required": ["distribution", "absent"],
            "properties": {
                "distribution": {"type": "array", "items": {"type": "object"}},
                "absent": {"type": "array", "items": {"type": "object"}},
            },
        },
        # phase_b_row_counts and published_row_counts are SEPARATE keys and must
        # never be merged: one is a dry run against a temporary database, the
        # other is what shipped.
        "evidence": {"type": "object"},
        "caps": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "remap_per_question",
                "regenerate_total",
                "retry_environmental_total",
                "regenerates_used",
                "remaps_used",
                "retries_used",
                "exhausted",
            ],
            "properties": {
                "remap_per_question": {"type": "integer"},
                "regenerate_total": {"type": "integer"},
                "retry_environmental_total": {"type": "integer"},
                "regenerates_used": {"type": "integer"},
                "remaps_used": {"type": "object"},
                "retries_used": {"type": "integer"},
                "exhausted": {"type": "array", "items": {"type": "string"}},
            },
        },
        "narrative": {
            "type": "object",
            "additionalProperties": False,
            "required": ["origin", "disclaimer", "incomplete_extraction", "summary"],
            "properties": {
                "origin": {"const": "llm_authored"},
                "disclaimer": {"type": "string"},
                "incomplete_extraction": {"type": "string"},
                "summary": {"type": "string"},
            },
        },
    },
}


def spec_schema() -> dict:
    """Emit the single section-specific v2 schema used by the harness."""
    return _v2.SCHEMA


# ---------------------------------------------------------------------------
# record init / append / query (§2.2)
# ---------------------------------------------------------------------------

def record_init(
    record_path: Path,
    lock_path: Path,
    *,
    spec_report: dict | None = None,
    generator_model: str = "unknown",
    now_ms: int | None = None,
) -> dict:
    """Write the envelope and fill `s0_spec` in the same pass.

    `record init` is the ONLY writer of `s0_spec`. Every other stage has an
    obvious producer and this one had none, which would leave it `not_reached`
    forever and make the materialization predicate permanently false — a fully
    green, published product stuck at `in_progress`.

    It validates the SNAPSHOT, not the live IR: the snapshot is what the closure
    was compiled from. Re-validating after an `open_questions` write-back does
    not update it — `s0_spec` describes the plan the closure was built from and
    is frozen with it. The live IR having moved is `plan_moved`, not an
    `s0_spec` regression.
    """
    lock_path = resolve_lock_path(lock_path)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock_problems = validate_lock(lock)
    if lock_problems:
        raise SpecReadError(
            f"{lock_path.name} is not a complete v2 lock: {'; '.join(lock_problems)}"
        )
    closure = record_path.parent
    if lock.get("schema") == V3_LOCK_SCHEMA_ID:
        for field in ("snapshot", "proposal_snapshot"):
            reference = Path(str(lock[field]))
            if reference.is_absolute() or ".." in reference.parts or not _stays_within_closure(closure, closure / reference):
                raise SpecReadError(
                    f"the v3 lock {field} reference escapes the closure: {reference}"
                )
    record = new_build_record(
        lock, closure, generator_model=generator_model, now_ms=now_ms
    )

    if spec_report is None:
        snapshot_path = closure / str(lock.get("snapshot") or CLOSURE_SNAPSHOT)
        proposal_path = None
        if lock.get("schema") == V3_LOCK_SCHEMA_ID:
            proposal_path = closure / str(lock.get("proposal_snapshot") or V3_PROPOSAL_SNAPSHOT)
        spec_report = _validate_snapshot(snapshot_path, proposal_path)
    else:
        problems = validate_report(spec_report)
        if problems:
            raise SpecReadError(
                "the supplied --spec-report is not a valid "
                f"{REPORT_SCHEMA_ID}: {'; '.join(problems)}"
            )
        if spec_report.get("tool") != "validate_dp_spec":
            raise SpecReadError(
                f"--spec-report must be a {'validate_dp_spec'!r} report, not "
                f"{spec_report.get('tool')!r}"
            )
        if spec_report.get("spec_hash") != lock.get("spec_hash"):
            # Ingesting a report computed against different bytes would
            # silently certify the wrong plan.
            raise SpecReadError(
                "the --spec-report was computed against a different spec than "
                "the lock names: "
                f"{spec_report.get('spec_hash')} != {lock.get('spec_hash')}"
            )
    if lock.get("schema") == V3_LOCK_SCHEMA_ID:
        if spec_report.get("spec_hash") != lock.get("spec_hash"):
            raise SpecReadError(
                "the generated v3 spec report does not match the lock's spec hash: "
                f"{spec_report.get('spec_hash')} != {lock.get('spec_hash')}"
            )
        if spec_report.get("proposal_hash") != lock.get("proposal_hash"):
            raise SpecReadError(
                "the generated v3 spec report typed proposal hash does not match the lock: "
                f"{spec_report.get('proposal_hash')} != {lock.get('proposal_hash')}"
            )

    diags = _record_s0_diagnostics(spec_report)
    record["stages"]["s0_spec"].update(
        {
            "status": status_for(diags),
            "at_unix_ms": now_ms if now_ms is not None else _now_ms(),
            "origin": "tool_computed",
            "diagnostics": diags,
            "detail": {"counts": spec_report.get("counts", {})},
        }
    )
    write_record(record_path, record)
    return record


def _validate_snapshot(snapshot: Path, proposal: Path | None = None) -> dict:
    """Run the validator in-process against the snapshot.

    IMPORT DIRECTION: `validate_dp_spec` imports this module at module top, so
    this module must never import it at module top — that is a cycle. The import
    happens here, in the function body, by which point `dp_diagnostics` is fully
    loaded and already in `sys.modules`.
    """
    if not snapshot.is_file():
        raise SpecReadError(f"the snapshot {snapshot} does not exist")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import validate_dp_spec  # noqa: PLC0415 - deliberate, see docstring
    except Exception as exc:  # pragma: no cover - environment guard
        raise SpecReadError(
            f"could not import validate_dp_spec ({exc}) — re-run with "
            "--spec-report <report.json>. Writing s0_spec as not_reached would "
            "quietly recreate the hole record init exists to close."
        ) from exc
    report = validate_dp_spec.validate(snapshot, proposal)
    return report.to_dict() if hasattr(report, "to_dict") else report


def _record_s0_diagnostics(report: dict) -> list[dict]:
    """Adapt the v2 validator envelope to the build-record diagnostic shape.

    ``validate_dp_spec`` deliberately exposes field-addressed v2 findings so a
    Claude Desktop form can bind them to controls. Build records, however,
    carry the shared pipeline ``Diagnostic`` shape. Preserve the original v2
    code/path in evidence while using one registered bridge code; otherwise an
    invalid spec would make the record itself structurally invalid.
    """
    stored: list[dict] = []
    for item in report.get("diagnostics") or []:
        if isinstance(item, dict) and item.get("schema") == DIAGNOSTIC_SCHEMA_ID:
            stored.append(redact(item))
            continue
        if not isinstance(item, dict):
            item = {"message": str(item)}
        stored.append(
            diagnostic(
                "spec.v2.invalid",
                message=str(item.get("message") or "v2 spec validation failed"),
                path=str(item.get("path") or "spec"),
                evidence={
                    "validator_code": str(item.get("code") or "unknown"),
                    "validator_owner": str(item.get("owner") or "agent"),
                    "validator_control": str(item.get("control") or "text"),
                },
                stage="s0_spec",
            ).to_dict()
        )
    return stored


def read_record(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def query_record(record: dict, **filters) -> dict:
    """What failed, at which stage, who owns it, what changed between attempts.

    The point of this is that an agent reads structured data instead of
    re-reading prose logs.
    """
    stage = filters.get("stage")
    owner = filters.get("owner")
    severity = filters.get("severity")
    code = filters.get("code")
    unresolved = filters.get("unresolved")
    attempt_no = filters.get("attempt")

    hits: list[dict] = []
    for name in STAGES:
        entry = (record.get("stages") or {}).get(name) or {}
        if stage and name != stage:
            continue
        if unresolved and entry.get("status") != "failed":
            continue
        for diag in entry.get("diagnostics") or []:
            if owner and diag.get("owner") != owner:
                continue
            if severity and diag.get("severity") != severity:
                continue
            if code and diag.get("code") != code:
                continue
            if unresolved and diag.get("severity") != "error":
                continue
            hits.append(diag)

    attempts = record.get("attempts") or []
    if attempt_no is not None:
        attempts = [a for a in attempts if a.get("attempt") == attempt_no]
    if stage:
        attempts = [a for a in attempts if a.get("stage") == stage]

    blockers = [b for b in record.get("blockers") or []]
    concessions = [c for c in record.get("concessions") or []]
    if unresolved:
        blockers = [b for b in blockers if b.get("disposition") == "blocked"]
        concessions = [c for c in concessions if not c.get("disclosed")]

    return {
        "schema": REPORT_SCHEMA_ID,
        "tool": "dp_diagnostics",
        "target": record.get("closure_path"),
        "ok": not any(d.get("severity") == "error" for d in hits)
        and not blockers
        and not (unresolved and concessions),
        "counts": {
            sev: sum(1 for d in hits if d.get("severity") == sev) for sev in SEVERITIES
        },
        "spec_hash": record.get("compiled_from"),
        "diagnostics": hits,
        # Not part of the envelope a producer emits — this is a query result,
        # and these three are what the agent's queues are built from.
        "stages": {
            name: (record.get("stages") or {}).get(name, {}).get("status")
            for name in STAGES
        },
        "attempts": attempts,
        "blockers": blockers,
        "concessions": concessions,
        "caps": record.get("caps", {}),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _json_arg(value: str | None) -> Any:
    """Accept inline JSON or `@path/to/file.json`."""
    if value is None:
        return None
    if value.startswith("@"):
        return json.loads(Path(value[1:]).read_text(encoding="utf-8"))
    return json.loads(value)


def _print_report(report: dict) -> None:
    for diag in report.get("diagnostics") or []:
        label = {"error": "ERROR", "warning": "WARN ", "info": "INFO "}[diag["severity"]]
        where = f" {diag['path']}" if diag.get("path") else ""
        print(f"{label} {diag['code']}{where}: {diag['message']}")


def cmd_hash(args) -> int:
    raw = args.spec.read_bytes()
    value = spec_hash(raw)
    canonicalization = _v3.CANONICALIZATION if _spec_version(raw.decode("utf-8")) == _v3.SPEC_VERSION else CANONICALIZATION
    if args.json:
        _print_json({"spec": str(args.spec), "spec_hash": value, "canonicalization": canonicalization})
    else:
        print(value)
    return 0


def cmd_canonicalize(args) -> int:
    sys.stdout.write(canonical_bytes(args.spec.read_bytes()).decode("utf-8") + "\n")
    return 0


def cmd_emit(args) -> int:
    obj = json.loads(args.canonical.read_text(encoding="utf-8"))
    sys.stdout.write(emit(obj))
    return 0


def cmd_schema(args) -> int:
    if args.spec_diagnostic:
        _print_json(SPEC_DIAGNOSTIC_SCHEMA)
    elif args.diagnostic:
        _print_json(DIAGNOSTIC_SCHEMA)
    elif args.record:
        _print_json(BUILD_RECORD_SCHEMA)
    elif args.lock:
        _print_json(LOCK_SCHEMA)
    else:
        _print_json(spec_schema())
    return 0


def cmd_lock_write(args) -> int:
    lock, report = write_lock(
        args.spec,
        args.closure,
        proposal=args.proposal,
        plugin_version=args.plugin_version,
    )
    if not report.ok:
        if args.json:
            _print_json(report.to_dict())
        else:
            _print_report(report.to_dict())
            print("\nlock NOT written — fix the IR, never rewrite the copy.")
        return 1
    if args.json:
        _print_json(lock)
    else:
        print(f"snapshot  {args.closure / CLOSURE_SNAPSHOT}")
        print(f"lock      {args.closure / CLOSURE_LOCK}")
        print(f"spec_hash {lock['spec_hash']}")
        print(f"status at copy: {lock['spec_status_at_copy']}")
    return 0


def cmd_lock_verify(args) -> int:
    report = verify_lock(args.closure, args.spec)
    payload = report.to_dict()
    if args.json:
        _print_json(payload)
    else:
        _print_report(payload)
        if report.ok:
            scope = "snapshot" if args.spec is None else "snapshot and live spec"
            print(f"\nlock OK — canonical hash matches the lock ({scope}).")
        else:
            print(f"\nlock FAILED — {len(report.errors)} error(s).")
    return 0 if report.ok else 1


def cmd_record_init(args) -> int:
    try:
        record = record_init(
            args.record,
            args.lock,
            spec_report=_json_arg(f"@{args.spec_report}" if args.spec_report else None),
            generator_model=args.generator_model,
        )
    except _READ_FAILURES as exc:
        print(f"record init failed: {exc}", file=sys.stderr)
        return 2
    status = record["stages"]["s0_spec"]["status"]
    if args.json:
        _print_json(record)
    else:
        print(f"build record {args.record}")
        print(f"compiled_from {record['compiled_from']}")
        print(f"s0_spec {status}")
    return 0 if status in _GREEN else 1


def cmd_record_append(args) -> int:
    try:
        record = read_record(args.record)
    except (OSError, ValueError) as exc:
        print(f"could not read {args.record}: {exc}", file=sys.stderr)
        return 2

    if args.stage == "s0_spec":
        print(
            "record append --stage s0_spec is rejected — record init is the only "
            "writer of s0_spec, and it is frozen with the snapshot.",
            file=sys.stderr,
        )
        return 2

    problems: list[str] = []
    if args.source:
        try:
            report = json.loads(args.source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"could not read {args.source}: {exc}", file=sys.stderr)
            return 2
        tool = report.get("tool")
        if args.stage and tool in REPORT_TOOLS and args.stage not in REPORT_TOOLS[tool]:
            print(
                f"a {tool!r} report cannot carry stage {args.stage!r} "
                f"(allowed: {', '.join(REPORT_TOOLS[tool])})",
                file=sys.stderr,
            )
            return 2
        problems += merge_report(record, report, stage=args.stage, status=args.status)
    elif args.status:
        stamp = _now_ms()
        if args.status == "skipped" and args.stage not in SKIPPABLE_STAGES:
            print(
                f"{args.stage!r} may never be skipped — only "
                f"{' and '.join(SKIPPABLE_STAGES)} can be",
                file=sys.stderr,
            )
            return 2
        record["stages"][args.stage].update(
            {"status": args.status, "at_unix_ms": stamp, "origin": "agent_observed"}
        )

    if args.attempt:
        problems += append_attempt(record, _json_arg(args.attempt))
    if args.concession:
        problems += append_concession(record, _json_arg(args.concession))
    if args.blocker:
        problems += append_blocker(record, _json_arg(args.blocker))
    if args.disclose:
        hit = False
        for c in record.get("concessions") or []:
            if c.get("code") == args.disclose:
                c["disclosed"] = True
                hit = True
        if not hit:
            problems.append(f"no concession carries code {args.disclose!r}")
    if args.evidence:
        # `--evidence` is RECORD-level (build-wide facts: row counts, source
        # state). It never reaches a stage, and `--status` stamps
        # `origin: agent_observed` — so pairing it with `--stage` reads like
        # "attach this supervisor payload to that stage" while silently doing
        # something else. That is how a real supervisor's diagnostic.json gets
        # dropped on the floor at exit 0. Stage-level supervisor evidence has
        # exactly one route: `--from` with a diagnostic report, which carries
        # the code/severity/owner/origin the relay criterion checks.
        if args.stage:
            print(
                "--evidence is record-level and cannot carry stage evidence; "
                f"pairing it with --stage {args.stage} would drop the payload. "
                "Use --from <report.json> to attach a stage diagnostic, or drop "
                "--stage to record a build-wide fact.",
                file=sys.stderr,
            )
            return 2
        record.setdefault("evidence", {}).update(_json_arg(args.evidence))
    if args.narrative:
        record.setdefault("narrative", {}).update(_json_arg(args.narrative))

    recompute_caps(record)
    problems += validate_build_record(record)
    write_record(args.record, record)

    if problems:
        for problem in problems:
            print(f"ERROR {problem}", file=sys.stderr)
        return 1
    if args.json:
        _print_json(record["stages"][args.stage] if args.stage else record)
    else:
        if args.stage:
            print(f"{args.stage} {record['stages'][args.stage]['status']}")
        print(f"record updated: {args.record}")
    return 0


def cmd_record_query(args) -> int:
    try:
        record = read_record(args.record)
    except (OSError, ValueError) as exc:
        print(f"could not read {args.record}: {exc}", file=sys.stderr)
        return 2
    result = query_record(
        record,
        stage=args.stage,
        owner=args.owner,
        severity=args.severity,
        code=args.code,
        unresolved=args.unresolved,
        attempt=args.attempt,
    )
    if args.json:
        _print_json(result)
    else:
        _print_report(result)
        for blocker in result["blockers"]:
            print(f"BLOCKER {blocker['code']}: {blocker['question']}")
        for concession in result["concessions"]:
            flag = "disclosed" if concession.get("disclosed") else "UNDISCLOSED"
            print(f"CONCESSION [{flag}] {concession['code']}: {concession['what']}")
    return 0 if result["ok"] else 1


def cmd_materialized(args) -> int:
    try:
        record = read_record(args.record)
    except (OSError, ValueError) as exc:
        print(f"could not read {args.record}: {exc}", file=sys.stderr)
        return 2
    lock = None
    if args.lock:
        lock_path = resolve_lock_path(args.lock)
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"could not read {lock_path}: {exc}", file=sys.stderr)
            return 2
    live = None
    if args.spec:
        try:
            live = spec_hash(args.spec.read_bytes())
        except _READ_FAILURES as exc:
            print(f"could not hash {args.spec}: {exc}", file=sys.stderr)
            return 2

    result = materialization_state(record, lock, live)
    if args.json:
        _print_json(result)
    else:
        print(f"materialized: {result['materialized']}")
        print(f"state: {result['state']}")
        for why in result["why"]:
            print(f"  - {why}")
        if result["materialized"]:
            print(
                "\n'materialized' means the approved plan was compiled, it ran, "
                "and it published. It is NEVER a claim that the numbers are right."
            )
    return 0 if result["materialized"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnostics, canonical spec hashing, lock and build record."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_hash = sub.add_parser("hash", help="canonical semantic hash of a dp-blueprint.md")
    p_hash.add_argument("spec", type=Path)
    p_hash.add_argument("--json", action="store_true")
    p_hash.set_defaults(func=cmd_hash)

    p_canon = sub.add_parser("canonicalize", help="canonical JSON for a dp-blueprint.md")
    p_canon.add_argument("spec", type=Path)
    p_canon.add_argument("--json", action="store_true", help="accepted; output is JSON")
    p_canon.set_defaults(func=cmd_canonicalize)

    p_emit = sub.add_parser("emit", help="render canonical JSON back to markdown")
    p_emit.add_argument("canonical", type=Path)
    p_emit.add_argument("--json", action="store_true", help="accepted; output is markdown")
    p_emit.set_defaults(func=cmd_emit)

    p_schema = sub.add_parser("schema", help="machine-readable schemas")
    p_schema.add_argument("--json", action="store_true", help="the spec schema (default)")
    p_schema.add_argument("--diagnostic", action="store_true")
    p_schema.add_argument("--spec-diagnostic", action="store_true")
    p_schema.add_argument("--record", action="store_true")
    p_schema.add_argument("--lock", action="store_true")
    p_schema.set_defaults(func=cmd_schema)

    p_lock = sub.add_parser("lock", help="write or verify the spec snapshot lock")
    lock_sub = p_lock.add_subparsers(dest="lock_command", required=True)

    p_lw = lock_sub.add_parser("write", help="snapshot an approved spec into a closure")
    p_lw.add_argument("spec", type=Path)
    p_lw.add_argument("closure", type=Path)
    p_lw.add_argument("--proposal", type=Path, default=None, help="v3 typed proposal JSON snapshot")
    p_lw.add_argument("--plugin-version", default=None)
    p_lw.add_argument("--json", action="store_true")
    p_lw.set_defaults(func=cmd_lock_write)

    p_lv = lock_sub.add_parser("verify", help="canonical hash check (Phase C does bytes)")
    p_lv.add_argument("closure", type=Path)
    p_lv.add_argument("--spec", type=Path, default=None, help="also check the LIVE IR")
    p_lv.add_argument("--json", action="store_true")
    p_lv.set_defaults(func=cmd_lock_verify)

    p_record = sub.add_parser("record", help="the generated build record")
    rec_sub = p_record.add_subparsers(dest="record_command", required=True)

    p_ri = rec_sub.add_parser("init", help="write the envelope and fill s0_spec")
    p_ri.add_argument("--record", type=Path, required=True)
    p_ri.add_argument("--lock", type=Path, required=True)
    p_ri.add_argument("--spec-report", default=None)
    p_ri.add_argument("--generator-model", default="unknown")
    p_ri.add_argument("--json", action="store_true")
    p_ri.set_defaults(func=cmd_record_init)

    p_ra = rec_sub.add_parser("append", help="merge a stage result into the record")
    p_ra.add_argument("--record", type=Path, required=True)
    p_ra.add_argument("--stage", choices=list(STAGES), default=None)
    p_ra.add_argument("--from", dest="source", type=Path, default=None)
    p_ra.add_argument("--status", choices=list(STAGE_STATUSES), default=None)
    p_ra.add_argument("--attempt", default=None, help="attempt JSON, or @file.json")
    p_ra.add_argument("--concession", default=None, help="concession JSON, or @file.json")
    p_ra.add_argument("--blocker", default=None, help="blocker JSON, or @file.json")
    p_ra.add_argument("--disclose", default=None, help="flip a concession to disclosed")
    p_ra.add_argument("--evidence", default=None, help="evidence JSON, or @file.json")
    p_ra.add_argument("--narrative", default=None, help="narrative JSON, or @file.json")
    p_ra.add_argument("--json", action="store_true")
    p_ra.set_defaults(func=cmd_record_append)

    p_rq = rec_sub.add_parser("query", help="what failed, where, and who owns it")
    p_rq.add_argument("--record", type=Path, required=True)
    p_rq.add_argument("--stage", choices=list(STAGES), default=None)
    p_rq.add_argument("--owner", choices=list(OWNERS), default=None)
    p_rq.add_argument("--severity", choices=list(SEVERITIES), default=None)
    p_rq.add_argument("--code", default=None)
    p_rq.add_argument("--unresolved", action="store_true")
    p_rq.add_argument("--attempt", type=int, default=None)
    p_rq.add_argument("--json", action="store_true")
    p_rq.set_defaults(func=cmd_record_query)

    p_mat = sub.add_parser("materialized", help="the derived predicate, never 'correct'")
    p_mat.add_argument("--record", type=Path, required=True)
    p_mat.add_argument("--lock", type=Path, default=None)
    p_mat.add_argument("--spec", type=Path, default=None, help="the LIVE IR")
    p_mat.add_argument("--json", action="store_true")
    p_mat.set_defaults(func=cmd_materialized)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SpecReadError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"no such file: {exc.filename}", file=sys.stderr)
        return 2
    except _READ_FAILURES as exc:
        print(f"{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
