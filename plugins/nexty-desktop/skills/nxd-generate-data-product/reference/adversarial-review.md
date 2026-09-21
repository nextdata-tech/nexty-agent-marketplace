# Adversarial review round: dispatch, and adjudicating what comes back

## Contents

- [When to dispatch](#when-to-dispatch)
- [Dispatching](#dispatching)
- [Fresh retained-capture handoff](#fresh-retained-capture-handoff)
- [Adjudicate every finding](#adjudicate-every-finding--this-is-the-point)
- [Relay and authorization](#relay-and-authorization)
- [Land the adjudication](#land-the-adjudication)

The self-check is structural. It parses `models.py` and `spec.py`, holds the
naming invariant, dry-runs the transform, and checks the policy boundary. It
cannot know whether the closure ANSWERS THE REQUEST — `self-check.md` says so
directly, and that is the gap this round exists to close.

Two failures make the case, both observed on real runs:

- A closure landed clean base models, passed every structural check, and had no
  model at the grain the user's second question needed. The question was
  unanswerable and nothing in the closure said so.
- A closure concluded a platform capability did not exist, having searched for
  it by one term, and shipped a lesser product. The reference doc describing
  that capability was linked from the skill twice.

Neither is a structural defect. Both are wrong answers.

## When to dispatch

Under workflow v2, dispatch after Step 7 has finished every mutable self-check
and record write and after the supervisor has captured the closure. Run one
review for each capture generation over the supervisor-provided retained paths.
The activated contract makes the review mandatory; there is no complexity-based
skip. A behavior-changing fix resets capture and requires a fresh review of the
new generation. A returned, adjudicated round is recorded as `complete`; an
interrupted child remains pending for the user or runtime that owns it.

## Dispatching

Explicitly dispatch **one built-in read-only subagent**; never add, select, or
rely on a custom/plugin agent definition. Give it both halves:

1. **The closure path.**
2. **The original request, verbatim except credentials**, under the
   `sanitized_original_request` contract — every question asked and every
   supplied procedure preserved, with credentials replaced as specified below.

Those are the **only** dispatch inputs. In particular, never pass
`job_helper_dir`: the reviewer is read-only and does not execute the local desktop
helpers; it inspects their recorded evidence inside the closure.

The second is load-bearing. The defects this round targets are OMISSIONS, so a
reviewer holding only the artifact will pass a well-formed closure that answers
the wrong question. Dispatching without the request wastes the round.

Before dispatch, inventory every value the user designated as a credential and
every value carried by a non-public credential field. Preserve the rest of the
request, including every question and procedure, but replace each inventoried
value with a named placeholder such as `[CREDENTIAL:database_password]`.
Verify that no inventoried value remains anywhere in the reviewer prompt. If
the inventory or complete replacement cannot be established, **do not
delegate**: stop and report that credential-safe review dispatch is blocked.
No credential may reach the reviewer.

## Fresh retained-capture handoff

Treat `retained_capture_root` and `retained_blueprint_path` as the supervisor's
authoritative, fresh inputs for the current capture generation. Select
`review_input` only from the `RequirementView` whose `requirement_id` matches the
review action in the current `next_actions[]`. Carry that view's requirement
identity, generation, subject digest, and dependency digest through the review
relay. A path from an earlier supervisor response or another requirement is
stale even when it still exists on disk.

On a host without a runner-owned review guard, the owning thread must perform a
**non-content path/accessibility check** for both exact retained paths with the
session's allowed read-only host filesystem mechanism (a metadata/stat or
equivalent path-access check, not a content read). On a shellless host, an
allowed `Glob` or `Grep` call with an explicit absolute `path` is the supported
equivalent; do not omit that `path` or open file contents merely to establish
accessibility. On a host with a runner-owned guard, do not issue a separate
owning-thread check: the guard performs and repeats it immediately before
accepting the child dispatch. Confirm that:

1. `retained_capture_root` is an existing directory.
2. `retained_blueprint_path` is an existing regular readable file.
3. The resolved paths remain inside the session's allowed host-visible roots on
   the same host surface available to the read-only child; reject symlink or
   realpath escapes.
4. Both values are byte-for-byte the current matching `review_input` values and
   are bound to the current capture generation; do not infer freshness from a
   filename, timestamp, attachment id, or directory name.

The eval/live runner's review guard also limits the child to these exact
retained inputs. A host runtime without that guard must provide an equivalent
metadata/stat mechanism. Do not use a content read as a substitute for this
precondition.

Do not open either retained path or inspect its contents during this
precondition. If the read-only mechanism cannot establish every check, treat
the handoff as blocked. Report an explicit incomplete blocker such as
`INCOMPLETE — retained capture handoff blocked: <field> is
<missing|stale|outside the allowed host-visible roots|inaccessible> for
requirement <id>, generation <generation>; no reviewer was dispatched.` Leave
the requirement pending, send no review verdict, and never substitute an older
capture, scratch or fallback path, the mutable authoring root, or a path from
another requirement. This path check proves availability only; it is not review
evidence and does not authorize inline review.
On a guarded host, the runner permits the owning thread to stop with this
incomplete blocker but does not permit a retry or any tool that could replace
the captured handoff. Obtain a fresh supervisor capture in a new session.

Normalize the closure path relative to the workspace (`closure` or
`nxd-jobs/<workflow>/closure`) and use this complete prompt block. Replace only
the angle-bracketed values with the exact `review_input` paths and the fully
sanitized request. Keep both retained-path lines exactly once, keep exactly one
nonblank `Sanitized original request:` line, and include the marker exactly
once. The prompt must tell the child to load and follow `nxd-review-closure`,
and must give it the retained capture root, retained blueprint path, and the
original request under the existing sanitized-request contract:

```text
retained_capture_root: <exact retained_capture_root from review_input>
retained_blueprint_path: <exact retained_blueprint_path from review_input>
Load and follow nxd-review-closure.
Sanitized original request: <complete request with credentials replaced>
review_time_budget_seconds: 300
review_inspection_cutoff_seconds: 240
NXD_REVIEW_DISPATCH {"closure_path":"closure","request_contract":"sanitized_original_request","return":"claims_only","review_round_index":0}
```

Replace only the example `closure_path` and `review_round_index` values in the
marker. Keep both retained-path values byte-for-byte identical to the values
that passed the pre-dispatch check; never rebase, shorten, normalize, or replace
them for the child. Keep every other key/value unchanged and add no colon, slug
or prose prefix. Invoke the child inline with `run_in_background: false` when the
installed `Agent`/`Task` schema exposes that field; otherwise omit it, and
never set it to `true`. Load `nxd-review-closure` and **return claims only**.
The reviewer receives read-only tools, never edits, builds, serves, runs the
transform, or starts a user conversation.

The Desktop workflow records only a returned, adjudicated review round. Do not
invent a timeout record, a review verdict, or claims when a client-side review
child cannot return; keep the workflow pending for the user or runtime that
actually owns that interruption. An incomplete child result is not a clean
review and never justifies a second reviewer for the same capture generation.

The reviewer inspects disclosure paths first: output promises and exposed
ports, then model roles and physical writes, then both semantic and direct-store
reachability. It continues with the broader logical and semantic review after
that pass. This ordering is a threat-model checklist, not a supplied finding or
resolution.

## Adjudicate every finding — this is the point

**What comes back is a claim, not a verdict.** A reviewer told to find problems
will invent some when the closure is clean. If you accept findings unexamined
you will damage a good closure to satisfy a fabricated critique; if you ignore
them all the round is theatre. Neither is acceptable, and the difference between
them is adjudication.

For each finding, first decide and record one of:

- **`accepted`** — you verified it against the closure and the request, and it
  holds. This is an assessment, not permission to fix it.
- **`rejected`** — you verified it does NOT hold. Cite the evidence that
  refutes it: `file:line`, or the request text. **"I checked and it is fine" is
  not a rejection.** A rejection without a citation is a shrug, and it is
  indistinguishable from not having checked.
- **`out_of_scope`** — real, but outside what this request asked for. Say what
  would have to change for it to be in scope.

Verify before you act, in both directions. A finding that names a line number
may be pointing at a line that says something else. A finding that says a
question is unanswerable may be wrong because the answer lives in a model the
reviewer did not open. Check the artifact, not the claim about it.

Do not argue with a finding you have not verified, and do not fix one either.

## Relay and authorization

Relay **every** finding to the user before mutation: ID, severity, claim,
evidence, adjudication/citation, proposed effect, classification and applied
state. All review findings default to behavior-affecting because this role hunts
logical and semantic defects; they remain `needs_user` until the user explicitly
approves their IDs. Rejected and out-of-scope claims are still relayed but change
nothing. The only automatic exception is a syntax, mechanical, or procedural
`structural_note` backed by evidence that the spec hash, model/field set, grain,
row inclusion, values, aggregations, thresholds, verdicts and assertions remain
unchanged.

## Bouncing back for a ruling

If you accept a HIGH finding whose fix needs a NEW decision the user has not
made — a convention the source cannot settle, a scope question, a grain choice
with real consequences — do not guess inside the subagent. Return `gap_found`
the way the generate subagent does, and let the main thread run the read-back.

A fix that silently invents a ruling is a worse defect than the one it fixes,
because it looks settled.

## Land the adjudication

Record the round in the job-level `review-record.json` outside the captured
closure, under schema `nxd-conversation-review-ledger-v1`, the workflow id, and
append-only `review_rounds[]`: deadline and elapsed time, completeness/status,
every original claim, adjudication, classification, user decision, proposed
effect and applied files. Never mutate the captured closure with review output.
Use the ledger status values `complete`, `needs_user`, or `timed_out`; do not
copy the supervisor's separate `report.verdict` values (`clear`, `findings`,
`rejected`, or `indeterminate`) into this field. A `findings` report can still
belong to a `complete` ledger round when the rich claims and adjudications are
complete; the two vocabularies answer different questions.
Also record `deferred_finding_ids`: it is empty unless the cited user decision
explicitly continues while leaving accepted behavior-affecting findings
unapplied, in which case it names those finding IDs exactly.
Only an explicitly authorized mutation is also recorded as its normal heal
attempt. This keeps pending, rejected, denied and timed-out findings auditable.

This follows the pack's existing stance that rulings are landed as reviewable
data rather than buried in prose. It also makes the round auditable — a later
reader can see what was challenged and why it was kept, and a reviewer of the
NEXT revision does not re-raise a finding that was already refuted.

A completed round that returned no findings is recorded too. "Reviewed, nothing
found" is information; a missing or timed-out section is not a clean review.
The marker is a declaration of the sanitization contract, not proof that the
delegated request was faithful or credential-free. Number each dispatch from
zero in array order. The live attestation for that review carries the same
`review_round_index` and uses the exact evidence reference
`<normalized-job>/review-record.json#review_rounds/<review_round_index>`;
it does not need a `turn` field. If an older recording carries `turn`, it is
informational only: chronology comes from the harness-observed dispatch,
self-check, and build events. Treat the round as observed only when that
reference, the marker path, the closure-keyed round, and the published build's
matching supervisor `run_id` and `artifact_id` all identify the same closure.
Never combine evidence from sibling closures.
