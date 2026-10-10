# Supervisor workflow v2 construction

Use the supervisor workflow API for every new local construction. This path
owns consent, capture, review evidence, validation, admission, and publication;
do not fall back to a direct CLI or local substitute.

## Contents

- [Gate on the connected capability](#gate-on-the-connected-capability)
- [Prepare the prose blueprint](#prepare-the-prose-blueprint)
  - [Revised or successor plan](#revised-or-successor-plan)
  - [Classify admission failures before recovery](#classify-admission-failures-before-recovery)
  - [Recovering a rejected proposal](#recovering-a-rejected-proposal)
- [Relay consent and capture](#relay-consent-and-capture)
- [Run and report the review](#run-and-report-the-review)
- [Follow returned actions through admission](#follow-returned-actions-through-admission)
- [Mapper approval](#mapper-approval)
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
closure. Write `dp-blueprint.proposal.json` before every `prepare_workflow`
call, retries included, so the file always equals the proposal that call
received, even when the call is rejected.

Before the first draft, write the conventions the transform will apply as
explicit defaults, per [dp-blueprint.md § Conventions every plan states](dp-blueprint.md#conventions-every-plan-states).

Treat the approved blueprint as canonical: preserve every Model and Output
name exactly in the typed proposal and generated closure. Break each Questions
entry into all of its requested clauses and map every clause to the Models,
Transform, and Outputs that answer it. Never rename or drop a clause to fit the
implementation; if a clause cannot be covered, leave it open and return to the
user for a decision before approval. Populate Decisions only from a user
statement or a policy the user explicitly designated as controlling. A bare
"yes" or "sure" to a message with several questions or alternatives is not such
a statement; see "Relay consent and capture". Evidence
and contracts may establish facts, but do not imply a decision or an
anticipated reversal; leave unmade choices open and mark them `[DECIDE]`.

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
putting `P3` only in the typed term is not disclosure. The default is also
covered as its own path: list `v3:terms[<id>].priority` in `echo.coverage` for
each such term, next to that term's `.text` entry. The prose sentence alone
fails with `v3.echo.default_uncovered`.

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

Bound `prepare_workflow` retries. When it rejects a proposal, fix the named
finding from the parser's source map, not by estimating offsets or trying
variants. If a proposal rebuilt from a fresh recovery map
(`inspect_prepare_recovery`) is still rejected, stop calling
`prepare_workflow`: report the exact rejection codes and the blueprint section
they point to, and ask the user how to proceed. Repeated guessed calls do not
converge and consume the turn. That stop applies to span and coordinate
rejections, the kind a recovery map can fix. A content rejection whose shape
this reference documents (a `v3.echo.*` coverage or disclosure code, a
provenance code) is not a question for the user: fix it to the documented shape
and call again. Stop and ask only if the same code survives that fix.

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

### Revised or successor plan

A refresh, new-version, or successor request starts here, before you draft the
plan, and not only after a failure. The source may have changed since the
earlier build, so "rules carried over unchanged" and "the data pulled fresh" are
not claims you can make without looking.

1. **Probe the live source the way the original build did.** For an API source
   that is the closure-local `connectivity_check.py` from the earlier build: run
   it (it prints the response shape, keys, and distinct values of enumerated
   fields) when the session has a shell. For a file, read or profile the file;
   for a database, use the connector's declared tables. If the session has no
   shell, you cannot run the probe, and the supervisor's validation scratch run
   is the only contact with the live source, so make that run the probe. Put the
   drift promises in the revised plan, so they go through approval, never in the
   closure alone: one that fails when a published measure is null in every current
   row, and one that checks each enumerated field against its accepted set. Have
   the transform land, without raising, the source keys it did not map (each with
   its kind of value and the number of current rows it appears on) and the current
   row count in the fixed `source_diagnostics` model, copied verbatim from the
   unmapped-key rule in
   [custom-contracts.md](../../nxd-generate-data-product/reference/custom-contracts.md)
   (names, kinds and counts only, never row values or PII). The stage promise reads
   enumerated values from the landed column itself. Have each promise verifier put
   what it finds in its failure `VerifyResult.context`. Never add any other
   diagnostics table, column or wording.
   These drift promises, the fixed diagnostics model, type-pinned columns, and a
   transform that lands an unexpected value rather than raising are part of every
   refresh plan for a live API source, not optional extras. An instruction to carry
   forward only the approved corrections does not remove them: they are how this
   refresh sees the source. List them in the plan for approval. Leave one out only
   when the user declines it by name, and then say which change in the source
   would go unnoticed.
   Read the first validation result as the probe and make the diff from it. A
   passing first validation verifies only the fields those promises cover; until
   it runs, mark each affected plan item "unverified against the live source".
   Asking the user for the current field names, category values, or a sample is a
   secondary option, not the main path: the user often does not know the current
   shape.
2. **Diff, then write every difference down.** Compare the observed keys and
   values against every field the existing transform reads and every enumerated
   value it declares. Put each difference in the revised plan as an explicit
   item, or as a question to the user when it changes what a field, measure, or
   category means; a user who hands a renamed-key decision back is delegating, and
   a like-for-like rename is then mapped as a recorded assumption, per
   [failure-handling.md](failure-handling.md). Carrying a rule over as unchanged is allowed only for items
   the diff covered, or that are marked unverified.
3. **Write the implicit conventions as defaults.** State what the earlier
   transform applied without saying, using the list in
   [dp-blueprint.md § Conventions every plan states](dp-blueprint.md#conventions-every-plan-states),
   which applies to every plan. Review blocks on unwritten conventions, and each
   block costs a reset and a fresh approval.

### Classify admission failures before recovery

A failed `prepare_workflow` is a pre-admission result, not an admitted workflow
state. Do not call `inspect_workflow` after a rejection unless admission
actually created a workflow; its absence is expected for a proposal that failed
trusted validation. Classify the returned details first:

- `v3.open_question.blocking_unresolved`: return to the user and ask the
  indicated Open Question. Record their answer in the blueprint as a Decision,
  reparse the final blueprint, regenerate every proposal field and span,
  strictly validate the complete proposal, and prepare with a fresh `request_id`.
  Never set `blocking: false` yourself; any question-state change must faithfully
  reflect the user's answer.
- `workflow/proposal_validator_unavailable`: stop and report the runtime
  failure. Do not regenerate the proposal. Only if `retryable: true`, a retry
  of the identical request is permitted later; do not automatically retry or
  enter a regeneration loop.
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

The blocking-question branch waits for the user's answer; the runtime-failure
branch stops without regeneration. For proposal recovery after a blueprint edit,
all parsed spans are invalid: re-read the final blueprint, regenerate every
proposal section, strictly validate the complete replacement, and send a fresh
request id before retrying.

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
   Before retrying `prepare_workflow`, write the complete corrected proposal to
   `dp-blueprint.proposal.json` and pass that same object inline, as the file
   must equal what every `prepare_workflow` call receives.

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
blueprint read-back, call `advance_workflow` with `session_decision`.

A bare reply such as "yes", "sure", "fine" or "ok" that names nothing settles
nothing. It is not approval of the read-back, and it does not choose among
alternatives or answer open questions in the same message, including your own
recommendations. Do not record a Decision, relay consent, or generate the
closure from it. Re-ask once, naming the single item you need. For approval,
that is the specific blueprint read-back, e.g. "Do you approve this plan as
shown? Reply 'approved' to build it." For a choice, name the options and your
recommendation, and ask for the choice itself. An affirmative reply to that
single-item re-ask settles that item; a reply naming the item settles it on
the first ask, and so does a reply that says which field or rule carries the
meaning the request needs (record that as an `inferred` Decision, see
[dp-blueprint.md](dp-blueprint.md)). Relay the
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
`{"action_kind":"self_check","outcome":"pass","evidence_ref":"<closure>/build-record.json#self_check"}`;
each retained-input review adds exactly
`{"action_kind":"adversarial_review","outcome":"complete","evidence_ref":"<job>/review-record.json#review_rounds/<index>","review_round_index":<index>}`.
`<closure>` is the captured closure's path relative to the agent workspace
root, and `<job>` is that path's parent directory; the review ledger is the
captured closure's sibling. Derive both from the path you actually captured:
for `nxd-jobs/<workflow>/closure` they are `nxd-jobs/<workflow>/closure` and
`nxd-jobs/<workflow>`; for a closure captured at `closure` in the workspace
root, `<job>` is empty and the references are
`closure/build-record.json#self_check` and
`review-record.json#review_rounds/<index>`. Never add a directory prefix the
captured path does not have. `<index>` is zero-based within the current
workflow's `review-record.json`; reset it to `0` when a repair moves to a new
workflow id.
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

Ledger timestamps (`started_at_unix_ms`, `ended_at_unix_ms`, and, when set,
`user_decision.approved_at_unix_ms`) are actual epoch milliseconds read from
the current clock at the corresponding event (for example a portable
`python3 -c 'import time; print(time.time_ns() // 1_000_000)'` call; BSD
`date` on macOS has no `%N`); never estimate or invent them. A round's
`ended_at_unix_ms` must not precede `started_at_unix_ms`, a `complete` or
`needs_user` round must end within `budget_ms` of its start, and a `timed_out`
round must span at least `budget_ms`. Each finding's `evidence` is a non-empty
list of non-empty citation strings, never a single string.
For every finding, `state: "applied"` requires a non-empty `applied_files`, and
every other state requires `applied_files: []`; list only files actually
changed. A `not_applied` finding lists no files. `deferred_finding_ids` may
contain only accepted `behavior_affecting` findings the user explicitly
deferred; never defer a rejected, `out_of_scope`, or `structural_note` finding.
When findings force a reset, write each user ruling on them into the
blueprint's Decisions before re-preparing, so the fresh review grades against it.
Never recommend deferring an accepted `HIGH` finding; recommend its fix (see
[Relay and authorization](../../nxd-generate-data-product/reference/adversarial-review.md#relay-and-authorization)).
When shell access is available, load the shipped
`$JOB_HELPER_DIR/scripts/dp_diagnostics.py` and call
`validate_review_round(round)` before writing the round; fix every reported
problem first. Without shell access, apply these rules inline and do not claim
the round was helper-validated.

```json
{
  "schema": "nxd-conversation-review-v1",
  "verdict": "clear",
  "findings": [],
  "rejection_code": null
}
```

A round whose only claims are `LOW` is clear once you resolve them. Record each
one in the ledger as `classification: "structural_note"`, `state:
"not_applied"`, `applied_files: []`, and a non-empty `proposed_effect` that
says no closure change follows (for example `"No closure change; advisory
coverage note."`), with an `accepted` adjudication, in a `complete` round. The
ledger validator rejects an empty `proposed_effect` on every finding, advisory
or not, and a rejected round cannot pair with its review. Then
report `verdict: "clear"` with an empty `findings` list. The advisory claims
stay in `review-record.json` for the user, and they need no user decision. Never
hold publication for an advisory claim, and never downgrade a `HIGH` or `MEDIUM`
claim to reach this path. After publication, report advisory claims as notes.
A stricter alternative to an approved Decision is an optional change you may
offer, not an open question: do not say the result is not ready to circulate,
do not ask the user to decide it, and do not end the turn waiting on it.

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

When the supervisor returns `workflow/review_findings` containing any
`behavior_affecting` finding, the review is not authorization: set the round to
`needs_user`, relay the finding IDs, evidence, adjudication, and proposed
effects, and ask the user to decide the specific IDs. Do not edit the mutable
authoring closure, reset, or recapture in that turn. Continue only after an
explicit user approval in a later turn. “I don't know, you tell me” and
“whatever you think” are deflections, not approval; explain the choice and
wait. A new review finding requires a new user decision for its IDs. Only
accepted findings the user approved may be applied; rejected, out-of-scope,
and structural findings stay unapplied and are not deferred.

A user reply that decides only some of the round's IDs is a partial decision.
Once a round carries a `user_decision`, every accepted behavior-affecting
finding in it must be applied or listed in `deferred_finding_ids`; the ledger
validator rejects the round otherwise. So record a partial approval either by
deferring the still-open IDs in that round, or by leaving `user_decision: null`
on the `needs_user` round and recording the decision in the next round. An
applied structural note needs no user decision.

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

## Mapper approval

A closure whose transform calls `nxd.experimental.field_mapper` needs a
formal approval before validation and admission. That approval exists only
when the activated contract carries a `mapper-confirmation-v1` requirement,
which depends on capture and on the conversation review. Read which contract
the install has from the requirements `prepare_workflow` returns:

- Current Desktop installs activate five requirements: consent, capture,
  review, `approval` (`mapper-confirmation-v1`), validation. Every build takes
  the approval step, and a closure without a mapper completes it with no
  prompt (below).
- Older supervisors activate four requirements with no approval step. There a
  mapper closure is never prompted for and stops at validation with
  `validation/mapper_closure_unsupported`. Report that and stop. Do not
  remove the mapper to get past it.
- An upgraded install whose data directory still held workflows keeps its
  previous contract: setup does not replace it while instances exist, and says
  to use `remove_workflow` or an empty `--data-dir` to adopt the new one. Only
  the operator does that; never remove workflows to change the contract.

Mapper builds also run only on macOS, because mapper-mode compute needs the
macOS sandbox. The shipped policy limits a grant to provider `anthropic`;
priced haiku, sonnet, opus or fable model ids (exact ids, dated aliases
included); 1000 calls, 2,000,000 tokens and 25 USD per approval; 100 USD
lifetime per mapper spec; and no recurring grants. The approval window is
300 s, the dialog times out after 120 s, and a decline starts a 30 s cooldown.

When the contract does carry the requirement and `next_actions` offers
`start_requirement` for it, the pending code is `workflow/approval_required`.
For a closure that does not map, the same `start_requirement` is satisfied at
once with no prompt. Inspection then shows `mapper_use: not_present` and
approval state `not_required`.

Before you call it, tell the user what the prompt will show and that the
decision is theirs. It shows the provider, the model and any corroboration
model, the call, token and USD ceilings, the lifetime USD ceiling and
lifetime spend for this mapper spec, and the fields the grant declares. It
does not show the grant's expiry or whether it is recurring, so state both
yourself. Then call `start_requirement` with the returned requirement id and
the current revision. The supervisor opens a native OS dialog. That dialog is
the only approval surface. The response carries
`approval: {state: "awaiting_user", expires_at}` and no URL, token, capability
or key, and you must not look for one. You cannot approve, decline, extend,
widen or reset an approval. Do not click the dialog, type into it, or drive it
with any automation tool. Wait for the user, then read the new state with
`inspect_workflow`.

`inspect_workflow` reports a `mapper` block for the requirement. It carries
the approval `state` and `code`, the declared scope, each spend ledger (its
state, reserved and settled totals, ceilings, and integrity), and a `lifetime`
figure, `settled_and_reserved_usd_micros`. The `lifetime` block is omitted
when that spend cannot be verified. Report lifetime spend as unavailable then,
never as zero.

The grant's `input_fields`, `document_classes` and `pii_category` are
declared by the author and reviewed by the user. The supervisor does **not** enforce them. It
enforces the provider (exactly `anthropic`), the models, the expiry, the
request shape and the ceilings. Never tell the user that the declared field
list limits what the transform sends.

A grant is fixed once the user approves it. Any wider scope (a new model,
higher ceilings, a later expiry, `recurring: true`) needs a grant the user
edits or confirms, a recapture, a fresh review and a fresh OS approval. Never
widen a grant yourself to get past a refusal. Never retry `start_requirement`
in a loop: each call can put a dialog in front of the user.

| Code | Meaning | Tell the user | Next action |
|---|---|---|---|
| `workflow/mapper_scope_invalid` | The grant is missing, malformed, not strictly typed, or has an unknown key. `provider` must be exactly `anthropic`, `max_calls` and `max_tokens` positive integers, `max_usd` positive, `expires_at` a future RFC3339 time, `recurring` a bool. Also raised when both grant paths exist or the spec is missing. No prompt was shown. | The grant needs correcting before it can be shown for approval. | Fix the grant shape in the authoring closure (confirm any scope value with the user), reset capture, recapture, review, then request approval again. |
| `workflow/mapper_approval_claims_rejected` | The grant carries approval claims such as `approved`, `approved_by`, `receipt` or `signature`. | Nothing was approved; the grant tried to assert approval. | Remove the claims, reset capture, recapture, review, request approval again. Never add approval fields. |
| `workflow/mapper_grant_exceeds_policy` | The grant asks for more than the installed policy allows (ceilings, provider, model, recurring). | The request is larger than this install permits. | Ask the user to narrow the scope, then edit, recapture, review and request again. Do not look for a way to raise the policy. |
| `workflow/mapper_model_unpriced` | The model has no price entry, so spend cannot be bounded. | That model cannot be used under supervision. | Ask the user to choose a priced model; edit the spec and grant, recapture, review, request again. |
| `workflow/approval_pending` | An approval request for this requirement is already open. | A prompt is already waiting for their decision. | Wait. Do not call `start_requirement` again; read state with `inspect_workflow`. |
| `workflow/approval_declined` | The user declined in the OS dialog. The requirement is pending again. | They declined; nothing ran and nothing was spent. | Stop and ask what they want to change. Request again only when they ask for it. |
| `workflow/approval_cancelled` | The dialog was cancelled. | The prompt was cancelled; nothing was approved. | Ask whether to prompt again; request again only on their yes. |
| `workflow/approval_expired` | The dialog timed out; the approval's expiry passed before admission; or a non-recurring approval was already spent by the publication it admitted. | The approval no longer applies. | Ask whether to prompt again. If the grant's own `expires_at` is past, the user must set a new expiry: edit, recapture, review, request again. |
| `workflow/approval_cooldown` | A request for the same mapper subject came too soon after a decline or cancel. | A short wait applies after a decline. | Do not poll. Tell the user, and request again only when they ask after the cooldown. |
| `workflow/approval_interrupted` | The supervisor restarted while a prompt was open. | The prompt was lost when the supervisor restarted. | Ask whether to prompt again; request again only on their yes. |
| `workflow/approval_surface_unavailable` | The OS dialog could not be shown. | Approval cannot be collected on this machine right now. | Stop and report the code. Never substitute a chat "yes" for the dialog. |
| `workflow/approval_ceiling_reached` | The approval's call, token or USD ceiling, or the spec's lifetime USD ceiling, is spent. | The approved budget is used up. | Stop and report spend. A new budget needs a fresh approval that the user asks for; follow only the returned actions. |
| `workflow/mapper_subject_changed` | The approved capture no longer matches what is being admitted. | The approved closure changed, so the approval no longer applies. | Stop and report. Do not retry; a legitimate change goes through reset, recapture, review and fresh approval. |
| `workflow/mapper_integrity` | The approval or spend ledger failed its integrity check, the supervisor's state key is unavailable, or a lookup the gate depends on failed. The supervisor fails closed. | Approval records could not be verified. | Stop and report the code. Do not edit or delete supervisor state. |

Validation in mapper mode runs the scratch build against a stub: it makes no
network call and charges nothing. Stub answers are placeholders, so contract
or expectation failures confined to columns the mapper spec declares as
outputs become the advisory `validation/mapper_output_unverified`. Scratch
validation proves wiring, not the quality of mapped values; say so when you
report. Every other contract failure still fails.

| Validation code | Recovery | Next action |
|---|---|---|
| `validation/mapper_closure_unsupported` | `stop` | The activated contract has no mapper approval requirement: an older supervisor, or an upgraded install that kept its previous contract because workflows existed. Report it; mapper closures cannot be built on this install as activated. |
| `validation/mapper_scope_invalid` | `repair_then_retry` | The supervisor could not read the mapper proposal (spec or grant). Fix the files in the authoring closure, then reset capture, recapture, review and validate again. |
| `validation/mapper_approval_missing` | `stop` | The mapper closure reached validation with no approval binding. Report it; do not work around it. |
| `validation/mapper_subject_changed` | `stop` | The pinned closure does not match the approved one. Report it as a bug or tampering; do not retry. |
| `validation/mapper_ceiling_exceeded_in_scratch` | `repair_then_retry` | The scratch run would exceed the approved ceilings. Reduce what the transform maps, or ask the user whether they want to approve a larger budget (edit, recapture, review, fresh approval). |
| `validation/mapper_grant_refused` | `repair_then_retry` | The transform asked for a model outside the grant, or sent a request shape the broker refuses. Make the transform use the granted model and an allowed request; a different model needs the user's fresh approval. |
| `validation/mapper_provider_unavailable` | `stop` | The validation stub is not available in this supervisor build, or the scratch run tried to reach a real provider. Report it. |
| `validation/mapper_output_unverified` | advisory | Not a failure. Report that mapper outputs were not checked against their contracts in scratch. |

A failed run's diagnostic can carry `mapper: {code, ledger_key_short}`.
`mapper_ceiling_reached` means the live run hit the approved ceiling and was
refused before the network; the mapper raised `BudgetExceededError`.
`mapper_grant_refused` means the broker refused a request outside the grant (a
model not granted, or an approval past its expiry). `mapper_request_refused`
means it refused a disallowed request shape, such as server tools, images or
streaming. The mapper raised `GrantError` for both. `mapper_integrity` means
the spend ledger or the run's mapper setup could not be verified. For all
four, stop and report. Do not rerun to "try again". If the transform's error
names `mapper_provider_credential_missing`, the supervisor has no provider key
installed. The user fixes that through Desktop setup, never by pasting a key
into chat or into the closure. Never supply a key, token or base URL yourself.

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
- When the failure diagnostic, or `inspect_run` for its `diagnostic_run_id`,
  carries `failed_contracts` or `exception_class`, repair those first.
  `check_data_product` does not execute against a live source, so it can pass
  while a source-dependent failure remains. Each `failed_contracts` entry
  names the promise that failed, its model, and how many rows failed, and its
  `context` carries the verifier's identifiers (field names, stage values, counts); an
  `exception_class` such as `decimal.ConversionSyntax` names what the transform
  raised on real source data. When a contract failure names no field or row,
  follow [failure-handling.md](failure-handling.md): localise it inside the
  existing verifier and revalidate before asking the user.
- `recovery: retry_unchanged` means a bounded runtime limit was hit. Retry
  once with a fresh request id.
- `recovery: stop` is a blocker. Report it with its code.

`check_data_product` is diagnosis only. It never substitutes for supervisor
validation, admission, or publication.

## Reset after behavior changes

Close the pending review round before resetting. Wait for an explicit approval
of the specific accepted finding IDs in a later user turn. Change only those
approved findings in the mutable authoring closure; never edit the retained
capture. Then update that same round in `review-record.json` before calling
`reset_workflow` or appending another round: record the real `approved_at_unix_ms`, the exact authorizing
user message in `citation` (who authorized what), and only the approved finding
IDs in `approved_finding_ids` (which IDs). Set `state: "applied"` and list
`applied_files` only after those files were actually changed. If the user
explicitly defers another accepted finding, leave it `not_applied` with no
files and include only its ID in `deferred_finding_ids`. Validate the completed
round with `validate_review_round` when the helper is available, then write it
and reset. Never reset first, leave `user_decision: null`, or carry approval
from an earlier turn or a different finding ID. A round with unresolved
accepted findings fails the construction review check even when every later
round is clear.

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
