# When a step fails — classify, heal, ask, or retry

## Contents

- [What this file is](#what-this-file-is)
- [The ladder, as the loop sees it](#the-ladder-as-the-loop-sees-it)
- [Three caveats that decide most misclassifications](#three-caveats-that-decide-most-misclassifications)
- [Classification fails closed](#classification-fails-closed)
- [Typed exits, and caps you count instead of estimate](#typed-exits-and-caps-you-count-instead-of-estimate)
- [A blocker is an open question found late](#a-blocker-is-an-open-question-found-late)
- [After a failed supervisor operation: inspect once, then classify](#after-a-failed-supervisor-operation-inspect-once-then-classify)
- [Keychain credential and saved-state access outcomes](#keychain-credential-and-saved-state-access-outcomes)
- [User-facing narration](#user-facing-narration)
- [The commands](#the-commands)

## What this file is

The loop's **operating procedure** for a failing step: how to tell whose fault a
failure is, what you are allowed to do about it, and when you must stop and ask.
The record it operates over — the diagnostic shape, the build record's fields,
the materialization predicate — is defined in
[build-record.md](build-record.md). **Link, don't re-derive**: if this file and
that one disagree about a field, that one is right.

User-facing workflow and status wording is defined separately in
[user-facing-language.md](user-facing-language.md). This file owns failure
classification, typed exits, retry boundaries, and the evidence needed to make
those decisions.

One rule sits above everything here, and it is the compiler framing made
operational: **a heal may change generated code; it may never change the plan.**
A compiler does not edit your source to make the build pass. If green is only
reachable by narrowing the population to dodge a bad join, dropping a model whose
grain won't resolve, or relaxing a threshold, that is a **spec edit requiring
re-approval**, not a heal — stop and escalate it.

## The ladder, as the loop sees it

Classify a failure by **which step it died in**, never by reading the exception
text. The steps genuinely differ in what they can reach, and that difference is
the whole classifier.

| Loop step | What ran | Stage | A failure here is |
|---|---|---|---|
| 1b validate | `validate_dp_spec.py` — the spec alone | `s0_spec` | a gap you fill, or a ruling only the user can make |
| 3 self-check A | structure vs the pinned DSL; **nothing executed** | `s1_structure` | malformed generated code — yours |
| 3 self-check B | the transform **executes for real**, scratch DuckDB, no kernel, no network | `s2_transform` | **unambiguously the code** |
| 3 self-check C/D | closure completeness; policy boundary | `s3_closure` | structural or governance — yours |
| 4 admission | the workflow-v2 supervisor validates and admits the captured closure (`start_requirement` → `start_run`) | `s4_pin` | **code the offline checks could not see** — yours |
| 4 serve | provision, kernel, dependencies, endpoint | `s5_serve` | usually the environment |
| 4 run | the transform on the supervisor | `s6_run` | mixed — an assert that fired is code, a refused connection is not |
| 4a publish | publish, verify, static artifact | `s7_publish` | mixed |
| 5 describe/query | the served catalog answers | `s8_answer` | a **green build with a wrong answer** |

**Steps 1b and 3 are offline and deterministic.** They touch no kernel, no
network and no supervisor, so a failure there is **never** environmental. There
is nothing to retry: fix the code.

## Three caveats that decide most misclassifications

1. **An admission failure masquerades as an environment failure.** The
   structural check cannot execute the supervisor — a closure can pass it in
   full and still fail when the supervisor validates or admits it. In workflow
   v2, a `start_requirement` or `start_run` error is **not** presumptive
   evidence of a bad machine. Treat it as yours by default and use the
   supervisor's returned operation and requirement evidence to classify it.
2. **The transform dry-run covers `transform/main.py` only.** A green dry-run
   says nothing about `spec.py` or `models.py`, and its own `unverified:` list is
   its declared blind spot for dynamic constructs. Carry those lines into the
   record rather than letting them scroll past.
3. **A wrong answer produces a fully green build.** Nothing upstream fails. The
   distribution read-back is what makes it visible — a uniform classification
   column is the tell — and it stays **non-gating**. What changed is that it is
   recorded in `build-record.readback` and surfaced next to concessions, so a
   uniform column is never merely mentioned once and lost.

## Classification fails closed

> When the evidence does not settle whether a failure is environmental, default
> to **not** environmental.

The discriminating test is one question: **would re-running this closure
unchanged, on a healthy machine, pass?** Answering *yes* requires
supervisor-reported evidence — a verbatim error body or traceback the tool
returned to you, not your own reading of it. Absent that, the answer is *no*, the
failure is yours, and you heal it.

The asymmetry is deliberate. Misclassifying a real bug as "the environment" is
what ships a broken data product flagged green; the cost of the opposite mistake
is one wasted heal attempt.

**One named instance, because it is common and reads as yours.** A
`runtime_error` whose supervisor-reported body says `database is locked` is the
kernel host's own status store contending, typically after consecutive failed
runs in the same session. It is `retry_environmental`: re-run the closure
**unchanged** once before changing anything, because editing in response to it
burns a heal attempt on code that was never at fault. A live run hit this after
three failed builds and the unchanged retry passed. It qualifies under the test
above — the lock message is a verbatim supervisor body, not an inference.

## Typed exits, and caps you count instead of estimate

Every attempt is appended to `build-record.json` `attempts[]` **before** the
re-run, and ends in exactly one typed exit:

| exit | means |
|---|---|
| `healed` | the re-run of the failing step passed, nothing was given up |
| `healed_with_concessions` | it passed, but you did something the skills discourage — `concessions[]` grew and the user must hear it |
| `caps_exhausted` | the bound was reached; report the user-visible impact and next action |
| `blocked` | a ruling only the user can make; the spec un-approves |
| `retry_environmental` | supervisor-reported evidence of an environment fault; retry |

The caps are the loop's existing bounds, now **counted from `attempts[]` rather
than estimated**: **remap ≤ ~2 per question**, **regenerate ≤ ~3 total**, and
**environmental retries ≤ ~3**. A retry consumes none of the first two — that is
why it needs a bound of its own, and why an environment fault that never clears
eventually becomes something the user hears about instead of a silent loop.

A cap is a bound on retrying, not a verdict on the closure. Exhausting the retry
bound does not make the closure known-bad; it means you stop retrying and say so.

## A blocker is an open question found late

There is **no second mechanism** for "I need something from you at build time."
A build-time blocker is an `## Open Questions` entry discovered late, and it goes
where every other one goes:

1. Record the attempt with `exit: "blocked"` **first**.
2. Record the blocker.
3. **Then** write the question back into the live `dp-blueprint.md`'s
   `## Open Questions`, with a `blocks:` list naming what it stops.
4. That **un-approves** the spec — the plan the closure was built from no longer
   matches the live plan — so re-enter Step 1b: ask, get the ruling, re-approve,
   regenerate.

The ordering matters and is not stylistic: the attempt itself did not edit the
plan, so it records the same spec hash before and after. Editing the plan is a
separate, separately recorded event. Get that backwards and the record accuses
you of cheating on an entirely correct blocked exit.

The consequence worth internalizing: the elicitation contract is a **loop**, not
a pre-build gate. The same "I need something from you" queue serves a gap found
while authoring the spec and a gap found while running it.

## After a failed supervisor operation: inspect once, then classify

A failed workflow-v2 supervisor operation returns an operation or requirement
status plus an error payload. Treat the payload as the **outermost** frame — it
may still be a generic timeout or "transform execution failed" wording that
says nothing about which stage died. Do not classify from it, and do not retry
on it.

1. **For workflow v2, call `mcp__nxd-desktop__inspect_workflow` once**, passing
   the failed workflow and the current operation/requirement identity returned
   by the supervisor. It returns the bounded, path-redacted diagnostic and
   durable status for that operation. It takes no ownership lock and starts no
   runtime, so it is safe while another session builds. **Once**, not in a loop:
   it reads recorded workflow evidence, so a second identical call cannot
   return anything new.
2. **If the admitted operation returns a `run_id`, call
   `mcp__nxd-desktop__inspect_run` once**, passing that failed `run_id`. It
   returns the run's durable status plus the bounded, path-redacted child
   failure diagnostic — the actual exception from inside the transform. Omit
   `run_id` only to list recent failed-run summaries when the supervisor directs
   that recovery path.
3. **Classify by the stage it died in**, using the ladder above — never by
   matching the exception text. If no operation or run identity is available,
   say that; do not substitute the outer error string for the diagnostic you
   could not read.
4. **Preserve the artifact.** Keep the failing closure and its diagnostic. They
   are the evidence for the report, and re-running destroys the state that
   explains the failure.

### Do not retry a deterministic failure

When a custom contract fails, read `failed_contracts[].context` first: it
carries the verifier's own identifiers (field names, unexpected values, counts).
If `context` is present but lacks a key the verifier returns, that value's shape
was dropped by the agent-facing filter (nested dicts and strings with spaces or
`=` do not survive), so reshape it rather than guessing: rewrite the verifier to
report identifier-valued keys, with prose under `detail`. A context that is
absent altogether means nothing survived: nxd omits it when every key the
verifier returns was dropped, so first check each returned key and value against
the rules in [custom-contracts.md](../../nxd-generate-data-product/reference/custom-contracts.md).
Look at the other causes (an older nxd that passes no context, a verifier that
raised before returning) only when the verifier is already correctly shaped, and
do not rewrite it for them.

A retry is only ever justified when the failure could plausibly resolve on its
own. **These never do**, and retrying them unchanged burns time and spend while
producing the identical error:

- A `TypeError`, `AttributeError`, or unexpected-keyword-argument error from
  generated code — the call shape is wrong and will be wrong next run.
- An import error for a missing dependency — the runtime is under-provisioned
  until someone provisions it.
- A grant or spec-hash mismatch — the binding is wrong, not flaky.
- `validation/existing_workflow_unsupported` — this workflow ID already owns a
  published product, and the current operation scope cannot revise it. Treat this
  as terminal and non-retryable: preserve the published product and release; do
  not reset, recapture, validate, retry, remove, or replace that workflow. Report
  the limitation and ask whether the user explicitly authorizes a separate,
  versioned data product under a new workflow ID. Only after authorization, begin
  fresh workflow-v2 admission. Consumers must switch to the new product; this
  never updates the prior release. A request to revise is not that authorization:
  say so first, ask, and wait for the yes; never prepare the new ID in the same
  turn as the ask, and never fold it into the plan approval.
- A `transform_error` with empty `stderr_tail`, `stdout_tail` and no `failed_contracts`
  after the source changed (a renamed column, a new stage value) is source drift
  behind a raw transform raise. Compare the source against the blueprint's Inputs,
  name the drift, ask the business question, and do not retry or keep asking for
  diagnostics; for a new stage value that question is how to treat it. Then, if the field or stage
  set is already a declared Promise in the approved blueprint, the repair is making the
  transform stop raising (map a missing field to null, pass an undeclared value
  through unchanged) so the existing promise runs and names the drift. If it is not
  declared, amend the blueprint so it is a declared Promise (fresh approval, not a
  bounded repair) and regenerate so the transform lands the drifted value instead of
  raising; never add a contract to the closure alone
  ([custom-contracts.md](../../nxd-generate-data-product/reference/custom-contracts.md)).
  A renamed field the transform maps to null passes silently when the model is
  nullable and the user never declared it required; if a published measure or governed
  output depends on it, the blueprint needs a named promise that fails when it is null
  across all current rows (unless all-null is a recorded accepted state), and the
  transform lands the unmapped source key names, each with its kind of value and
  the number of current rows it appears on (never values), in the fixed
  `source_diagnostics` model from
  [custom-contracts.md](../../nxd-generate-data-product/reference/custom-contracts.md),
  so the failure names the missing and the unseen fields and you can ask about the
  new field in the same revision rather than building another version just to look.
  Never invent another diagnostics table or bespoke wording for it.
- A revised or successor workflow built after a source change re-derives its
  transform from the live source; it never carries the earlier transform forward
  unexamined. Before preparing it, re-read or re-profile the live source and diff
  its keys and types against every field the existing transform reads. A key the
  transform reads that the source no longer has must be resolved, and when
  resolving it changes what a field or measure means, that is a plan decision put
  to the user, not a silent remap.
  An explicit hand-back of a renamed-key decision ("you decide", "you tell me",
  "your call"), including after you explained the trade-off, is a delegation, not
  a non-answer: do not keep re-asking and do not default to stopping. A deferral
  ("I need to check with Finance") is not a hand-back, and it stays a deferral even
  when the same message ends with "can you decide?": the user has said someone else
  owns the answer. Decline both questions, say what stays open and what you would do
  under each answer, and do not re-prepare, edit, capture, validate, run or publish
  anything that depends on them until the user rules. A later message that hands
  the same question back ("I don't know, you tell me") ends the deferral: it is a
  delegation, so apply the rule above. Once the user has ruled on or delegated
  every decision a prepared plan depends on, an approval of that plan that says
  "after resolving the open business decision" (or names any decision the plan
  already records a ruling or recorded assumption for) is an approval: relay it,
  and say in the same reply which recorded decision you took it to mean. Ask which
  decision is meant only when the plan still leaves one open. Other drift decisions, such
  as how to treat a new category value, keep the usual rule: explain the options and
  ask again. Never add a new category or stage value to the accepted set on your own
  authority, whatever the user later rules (include it, leave it out, or publish it
  flagged as a warning). Nor does this apply to approving a review finding, where
  the same words remain a deflection; a mapping recorded under this rule is a
  Decision the review grades against, not a finding to re-ask. The rename is
  like-for-like when exactly one field the transform read is gone; exactly one new
  source key is a plausible replacement for it by name, has the same kind of source
  value as the old field (int and float are both numbers; the type the old field
  landed as, such as `decimal(38,9)`, is not the comparison), and is present on
  every current row (its row count in the transform's diagnostic
  equals `current_row_count`; see
  [custom-contracts.md](../../nxd-generate-data-product/reference/custom-contracts.md));
  other new keys that plainly mean something else, such as a person object or
  free-text field, are not candidates and do not block it; and nothing you can see
  contradicts the mapping. Where you can see the new key's values (a shell probe),
  compare them with the old field's values for records whose id and `updatedAt` did
  not change: a true rename matches on every such row, while a weighted or converted
  measure does not. Then adopt the mapping as a recorded assumption. Add a decision
  row that says it is the agent's assumption made on the user's delegation, cites
  the message, and records which of these checks it passed; say so plainly in the
  revised plan and in the final report (labelled assumption, with what would change
  if the mapping is wrong); keep or declare a promise that fails if the mapped column
  is null or not of the old field's landed type across current rows; and still go through
  the normal re-prepare and plan approval; never map silently. Before publishing,
  read the prior release's id, `updatedAt` and old-field value for every current
  record (a same-workflow revision stops serving them); after publication, compare
  the new values on the records whose id and `updatedAt` match. For either value
  comparison, with no matching records or a prior release that does not publish
  them, record it as not run, not passed. Say in the final report whether the values matched, and on how
  many unchanged records. If any differs, say plainly that the mapping is not a rename and the
  published figures for that measure are wrong, and propose a revision before
  answering queries on it. When it is not like-for-like (several candidates, a change
  in the kind of source value, a candidate missing on some current rows, or a value mismatch), say you cannot pick safely, name the
  specific evidence that would settle it, and stop.
- A contract failure that reports a count and names no field or row (for example
  `failed_count=1`) is not a question for the user and not a reason to re-run
  validation unchanged. Localise it yourself first, inside the existing verifier
  and keeping its contract id and fields unchanged: check each declared field
  separately, or add per-field null counts to its `VerifyResult.context`, and
  revalidate. Splitting one contract into several changes the approved contract
  inventory and needs fresh approval. Then report the specific field.
- Anything that died in `s0_spec`, `s1_structure`, `s2_transform` or `s3_closure`:
  those stages are offline and deterministic, so **there is nothing to retry**.

Fix the cause, or escalate it as a blocker. "Retrying unchanged would not help"
is itself a finding worth stating.

### Repair a deterministic contract-inventory rejection once

When workflow-v2 capture or preflight returns
`closure.contract_inventory_mismatch`, allow one bounded mechanical repair of
the generated closure only when the approved proposal's semantics are
unchanged. Preserve the approval, typed proposal, Terms, contract identifiers,
attachments, phases, delivery, anchors, and all approved values; repair only
the missing, extra, or unwired generated inventory and then recapture and run a
fresh review for that generation. This is not a new approval.

If the repair changes semantics, Terms, contracts, delivery, or any typed
proposal content, stop, reset the workflow, and obtain fresh user approval
before generating again. An ambiguous operator reply is not approval. Use no
unbounded repair loop: after the single bounded attempt, report the mismatch
and stop or ask for the decision the workflow requires.

### Say whether the model was contacted

For a mapper build, the user's first real question is whether it spent money and
whether anything was published. Answer it explicitly and separately, every time:

- **model dispatch: not reached / reached** — a failure constructing
  `MapperInput`, resolving credentials, or checking the grant happens **before**
  any dispatch. No calls, no spend.
- **publication: yes / no** — `map_inputs` returns proposals in memory; nothing
  is published until the second dlt run lands them, after the gate.

Never describe a passing self-check, a green consent gate, or a dispatched build
as a successful mapper run. Those are different claims, and collapsing them is
how a failed run gets reported as a working one.

### A patched closure is not evidence

If a closure was edited by hand to get past a failure — even a correct edit —
the run that failed and the code that now exists no longer match. Do **not**
re-run the patched copy and present the result as proof the original defect is
fixed.

- Use a **fresh closure generated from the corrected source** for the next
  acceptance run.
- Label the patched one **failed evidence**, and keep it alongside its
  diagnostic.

## Keychain credential and saved-state access outcomes

Treat these as distinct local permissions, with separate consequences:

1. **Credential read access** lets the supervisor read a provider key or a
   source credential from macOS Keychain. It does not grant permission to use
   the credential against its provider or source.
2. **Protected workflow-state access** lets the supervisor read the key used to
   authenticate saved workflow state. It does not grant credential access.
3. **Spending approval** is the user's separate native approval for paid model
   calls. Neither Keychain permission grants it.

For Keychain access outcomes, do not claim the app is asleep, offline,
unavailable, or needs sign-in. Never ask for a Mac password, a key, or any
credential in chat. Do not give terminal commands or environment-variable
edits as Keychain access recovery, or suggest deleting credentials, resetting
spending history, or reinstalling. Provider-key setup for a missing key is a
separate one-time setup outcome described below.

When advertised in `get_workflow_capabilities.operations`,
`provider_credential_status` is a read-only, no-argument check with one bounded
20-second read, below the bridge's 60-second deadline. It never returns a key
and never contacts the provider. With `access: success`, `present: yes` means a
credential is present, `no` means it is missing, and `unknown` means presence
was not established. `access: missing` also establishes that no credential is
present and reports `retryable: false`. Other access failures establish
neither presence nor absence. The
`permission: provider_credential_read` field describes credential-read access
only. `source` reports `keychain`, `startup_environment`, or `null`; null means
no source was reported. Do not infer source access, protected workflow-state
access, or spending approval from these fields.

Use the reported `reason` when present. If it is absent, explain only the
reported code and retryability; do not guess why macOS refused or delayed the
read. The table below is the canonical reason-to-action mapping for provider
status, mapper credential-access errors, source-connection errors, and saved
workflow-state errors. For saved workflow-state errors, describe the protected
state key rather than a provider or source credential.

The missing credential rows below apply only to provider or source
credentials. If a saved-workflow-state error carries `reason: missing`, say
only that the protected state key could not be read; do not direct the user to
provider or source credential entry. Stop and report; never retry to fix a
missing state key or create, replace, reset, or recreate it.

Retry the same access action only when the returned `retryable` value is
`true`, with the same request and revision; otherwise stop and report. The
single `pending` re-check described in the table is a bounded status check, not
another access retry. Integrity always stops, even if another field says
retryable. `missing` is never fixed by retrying; provider-key missing status
reports `retryable: false`.

| Reason or status | Tell the user | One next action |
|---|---|---|
| `access_not_granted` | macOS has not granted this app access to read the credential or protected state key. | Allow the read when macOS asks, then retry the same action only when `retryable: true`. |
| `interaction_not_allowed` | macOS could not allow this read in the current session. This can mean a locked Keychain or no GUI session; the result cannot distinguish them. | Use the Mac in an interactive, unlocked session; retry only when `retryable: true`. |
| `keychain_unavailable` / `unavailable` | macOS Keychain could not complete this read. | Unlock or sign in to the Mac's login Keychain session, then retry the same action only when `retryable: true`. |
| `timeout` | The bounded read did not finish; this does not mean access was denied. macOS may still be waiting for permission, possibly in a window behind others. | If macOS asks, allow the read; then retry the same action only when `retryable: true`. Do not claim a prompt is visible. |
| `pending` | Another Keychain access is already in progress. | Re-check once after the user confirms the request completed or was dismissed, or after a short wait. If still pending, report it and stop. Do not poll repeatedly or start another read. |
| `integrity` | The protected credential or saved state could not be verified, so access was refused. | Stop and report the refusal; do not attempt recovery. |
| `unsupported` | This runtime does not support this Keychain read. | Stop and report the limitation; do not suggest reinstalling. |
| `failed` | The bounded access check failed. | Retry the same action only when `retryable: true`; otherwise stop and report. |
| Provider missing: `access: missing`, or `access: success` with `present: no` | No provider key is stored. This is one-time setup, not access recovery. | Re-run the installer with `ANTHROPIC_API_KEY` set, or pipe the key into `<bin> credentials set-provider-key --provider anthropic`. The key never goes in chat; the agent never runs the command or handles the key. |
| Source credential missing | No credential is stored for this source. | Inspect and reuse a compatible source connection. Native masked entry is available only for infra-profile source connections when the runtime offers it. |

The user's confirmation that a macOS request was completed or dismissed is a
trigger to re-check, not an access action. Re-check once with
`provider_credential_status` when advertised, or retry the same provider action
only when it is retryable. Without confirmation, one re-check after a short
wait is allowed. If the result remains `pending`, report it and stop.
`provider_credential_status` is provider-only; it does not establish
source-credential readability or protected workflow-state access. For a source
or saved-state outcome, retry only that exact source/state action when it is
retryable; inspect an uncertain native-entry request before retrying. Do not use
the provider status tool as a source or saved-state check.

The provider mapper code `mapper_provider_credential_missing` identifies a
missing-key setup outcome. After setup, check `provider_credential_status`; if
it confirms the key is present, resume the same request and revision. The
`mapper_provider_credential_access_{not_granted,interaction_not_allowed,unavailable,timeout,pending,integrity,unsupported,failed}`
codes describe provider credential acquisition/read failures; they do not
prove whether a credential exists. A failed provider acquisition at
`start_run` happens before the request is queued. After setup is confirmed or
an allowed access retry resolves, resume only the same supervisor action with
the same request and revision; do not reset, rewrite, or start a duplicate run.

`workflow/state_mac_key_unavailable` may carry a `reason`; when it is absent,
say only that protected workflow state could not be read and do not guess the
cause. Follow the table and general retry rule above. Integrity always stops.
`workflow/state_mac_key_invalid` is always an integrity refusal: report it and
never reset or recreate saved state. Source
`source_connection/protected_access_timeout` means the bounded protected read
timed out even when its optional `reason` is absent; that is not a denial. Use
the table's timeout guidance and retry only when the operation is retryable.
The code may also carry a `reason`, which adds detail without replacing the
timeout meaning. `source_connection/state_mac_key_unavailable` is a separate
protected-state outcome and may carry a `reason`; without one, say only that
protected state could not be read. Follow the table and general retry rule
above. Do not guess a cause.

## User-facing narration

This file decides what a failure means and whether it may be healed, retried, or
raised to the user. It does not define a fixed set of message classes or a
blacklist of ordinary words. Apply [user-facing-language.md](user-facing-language.md)
for progress, approval and clarification requests, blockers, retries,
concessions, partial results, and final outcomes.

Automatically repaired diagnostics remain internal. Disclose a change only when
it changes the result, discards or limits data, spends user resources, or leaves
the user with a meaningful choice. Never copy raw tool output into chat. A
failure that is not settled by the evidence must be described as unsettled, not
called an environment problem.

## The commands

```bash
# what failed, at which step, and who owns it — instead of re-reading logs
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" record query --record <closure>/build-record.json --unresolved
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" record query --record <closure>/build-record.json --owner user

# did the plan move, is anything still open, is this actually finished
# --lock resolves a pre-v0.38.0 closure's dp-spec.lock.json on its own; --spec does NOT
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" materialized --record <closure>/build-record.json \
    --lock <closure>/dp-blueprint.lock.json --spec <workflow>/dp-blueprint.md
```

`materialized` is the word. **Never `correct`.** It says the approved plan was
compiled, the compiled artifact ran, and it published. It says nothing whatever
about whether the numbers are right.
