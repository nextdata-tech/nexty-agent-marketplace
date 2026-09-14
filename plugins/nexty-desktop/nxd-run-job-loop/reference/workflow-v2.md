# Supervisor workflow v2 construction

Use the supervisor workflow API for every new local construction when the
connected runtime advertises v2 execution. This path owns consent, capture,
review evidence, validation, admission, and publication; do not fall back to
legacy build or validation tools.

## Contents

- [Gate on the connected capability](#gate-on-the-connected-capability)
- [Prepare the prose blueprint](#prepare-the-prose-blueprint)
- [Relay consent and capture](#relay-consent-and-capture)
- [Run and report the review](#run-and-report-the-review)
- [Follow returned actions through admission](#follow-returned-actions-through-admission)
- [Reset after behavior changes](#reset-after-behavior-changes)

## Gate on the connected capability

Before starting construction, call `get_workflow_capabilities`. Continue only
when its structured response has `execution_enabled: true` and no unavailable
condition. A false or unavailable capability is a blocker. Do not call
`build_data_product`, `validate_data_product`, or a direct supervisor CLI as a
fallback for this construction path, and do not infer capability from a tool
catalog, a binary, or a previous session.

## Prepare the prose blueprint

Write and deterministically validate the prose `dp-blueprint.md` first. Then
write the exact, real typed proposal JSON beside it as
`dp-blueprint.proposal.json`. Parse that complete object and validate/bind it to
the blueprint before consent, then call `prepare_workflow` with the inline
`typed_proposal` object, before the approval turn and before generating the
closure:

```json
{
  "request_id": "prepare-<workflow>-<unique>",
  "workflow": "<workflow>",
  "kind": "generated-data-product",
  "blueprint_path": "/host-visible/nxd-jobs/<workflow>/dp-blueprint.md",
  "typed_proposal": {
    "schema": "nxd-dp-spec-proposal-v3",
    "authoring_version": "nxd-dp-spec-authoring-v1",
    "proposal": { "<complete typed proposal payload>": "from dp-blueprint.proposal.json" },
    "provenance": { "<complete provenance map>": "from dp-blueprint.proposal.json" },
    "source_spans": { "<complete source-span map>": "from dp-blueprint.proposal.json" },
    "anchors": { "<complete anchors map>": "from dp-blueprint.proposal.json" },
    "echo": { "text": "<complete natural-language echo-back>", "coverage": ["<all covered paths>"] }
  },
  "requested_transform_budget_secs": 60
}
```

The typed-v3 proposal's contract inventory is part of generation, not optional
closure decoration. Compile every `proposal.inputs[*].expectations[*]` and
`proposal.outputs[*].promises[*]` entry into exactly one verifier script and
exactly one matching `custom(...)` chain at its declared attachment and phase.
Carry the contract `id`, attachment, model, phase, guarantee, rule, and fields
unchanged. An ordinary `.promise(model)` is only the schema/model promise; it
does not satisfy a custom contract. Do not add extra custom contracts,
placeholders, or unwired scripts: capture/preflight reject a missing or extra
inventory. For an API source, fail and ask for a supported phase when a custom
input expectation cannot execute on the CSV-first runtime; never emit it as
decorative unwired code. Wire output promises when their runtime is supported,
and do not invent contracts from inferred schema facts.

Source spans are exact coordinates from the trusted parser, not approximate
Markdown locations. Copy all four integers (`line_start`, `line_end`,
`offset_start`, and `offset_end`) from the parser's source map for the matching
path. For a `###` subsection, the `.text` span covers the subsection body only:
the heading has its own source-map path, while the parser's exact block range
may include separator blank lines. Do not trim or widen that range. An anchor
maps the parser source path to a typed proposal path; omit it when the paths
already match. Do not calculate spans from memory; rerun the parser after every
blueprint edit and validate the exact proposal before calling `prepare_workflow`.

If `prepare_workflow` returns `v3.provenance.span_mismatch` with a structured
`validation_issue.expected_source_span`, replace only that path's coordinates
with the returned four integers and retry the exact proposal. Treat the hint as
coordinates, not permission to widen the span or alter the typed value.

Omit `source_hash` from the caller-authored object. The supervisor inserts or
replaces it with the canonical hash of the retained Markdown before validating
and binding the proposal; callers must not guess, hand-compute, or send a
pseudo-hash for this field.

Use the returned `revision`, `invalidation_epoch`, requirement identities,
subjects, and `next_actions` as the current state. The response is a durable
snapshot, not approval. Do not invent requirement ids, subjects, revisions, or
epochs from the contract or from local files.

## Relay consent and capture

Find the consent action in `next_actions`. After the user approves the exact
blueprint read-back, call `advance_workflow` with `session_decision`. Relay the
approval quote exactly as it appeared in the current session; do not summarize,
reword, or manufacture a second approval. Echo the supervisor-provided
`subject_sha256` and use the returned revision for the compare-and-swap.

Every advance uses the strict tagged envelope below. Generate a globally unique
`request_id` for each new operation; reuse one only to replay the exact same
request.

```json
{
  "request_id": "consent-<workflow>-<unique>",
  "workflow": "<workflow>",
  "expected_revision": 3,
  "action": {
    "type": "session_decision",
    "parameters": {
      "requirement_id": "<from-next-actions>",
      "subject_sha256": "<from-next-actions>",
      "quote": "<exact-user-message>",
      "session_ref": "<current-session-ref>",
      "message_ref": null,
      "approved": true
    }
  }
}
```

The approval authorizes the already presented plan and the proposal bound during
prepare. Generate the closure only after this action succeeds. Under the
shellless contract, do not run `dp_diagnostics.py lock write`, hand-author
reserved v3 metadata, or copy a checker into the closure before capture. Follow
the returned `next_actions` and call the indicated `capture` action with the
generated closure's host-visible authoring root. Capture is the supervisor's
retained, sealed input for all later work: it materializes and verifies
`dp-blueprint.approved.md`, `dp-blueprint.proposal.approved.json`,
`dp-blueprint.lock.json`, and the trusted `self_check.py`. Agent-authored copies
of those reserved surfaces are rejected. If helper tools exist, agent-side
self-check and lock verification are optional evidence only; they do not
replace supervisor capture or strict admission. Do not mutate that authoring
tree or run review/validation against a mutable path after capture.

When this flow is being run by the dp-scenarios harness, persist the short
construction attestations before `start_run`. Write the root JSON array to the
exact path in `NXD_EVAL_ATTESTATIONS_PATH` (or, when the variable cannot be
expanded by the file tool, `agent-attestations.json` at the agent workspace
root). Do not place it under `closure/`, `artifacts/`, or the review ledger.
Use only these objects: self-check is exactly
`{"action_kind":"self_check","outcome":"pass","evidence_ref":"nxd-jobs/<workflow>/closure/build-record.json#self_check"}`;
each retained-input review adds exactly
`{"action_kind":"adversarial_review","outcome":"complete","evidence_ref":"nxd-jobs/<workflow>/review-record.json#review_rounds/<index>","review_round_index":<index>}`.
Replace only `<workflow>` and `<index>`. The review ledger is the captured
closure's sibling, and every `evidence_ref` is relative to the agent workspace
root; do not shorten it to a bare filename.
The root array may also carry a positive-integer `turn` on an object, but no
other keys. Keep one indexed review attestation for every external
`review_rounds[]` entry, including after resets. This sidecar is a
non-authoritative harness observation; it never replaces the durable
`build-record.json` or `review-record.json` evidence.

```json
{
  "request_id": "capture-<workflow>-<unique>",
  "workflow": "<workflow>",
  "expected_revision": 4,
  "action": {
    "type": "capture",
    "parameters": {
      "requirement_id": "<from-next-actions>",
      "authoring_root": "/host-visible/nxd-jobs/<workflow>/closure"
    }
  }
}
```

Capture is supervisor-owned materialization, not an agent-authored snapshot.
The supervisor generates and verifies the reserved v3 metadata
`dp-blueprint.approved.md`, `dp-blueprint.proposal.approved.json`,
`dp-blueprint.lock.json`, and the trusted `self_check.py`; agent-authored copies
of those files, hashes, or locks are rejected. The agent may run available
self-check or lock tools as optional evidence, but those checks do not replace
capture or strict admission.

## Run and report the review

When capture returns a review action, use its `review_input` exactly as supplied
by the supervisor. The response may contain several `RequirementView` entries:
select the one in `next_actions[]` whose `requirement_id` is the requirement id
of the returned review action, then take `review_input` from the matching
`RequirementView`. Never
take a `review_input` from another requirement, infer one from its array
position, or substitute the mutable authoring root. Use only that matching view.
The owning/main thread owns the complete workflow-v2 sequence: it performs
semantic inference and closure generation, captures the closure, dispatches the
review child, and relays every workflow action. Do not delegate Steps 2–3,
`advance_workflow`, or `report_requirement` to a child. The owning/main thread
must
now make exactly one built-in `Agent` or `Task` dispatch for this capture
generation. A `general-purpose` subagent is acceptable; the reviewer is a
conversation child, not a supervisor/MCP operation. Its prompt must tell the
child to load and follow `nxd-review-closure`, give it the retained blueprint
path, retained capture root, and original request under the existing
sanitized-request contract, and contain exactly this canonical marker line
(replace only the example closure path and round index):

```text
NXD_REVIEW_DISPATCH {"closure_path":"closure","request_contract":"sanitized_original_request","return":"claims_only","review_round_index":0}
```

The main thread must not invoke `Skill(nxd-review-closure)` or conduct
the review with its own `Read`/`Glob`/`Grep` calls. The main thread may load this
workflow guidance, then waits for the child to return claims; it does not
silently turn those claims into a verdict. The child may read and return claims,
but it must not edit, build, serve, transform, start a supervisor operation, or
talk to the user. A timeout or partial child result does not justify dispatching
a second reviewer for the same capture generation.

Keep the rich review ledger in
`…/nxd-jobs/<workflow>/review-record.json`, adjacent to the blueprint and
outside the captured `closure/`: every claim, evidence citation, adjudication,
classification, applied file, and user decision stays there, including
rejected, indeterminate, and scope-refused outcomes. Use schema
`nxd-conversation-review-ledger-v1`, the workflow id, and append-only `review_rounds[]`
with the existing adversarial-review round shape. Never write a review result
into the captured closure. The ledger is the user-facing record; the supervisor
receives only this bounded summary through `report_requirement`:

```json
{
  "schema": "nxd-conversation-review-v1",
  "verdict": "clear",
  "findings": [],
  "rejection_code": null
}
```

Map unresolved `HIGH` and `MEDIUM` claims to `severity: "blocking"`; map
unresolved `LOW` claims to `severity: "advisory"`. Project each unresolved
reviewer claim to exactly `{ "id", "severity", "description" }`, with
`description` copied from the reviewer's `claim`. Do not include `evidence`,
`why_it_matters`, adjudication, or any other key in the supervisor finding;
those richer fields remain only in `review-record.json`.

The accepted combinations are exact: `clear` has an empty finding list and no
rejection code; `findings` has one or more projected findings and no rejection
code; `rejected` uses `rejection_code: "scope_refused"`; `indeterminate` has no
rejection code and may preserve bounded partial projected findings. A clear
report is permitted only after the rich ledger is complete and every accepted
behavior-affecting claim has been resolved and, when it changed the closure,
re-reviewed. Rejected, indeterminate, and findings reports remain unsatisfied;
never turn them into `clear` or treat a missing report as approval.

There is exactly one retained-input review per capture generation, not one per
workflow lifetime. When an accepted finding changes behavior, record the user
decision in the external ledger, reset the returned capture requirement, edit
and optionally check the mutable closure when tools exist, recapture it, and run
a fresh review for the
new generation. Do not report the old generation as clear.

Relay the report with the requirement's returned generation, subject digest,
dependency digest, session reference, and the current revision. The supervisor
derives evidence identity from its current binding; caller-provided claims are
not execution authority. Reporting is the main thread's relay step after the
conversation child returns; it is not a supervisor-launched review.

```json
{
  "request_id": "review-<workflow>-<unique>",
  "workflow": "<workflow>",
  "expected_revision": 5,
  "action": {
    "type": "report_requirement",
    "parameters": {
      "requirement_id": "<from-next-actions>",
      "generation": 1,
      "subject_sha256": "<from-next-actions>",
      "dependency_evidence_sha256": "<from-next-actions>",
      "session_ref": "<current-session-ref>",
      "message_ref": null,
      "report": {
        "schema": "nxd-conversation-review-v1",
        "verdict": "findings",
        "findings": [
          {"id": "wrong-grain", "severity": "blocking", "description": "The requested total is not available at its required grain."}
        ],
        "rejection_code": null
      }
    }
  }
}
```

## Follow returned actions through admission

After every `advance_workflow` response, discard earlier action parameters and
follow only its new `next_actions`. Start supervisor-owned validation with the
returned `start_requirement` action and current revision. Then use the returned
`start_run` action with the current `revision` and `invalidation_epoch`.

```json
{
  "request_id": "validate-<workflow>-<unique>",
  "workflow": "<workflow>",
  "expected_revision": 7,
  "action": {
    "type": "start_requirement",
    "parameters": {"requirement_id": "<from-next-actions>"}
  }
}
```

```json
{
  "request_id": "run-<workflow>-<unique>",
  "workflow": "<workflow>",
  "expected_revision": 9,
  "action": {
    "type": "start_run",
    "parameters": {"expected_invalidation_epoch": 0}
  }
}
```

`start_run` is the only construction admission boundary. It derives the durable
admission, run, artifact identity, and publication path from supervisor state.
It accepts no locally asserted run id, receipt, validation result, executable
hash, or completion claim. Do not claim completion from closure files, a local
DuckDB, `build-record.json`, or a successful local self-check. Claim readiness
only from the supervisor's structured response and its admitted publication
state.

## Reset after behavior changes

If the blueprint changes, call `reset_workflow` with the supervisor-returned
graph-root requirement (the activated contract currently returns consent) and
the new host-visible `replacement_blueprint_path`. Do not invent the root id;
read it from the current requirements/actions. If only the generated closure
changes behavior, reset the returned capture requirement and set
`replacement_blueprint_path` to null. Then follow the returned actions from the
new generation and epoch. Re-capture, re-review, and re-run validation as
indicated; stale evidence cannot satisfy admission.

```json
{
  "request_id": "reset-<workflow>-<unique>",
  "workflow": "<workflow>",
  "expected_revision": 10,
  "requirement_id": "<returned-graph-root-or-capture>",
  "replacement_blueprint_path": "/host-visible/new-blueprint.md"
}
```

Record the reset and any external effects honestly. Reset invalidates durable
workflow eligibility and requests cancellation where needed; it does not undo
disclosure, spending, provider calls, or another irreversible external effect.
