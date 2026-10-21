# Field mapper — mapping from inside a transform

The one sanctioned way a transform may call a model, and the way a **packaged**
data product infers. Agent-side judging that lands as CSV before the build
(llm-judgments.md) is the exploration lane — right while the rubric is still
moving, wrong as a shipping shape, because it leaves the procedure outside the
artifact. This path is gated by consent rather than trusted, and the gate is the
price of having the logic travel with the closure.

## Contents

- [When to use it (and when not to)](#when-to-use-it-and-when-not-to)
- [What the Desktop flow supports today](#what-the-desktop-flow-supports-today)
- [Importing it in a closure](#importing-it-in-a-closure)
- [Authoring the spec and the grant](#authoring-the-spec-and-the-grant)
- [Desktop supervisor approval boundary](#desktop-supervisor-approval-boundary)
- [Evidence modes — what each one proves](#evidence-modes--what-each-one-proves)
- [Reviews: `data/mapper_reviews/`](#reviews-datamapper_reviews)
- [Two dlt runs, in this order](#two-dlt-runs-in-this-order)
- [Spend: the self-check really pays](#spend-the-self-check-really-pays)
- [Disclosing what is sent to the model](#disclosing-what-is-sent-to-the-model)
- [What the consent gate cannot see](#what-the-consent-gate-cannot-see)
- [Verifying the installed harness](#verifying-the-installed-harness)

## When to use it (and when not to)

**Use the field mapper whenever a packaged closure's answers depend on
inference.** That covers the case it was first written for — a mapping over rows
the transform itself produces, where no fixed CSV can be authored ahead of it —
and it also covers the ordinary case of a rubric applied to a bounded list, once
that product stops being an experiment and starts being something that ships,
gets handed off, or is rebuilt later. In every one of those, the procedure has to
be inside the closure, and this is what puts it there.

It brings real cost, and the cost is worth stating plainly: an agent-authored spending proposal, a human supervisor approval interaction,
and live model calls (with real spend) during admitted execution.

**Use [reference/llm-judgments.md](llm-judgments.md) — agent-side judging, landed
as CSV — while the product is still being explored.** Judging a bounded list
in-session costs nothing, needs no grant, and is the right tool while the
criteria are still changing and the whole product may be discarded. It is a
scaffold. When the same product is packaged, the judging moves here; a closure
that ships with agent-authored score rows is carrying an output whose procedure
nobody can re-run from the artifact.

A fixed set of rulings that is **not** inference — an FX rate, a
merchant→category mapping the user confirmed — stays landed reference data in
both lanes. Those are not judgements the closure re-derives; they are values it
was told.

## Before you build: run the preflight

**Run the Desktop preparation in [mapper-preflight.md](mapper-preflight.md)
before every mapper build.** Check `get_workflow_capabilities.mapper_preparation`,
call `prepare_mapper(definition, workflow)`, save its returned `proposed_grant`
through the normal authoring surface when the ID is missing, save its complete
`proposed_spec` when `mapper/model_missing` identifies an omitted model, and
recheck after each save until
no findings remain. Resolve unknown/unpriced model choices from the returned
catalog before capture or consent; default an unspecified primary to the
`opus` family and preserve a priced explicit choice.
Before every capture, run the structural check in [preflight before capture](pre-capture-audit.md#preflight-before-capture) and fix its findings.
Say "The setup checks passed; scoring has not run." This is not live validation
or admission. If the capability is absent, this setup step needs a Desktop
update; never hand the user commands, hashes, source inspection or JSON edits.
Keep technical status blocks internal and use the canonical
[user-facing language](../../nxd-run-job-loop/reference/user-facing-language.md).

`prepare_mapper` also returns `answer_format_check`. Its `provider_check` is
always `not_checked`: preparation never sends the answer format, so it cannot
say the provider will accept it. What else it carries depends on the installed
Desktop:

- **It reports `calls_per_row`.** The mapper splits a wide answer itself into
  groups of at most `max_fields_per_call` fields (default 9), one request per
  group per row. Do not split target fields into scoring steps by hand. A
  `note` with code `mapper/answer_format_split` gives the group sizes
  (`answer_groups`). Size `max_calls` from `calls_per_row`
  ([call ceiling](#authoring-the-spec-and-the-grant)), and before approval tell
  the user in plain words that the run sends that many requests per row, which
  multiplies its cost. An `advisory` (`mapper/answer_format_may_be_too_large`)
  here means one group is still over 9 fields because the spec set
  `max_fields_per_call` above 9: lower it or remove it, and prepare again.
- **It does not report `calls_per_row`.** An older Desktop: one request
  carries every field. When the answer is wider than the size seen accepted,
  the `advisory` carries a suggested split. It does not block preparation, but
  act on it before requesting approval: split the target fields into the
  suggested scoring steps (see [answer size](#authoring-the-spec-and-the-grant))
  and prepare again.

Approving a setup the provider then refuses spends an approval for nothing.

## What the Desktop flow supports today

State the limits accurately; never invent a tighter one such as "one AI
instruction per saved view" (a workflow can save more than one view; the
limit is per build).

- The current Desktop flow carries one mapper spec with one instruction per
  workflow. That one instruction can make many model calls, for example one per
  row.
- A second, distinct verification task in the same workflow is not supported
  yet. Say so plainly, for example: "This version runs one AI step per build.
  It applies that step to every row, but checking its output with a second AI
  step is not available yet." Do not offer corroboration as a substitute on
  Desktop: it reruns the same instruction on a second model and only for media
  rows, which supervised Desktop runs refuse, so it never runs there. A
  separate product would need its own plan and its own spending approval.
- The agent proposes the checks on the AI output itself (for example allowed
  values, required fields, quote presence) in the plan it shows for approval.
  Do not ask the user how to check the summaries.
- A quote that appears in the source is **not** full verification. Describe it
  as "the quoted passage exists in the source". It does not show the passage
  supports the conclusion drawn from it.

## Importing it in a closure

The harness ships inside the `nxd` package the runtime already installs. There
is nothing to copy:

```python
from nxd.experimental.field_mapper import MapperInput, MapperSpec, map_inputs
```

That dotted path is also what the consent gate triggers on, matched on dot
boundaries — so the `import nxd` and `from nxd.spec import ...` that every
closure carries are not collateral, and neither is a sibling such as
`nxd.experimental.semantic`.

Drift is re-consented rather than silent: `harness_version` is an input to
`mapper_spec_id`, so a release that changes the harness moves every spec id,
existing grants stop binding, and the gate demands fresh ones. Because the
harness now travels with the package rather than with each closure, that
re-consent lands on an `nxd` upgrade and applies to every mapper closure at
once, rather than only when a closure re-copied the directory. That is a wider
blast radius than vendoring had, and it is the intended trade: the alternative
is a closure quietly mapping under a grant issued against a different harness.

## Authoring the spec and the grant

Write the mapper spec as JSON at `contracts/mapper_spec.json` and load it with
`MapperSpec.load`; `prepare_mapper` reads only that name. A spec inlined
as a Python literal has no stable id, so nothing can be consented to.

On Desktop, the agent owns authoring the spec and capped scope proposal;
`prepare_mapper` supplies the canonical binding and static findings as described
in [mapper-preflight.md](mapper-preflight.md#desktop-preparation). Preparation is
read-only and advisory (`native_approval_required:true`, `run_admitted:false`);
it never records consent.

**Answer size per scoring step.** The provider turns each answer format into a
grammar and refuses one that is too large. About 9 fields per answer is the
observed limit (for example 8 scored fields plus a rationale): with this kind
of request 9 fields were accepted and 14 and 17 were refused, and the provider
documents no number. No free check can confirm the size; only the provider
can, at the start of an approved run.

- **When `prepare_mapper` reports `calls_per_row`,** the mapper groups the
  fields for you, in declared order, into the fewest balanced groups of at
  most `max_fields_per_call` (default 9): 17 fields become 9 + 8. Leave
  `max_fields_per_call` unset unless the provider refused a group; then set it
  lower, for example 6. Each group's answer sees only its own fields, so
  declare a whole-row rationale last, next to the scores it explains. Drop
  fields the agreed questions do not need: every extra group is another
  request per row.
- **Otherwise,** keep each scoring step's answer to about 9 fields or fewer.
  When a setup needs more target fields, split them into several scoring steps
  of about 9 fields or fewer each, or drop fields the agreed questions do not
  need, rather than asking for one wide answer.

**Size the call ceiling in requests, not rows.** `max_calls` counts provider
requests. Each row sends `calls_per_row` requests (1 when `prepare_mapper` does
not report it), and each validation retry is one more request for its group.
An approved run also first sends one format-check request per answer group per
model: the primary, and the corroborating model only when a media-direct row
will reach it. The Desktop flow refuses media inputs, so there it is one check
per group. The checks use placeholder text and no source data, are billed, and
count against the same call, token and USD ceilings as the rows. Set
`max_calls` to rows × `calls_per_row`, plus one check per group, plus room for
retries. When the ceiling has no spare room for every group's check, none is
sent and the first row does the checking, so a refusal then comes after that
row's source text was already sent.

**Developer/CI reference only — not the Desktop conversation path.**
For the standalone harness, compute the id the grant must carry:

```bash
python -m nxd.experimental.field_mapper spec-id contracts/mapper_spec.json
```

Paste that value into the grant's `mapper_spec_id` and write the grant to
`contracts/`. A hand-computed hash, or the `"<derived>"` placeholder the
`samples/` fixtures use, binds nothing — the gate rejects `<derived>` by name.

**The id is taken over the BOUND spec, and binding is not what `load` does.**
`spec-id` loads the spec, compiles the wire schema into it, and stamps
`harness_version` before reading `mapper_spec_id` — the same sequence
`map_inputs` re-derives at its gate. `MapperSpec.load(path).mapper_spec_id` on a
spec that does not declare `harness_version` is a **different** 32-hex string.
So a grant carrying the id `spec-id` printed is correct, and any check that
compares it against an unbound spec refuses it with `spec_mismatch` — a consent
failure invented by the checker, on a grant the user authored correctly. Use
`grant-check`, which binds the way a run does, and do not hand-roll the
comparison. Declaring `harness_version` in the spec file does not fix it either:
that changes the canonical bytes, and so moves the id again.

`python -m nxd.experimental.field_mapper grant-check <spec> <grant>` applies the same statically
decidable checks the gate subprocesses (hash, primary and corroboration model,
expiry) and prints JSON. It has no `<derived>`-specific rule: it fails that value
on the hash like any other non-matching id, reporting `spec_mismatch`. The gate
calls the placeholder out by name as `grant.invalid` before it ever subprocesses,
because a value that binds to whatever it is handed is worth distinguishing from
a grant for the wrong spec. **Both refuse it** — only the classification differs.

**Standalone consent is the user's act.** You cannot author an authorized standalone grant on the user's behalf,
extend an expiry, or decide a drifted rubric is still acceptable. Editing the
spec — the instruction, a threshold, the target fields, the model — moves the id
and revokes the grant on purpose.

Thresholds have **no defaults**: `max_degrade_share`, `max_error_rate` and
`max_unverified_share` must be declared, and a wrong default would be worse than
an absent one. `max_absent_share` is genuinely optional.

## Desktop supervisor approval boundary

The grant above is the **standalone field-mapper harness** contract:
`map_inputs` requires a user-authored `Grant`, and a matching `Grant.check`
runs before the harness dispatches a model call. On Desktop the grant is also
the **scope proposal** for the supervisor's formal approval. It is never
authorization by itself.

**Availability.** The supervisor can collect a mapper approval only when the
activated workflow contract carries a `mapper-confirmation-v1` requirement and
the install's policy has a formal-approval entry for it. Which contract an
install has depends on its supervisor, so read it from the requirements that
`prepare_workflow` returns rather than assuming:

- **Current Desktop installs** activate a five-requirement contract: consent,
  capture, review, `approval` (`mapper-confirmation-v1`), then validation.
  Validation depends on the approval. A closure without a field mapper
  completes that step at once as `not_present`, with no prompt, so it only
  costs one extra `start_requirement`. The policy allows provider `anthropic`
  only; models by exact id (dated aliases included) limited to the priced ids
  of the Claude haiku, sonnet, opus and fable families, or, for the primary
  model only, a bare family alias that resolves at native approval; anything
  unpriced is `workflow/mapper_model_unpriced`; at most 1000 calls, 2,000,000 tokens
  and 25 USD per approval, 100 USD lifetime per mapper spec; no recurring
  grants; a 300 s approval window, a 120 s dialog timeout and a 30 s cooldown
  after a decline. A grant beyond any of these is
  `workflow/mapper_grant_exceeds_policy`, so size it inside them.
- **Older supervisors** activate a four-requirement contract (consent,
  capture, review, validation) with no approval step. There a mapper closure
  is never prompted for and stops at trusted validation with
  `validation/mapper_closure_unsupported` (recovery `stop`). Report that code
  and stop. Do not strip the mapper to get past it, and do not describe mapper
  approval as available on that install.

Upgrading does not switch an existing data directory over while it still holds
workflow instances: setup keeps the previously activated contract, registers
the plugin anyway, and says to remove the existing workflows
(`remove_workflow`) or use an empty `--data-dir` to adopt the new one. So an
upgraded install can still report the four-requirement contract; trust the
returned requirements, not the version.

Mapper builds also run only on macOS. Mapper-mode compute needs the macOS
sandbox, and the supervisor refuses it on any other platform.

**Where the supervisor looks.** It reads the spec at
`contracts/mapper_spec.json` and the grant at exactly one of
`contracts/mapper_grant.json` or `contracts/mapper_spec_grant.json`. When both
grant files exist it refuses the closure. Phase G reports that as
`grant.ambiguous_path`, and a grant stored anywhere else as
`grant.not_at_supervisor_path`.

**The grant must be strictly typed.** The supervisor parses it before any
prompt. It rejects unknown keys and requires:

- `provider` exactly `anthropic`. `claude_cli`, `recorded` and `stub` are
  refused under supervision.
- a non-empty `model`, and a non-empty `corroboration_model` when one is
  given;
- `max_calls` and `max_tokens` as positive integers, and `max_usd` as a
  positive decimal with at most six decimal places;
- `expires_at` as an RFC3339 time in the future;
- `recurring` as a bool.

These failures never reach the user as a prompt. You repair them by editing
the grant (confirming any scope value with the user), recapturing, and
getting a fresh review:

| Code | Cause |
|---|---|
| `workflow/mapper_scope_invalid` | missing, malformed or loosely typed grant; grant has no `input_fields` (Desktop requires a non-empty list); an unknown key; both grant paths present; spec missing |
| `workflow/mapper_approval_claims_rejected` | the grant carries approval claims: `approved`, `approval`, `approved_by`, `approval_id`, `approval_hash`, `user_approved`, `decision`, `receipt` or `signature`. The evidence keys `approval_evidence`, `execution_evidence`, `mapper_evidence`, `run_evidence` and `validation_evidence` are unknown keys here and fail as `workflow/mapper_scope_invalid`; `prepare_mapper` reports all fourteen, nested ones included, as this claims finding |
| `workflow/mapper_grant_exceeds_policy` | provider, model, ceilings or `recurring` beyond the installed policy |
| `workflow/mapper_model_unpriced` | a model with no price entry, so spend cannot be bounded |

Under a contract without the mapper requirement, a proposal the supervisor
cannot read surfaces at validation as `validation/mapper_scope_invalid`
instead.

An agent must never author approval claims. They are rejected, not treated as
consent. A green Phase G proves only that the static harness check found a
binding artifact. It is not proof of human authorization on Desktop.

**The flow.** Where the contract carries the `mapper-confirmation-v1`
formal-approval requirement, it depends on capture and on the conversation
review, and validation and admission depend on it. When the returned actions
offer `start_requirement` for it:

1. The supervisor derives the mapper subject from the retained capture, not
   from anything the agent sends.
2. It checks the strict grant against policy.
3. It opens a native OS dialog. The dialog shows the workflow, provider,
   model and corroboration model, the call, token and USD ceilings for this
   approval, the lifetime USD ceiling and lifetime spend for this mapper spec
   (settled plus reserved, or "unavailable" when it cannot be verified), the
   reason for a re-approval, and the declared fields. It does not show the
   grant's expiry or `recurring` flag, so tell the user those yourself. Decline
   is the default button.
4. The response to the agent carries
   `approval` with `state:"awaiting_user"` and returned timing details.
   It contains no URL, token, capability or key. `request_expires_at` is the
   request deadline; `dialog_timeout_seconds` is the native decision time
   limit, with `dialog_started_at` / `dialog_deadline_at` when recorded;
   `grant_expires_at` is the spending permission expiry. Never guess durations.
   Legacy `expires_at` means the request deadline while pending and grant
   expiry after a decision; older records may omit separate fields.
5. After opening the prompt, call read-only `wait_workflow_approval` with
   `workflow`, `request_id` (the approval request id from the
   `start_requirement` response's `approval.request_id`, or
   `mapper.approval.request_id`; never the `advance_workflow` idempotency
   key), optional `after_revision`, and `timeout_seconds`
   (default 20, cap 25 seconds; zero checks once). Use a few calls at most,
   respecting the conversation; fall back to bounded `inspect_workflow`
   polling if unavailable. Discover limits in `get_workflow_capabilities.approval_wait`;
   serialize waits, since a concurrent second wait for the same workflow may
   briefly return `workflow/state_unavailable` (the supervisor polls every 300 ms).
   Check `mapper.approval.recorded_decision` for the
   matching request: when `outcome:"accepted"`, say "Your spending approval is
   recorded" and continue authorized returned actions with the latest revision
   without requiring another chat message. Acceptance does not start a run or
   bypass expiry, budgets, validation or admission.
   For terminal `dialog_timed_out`, legacy `timed_out`, `request_expired`,
   `declined` or `cancelled`, ask whether to reopen the same capped request;
   reopen only on their yes and if eligible. `surface_unavailable` (the native
   dialog could not be shown) and `integrity_unavailable` (the decision could
   not be verified) record no consent: report the blocker and inspect state.
   For `pending`, stop after the bounded wait and report it is still pending;
   for `superseded` or `interrupted`, inspect current state and never reopen
   automatically. Interrupted responses omit top-level `approval` and clear
   `next_actions`; never reuse stale prompt actions. A `terminal` response with
   `wait_reason:"request_deadline_passed_not_yet_recorded"` observes a passed
   deadline without a recorded choice; inspect current state before reporting
   an outcome. A chat message "I clicked approve" is not acceptance;
   check the runtime receipt.

A decline, cancel, timeout, interrupted prompt or unavailable dialog returns
the requirement to pending with a `workflow/approval_*` code. The user can be
asked again, but only when they want to be. The recovery table for every code
is in the job loop's `reference/workflow-v2.md` § Mapper approval. The OS
dialog confirms local presence. It is not cryptographic proof of the user's
identity, and it is the only approval surface: no chat reply, MCP call, file
or form counts as approval.

**Fields sent to the model are checked; the rest is declared.** In Desktop
the grant must list a non-empty `input_fields`, or the run fails closed with
`workflow/mapper_scope_invalid`. Every `MapperInput` with landed text must set
`text_fields`, the source fields its `landed_text` was built from, and
`landed_text` must equal their canonical rendering. The mapper runtime refuses
a request whose `text_fields` are not all in `input_fields`, or whose text is
not their rendering (`landed_text_undeclared`), before any model call; the
`make_call` callable repeats the check against the supervisor-verified grant.
The dialog lists `input_fields` as "Fields this workflow sends to the AI model
(the scoring step refuses requests built from other fields)". `document_classes`
is checked against `MapperInput.document_class`. `pii_category` and `purpose`
stay declared only: the dialog shows them under "Other details this setup
states (for your review; Nexty does not enforce these entries)".

The check has limits. It runs inside the closure, so it does not stop a
closure that bypasses `make_call`, and it cannot tell whether a field's value
matches its name: a column named `title` that holds a full description passes.
Keep the field names and the declared list honest, because they are what the
user reviews.

**What is enforced.** The supervisor holds the provider key in the macOS
Keychain, and every mapper call goes through its broker. The closure gets a
per-run capability for a local socket, never a key. Before any network call
the broker checks that:

- the provider and model are the granted ones, the model is priced, and the
  approval has not expired;
- the request shape is allowed: no beta headers, no streaming, custom tools
  only (no server tools), no images or documents, and extended thinking only
  as `adaptive` or `disabled`;
- the reservation fits the approval's call, token and USD ceilings.

Admission also enforces the lifetime USD ceiling for the mapper spec. A
refusal reaches the harness as `GrantError`, and an exhausted ceiling as
`BudgetExceededError`. Never put a key, token or base URL in the closure or
the environment as a workaround.

An optional experimental cumulative cap can also cover every mapper ledger in
one Nexty data folder, across specifications and workflows. First read
`get_workflow_capabilities.mapper_budget`; only when it reports
`availability: available` use its returned inspection and proposal tool names.
When available, inspect before paid scoring; the folder-wide cap is active only
when its returned state is established, complete and open. The proposal records
limits but does not activate them and must use only limits the user explicitly
stated. If the user requested a cap but the capability is missing or
unavailable, stop paid scoring while unpaid work may continue. Unknown or
integrity-failed totals pause paid scoring; exhausted totals end paid scoring in
that folder.
The cumulative cap excludes other data folders, scoring outside Nexty, and
sessions that were already running when it was enabled. Folder replacement or
restore from an older copy is outside its guarantee. Do not ask the user for
historical usage or describe no earlier recorded scoring as zero for the whole
test. The `nxd-run-job-loop` skill's `reference/workflow-v2.md` defines
inspection, consent, states and refusals.

**Widening a per-run grant needs a fresh approval.** A new model, higher
per-approval or mapper-spec ceilings, a later expiry or `recurring: true` changes
the grant. That means recapture, a fresh review and a new OS approval. The
cumulative folder cap cannot be raised, reset or disabled in this version. With
`recurring: false`, an approval is spent by
the first publication it admits. After that, admission reports it as
`workflow/approval_expired`. Recapturing a changed closure moves the subject
and invalidates the approval on purpose, unless a repair keeps the spec and
grant byte-identical and the supervisor carries the accepted approval forward;
see "Keep an accepted spending approval across a repair" in the
`nxd-run-job-loop` skill's `reference/workflow-v2.md`.

**Binding-aware runtime contract.** The binding and strict scratch conditions
below describe a Desktop release that checks the output-model binding; they
are runtime-contract reference, not a support signal. The current
`get_workflow_capabilities` response has no signal for binding-aware scratch
coverage. With this interface, always report the advisory as wiring-only and
never as verified output coverage. Do not infer binding-aware support from the
advisory code, the binding file or a version guess. Never ask the user for a
version to select the reporting.

**Bind the mapper's output model.** Generate
`contracts/mapper_output_binding.json` with exactly these keys:

```json
{"schema":"nxd-mapper-output-binding-v1","output_port":"duckdb","output_model":"latest_open_postings"}
```

Use the real output port and model from the authored closure; the names above
are an example. Bind exactly one existing port/model pair whose schema contains
all mapper target fields. The model must be public and carry its built-in model
promise. Include this file in capture and independent review.
Keep the existing mapper spec format unchanged. Generate the binding as part
of authoring; never ask the user to supply internal identifiers or this JSON.
A field with the same name on a different model does not establish attribution.
Missing, invalid or ambiguous binding cannot authorize an advisory under the
binding-aware contract.

The inspected earlier capture source retains an extra regular `contracts/` file
under its normal safety and size limits; retaining the binding does not make
that runtime binding-aware. Capture compatibility with other older installed
binaries remains unverified. If an installed capture rejects the file, stop and
report the unsupported setup; do not remove it to obtain an advisory.

For a custom guarantee on a mapper target field, return singular identifiers
in the verifier's failure context:

```python
return VerifyResult(VerifyResultEnum.FAILED, {
    "contract": "view-summaries-checked",
    "model": "latest_open_postings",
    "column": "summary",
})
```

Use the actual custom contract, model and mapper field. The context becomes
the runtime result's `extra_info_json`; the kernel supplies the positive outer
`failed_count` separately. Do not put that count inside the context, infer
attribution from prose, or use a plural `columns` tag as a substitute.

**Validation does not spend.** Trusted validation runs the scratch build
against a stand-in broker: the mapper makes no provider call and charges
nothing. In the matching binding-aware runtime, source fetching remains real;
source-fetch behavior on earlier releases is unverified here. An advisory alone
never proves a source refetch. For the supported local one-shot
self-managed batch, Nexty may return `validation/mapper_output_unverified`
only for a trusted failed-promise refusal after verifying complete same-run
output checks, the scratch artifact and readback, and failures confined to the
captured binding's mapper fields. Ordinary successful scratch does not require
advisory coverage or this output-model binding. A refusal with missing or
incomplete evidence, unrelated or mixed failures, invalid typed outputs, or
missing verifier context remains blocking. Transform, sandbox, timeout,
cancellation, host lifecycle and cleanup faults also block validation.
Every other contract failure still fails. Keep every guarantee and the required
free test; do not weaken them to obtain an advisory.

Scratch evidence proves wiring, not the quality of mapped values.
Explain an advisory using the user's requested field names: “The free test
checked the wiring but could not verify the AI-filled values. The real run
will check those values before saving the view.” Keep technical identities in
review artifacts unless the user requests diagnostics. The real run still
enforces every guarantee; every real-run failure still blocks publication,
including a failure in any required AI-filled value.

## Evidence modes — what each one proves

| Mode / path | Evidence kind | What it actually proves |
|---|---|---|
| `structured` over text already landed in the closure | `verified` | The quoted span is a substring of landed text (the quoted passage exists in the source; this is not full verification of the conclusion); re-checkable offline from `text_hash` + offsets. |
| `evidence_mode: "citations"` | `api_cited` | The API extracted `cited_text` server-side from the document, with page locations — a second party, not the answering model. Verified-equivalent for gating, but not offline-re-runnable. |
| Media sent direct, no citations | `evidence_unverified` | Nothing. It counts against `max_unverified_share` by design. |

Scanned PDFs are not citable, and image citations do not exist. Page counting is
stdlib-only and returns `None` for encrypted or compressed PDFs, which degrades
the **cost estimate**, not the mapping.

## Reviews: `data/mapper_reviews/`

Human reviews live **inside the closure**, at
`data/mapper_reviews/batch-NNN.csv`. They survive `write_disposition="replace"`
because dlt replace-loads the full glob every run. `mapper_reviews` is an
optional-empty base model: add it to `PHYSICAL_MODELS` and
`OPTIONAL_EMPTY_MODELS`, and register it with `.model(mapper_reviews)` rather
than `.promise(mapper_reviews)`. A fresh closure may have no
`data/mapper_reviews/` directory and no physical table; that is valid until a
human review is written.

- **To review**: add `data/mapper_reviews/batch-<next>.csv`.
- **To revoke or correct**: add a **new row**, never edit an existing one. Rows
  are immutable and append-only, like the ledger.

The tradeoff is stated rather than hidden: durable human state sits inside
`data/` beside agent-managed exports, protected by the rule that supplied source
exports are preserved exactly by default. The personal-data projection
exception applies to supplied source columns only; it never authorizes edits to
`data/mapper_reviews/`. A wiped `data/mapper_reviews/` is *loss* — the reviews
are gone and the cells re-infer — never *corruption*,
because `bound_value_hash`, `bound_input_snapshot_id` and `bound_mapper_spec_id`
make it impossible for a review to silently attach to the wrong value.

## Review outcomes: `mapper_review_outcomes`

The mapper runtime now emits one deterministic review outcome for every durable
review considered during resolution. The published outcome projection records
`applied`, `rejected`, or `ignored`, the stable reason, the review ID,
reviewer/timestamp, and both the review-side and proposal-side binding evidence
(value hashes, input snapshot IDs, and mapper-spec IDs where present).

This is proof of how the resolver accounted for a recorded review in the
published projection. It is not proof that the named reviewer authenticated
their identity or that the reviewer performed the original action. The
supervisor's separate pre-call approval gate remains the authority for external
LLM use.

The resolver asserts that every durable review has exactly one outcome, including
stale, invalid, missing-target, and superseded reviews. Generated transforms
must land this projection with the proposals and evidence from the same
resolution bundle; do not infer it later by joining the wide table.

## Two dlt runs, in this order

A mapping transform is **two** `pipeline.run` calls, not one:

1. **run 1** lands the base rows.
2. **read** them back out of the loaded destination.
3. **map** those rows with `map_inputs`.
4. **gate** on the result — block the build if the spec's thresholds are
   exceeded, *before* landing anything derived.
5. **run 2** lands the proposals, evidence, review outcomes, and ledger.

The order is load-bearing. Mapping before run 1 maps rows that may never land;
landing derived rows before the gate publishes judgements the thresholds would
have rejected.

### Step 3, exactly: build `MapperInput`s, then call

Copy this shape. It is the whole of the API a closure touches, and getting it
wrong is the single most common mapper build failure — a real Desktop build died
at `MapperInput.__init__() got an unexpected keyword argument 'document_id'`
before any model was contacted.

```python
from pathlib import Path

from nxd.experimental.field_mapper import (
    Grant,
    MapperInput,
    MapperSpec,
    make_call,
    map_inputs,
)

spec = MapperSpec.load("contracts/mapper_spec.json")
grant = Grant.load("contracts/mapper_grant.json")

mapper_run_dir = Path(run_dir) / "run" / "mapper"

inputs = [
    MapperInput(
        input_id=str(document_id),              # this record's handle
        identity={"document_id": document_id},  # the spec's identity_fields
        fields={"document_id": document_id, "page_text": text},  # not sent as-is
        landed_text=text,                       # the substring haystack
        text_fields=("page_text",),             # what landed_text was built from
        document_class="invoice",
    )
    for document_id, text in rows
]

call = make_call(
    spec=spec,
    grant=grant,
    # Explicit secret mapping. `None` plus `allow_env=True` uses the
    # allowlisted ANTHROPIC_API_KEY instead. Without `allow_env=True` there
    # is NO environment fallback and the first dispatch fails with
    # `credential_missing`, however visible the key is.
    secrets=None,
    allow_env=True,
)

result = map_inputs(
    inputs,
    spec=spec,
    grant=grant,
    run_dir=str(mapper_run_dir),
    call=call,          # the injected model callable
)
```

When the text sent combines more than one source column, never join them by
hand. Build the input from the columns so the declared fields and the text
cannot drift; the grant's `input_fields` must list every one of them:

```python
MapperInput.from_text_fields(
    input_id=str(job_id),
    identity={"job_id": job_id},
    fields={"job_id": job_id, "title": title, "description": description},
    text_fields=("title", "description"),   # both are sent; the grant lists both
)
```

A `landed_text` assembled by hand from two columns fails at `map_inputs` with
`landed_text_undeclared`, even when it looks identical: the canonical rendering
of several fields is `name:` + newline + value blocks joined by a blank line.
Field values named in `text_fields` must be plain `str`.

A source column that can be NULL has no text to send for that row. Name only
the fields that hold a string, for example
`text_fields=tuple(n for n in ("title", "description") if isinstance(row[n], str))`;
the grant still lists every column the run may send. Do not coerce with
`str(x or "")`: that sends a rendered `description:` block with nothing in it.
A row with no text field left has nothing to score. Leave it out of `inputs`
rather than sending an empty input; its mapped columns stay empty, so report
how many rows that affected.

`make_call` is the only supported provider seam for generated code. It creates
the provider client and resolves credentials lazily, after `map_inputs` has
checked the grant. An explicitly supplied `secrets["anthropic_api_key"]` wins;
when it is absent, the adapter may use the allowlisted `ANTHROPIC_API_KEY`
environment fallback, which is OPT-IN: `allow_env` defaults to False, so a closure that omits it gets no ambient
credentials. Missing credentials are a blocking, sanitized
`CredentialMissingError`; the key never appears in diagnostics or artifacts.
Do not import `anthropic`, use tool-use output, construct a private transport
client, or return a raw SDK response. The callable returns a parsed object with
one nested block per target field:

```json
{
  "category": {
    "value": "Software & Cloud",
    "evidence": [
      {"quote": "verbatim source span", "source_field_name": "description"}
    ]
  }
}
```

The adapter is synchronous: generated transforms must not return a coroutine or
an SDK response object. Its callback receives keyword-only `item`, `spec`,
`wire_schema`, and `violations`; `map_inputs` unwraps the transport result and
validates the returned mapping. A `SystemicError` (missing credential,
dependency, grant, model, schema, budget, or cancellation) blocks the run; a
`CellError` is recorded against the row and handled by the mapper's bounded
retry/gate policy. Error messages are sanitized and machine outcomes use the
stable `error_code` values documented in `mapper/CONTRACT.md`.

Provider refusals that stop a run carry a `reason_code`; recover from these
without exposing the code or internals to the user:

| Code | Cause | Recovery |
|---|---|---|
| `mapper/schema_too_large` | the provider refused the answer format as too large (`error_code` stays `schema_reject`); the run stopped before any row was scored. On Desktop the run diagnostic's `mapper.code` is `mapper_schema_too_large` | when `prepare_mapper` reports `calls_per_row`, set a lower `max_fields_per_call` (for example 6) or drop fields; otherwise split the target fields into several scoring steps of about 9 fields or fewer each ([answer size](#authoring-the-spec-and-the-grant)). Then prepare again, recapture, review and ask for a fresh approval. Tell the user: "The AI provider said this setup asks for too many fields in one answer, so nothing was scored. I'll ask for fewer fields at a time and ask for your approval again." |

`target_row_key` is a content-derived hash, not the source row ID. Keep the
`MapResult` proposals/evidence together and resolve from that bundle; never
reconstruct a join from a model response's guessed row key.

**`MapperInput` takes no arbitrary keyword per source column.** There is no
`MapperInput(document_id=...)`, and there is no `deps` argument to `map_inputs` —
an older revision of `mapper/CONTRACT.md` described one, and it never existed.

The five slots are not interchangeable:

| Slot | Holds | Why it cannot be merged |
|---|---|---|
| `input_id` | this record's handle | keys the ledger and the recorded responses |
| `identity` | the spec's `identity_fields` | what the grant and `input_snapshot_id` bind to — empty means nothing binds |
| `fields` | context for the closure and records; never sent to the model as-is | un-bound: changing it does not revoke a review |
| `landed_text` | text already landed in the closure | the only surface `verify_quote` checks a quote against |
| `text_fields` | the `fields` keys `landed_text` was rendered from, in order | what the grant's `input_fields` is checked against; required in Desktop whenever `landed_text` is set |

Putting the document identity **only** in `fields` leaves `identity` empty and
silently unbinds every review. Never introspect these signatures to decide how to
call them: a closure that adapts to whatever is installed converts a loud
`TypeError` into a silent difference between two runtimes.

`mapper/examples/e2e/` in this skill's repo checkout holds runnable proofs —
`run_e2e.py` for the data chain and `transform_main.py` for the platform
entrypoint. `run_e2e.py` uses the public `make_call` seam on `--live` and a
recorded caller on replay; `transform_main.py` exercises the same caller through
the platform entrypoint. Treat them as reference implementations: do not import
the provider SDK or private transport modules into a generated transform. The
proofs need an nxd monorepo checkout.

## Spend: the self-check really pays

Phase B of the self-check **executes** the transform, and the harness resolves
its API key from secrets with an environment fallback. With `ANTHROPIC_API_KEY`
set, self-checking a mapper closure makes **live model calls**. Phase G runs
before Phase B precisely so that spend can only happen under a binding grant;
the ceilings on the spend itself (`max_calls`, `max_tokens`, `max_usd`) are
enforced inside the harness at call time, not by the gate.

## Disclosing what is sent to the model

In the Desktop flow (supervised mode, which refuses media and citations
inputs), only the mapper input's text (`landed_text`) and the instruction reach
the model. The fields sent are each input's `text_fields`; their union across
inputs is the disclosure list, and the grant's `input_fields` must cover it or
the run is refused before any model call. Outside Desktop, every `media`
attachment (document or image) is also sent and must be named in the
disclosure. Before spending approval, check that every `MapperInput` sets
`text_fields` (or is built with `MapperInput.from_text_fields`) and that the
grant's `input_fields` lists each of them. Then describe those fields to the
user in plain words, for example "the job title, the job description and the
location". Do not omit long free-text fields such as descriptions. Fields that
appear only in `fields` are not sent; do not list them as sent.

If the `text_fields` differ from the plan or the grant's `input_fields`, fix
the closure or update the plan and grant (fresh approval) before asking for
spending. Never just mention the difference in chat. The runtime checks names,
not meanings, so a field whose name misdescribes its content is still yours to
catch; see "Keep the field names and the declared list honest" above.

## What the consent gate cannot see

Green Phase G means a binding consent artifact exists. It is not proof that no
unconsented mapping happened, and the full list of what it misses — the
self-attested hash, the decoy-spec limit, `importlib` and inlined-source
routes, runtime coverage versus static binding — is in
[reference/self-check.md](self-check.md) § "What Phase G cannot see". Read it
before treating a green gate as a safety guarantee.

## Verifying the installed harness

The fixtures ship inside the package and are the acceptance suite, not a demo,
so this answers "is this harness intact?" from any install:

```bash
python -m nxd.experimental.field_mapper verify   # expect 13/13
python -m nxd.experimental.field_mapper pins     # spec-id stability
```

Record schemas, `value_status` semantics, the blocking rules and the resolution
order are normative in `mapper/CONTRACT.md`.
