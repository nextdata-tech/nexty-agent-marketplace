# Supervisor workflow v2 construction

Use the supervisor workflow API for every new local construction. This path
owns consent, capture, review evidence, validation, admission, and publication;
do not fall back to a direct CLI or local substitute.

## Contents

- [Gate on the connected capability](#gate-on-the-connected-capability)
- [Prepare the prose blueprint](#prepare-the-prose-blueprint)
  - [Revised or successor plan](#revised-or-successor-plan)
  - [Refresh in place](#refresh-in-place)
  - [Revise a published product in place](#revise-a-published-product-in-place)
  - [Classify admission failures before recovery](#classify-admission-failures-before-recovery)
  - [Recovering a rejected proposal](#recovering-a-rejected-proposal)
- [Relay consent and capture](#relay-consent-and-capture)
- [Run and report the review](#run-and-report-the-review)
- [Follow returned actions through admission](#follow-returned-actions-through-admission)
- [Mapper approval](#mapper-approval)
  - [Keep an accepted spending approval across a repair](#keep-an-accepted-spending-approval-across-a-repair)
  - [Provider credential access](#provider-credential-access)
  - [Cumulative folder budget](#cumulative-folder-budget)
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

This priority describes glossary-term importance, used to filter or sort
definitions; it is not an issue's business priority, score or rank by default.
An omitted, platform-fixed value needs no separate priority-selection question.
Keep the required disclosure brief and group terms that share the same default,
naming every affected term and retaining literal `P3` and `default`. For example:
"The Open issue, Priority score and Rank definitions use standard glossary
importance (P3 default); issue ordering follows the scoring rules above."
Use that distinction only when the approved plan does not use glossary importance
as a business or governance rule. Preserve explicit priorities, their disclosures
and any result-changing governance choice; approval still covers the whole
prepared plan. The current checker requires raw `P3`/default disclosure in the
human echo, so a guidance-only clarification cannot remove it.

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

Approved rules and code remain pinned; this does not freeze live source data.
Each admitted live-source run refetches source data. When a mapper is part of the
approved plan, that run invokes its bound mapper under the applicable spending
approval and all admission gates. Querying or
reopening an existing publication alone does not refetch or rescore it. Claim a
fresh fetch or mapper execution only from evidence of that admitted run; never
substitute a chat-connector fetch or agent-scored file for in-product execution.
Pinned state alone does not establish that a refresh requires a new version.
Use returned runtime capability/state evidence before claiming that requirement;
a behavior-changing revision goes in place under a freshly approved plan
([Revise a published product in place](#revise-a-published-product-in-place)).
A source-access blocker is not a request to change source scope:
never offer or solicit a chat/file substitute, even as a separate publication.
Only an independent user request to change source scope permits that different plan.

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

### Refresh in place

A published workflow admits a refresh: another admitted run of the same
workflow ID, not a new workflow. The flow is the ordinary one, on the same
workflow: **capture -> review -> approval -> validation -> `start_run`**.
Capture the same authoring closure, with new source rows under its `data/` for
a file source, or unchanged for a live source that refetches. Follow the
actions `inspect_workflow` returns; do not call `reset_workflow` or
`remove_workflow` to start a refresh. Consent, review, spending approval and
their carry-forward rules are unchanged. A workflow whose admitted run failed
refreshes the same way once that run is terminal. A failed run that sent no
request to the provider does not use up its one-run spending approval, so the
retry may keep it
([Keep an accepted spending approval across a repair](#keep-an-accepted-spending-approval-across-a-repair)).

What the refresh starts from depends on the transform:

- **Stateless transform:** rebuilds from an empty database on every run.
- **Stateful transform (`transform_state`):** inherits the published release's
  database, dlt state and per-model bags, but only while the closure's logic is
  unchanged (the same pinned definition apart from `data/`). Validation seeds
  its scratch run from that same base, then replays the transform once with no
  new rows; every landed table must keep its row count.
- **Any change to the transform, spec, models, contracts or mapper grant:**
  a new state lineage, so the run starts from an empty bag and an empty
  database: a full re-land and, for a mapper, a full re-score. Say so, with
  the cost, before the spending approval.

The state lineage decides what a run inherits; it does not decide whether a
new product is needed. A change to the transform, spec, models or contracts of
a published product is a behavior-changing revision, and it also goes in
place on the same workflow
([Revise a published product in place](#revise-a-published-product-in-place)).
Each refresh of a mapper product needs a fresh
spending approval, because a non-recurring approval is spent by the
publication it admits. While the grant's `expires_at` is still in the future,
request that approval for the unchanged grant: the lineage holds and the bags
carry over. Do not rewrite the grant just to refresh. When the refresh does
need a grant change (the grant has expired, or the scratch run exceeds its
ceilings), a grant-only change (the same model, with only a later expiry or
new ceilings) is not a behavior-changing revision: it stays on the same
workflow and starts empty.

On a refresh, or any request for newer data, validate against the live source
first. Keep the supervisor order: run only the review the returned
`next_actions` require, and do not widen it into a re-audit of logic the
published release already passed. Do not propose a revision of the product's logic, including from that
review's hardening notes, until validation shows real drift or returns a code
whose recovery is a logic change. If validation shows new
values or drift, or a refresh run fails because the source changed, tell the
user in plain terms what changed (the values and counts) and ask what to do
with it. Make no closure change, including adding diagnostics or hardening,
until the user decides; hardening comes after that answer, never in front of it.

### Revise a published product in place

A change to what a published product computes (a new rule, a corrected
measure, a fixed cursor, a different mapper instruction) goes to the same
workflow ID. Consumers keep the product they already use; there is no new
workflow, no successor product and no switch for them to make.

1. **Before the plan approval, say what changes for consumers,** in plain
   words: the product keeps its name; its next publication replaces the
   current results with ones computed by the new logic; the earlier results
   stop being served and are not kept as a separate product; and the run
   rebuilds from an empty database, which for a scoring step means scoring
   every row again, with its cost. Never mix old-logic and new-logic rows.
2. **Revise the plan** when its text no longer describes the new logic: edit
   `dp-blueprint.md`, record each change as a Decision, and run the normal
   replacement on the same workflow (`reset_workflow` with the graph-root
   requirement, a replacement blueprint and its matching inline
   `replacement_proposal`; [Reset after behavior changes](#reset-after-behavior-changes)).
   The user approves the revised plan through the returned consent action;
   that approval is the authorization for the in-place change. A code fix
   that the approved plan already describes exactly needs no plan change:
   recapture it as a repair.
3. **Then the ordinary flow:** capture, review, approval, validation and
   `start_run`, as in [Refresh in place](#refresh-in-place). A changed
   closure starts a new state lineage, so the run starts empty. A mapper
   product needs a fresh spending approval; say the full re-score cost
   first.
4. **If the supervisor refuses** the reset, capture or admission on the
   published workflow, keep the code internal and say in plain words that
   this version can't apply the change to the published product in place.
   Offer the separate product below instead, asked first. Do not switch to a
   new workflow ID on your own.

Build a separate versioned product under a new workflow ID only when the user
asks to keep the current release unchanged beside the new one (for example,
consumers who must not see the change yet). That is a new product: ask for
explicit authorization, wait for the yes, and never prepare the new ID in the
same turn as the ask or fold it into the plan approval.

Refresh-specific outcomes:

| Code | Meaning | Recovery |
|---|---|---|
| `workflow/unsupported_rebuild` | An earlier run of this workflow has not finished. | Call `inspect_run` once; wait for that run to reach a terminal status, then follow the returned actions. |
| `validation/refresh_not_idempotent` | The replay changed a table's row count: the cursor is not working. | Never retry unchanged. **Not yet published:** fix the transform ([incremental-transforms.md](../../nxd-generate-data-product/reference/incremental-transforms.md)), then reset, recapture and review. **Published workflow:** the fix is a behavior-changing revision; report the broken cursor, then fix it in place ([Revise a published product in place](#revise-a-published-product-in-place)). The current release keeps serving until the fixed one publishes. |
| `validation/refresh_base_unavailable` | The published base could not be leased for validation. | Retry validation. |
| `workflow/stale_execution` at `start_run` | A newer release published after validation, so the bound base is stale. | Validate again against the current release. |

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

At this consent point, ask once: "Would you like your consent to carry forward
if the supervisor re-prepares this same plan after an update?" This is
optional; never set `carry_forward_unchanged_plan` without the user's explicit
opt-in. Omit the field unless the user opted in; older supervisors reject
unknown fields. When opted in, include `"carry_forward_unchanged_plan": true`
in the same approved `session_decision`. It is valid only with `approved: true`. Re-preparation always computes a new consent
subject. Consent carries only when the blueprint semantics, policy, review
scope, budget, and proposal identity are unchanged and only supervisor tooling
identity changed. Otherwise ask for fresh consent. Tell the user when the
snapshot reports that consent was carried forward; a later denial still
revokes it.

Interpret authorization by the explicitly authorized object and limits, never
the word "approved" alone. Permission to inspect access, check connectivity or
draft a plan is not consent to the complete prepared blueprint. For example,
"Approved to check whether Nexty already has access. Do not start paid scoring
until the shared spending limit is accepted and the recorded usage is verified."
authorizes only that access check: keep plan consent pending, do not relay this
quote as the plan's `session_decision`, and do not generate, build or publish.
Perform only supported checks within the user's authorization; an access-check
permission does not authorize pre-consent closure artifacts or bypass their
generation gate. If the user has not approved the complete prepared read-back,
ask for that specific approval before relaying plan consent.

Conversely, explicit approval of the complete prepared plan may satisfy plan
consent and permit generation even when the user separately withholds paid
scoring. That plan approval does not accept a cumulative budget, grant mapper
spending or bypass native approval, verified budget state or any admission gate.

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

Only after the explicit opt-in, add this field to the parameters above:

```json
"carry_forward_unchanged_plan": true
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

Before every capture, run the [preflight before capture](../../nxd-generate-data-product/reference/pre-capture-audit.md#preflight-before-capture).

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

The ledger vocabularies are closed; the validator rejects any other value.
No other classification or disposition token is valid: never write an invented
one such as `semantic_defect`, `deferred_to_operator`, `behavior_gap`,
`blocking` or `needs_decision`:

| Field | Accepted values |
| --- | --- |
| round `status` | `complete`, `timed_out`, `needs_user` |
| finding `classification` | `behavior_affecting`, `structural_note` |
| finding `state` | `not_applied`, `needs_user`, `applied` |
| adjudication `disposition` | `accepted`, `rejected`, `out_of_scope` |

`needs_user` is a round status and a finding state, never an adjudication
disposition. Recipe for an unresolved `MEDIUM` or `HIGH` claim (verified, not
yet fixed, awaiting the user): `classification: "behavior_affecting"` +
`disposition: "accepted"` + finding `state: "needs_user"` + round `status:
"needs_user"`, with `applied_files: []` and `user_decision: null`. A round holding an unresolved `MEDIUM`/`HIGH` claim is `needs_user`, never `complete`, until the user decides; clear, LOW-only and rejected-only rounds are `complete` with `user_decision: null`. Once the user rules, update that same round (do not append a
new one): set `user_decision` with the real `approved_at_unix_ms`, the
authorizing `citation` and `approved_finding_ids`; move each finding to
`applied` (with `applied_files`) or, if deferred, move it to `not_applied` and list
it in `deferred_finding_ids`; then set the round `status` to `complete`. A
round left at `needs_user` with `user_decision: null` after the user ruled is
stale.

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

**A `LOW` or advisory claim never goes into `findings` and never makes the
verdict `findings`; it stays in `review-record.json`.** A round whose only
claims are `LOW` is clear once you resolve them. Record each
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

Only unresolved `HIGH` and `MEDIUM` claims become `findings` entries, each with
`severity: "blocking"`. If the supervisor refuses a report that carries only
`LOW` or advisory entries (typically with empty `next_actions`), do not ask the
user and do not hold publication: resend the same round as `verdict: "clear"`
with `findings: []`. Project each unresolved
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
never turn a report with blocking findings into `clear` or treat a missing
report as approval. A `LOW`-only report was built wrong: resend it as `clear`
(rule above).

When the supervisor returns `workflow/review_findings` containing any
`behavior_affecting` finding, the review is not authorization: set the round to
`needs_user`, relay the finding IDs, evidence, adjudication, and proposed
effects, and ask the user to decide the specific IDs. Do not edit the mutable
authoring closure, reset, or recapture in that turn. Continue only after an
explicit user approval in a later turn. “I don't know, you tell me” and
“whatever you think” are deflections, not approval; explain the choice and
wait. A new review finding requires a new user decision for its IDs, but ask once per
round: batch every blocking finding of the round into one list, one plan amendment
when the plan wording changes, and one re-approval request. Advisory findings and
cosmetic hardening (error wording, message sanitising) are not plan changes unless
the reviewer marked them blocking and they affect published results. After two
consecutive rounds whose findings are only about the previous fix, stop and tell
the user plainly what is left, what shipping it as is means for the published
numbers, and what another round would cost, instead of starting a third. Only
accepted findings the user approved may be applied; rejected, out-of-scope,
and structural findings stay unapplied and are not deferred.

A user reply that decides only some of the round's IDs is a partial decision.
Once a round carries a `user_decision`, every accepted behavior-affecting
finding in it must be applied or listed in `deferred_finding_ids`; the ledger
validator rejects the round otherwise. So when a reply decides only some IDs,
ask the user to approve or defer each remaining ID before writing
`user_decision`, and list in `deferred_finding_ids` only IDs the user
explicitly deferred; never leave `user_decision: null` on a `needs_user` round
the user has ruled on, because a later round does not clear it. An applied structural note needs no user decision.

There is exactly one retained-input review per capture generation, not one per
workflow lifetime. When an accepted finding changes behavior, edit the
approved findings in the mutable closure, record the user decision in that same
round of the external ledger, reset the returned capture requirement, check the
closure, recapture it, and run a fresh review for the new generation. Do not report the old generation as clear.

Build `findings` only from blocking claims (the `LOW`-only rule above); a
`LOW`-only round is reported `clear`. Relay the report with the requirement's returned generation, subject digest,
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
follow only its new `next_actions`. **Before every validation
`start_requirement`, in any workflow or revision, including a new workflow
opened late in the conversation, call `check_data_product` once more on the
unchanged authoring closure once the review is clear, so the last successful
check follows the last review report for this capture** (full preflight with no
`mode` argument without the mapper; `mode:"structural"` for a mapper closure,
since full mode is refused before admission with `mapper_approval_required`). Edit nothing between that check and the run. If it
fails, the authoring closure no longer matches the capture: repair, reset and
recapture rather than validate. Then start supervisor-owned validation with the
returned `start_requirement` action and current revision. A queued or running
operation is pending work: poll `inspect_workflow` without dispatching another
validation. Continue only when validation is satisfied. Do not end the turn, or
ask the user to say "continue", while that validation or a run you started is
queued or running. A rate-limited or paginated source can keep it running for
several minutes, and a hand-back returns control with nothing to decide: the
next message can change the plan and discard a validation that was about to
pass. Keep polling until the operation is terminal. Hand back mid-operation only
when `inspect_run` reports it as unknown rather than in progress, or when a
decision only the user can make is waiting. Then use the returned
`start_run` action with the current `revision` and `invalidation_epoch`.

An admitted `start_run` response identifies the run but can precede transform
execution and publication. Poll `inspect_run` with that run ID and
`inspect_workflow` for the current operation until it is terminal. Report a
completed build and resume or render only when the supervisor reports
publication. A terminal failure is a failure even though the original tool call
returned successfully; inspect its diagnostic and follow its recovery guidance.
Older supervisors may return terminal results directly, which need the same
completion checks. Keep the MCP session open while its work advances. If a call
response is lost, inspect or replay the exact request rather than starting a
second validation or run; use each new snapshot's revision for the next action.

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
included, or a bare family alias for the primary model only); 1000 calls,
2,000,000 tokens and 25 USD per approval; 100 USD
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
yourself. For a closure with several AI steps
([field mapper](../../nxd-generate-data-product/reference/field-mapper.md#several-ai-steps-in-one-build)),
say instead that one window covers every step: it shows the shared call,
token and dollar limits and the expiry, then each step's purpose, model, the
fields it sends (an earlier step's output reads as "the summary written in
step 1") and any per-step call limit, and one choice accepts or declines all
of them. When the `advance_workflow` tool description mentions
`carry_forward_unchanged_approval`, also say that, until it is used for a
publication or by a run that reaches the provider, or expires, the approval
still covers fixes to how the view is built, as long as the data sent, the model, the limits and the expiry stay the
same. Then call `start_requirement` with the returned requirement id and
the current revision. The supervisor opens a native OS dialog. That dialog is
the only approval surface. The response carries
`approval` with `state:"awaiting_user"` and timing details, and no URL, token,
capability or key, and you must not look for one. You cannot approve, decline,
extend, widen or reset an approval. Do not click the dialog, type into it, or
drive it with any automation tool.

Explain the three times separately from returned fields, never guessed durations:
`request_expires_at` is the request deadline; `dialog_timeout_seconds` is the
native decision time limit, with `dialog_started_at` and `dialog_deadline_at`
when recorded; `grant_expires_at` is spending permission expiry. Legacy
`expires_at` remains readable: request deadline while pending, grant expiry
after a decision. Older records may omit timing details; do not reconstruct them.

Once the prompt is open, use read-only `wait_workflow_approval(workflow,
request_id, after_revision?, timeout_seconds?)` in a bounded loop of a few calls,
respecting the conversation. `request_id` is the approval request id from
`approval.request_id` in the `start_requirement` response (or
`mapper.approval.request_id`), never the `advance_workflow` idempotency key; a
wrong id returns `workflow/approval_request_not_found` or
`workflow/invalid_request`. The default wait is 20 seconds, capped at 25 per
call; zero checks once. Capability `approval_wait` carries
`default_timeout_seconds`, `max_timeout_seconds`, and `fallback:"inspect_workflow"`;
the tool is listed in `operations`.
If absent, use bounded `inspect_workflow` polling.
The result is the flattened current snapshot plus `wait_status`: `pending`, `terminal`,
`superseded` or `interrupted`. A revision or dialog-phase change may return early;
ending a wait does not expire the request. Do not reopen a prompt while waiting.
The supervisor polls every 300 ms and allows one wait reader per workflow.
A concurrent second wait may briefly return `workflow/state_unavailable`;
serialize waits and retry within the bounded loop or inspect current state.
`workflow/approval_request_not_found` means the request was not found for this
workflow; inspect current state rather than creating a new request.
For `interrupted`, the response omits top-level `approval`, clears `next_actions`,
and sets `mapper.approval.state:"interrupted"` with no live `dialog_phase`.
Inspect current state before continuing; never reuse stale prompt actions.
When `wait_reason:"request_deadline_passed_not_yet_recorded"` accompanies
`terminal`, the deadline passed without a recorded decision. Do not report
acceptance or a recorded expiry from that observation; inspect current state.

Check `mapper.approval.recorded_decision` (`outcome`, `decided_at`, `request_id`)
against the request being observed. When `outcome:"accepted"`, tell the user
"Your spending approval is recorded" and continue the authorized returned
actions with the latest revision, without asking for another chat message.
The receipt proves a committed decision, not current eligibility: expiry,
budgets, other requirements and admission still apply. A user saying "I clicked
approve" is not acceptance; check the runtime.
For terminal `dialog_timed_out`, legacy `timed_out`, `request_expired`,
`declined` or `cancelled`, give one next step: ask whether to reopen the same
capped request. Reopen only on their yes and if runtime eligibility permits
it. `surface_unavailable` (the native dialog could not be shown) and
`integrity_unavailable` (the decision could not be verified) are not consent:
report the blocker and inspect state; never reopen automatically. If still
`pending` after the bounded wait, say it is pending and leave it open. For `superseded` or `interrupted`,
inspect current state; never reopen automatically.

`inspect_workflow` reports a `mapper` block for the requirement. It carries
the approval `state` and `code`, the declared scope, each spend ledger (its
state, reserved and settled totals, ceilings, and integrity), and a `lifetime`
figure, `settled_and_reserved_usd_micros`. The `lifetime` block is omitted
when that spend cannot be verified. Report lifetime spend as unavailable then,
never as zero.

The supervisor enforces the provider (exactly `anthropic`), the models, the
expiry, the request shape and the ceilings. A Desktop grant must also list a
non-empty `input_fields` (otherwise `workflow/mapper_scope_invalid`), and the
mapper runtime refuses any request built from fields outside it: each
`MapperInput` names its `text_fields`, and its `landed_text` must be their
exact rendering (`landed_text_undeclared` otherwise). The dialog shows this
list as "Fields this workflow sends to the AI model (the scoring step refuses
requests built from other fields)". That check runs inside the closure and
compares field names, not contents, so tell the user the fields it sends,
never that the list proves what a field holds. `pii_category` and `purpose`
are declared by the author and reviewed by the user, not enforced.

A grant is fixed once the user approves it. Any wider scope (a new model,
higher ceilings, a later expiry, `recurring: true`) needs a proposal the agent authors and the user
confirms, a recapture, a fresh review and a fresh OS approval. Never
widen a grant yourself to get past a refusal. Never retry `start_requirement`
in a loop: each call can put a dialog in front of the user.

### Keep an accepted spending approval across a repair

A repair that changes only how the view is built (a bug fix, a policy or
check finding, a transform correction) keeps the user's spending approval
when everything the dialog showed stays the same. The native dialog says so
up front: the permission also covers fixes to how the view is built, as long
as the data sent, the model, the limits and the expiry stay the same.

Supervisors older than nxd 0.41.294 cannot carry an approval and reject the
unknown field. Use this section only when the `advance_workflow` tool
description mentions `carry_forward_unchanged_approval`; otherwise omit the
field and follow the normal dialog flow above.

After such a repair, follow the normal reset, local correction, recapture and
one fresh review. When the returned mapper approval `start_requirement`
action appears, request carry-forward only if all of these hold:

- The earlier approval is accepted, unexpired and not yet used for a
  publication. On Desktop every approval is one-run, so after a publication a
  fix always needs a new prompt. A failed run does not count as a use when it
  sent no request to the provider, so after a failed run, request
  carry-forward for the retry when the other conditions hold. The supervisor
  checks the spending record and decides. It refuses, and the dialog opens
  again, when any request reached the provider (even one that cost nothing),
  when the run hit the approved ceiling (`mapper_ceiling_reached`), or when
  spend is still unsettled. Never promise the user the approval will carry
  before the supervisor answers.
- The plan was not re-approved: the repair kept the approved blueprint.
- `contracts/mapper_spec.json` and `contracts/mapper_grant.json` are
  byte-identical to the approved capture's. With several AI steps, so are
  `contracts/mapper_stages.json` and every step's spec; any changed step
  refuses (`workflow/mapper_approval_not_carried/stages_changed`).
- The fresh review is clear, including its check that every `text_fields`
  value traces to its named landed column (or, with several AI steps, to the
  earlier step's output it declares) and nothing else is sent to the
  model. That is how the review confirms mapper inputs are unchanged.

```json
{
  "request_id": "approval-<workflow>-<unique>",
  "workflow": "<workflow>",
  "expected_revision": 12,
  "action": {
    "type": "start_requirement",
    "parameters": {
      "requirement_id": "<from-next-actions>",
      "carry_forward_unchanged_approval": true
    }
  }
}
```

Omit the field when any condition fails, or when the repair changed what is
sent, the model, a ceiling, the expiry or `recurring`: the dialog must open
again. The supervisor decides. When it carries the approval it emits
`workflow/mapper_approval_carried_forward` and no dialog opens; say "Your
earlier spending approval still covers this fix" and continue the returned
actions with the latest revision. Spending continues against the same
limits. When it emits `workflow/mapper_approval_not_carried/<reason>`, the
normal dialog flow above applies: tell the user a new spending prompt is
coming, in plain words and without the reason code, and handle it as a first
approval. Never retry carry-forward after a refusal. If the request is
rejected because `carry_forward_unchanged_approval` is an unknown field (an
older supervisor),
send `start_requirement` once more without the field, with a fresh
`request_id` and the latest revision, and follow the normal dialog flow. Any
other rejection, such as a stale revision, follows its own recovery. Never
call carry-forward a bypass: validation and admission still run in full on
the repaired view.
Approvals made before the dialog carried this sentence, expired approvals, a
one-run approval already used for a publication, and one whose failed run
reached the provider are never carried. Supervisors before nxd 0.41.300
treat any admitted run as a use: they refuse after a failed run, and the
normal dialog flow applies.

### Provider credential access

Read permission for a provider key, read permission for a source credential,
access to the protected saved-workflow-state key, and approval to spend are
separate. One never grants another. Keep provider and source credential values
in their supported Desktop storage flows; never ask for a Mac password, a key,
or a credential in chat. For Keychain access recovery, use the canonical table
linked below; terminal or environment-variable setup applies only to its separate
missing-provider-key outcome.

When `provider_credential_status` appears in the live
`get_workflow_capabilities.operations`, it is a read-only tool with no
arguments. It makes one bounded 20-second read, well below the bridge's
60-second deadline, never returns a key, and never contacts the provider.
Its response has this shape:

```json
{
  "provider": "anthropic",
  "present": "yes|no|unknown",
  "access": "success|missing|access_not_granted|interaction_not_allowed|keychain_unavailable|timeout|pending|integrity|unsupported|failed",
  "retryable": false,
  "permission": "provider_credential_read",
  "source": "keychain|startup_environment|null",
  "explanation": "macOS permission to read the provider key, access to protected saved workflow state, and approval to spend money are separate. One never grants another."
}
```

With `access: success`, `present: yes` means a key is present, `no` means it is
missing, and `unknown` means presence was not established. `access: missing`
also establishes that no key is present and reports `retryable: false`. Other
access errors establish neither
presence nor absence. Treat the `permission` field as provider-key read access
only. `source` reports `keychain`, `startup_environment`, or `null`; null means
no source was reported. These fields say nothing about source access, saved
workflow-state access, or spending approval.

Use [failure-handling.md](failure-handling.md#keychain-credential-and-saved-state-access-outcomes)
for the canonical reason-to-action mapping for provider, source, and protected
workflow-state Keychain outcomes. A concurrent provider-key read returns
`pending` immediately without opening a second prompt. After the user confirms
the request completed or was dismissed, re-check once; without confirmation,
one re-check after a short wait is allowed. If it is still pending, report
that and stop. `provider_credential_status` is provider-only. After missing-key
setup, check that it confirms the key is present, then resume the same request
and revision. For an access error, retry only the same request and revision
when `retryable: true`. A failed provider acquisition at `start_run` happens
before the request is queued. Do not reset or recreate workflow state.

The code `mapper_provider_credential_missing` identifies a missing-key setup
outcome; follow its provider setup directions in the canonical table. The
codes `mapper_provider_credential_access_not_granted`,
`mapper_provider_credential_access_interaction_not_allowed`,
`mapper_provider_credential_access_unavailable`,
`mapper_provider_credential_access_timeout`,
`mapper_provider_credential_access_pending`,
`mapper_provider_credential_access_integrity`,
`mapper_provider_credential_access_unsupported`, and
`mapper_provider_credential_access_failed` identify provider credential
acquisition/read failures and do not establish whether a key exists. Use the
canonical table for their reason-to-action mapping.


The installer may verify and activate successfully while its optional provider
presence check remains unresolved. Report activation as successful and
credential access as unresolved; point to `provider_credential_status` for a
later check. Do not recommend reinstalling.

`workflow/state_mac_key_unavailable` may carry an optional `reason`.
Without a reason, say only that protected workflow state could not be read.
Use the canonical table in
[failure-handling.md](failure-handling.md#keychain-credential-and-saved-state-access-outcomes)
for its action. If the reason is `missing`, stop and report; never retry to
fix a missing state key or direct the user to provider/source credential entry.
For other access outcomes, retry the same action with the same request and
revision only when `retryable: true`. Integrity always stops.
`workflow/state_mac_key_invalid` is an integrity refusal: report it and never
reset or recreate saved state.

Source errors keep their code contracts: `source_connection/protected_access_timeout`
means the bounded source-access read timed out even when its optional `reason`
is absent; it does not mean access was denied. Use the canonical table for its
action, without claiming a prompt is visible. `source_connection/state_mac_key_unavailable`
is a separate protected-state error and may carry an optional `reason`; without
one, say only that protected state could not be read. If the reason is `missing`,
stop and report without credential entry or state replacement. Otherwise retry
the same source action with the same request and revision only when
`retryable: true`. Stop on integrity refusal. See [native infra-profile
connections](infra-profile-connections.md) for the source-entry lifecycle.

### Cumulative folder budget

The per-approval and per-mapper-spec ceilings above remain in force. A separate
experimental cumulative cap is optional and applies to recorded mapper ledgers
in one Nexty data folder: every specification and workflow, including earlier
approvals, revoked ledgers, verified tombstones, uncertain attempts and open
reservations. It does not cover other Nexty folders, scoring outside Nexty, or
other sessions that were already running when the cap was enabled. It assumes
the folder is not replaced or restored from an older copy. Reset, refresh,
repair, removal and specification changes cannot reset or lower its ceilings
or floors; removal may settle a reservation and increase recorded usage.

First call `get_workflow_capabilities` and read its structured
`mapper_budget` field. It reports `availability`, the runtime-provided
`inspect_tool` and `proposal_tool` names, `scope: data_folder`,
`experimental: true` and `reset_supported: false`. Treat the capability as
available only when `availability` is `available`; a tool-catalog listing is
not a substitute. When available, call the returned inspection tool before
paid scoring and use its returned data-folder identity and status. Call the
returned proposal tool only with limits the user explicitly stated; never fill
missing dimensions from examples or ask for a starting balance. A proposal
records requested limits but does not activate them.
If a required limit is missing, ask only for that limit before proposing.
Convert USD to integer millionths for `max_usd_micros` ($1 is 1,000,000);
divide returned `usd_micros` by 1,000,000 when reporting dollars.

Inspection distinguishes `status: legacy|proposed|established`,
`state: open|exhausted|integrity|null`, and
`completeness: not_applicable|complete|unknown`. Legacy or proposed records
have no established budget state (`state: null` and
`completeness: not_applicable`). The cap becomes active only after native
approval succeeds and inspection confirms an established, complete, open
budget. If the user requested a cumulative cap but `mapper_budget` is missing
or does not report `availability: available`, stop paid scoring and report
that the connected install cannot enforce it; unpaid work may continue. Do not
invent a command or alternate tool. Without a user request for a cumulative
cap, preserve the existing approval and per-spec behavior.

For legacy/proposed status, `state: null`, `completeness: not_applicable`,
`unknown_reasons: []` and null `used`/`remaining` mean no active cumulative cap;
they are not an integrity failure or a reason to demand a starting balance.
`legacy` is a no-accepted-cap state in the current API, not an instruction to
switch to old APIs or compatibility commands. Keep using the advertised current
MCP tools; tell the user that no cumulative cap is active rather than calling
their setup legacy.
When the capability is available and the user stated all three limits, call the
returned proposal tool for a legacy/proposed folder, then await native Establish
approval and inspect again. Establish re-verifies and adopts the earlier
recorded ledger history, including earlier approvals, in that same active
runtime data folder. It does not count only spending since acceptance. Until
verified inspection supplies totals, do not assert an earlier amount, ask for
remaining balance, consult a provider dashboard, or ask the user to estimate
past usage. Do not propose around an established unknown/integrity or exhausted
budget; those states still stop paid scoring.

The inspection's `data_dir` identifies the active runtime folder whose ledger
history is counted. An authoring, export or job folder is distinct: it neither
selects the budget folder nor removes its earlier history. Keep the runtime
folder identity internal; never choose another folder to escape a cap.

For `completeness: unknown` or `state: integrity`, `used` and `remaining` are
null. Pause paid scoring, continue unpaid work where possible, and never
reinterpret null as zero or ask the user to estimate historical usage. For
`state: exhausted`, paid scoring in this folder is finished. Do not propose a
raise, reset or disable. Report verified call, token and USD amounts only when
inspection provides them. A recorded zero means only that no earlier AI
scoring is recorded in this data folder; it never means the whole test or
experiment spent zero.

| Code | Meaning | Tell the user | Next action |
|---|---|---|---|
| `workflow/mapper_scope_invalid` | The grant is missing, malformed, not strictly typed, or has an unknown key. `provider` must be exactly `anthropic`, `max_calls` and `max_tokens` positive integers, `max_usd` positive, `expires_at` a future RFC3339 time, `recurring` a bool. Also raised when both grant paths exist or the spec is missing. No prompt was shown. | The grant needs correcting before it can be shown for approval. | Fix the grant shape in the authoring closure (confirm any scope value with the user), reset capture, recapture, review, then request approval again. |
| `workflow/mapper_approval_claims_rejected` | The grant carries approval claims such as `approved`, `approved_by`, `receipt` or `signature`. | Nothing was approved; the grant tried to assert approval. | Remove the claims, reset capture, recapture, review, request approval again. Never add approval fields. |
| `workflow/mapper_grant_exceeds_policy` | The grant asks for more than the installed policy allows (ceilings, provider, model, recurring). | The request is larger than this install permits. | Ask the user to narrow the scope, then edit, recapture, review and request again. Do not look for a way to raise the policy. |
| `workflow/mapper_model_unpriced` | The model has no price entry, so spend cannot be bounded. | That model cannot be used under supervision. | Ask the user to choose a priced model; edit the spec and grant, recapture, review, request again. |
| `workflow/approval_pending` | An approval request for this requirement is already open. | A prompt is already waiting for their decision. | Observe with bounded `wait_workflow_approval` (or `inspect_workflow`); do not call `start_requirement` again. |
| `workflow/approval_declined` | The user declined in the OS dialog. The requirement is pending again. | They declined; nothing ran and nothing was spent. | Ask whether to reopen the same capped request. Request again only on their yes. |
| `workflow/approval_cancelled` | The dialog was cancelled. | The prompt was cancelled; nothing was approved. | Ask whether to prompt again; request again only on their yes. |
| `workflow/approval_dialog_timed_out` | The native decision window timed out. | The approval window closed without recorded acceptance. | Ask whether to reopen the same capped request; only on their yes, follow returned actions. |
| `workflow/approval_request_expired` | The request deadline passed before a decision was recorded. | The spending approval request expired. | Ask whether to reopen the same capped request; only on their yes and if eligible. |
| `workflow/approval_expired` | Spending permission expired before admission, or a non-recurring approval was already spent by the publication it admitted; historical timeout records remain readable. | The approval no longer applies. | Ask whether to prompt again. If the grant's own `expires_at` is past, agree a new expiry, author it, recapture, review and request fresh native approval. |
| `workflow/approval_cooldown` | A request for the same mapper subject came too soon after a decline or cancel. | A short wait applies after a decline. | Do not poll. Tell the user, and request again only when they ask after the cooldown. |
| `workflow/approval_interrupted` | The supervisor restarted while a prompt was open. | The prompt was lost when the supervisor restarted. | Ask whether to prompt again; request again only on their yes. |
| `workflow/approval_surface_unavailable` | The OS dialog could not be shown. | Approval cannot be collected on this machine right now. | Stop and tell the user in plain words; keep the code internal. Never substitute a chat "yes" for the dialog. |
| `workflow/approval_ceiling_reached` | The per-approval call, token or USD ceiling, or the mapper-spec lifetime USD ceiling is spent. | The current approval or spec limit is used up. | Stop this run and report. A wider per-approval or per-spec grant requires fresh approval only if the user asks; it cannot raise an established cumulative folder cap. If that folder cap is exhausted, paid scoring there remains finished. |
| `workflow/mapper_budget_reached` | The cumulative folder-wide call, token or USD cap is exhausted; an in-flight attempt may still settle at its full reservation. | Paid scoring in this data folder is finished. | Stop paid scoring. Do not retry, move folders, or propose a higher cap. |
| `workflow/mapper_budget_unknown` | The cumulative total cannot be verified, including when its integrity key or recorded history is unavailable. | I can’t verify the cumulative total for this data folder, so paid scoring is paused. | Continue unpaid work where possible. Do not claim zero, estimate past usage, or edit supervisor state. |
| `workflow/mapper_budget_changed` | The approval binding no longer matches the folder budget state. | The folder budget changed before this approval could be used. | Inspect current state and follow returned actions for a fresh approval; do not reuse a stale binding. |
| `workflow/mapper_budget_mismatch` | A proposed limit differs from the established folder cap. | This folder’s existing cap cannot be changed in this version. | Keep the established limit; do not raise, reset or disable it. |
| `workflow/mapper_subject_changed` | The approved capture no longer matches what is being admitted. | The approved closure changed, so the approval no longer applies. | Stop and report. Do not retry; a legitimate change goes through reset, recapture, review and fresh approval. |
| `workflow/state_mac_key_unavailable` | The supervisor could not read the protected saved-workflow-state key; an optional `reason` identifies the access result. | The protected workflow state could not be read. This is separate from source/provider credential access and spending approval. | When `retryable=true`, use the matching local action in [failure-handling.md](failure-handling.md#keychain-credential-and-saved-state-access-outcomes), then retry the same action with the same request and revision. Otherwise stop and report. Never reset or recreate state. |
| `workflow/state_mac_key_invalid` | The protected saved-workflow-state key failed integrity verification. | The saved workflow state could not be verified. | Stop and report. Never reset or recreate state or suggest that another permission grants integrity. |
| `workflow/mapper_integrity` | The approval or spend ledger failed an integrity check, or a lookup the gate depends on failed. The supervisor fails closed. | Approval records could not be verified. | Stop and tell the user in plain words; keep the code internal. Do not edit or delete supervisor state. |

Validation in mapper mode uses stand-in answers for AI mapping: the mapper
makes no provider call and charges nothing. The strict conditions below
describe a Desktop release that checks the output-model binding; they
are runtime-contract reference, not a support signal. The current
`get_workflow_capabilities` response has no signal for binding-aware scratch
coverage. With this interface, always report the advisory as wiring-only and
never as verified output coverage. Do not infer binding-aware support from the
advisory code, the binding file or a version guess. Never ask the user for a
version to select the reporting. In the matching binding-aware runtime, source
fetching remains real; source-fetch behavior on earlier releases is unverified here.
An advisory alone never proves a source refetch.
Generate and capture the mapper's exact output model binding and singular
verifier attribution as described in
[field-mapper.md](../../nxd-generate-data-product/reference/field-mapper.md#desktop-supervisor-approval-boundary).
Do this as part of authoring; do not ask the user to construct internal IDs.
The inspected earlier capture source retains this extra regular `contracts/` file
under normal limits, without interpreting the binding. Compatibility with other
older installed captures remains unverified; if capture rejects it, stop and
report the unsupported setup rather than remove it to obtain an advisory.

The advisory `validation/mapper_output_unverified` is eligible only for a
trusted failed-promise refusal in the supported local one-shot self-managed
batch when Nexty verifies complete same-run output checks, scratch readback,
and failures confined to the bound model's mapper fields. Ordinary successful
scratch does not require advisory coverage or this output-model binding. A
refusal stays blocking with missing, invalid or ambiguous binding, incomplete
evidence, unrelated or mixed failures, invalid typed outputs or missing
context. Transform, sandbox, timeout, cancellation, host lifecycle and cleanup
faults also stop validation. Every other contract failure still fails.
Do not weaken a guarantee or skip the required free test to continue.
Legitimate artifact repairs require reset, recapture,
independent review and fresh approval; keep the existing cumulative budget.

Report the advisory using the user's requested field names: “The free test
checked the wiring but could not verify the AI-filled values. The real run
will check those values before saving the view.” The real run still enforces
every guarantee; every real-run failure still blocks publication, including a
failure in any required AI-filled value.
Scratch validation proves wiring, not the quality of mapped values.

| Validation code | Recovery | Next action |
|---|---|---|
| `validation/mapper_closure_unsupported` | `stop` | The activated contract has no mapper approval requirement: an older supervisor, or an upgraded install that kept its previous contract because workflows existed. Report it; mapper closures cannot be built on this install as activated. |
| `validation/mapper_scope_invalid` | `repair_then_retry` | The supervisor could not read the mapper proposal (spec or grant). Fix the files in the authoring closure, then reset capture, recapture, review and validate again. |
| `validation/mapper_approval_missing` | `stop` | The mapper closure reached validation with no approval binding. Report it; do not work around it. |
| `validation/mapper_subject_changed` | `stop` | The pinned closure does not match the approved one. Report it as a bug or tampering; do not retry. |
| `validation/mapper_ceiling_exceeded_in_scratch` | `repair_then_retry` | The scratch run would exceed the per-approval or per-spec ceilings. Reduce what the transform maps, or ask whether the user wants to widen that per-run grant (edit, recapture, review, fresh approval). This does not raise an established cumulative folder cap; when that cap is exhausted, paid scoring in the folder stops. |
| `validation/mapper_grant_refused` | `repair_then_retry` | The transform asked for a model outside the grant, or sent a request shape the broker refuses. Make the transform use the granted model and an allowed request; a different model needs the user's fresh approval. |
| `validation/mapper_provider_unavailable` | `stop` | The validation stub is not available in this supervisor build, or the scratch run tried to reach a real provider. Report it. |
| `validation/mapper_output_unverified` | advisory | Not a failure. With the current capability response, always report wiring-only; never claim verified output coverage. In the binding-aware runtime contract, stand-in checks failed only on the bound mapper fields; the values remain unverified until the real run checks them. |

A failed run's diagnostic can carry `mapper: {code, ledger_key_short}`.
`mapper_ceiling_reached` means the live run hit the approved ceiling and was
refused before the network; the mapper raised `BudgetExceededError`.
`mapper_grant_refused` means the broker refused a request outside the grant (a
model not granted, or an approval past its expiry). `mapper_request_refused`
means it refused a disallowed request shape, such as server tools, images or
streaming. The mapper raised `GrantError` for both. `mapper_integrity` means
the spend ledger or the run's mapper setup could not be verified. For all
four, stop and report. Do not rerun to "try again". With several AI steps,
`workflow/mapper_stage_call_cap_reached` means one step reached its own call
limit; treat it like `mapper_ceiling_reached`. `mapper_schema_too_large`
means the AI provider refused an answer group as too large before any row was
scored. Do not rerun it unchanged: follow the `mapper/schema_too_large`
recovery in
[field-mapper.md](../../nxd-generate-data-product/reference/field-mapper.md)
(fewer fields per request), then recapture, review and ask for a fresh
approval. If `start_run` reports
`mapper_provider_credential_missing`, follow the provider setup directions in
the canonical table in
[failure-handling.md](failure-handling.md#keychain-credential-and-saved-state-access-outcomes).
Provider acquisition fails before the run is queued. After setup, check that
`provider_credential_status` reports the key present, then resume the same
`start_run` request. Never supply a key, token or base URL yourself; never
place them in chat or the closure.

## When validation fails

A failed `start_requirement` for validation returns a bounded `phase`, `code`,
`detail`, and `recovery`. The supervisor deliberately omits compiler and
subprocess output from that diagnostic.

- Any diagnostic with `phase: capture` uses the capture-code table under
  **Reset after behavior changes**, even when surfaced by validation or run
  admission. In particular, `workflow/capture_proposal_blueprint_mismatch`
  requires full replacement of the blueprint and regenerated proposal, not a
  capture-only reset.
- `recovery: reprepare` means reset the supervisor-returned graph root with
  `reprepare_retained: true`, then follow the returned actions. Consent may be
  pending or carried forward; trust the returned requirement state.

- `recovery: repair_then_retry` means the retained closure has a defect you can
  fix. Run the [preflight before capture](../../nxd-generate-data-product/reference/pre-capture-audit.md#preflight-before-capture)
  now to read the specific finding behind the bounded code, and again before
  recapture. For example, a `structure/spec_compile_failed` finding
  carries the compiler's own error, such as a duplicate metric name. Repair the
  closure, then reset the capture requirement with `replacement_blueprint_path`
  set to null. Recapture, obtain a fresh review, and follow returned spending
  and validation actions. A repair that changes only how the view is built,
  while keeping the plan unchanged, never asks the user to re-approve the plan.
  Explain the fix in plain words; for example, "I fixed how the view is built;
  the plan is unchanged." For requested rules in the first plan, see
  [dp-blueprint.md](dp-blueprint.md). Never resubmit an unchanged closure after
  this outcome, and never substitute the agent-side policy audit for
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
- `recovery: stop` is a blocker. Report the user-visible impact in plain words; keep the code internal.

`check_data_product` is diagnosis only. It never substitutes for supervisor
validation, admission, or publication.

## Reset after behavior changes

Every repair cycle runs the same order, with no step dropped on a later cycle:
**edit -> record the round -> reset -> check -> capture -> review -> check again -> validate**. The
check after the review is clear is the one that makes the last successful
`check_data_product` follow the last review; the check before capture does not
count for it, and a final repair cycle is not exempt. Any recapture, including
one forced by a validation-time edit, restarts review -> check -> validate -> `start_run`; the check after the most recent review is always the last step before validation, and nothing is edited between it and `start_run`. Call it on the unchanged
authoring closure, then start validation.

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
`replacement_blueprint_path` plus the complete matching inline
`replacement_proposal`. The replacement proposal has the same complete typed-v3
shape used by `prepare_workflow`; do not omit it or send a summary. Then obtain
fresh consent when the plan changed. If the edit happens after consent, the
same reset/replacement rule applies. Do not invent the root id; read it from the
current requirements/actions. If only the generated closure changes behavior,
reset the returned capture requirement and set `replacement_blueprint_path` to
null. For an update that made the retained proposal's materializer identity
stale, use `reprepare_retained: true` at the graph root instead of copying the
proposal into the request. This option is mutually exclusive with replacement
inputs and re-prepares the stored blueprint and typed proposal using the
running supervisor. Then follow the returned actions from the new generation
and epoch.

Capture failure diagnostics have phase `capture` and stable public codes. Use
the diagnostic recovery value and follow this table:

| Code | Meaning | Recovery |
|---|---|---|
| `workflow/capture_materializer_stale` | The retained proposal is intact and bound to this blueprint, but was prepared by a different materializer version. | Reset the graph root with `reprepare_retained: true`; consent is pending unless the user opted into unchanged-plan carry-forward and every plan-bound field still matches. |
| `workflow/capture_proposal_blueprint_mismatch` | The proposal is bound to different blueprint semantics. | Regenerate the proposal, reset with both replacement inputs, and obtain consent for the changed plan. |
| `workflow/capture_proposal_integrity` | The retained proposal is missing, unreadable, malformed, or has a digest mismatch. | Stop and tell the user in plain words; keep the code internal. Recovery requires a full replacement blueprint plus regenerated proposal and renewed consent; retained re-preparation is refused. |
| `workflow/capture_blueprint_integrity` | Retained blueprint bytes do not match their content address. | Stop and tell the user in plain words; keep the code internal. |
| `workflow/capture_retained_integrity` | The retained capture is unreadable, conflicts with its digest, or changed while sealing. | Stop and tell the user in plain words; keep the code internal. |

Other capture codes identify input limits, reserved artifacts, or a specific
materialization failure and carry their own recovery disposition. Do not infer
a cause from diagnostic prose or weaken integrity checks.
The same typed proposal classification is repeated when validation starts and
when a run is admitted, so an update between capture and either boundary still
returns `workflow/capture_materializer_stale` with re-preparation guidance.
Re-capture, re-review, and re-run validation as indicated; stale evidence
cannot satisfy admission.

```json
{
  "request_id": "reset-<workflow>-<unique>",
  "workflow": "<workflow>",
  "expected_revision": 10,
  "requirement_id": "<returned-graph-root-or-capture>",
  "replacement_blueprint_path": "/host-visible/new-blueprint.md",
  "replacement_proposal": {
    "schema": "nxd-dp-spec-proposal-v3",
    "authoring_version": "nxd-dp-spec-authoring-v1",
    "proposal": { "<complete typed proposal payload>": "from dp-blueprint.proposal.json" },
    "provenance": { "<complete provenance map>": "from dp-blueprint.proposal.json" },
    "source_spans": { "<complete source-span map>": "from dp-blueprint.proposal.json" },
    "anchors": { "<complete anchors map>": "from dp-blueprint.proposal.json" },
    "echo": { "text": "<complete natural-language echo-back>", "coverage": ["<all covered paths>"] }
  }
}
```

For re-preparation after a supervisor update, use this mutually exclusive form
at the graph-root requirement:

```json
{
  "request_id": "reset-<workflow>-<unique>",
  "workflow": "<workflow>",
  "expected_revision": 10,
  "requirement_id": "<returned-graph-root>",
  "reprepare_retained": true
}
```

Record the reset and any external effects honestly. Reset invalidates durable
workflow eligibility and requests cancellation where needed; it does not undo
disclosure, spending, provider calls, or another irreversible external effect.
