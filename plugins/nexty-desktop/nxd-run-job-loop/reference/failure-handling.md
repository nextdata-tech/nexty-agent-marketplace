# When a step fails — classify, heal, ask, or retry

## Contents

- [What this file is](#what-this-file-is)
- [The ladder, as the loop sees it](#the-ladder-as-the-loop-sees-it)
- [Three caveats that decide most misclassifications](#three-caveats-that-decide-most-misclassifications)
- [Classification fails closed](#classification-fails-closed)
- [Typed exits, and caps you count instead of estimate](#typed-exits-and-caps-you-count-instead-of-estimate)
- [A blocker is an open question found late](#a-blocker-is-an-open-question-found-late)
- [After a failed supervisor operation: inspect once, then classify](#after-a-failed-supervisor-operation-inspect-once-then-classify)
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

A retry is only ever justified when the failure could plausibly resolve on its
own. **These never do**, and retrying them unchanged burns time and spend while
producing the identical error:

- A `TypeError`, `AttributeError`, or unexpected-keyword-argument error from
  generated code — the call shape is wrong and will be wrong next run.
- An import error for a missing dependency — the runtime is under-provisioned
  until someone provisions it.
- A grant or spec-hash mismatch — the binding is wrong, not flaky.
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
