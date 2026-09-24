# Supervisor workflow v2 construction

Use the supervisor workflow API for every new local construction. This path
owns consent, capture, review evidence, validation, admission, and publication;
do not fall back to a direct CLI or local substitute.

## Contents

- [Gate on the connected capability](#gate-on-the-connected-capability)
- [Prepare the prose blueprint](#prepare-the-prose-blueprint)
- [Relay consent and capture](#relay-consent-and-capture)
- [Run and report the review](#run-and-report-the-review)
- [Follow returned actions through admission](#follow-returned-actions-through-admission)
- [When validation fails](#when-validation-fails)
- [Reset after behavior changes](#reset-after-behavior-changes)

## Gate on the connected capability

Before starting construction, call `get_workflow_capabilities`. Continue only
when its structured response has `execution_enabled: true` and no unavailable
condition. A false or unavailable capability is a blocker. Do not use a direct
supervisor CLI or local substitute as a fallback for this construction path,
and do not infer capability from a tool catalog, a binary, or a previous
session.

## Prepare the prose blueprint

Write and deterministically validate the prose `dp-blueprint.md` first. Then
write the exact, real typed proposal JSON beside it as
`dp-blueprint.proposal.json`. Parse that complete object and validate/bind it to
the blueprint before consent, then call `prepare_workflow` with the inline
`typed_proposal` object, before the approval turn and before generating the
closure:

The following request-envelope sketch is intentionally abbreviated and is not
a complete request. Do not copy it as the `typed_proposal`; copy the complete
typed proposal object from `dp-blueprint.proposal.json` instead.

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

The `proposal` payload is a closed v3 object, not a free-form entity summary. It
must contain exactly `intent`, `questions`, `scope`, `terms`, `inputs`,
`models`, `transform`, `outputs`, `decisions`, `open_questions`, `delivery`,
and `contracts`. Frontmatter-only `name` and `workflow` must not be added to
this payload. Use these exact item shapes (with no legacy aliases or extra
keys):

The only allowed typed v3 transform operations are `filter`, `project`,
`derive`, `join`, `aggregate`, `union`, `deduplicate`, and `apply_procedure`.
API fetch and pagination are connector behavior, not typed transform
operations; do not add `fetch` or `paginate` steps to `proposal.transform`.

```json
{
  "intent": "Provide a queryable order summary.",
  "questions": [{"id": "order-count", "question": "How many accepted orders are there?"}],
  "scope": "Use the declared orders input and exclude refunded rows.",
  "terms": [{"id": "accepted-order", "name": "Accepted order", "definition": "An order that is not refunded.", "priority": "P3"}],
  "inputs": [{"id": "orders", "expectations": [{"id": "orders-rows", "model": "orders", "guarantee": "Each accepted input row has an order identifier.", "rule": "Reject rows without an order identifier.", "fields": ["order_id"]}]}],
  "models": [{"id": "orders", "fields": ["order_id"]}],
  "transform": [{"id": "exclude-refunds", "operation": "filter"}],
  "outputs": [{"id": "accepted-orders", "promises": [{"id": "accepted-orders-rows", "model": "orders", "guarantee": "Every output row is an accepted order.", "rule": "Exclude refunded rows.", "fields": ["order_id"]}]}],
  "decisions": [{"id": "refund-rule", "target": "orders", "ruling": "Exclude refunded rows.", "status": "proposed"}],
  "open_questions": [{"id": "late-orders", "question": "Should late-arriving orders be included?", "blocking": false}],
  "delivery": {"kind": "semantic_query", "profile": "desktop-local", "port": "duckdb", "provenance": "platform_fixed"},
  "contracts": [
    {"id": "orders-rows", "attachment": "input:orders", "model": "orders", "phase": "pre_transform", "guarantee": "Each accepted input row has an order identifier.", "rule": "Reject rows without an order identifier.", "fields": ["order_id"]},
    {"id": "accepted-orders-rows", "attachment": "output:accepted-orders", "model": "orders", "phase": "post_transform", "guarantee": "Every output row is an accepted order.", "rule": "Exclude refunded rows.", "fields": ["order_id"]}
  ]
}
```

If the prose `## Open Questions` section is empty, produce
`proposal.open_questions: []`. It must not contain a prose placeholder such as
`None`; `None` is not an open-question item. Because the parser has no
populated source block in that case, omit `v3:open_questions.text` from
`provenance`, `source_spans`, and `echo.coverage`; only cover populated parser
`.text` paths.

When a term priority is omitted, materialize the platform default as `P3` with
`platform_fixed` provenance and disclose that default; an explicitly written
priority uses `explicit` provenance. The natural-language echo must also name
the affected term and say that it uses the platform-default `P3` priority;
putting `P3` only in the typed term is not disclosure.

Input expectation and output promise source entries each have exactly `id`,
`model`, `guarantee`, `rule`, and `fields`. Compiled contracts each have exactly
`id`, `attachment`, `model`, `phase`, `guarantee`, `rule`, and `fields`; copy the
source entry's `id`, model, guarantee, rule, and fields unchanged. Input
contracts use `attachment: "input:<input-id>"` and `phase: "pre_transform"`;
output contracts use `attachment: "output:<output-id>"` and
`phase: "post_transform"`. A phase is not an attachment, so never use
`post_transform` as one. Decision items are exactly `id`, `target`, `ruling`,
and `status`, where status is `proposed` or `locked`. Do not emit
legacy `name`, `rationale`, or `text` keys for these items, or put an output id
in place of the `output:<output-id>` attachment.

Source spans are exact coordinates from the trusted parser, not approximate
Markdown locations. Copy all four integers (`line_start`, `line_end`,
`offset_start`, and `offset_end`) from the parser's source map for the matching
path. For a `###` subsection, the `.text` span covers the subsection body only:
the heading has its own source-map path, while the parser's exact block range
may include separator blank lines. Do not trim or widen that range. An anchor
maps the parser source path to a typed proposal path; omit it when the paths
already match. Do not calculate spans from memory; rerun the parser after every
blueprint edit and validate the exact proposal before calling `prepare_workflow`.

Treat source paths as opaque strings. Copy the exact parser key returned by the
source map, including the `v3:` prefix; do not independently slugify, snake-case,
or otherwise normalize it. The parser may normalize Markdown subsection ids to
underscores while typed proposal ids are hyphenated. Keep the two namespaces
distinct: when they differ, set `anchors` from the parser key to the typed key,
for example `v3:decisions[current_definition].text` →
`v3:decisions[current-definition].text`. For that anchored entry,
`provenance`, `source_spans`, and `echo.coverage` must use the target path
`v3:decisions[current-definition].text`, as required by the validator; the
source path remains in `anchors` so the validator can resolve its trusted span.
Without an anchor, use the exact parser path directly. A missing `v3:` prefix
or independently normalized id is a provenance/path failure, not a reason to
relax validation.

If `prepare_workflow` returns `v3.provenance.span_mismatch` with a bounded
`prepare_recovery_id`, call `inspect_prepare_recovery` with that opaque id.
Require its versioned result to carry the source semantic hash for the same
final blueprint and a complete `source_spans` map, then use that structural map
to regenerate the entire typed proposal. The inspection response contains only
canonical v3 parser paths and four integer coordinates; it contains no source
text or typed values. Rebuild every provenance/span entry, round-trip the
complete proposal, and strictly validate it before retrying. Do not patch only
the path named by the mismatch. The legacy
`validation_issue.expected_source_span` field remains available for v1
consumers, but it is only a location hint: even when it is present, regenerate
the complete proposal.

### Classify admission failures before recovery

A failed `prepare_workflow` is a pre-admission result, not an admitted workflow
state. Do not call `inspect_workflow` after a rejection unless admission
actually created a workflow; its absence is expected for a proposal that failed
trusted validation. Classify the returned details first:

- A bounded `prepare_recovery_id` is the only source for a retained complete
  parser map. Inspect it immediately, verify that it belongs to the unchanged
  final blueprint, and regenerate the whole proposal from that map.
- A stable `v3.provenance.source_map_unavailable` or
  `v3.provenance.source_map_oversized` code means the map was not retained.
  Obtain a fresh parser result; do not infer coordinates from error prose or
  retry the old payload.
- A proposal rejection without a `prepare_recovery_id` likewise has no retained
  map to inspect. Discard the proposal, reread the final blueprint, obtain a
  fresh parser/source-map result through the installed authoring flow, and
  regenerate the complete proposal with a new request id. Do not call
  `inspect_workflow` or resubmit the rejected payload.
- A typed field-reference failure means the proposal and its contract inventory
  disagree. Remove an absent field from the contract/model shape, or declare it
  in the model when the user actually requires it. Custom contracts cannot
  express redaction by naming absent fields or by using an empty field list.

In every branch, an edit invalidates all parsed spans. Re-read the final
blueprint, regenerate every proposal section, strictly validate the complete
replacement, and send a fresh request id before retrying.

### Recovering a rejected proposal

Treat recovery as a whole-proposal replacement, never as a repair to the path
named by the primary issue. Follow this order exactly:

1. Inspect the opaque `prepare_recovery_id` immediately.
2. Require the expected recovery schema, the semantic hash of the unchanged
   final blueprint, and a complete `source_spans` map. If any is absent, stale,
   unavailable, or oversized, discard recovery and obtain a fresh parse.
3. Re-read the entire final blueprint. The recovery record contains coordinates
   only; it deliberately contains neither source prose nor typed values.
4. Rebuild the entire typed proposal. Inventory every populated parser `.text`
   source path, including every populated Decision subsection.
5. Give every inventory item exactly one direct provenance entry with its exact
   source span. An explicit source-to-target anchor may resolve a differing
   typed path, but never replaces that provenance entry. Copy all four trusted
   coordinates verbatim for each non-`platform_fixed` entry and rebuild
   `echo.coverage` from the complete provenance key set.
6. Replace the complete proposal, round-trip and strictly validate it, then
   retry only when validation has zero issues and with a fresh `request_id`.

`source_block_uncovered` after an attempted recovery means this complete
regeneration invariant was violated. Do not patch a second named path, infer a
span from prose, or retry a partial proposal.

The validator result protocol is v2. It preserves the v1 `ok`, hash, and
primary issue `code`/`path`/`message` semantics and keeps the legacy single-span
field; v2 permits the supervisor to retain a complete map behind the opaque
recovery id. A v1 caller may ignore that addition or request the validator's
v1 compatibility output. If the map cannot be safely retained, the issue has
no recovery id and carries a stable
`source_map_code` of `v3.provenance.source_map_unavailable` or
`v3.provenance.source_map_oversized`; stop and obtain a fresh parser result.
Never guess, split, trim, widen, or alter typed values. If the MCP host renders
only error text, read only the bounded `prepare_recovery_id` or
`source_map_code` suffix; never attempt to recover a map from error prose.

Any blueprint edit invalidates the parsed map and every proposal coordinate.
Before `prepare_workflow` succeeds, reparse the final blueprint, regenerate
the complete proposal and its spans, replace the complete proposal file using
the available file operation, strictly validate it, and send a new
globally-unique `request_id`. Do not claim generic filesystem atomicity for a
file-tool replacement. Reuse a request id only for a byte-for-byte identical
request.

If the typed path differs from the parser path, resolve it through the proposal's
`anchors` map (`source_path` → `target_path`) and use the trusted parser's
source-map span for the resolved source path. A missing source mapping or more
than one anchor for the same typed target is an error: stop and repair the
proposal from a fresh parser/source-map result; never guess a span. After any
repair, round-trip the complete JSON, re-run strict proposal validation, and
send a new globally unique `request_id` because the payload changed. Reuse a
`request_id` only when replaying the byte-for-byte identical request.

Omit `source_hash` from the caller-authored object. The supervisor inserts or
replaces it with the canonical hash of the retained Markdown before validating
and binding the proposal; callers must not guess, hand-compute, or send a
pseudo-hash for this field.

Use the returned `revision`, `invalidation_epoch`, requirement identities,
subjects, and `next_actions` as the current state. The response is a durable
snapshot, not approval. Do not invent requirement ids, subjects, revisions, or
epochs from the contract or from local files.

The typed proposal is the consent candidate: every typed-v3 Decision must be
covered by the exact echo-back and may remain `status: proposed` during prepare.
After the supervisor records the subject-bound `session_decision`, its trusted
materializer projects proposed Decisions to `locked` in the approved closure
snapshot. `locked` is an approval-derived snapshot state, not authorization by
itself; do not change the proposal after consent.

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
generated closure's host-visible authoring root. Immediately before capture, run
the generator's [pre-capture audit](../../nxd-generate-data-product/reference/pre-capture-audit.md)
against the approved proposal and closure. Batch same-round mechanical
corrections before recapturing; do not spend one capture generation per
individual correction. Capture is the supervisor's
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
Replace only `<workflow>` and `<index>`. `<index>` is zero-based within the
current workflow's `review-record.json`; reset it to `0` when a repair moves to
a new workflow id. The review ledger is the captured
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

Treat the matching `review_input` as an opaque, generation-bound handoff. On a
host without a runner-owned review guard, the owning thread must perform a
**non-content path/accessibility check** for both exact retained paths using
the session's allowed read-only host filesystem mechanism (a metadata/stat or
equivalent path-access check, not a content read). On a shellless host, an
allowed `Glob` or `Grep` call with an explicit absolute `path` is an acceptable
equivalent; do not omit that `path` or use a content read as a substitute. On
a host with a runner-owned guard, do not issue a separate owning-thread check:
the guard performs and repeats it immediately before accepting the child
dispatch. Check that the exact `retained_capture_root` is an existing directory
and that the exact `retained_blueprint_path` is an existing regular readable
file. Resolve both paths for containment and verify that each remains inside
the session's allowed host-visible roots on the same host surface that the
child can read; reject missing paths, stale paths, inaccessible paths, and
symlink/realpath escapes. Establish freshness from the exact path pair on the
current matching `RequirementView` and its current requirement/generation
binding, never from a filename, timestamp, attachment id, or a path discovered
elsewhere. Do not open either path or inspect its contents as part of this
precondition.

In the eval/live runner, the run-scoped review guard performs this metadata
check when capture is received and repeats it immediately before accepting the
child dispatch. The owning thread does not need to inspect retained content to
establish availability. A host runtime without that guard must provide an
equivalent metadata/stat check; if no non-content mechanism is available, stop
with the incomplete blocker instead of using a content read or a fallback.

For the nxd desktop workflow-v2 supervisor, the runner maps its `data-dir` to
two retained-input roots: `<data-dir>/captures` for captured closure content
and `<data-dir>/blueprints` for approved blueprint content. The adapter exposes
only those roots to the reviewer. If the supervisor returns paths outside them,
or the roots are unavailable, the handoff fails closed with a distinct
configuration/availability diagnostic; never create or use a fallback root.

If either check fails, stop before dispatch and report an explicit incomplete
blocker, for example: `INCOMPLETE — retained capture handoff blocked:
<retained_capture_root|retained_blueprint_path> is <missing|stale|outside the
allowed host-visible roots|inaccessible> for requirement <id>, generation
<generation>; no reviewer was dispatched.` Leave the review requirement
pending and do not send a clear, findings, rejected, or indeterminate
`report_requirement`. Never substitute an older capture, scratch or fallback
path, the mutable authoring root, or a path belonging to another requirement.
On a guarded host, this is terminal for the current session: the runner permits
the owning thread to stop with the incomplete blocker, but does not permit a
retry or a tool that could replace the captured handoff. A new session must
obtain a fresh supervisor capture.

The owning/main thread owns the complete workflow-v2 sequence: it performs
semantic inference and closure generation, captures the closure, dispatches the
review child, and relays every workflow action. Do not delegate Steps 2–3,
`advance_workflow`, or `report_requirement` to a child. The owning/main thread
must
now make exactly one built-in `Agent` or `Task` dispatch for this capture
generation. A `general-purpose` subagent is acceptable; the reviewer is a
conversation child, not a supervisor/MCP operation. Its prompt must use the
following canonical block, replacing only the angle-bracketed values with the
exact matching `review_input` values and the sanitized request. Keep each
retained-path line exactly once, keep exactly one nonblank `Sanitized original
request:` line, and include the canonical marker line exactly once. The prompt
must tell the child to load and follow `nxd-review-closure`, and must give it
the retained blueprint path, retained capture root, and original request under
the existing sanitized-request contract:

```text
retained_capture_root: <exact retained_capture_root from review_input>
retained_blueprint_path: <exact retained_blueprint_path from review_input>
Load and follow nxd-review-closure.
Sanitized original request: <complete request with credentials replaced>
review_time_budget_seconds: 300
review_inspection_cutoff_seconds: 240
NXD_REVIEW_DISPATCH {"closure_path":"closure","request_contract":"sanitized_original_request","return":"claims_only","review_round_index":0}
```

The `300` and `240` values above are the current workflow-v2 runner policy; a
caller with another enforced budget must substitute those values in the same
fields. The inspection cutoff is advisory to callers that do not provide a
runner-owned guard, but when present it preserves time for the terminal claims
response.
Keep both retained-path values byte-for-byte identical to the paths that passed
the pre-dispatch check; do not rebase, shorten, normalize, or replace them for
the child. The check proves handoff availability only; it is not review
evidence and does not authorize the main thread to inspect the retained
capture.
Treat `review_time_budget_seconds` as a hard absolute budget from accepted
dispatch, not a suggestion. A runner-owned guard may deny further Read, Glob,
and Grep calls at `review_inspection_cutoff_seconds` so the finalization reserve
remains available for the terminal claims response. Front-load disclosure paths
and output promises, then model roles and physical writes, then semantic and
direct-store reachability. If the Agent runtime forwards intermediate child
text, allow one concise progress checkpoint to the owning thread around
halfway through the budget; it is informational and does not extend or reset
the deadline. The cutoff likewise does not extend or reset the hard deadline.
Stop reading at the cutoff and return complete or explicitly
partial evidenced claims; never wait for another message.

Invoke the child inline with `run_in_background: false` when the installed
`Agent`/`Task` schema exposes that field; otherwise omit the field, and never
set it to `true`. The child loads `nxd-review-closure` and returns claims only.

The main thread must not invoke `Skill(nxd-review-closure)` or conduct
the review with its own `Read`/`Glob`/`Grep` calls. The main thread may load this
workflow guidance, then waits for the child to return claims; it does not
silently turn those claims into a verdict. The child may read and return claims,
but it must not edit, build, serve, transform, start a supervisor operation, or
talk to the user. A timeout or partial child result does not justify dispatching
a second reviewer for the same capture generation.

After a review completes, the owning thread may use Bash for ordinary
remediation work before a reset and fresh capture. The runner guard rejects
owner Read/Glob/Grep/Write/Edit/NotebookEdit operations and shell commands that
target the retained capture or blueprint roots; those supervisor-retained
inputs remain immutable.

Keep the rich review ledger in
`…/nxd-jobs/<workflow>/review-record.json`, adjacent to the blueprint and
outside the captured `closure/`: every claim, evidence citation, adjudication,
classification, applied file, and user decision stays there, including
rejected, indeterminate, and scope-refused outcomes. Use schema
`nxd-conversation-review-ledger-v1`, the workflow id, and append-only `review_rounds[]`
with the existing adversarial-review round shape. Never write a review result
into the captured closure. The ledger is the user-facing record; the supervisor
receives only this bounded summary through `report_requirement`:

The ledger round's `status` is the ledger-completeness vocabulary
(`complete`, `needs_user`, or `timed_out`). The supervisor projection has a
different `report.verdict` vocabulary (`clear`, `findings`, `rejected`, or
`indeterminate`); never copy that verdict into the ledger `status` field.
`findings` is a supervisor report verdict, not a ledger round status, and a
round with findings is still recorded as `complete` only when its rich claims,
adjudications, and any required user decision are complete.

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

## When validation fails

A failed `start_requirement` for validation returns a bounded `phase`, `code`,
`detail`, and `recovery`. The supervisor deliberately omits compiler and
subprocess output from that diagnostic.

- `recovery: repair_then_retry` means the retained closure has a defect you can
  fix. Call `check_data_product` on the authoring closure to read the specific
  structure, runtime, contract, and semantic findings. For example, a
  `structure/spec_compile_failed` finding carries the compiler's own error,
  such as a duplicate metric name. Repair the closure, then reset the
  capture requirement with `replacement_blueprint_path` set to null. Recapture,
  obtain a fresh review, and start validation again. Never resubmit an
  unchanged closure after this outcome, and never audit by hand in place of
  `check_data_product`.
- `recovery: retry_unchanged` means a bounded runtime limit was hit. Retry
  once with a fresh request id.
- `recovery: stop` is a blocker. Report it with its code.

`check_data_product` is diagnosis only. It never substitutes for supervisor
validation, admission, or publication.

## Reset after behavior changes

Close the pending review round before resetting. When the operator authorizes
corrections for a round whose ledger `status` is `needs_user`, or which has
accepted `behavior_affecting` findings, update that same round in
`review-record.json` before calling `reset_workflow` or appending another
round. Fill its `user_decision`: `approved_at_unix_ms`, a `citation` that
copies the authorizing operator message byte for byte, and `approved_finding_ids`
listing the accepted findings being corrected. Also set each corrected finding's
`state`. A round left with `user_decision: null` and unresolved accepted findings
fails the construction review check even when every later round is clear.

If the blueprint or typed proposal changes after `prepare_workflow` succeeds,
do not retry the old operation or patch its binding. Call `reset_workflow` with
the supervisor-returned graph-root requirement (the activated contract
currently returns consent) and the new host-visible
`replacement_blueprint_path`. Then regenerate the complete proposal and obtain
fresh prepare and consent state. If the edit happens after consent, the same
reset/replacement/fresh-consent rule applies; old consent never transfers to an
edited blueprint or proposal. Do not invent the root id; read it from the
current requirements/actions. If only the generated closure changes behavior,
reset the returned capture requirement and set `replacement_blueprint_path` to
null. Then follow the returned actions from the new generation and epoch.
Re-capture, re-review, and re-run validation as indicated; stale evidence
cannot satisfy admission.

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
