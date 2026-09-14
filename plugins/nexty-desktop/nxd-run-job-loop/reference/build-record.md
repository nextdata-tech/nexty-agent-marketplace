# `build-record.json` — what happened, as data

## Contents

- [What this file is](#what-this-file-is)
- [The unified diagnostic record](#the-unified-diagnostic-record)
- [The report envelope](#the-report-envelope)
- [The build record, field by field](#the-build-record-field-by-field)
- [Where conversation review lives](#where-conversation-review-lives)
- [`attempts[]` — the part that makes claims checkable](#attempts--the-part-that-makes-claims-checkable)
- [The stage ladder](#the-stage-ladder)
- [The three caveats](#the-three-caveats)
- [Classification fails closed](#classification-fails-closed)
- [Ownership classes](#ownership-classes)
- [The concession split: forbidden vs discouraged](#the-concession-split-forbidden-vs-discouraged)
- [Typed heal exits](#typed-heal-exits)
- [Materialization is a predicate, not a status](#materialization-is-a-predicate-not-a-status)
- [User-facing narration boundary](#user-facing-narration-boundary)
- [The CLI](#the-cli)
- [What is honestly weak in this record](#what-is-honestly-weak-in-this-record)

## What this file is

`dp-blueprint.md` is the plan. `build-record.json` is **what the supervisor
materialized and verified during capture and later runtime stages**. Optional
agent-side checks may contribute evidence, but are not required before capture.
Conversation review is deliberately
separate because it begins only after the closure has been captured and made
immutable.

The split is not filing tidiness. The compiler framing behind this pack is: user
intent is the source, [`dp-blueprint.md`](dp-blueprint.md) is the IR, `nxd-generate-data-product` is
codegen, the closure's Python is the output artifact. An IR is a pure function
of its source, so an outcome — a row count, a runtime blocker, an attempt, or a
build result — cannot live in it. It lives here. Review claims and decisions live
in the job-level review ledger described below.

```
…/nxd-jobs/<workflow>/
├── review-record.json          append-only conversation review ledger
└── <captured-generation>/
    ├── dp-blueprint.approved.md    byte copy of the approved IR       — the plan
    ├── dp-blueprint.lock.json      its canonical hash + compiler version — the binding
    └── build-record.json           stages, attempts, concessions — this file
```

Three properties, all load-bearing:

- **Generated, never hand-authored.** No template, no prose sections to fill in,
  no discipline to remember. It is written by
  `"$JOB_HELPER_DIR/scripts/dp_diagnostics.py" record …` and, for the self-check stages, by
  `self_check.py --record build-record.json` at the closure root.
- **It records attempts, not just an outcome.** Per attempt: which stage failed,
  what the agent thought was wrong, what it changed, and what the re-run did.
  That is what turns "healed with concessions" from an assertion into something
  a reader can check, makes the regenerate cap countable rather than estimated,
  and gives a harness its retry / question / concession queues.
- **It travels with the closure.** A cold reader and the export handoff read the
  snapshot for the plan and this file for generation and execution history.
  Conversation review is read from the supervisor-bound job ledger, never from
  a mutable file inside the captured closure. Neither source requires the chat.

**This is not the old hand-written closure context document under a new name.**
The plan sections such a document used to duplicate are gone —
`dp-blueprint.approved.md` carries them, byte for byte, so there is nothing to copy
and nothing to drift. What remains here is outcome-only and mostly mechanical.

## The unified diagnostic record

One shape, emitted by **every** stage — the spec validator, each self-check
phase, the build loop, the supervisor-facing steps. They differ only in which
stage produced them. That is what keeps the tooling small: design the shape
once, and every producer conforms.

```jsonc
{
  "schema":   "nxd-diagnostic-v2",
  "stage":    "s2_transform",
  "code":     "runtime.assert_failed",
  "severity": "error",                  // error | warning | info
  "owner":    "agent",                  // agent | user | environment
  "origin":   "tool_computed",          // agent_observed | supervisor_reported
                                        // | tool_computed | llm_authored | unbound
  "path":     "closure:transform/main.py:214",
  "message":  "per-candidate uniqueness assert fired: 12 duplicate ashby_candidate_id",
  "evidence": { "count": 12 },
  "fix":      "deduplicate on ashby_candidate_id before the join"
}
```

Everything except `evidence` (defaults to `{}`) and `fix` (optional) is
required, and **no other keys are permitted** — an unknown key is a producer
bug and the shared validator rejects it.

### `code`

`<domain>.<slug>[.<slug>]`, lowercase. **Stable forever**: a code is never
renamed and never repurposed, and a changed meaning is a new code. Match on the
code, never on `message` text.

The registry is `"$JOB_HELPER_DIR/scripts/dp_diagnostics.py"::CODES`, mapping each code to its
`stage`, `severity`, `owner`, `control`, `agent_fillable` and a summary.
Producers supply the code plus a per-instance `path` / `message` / `evidence`.
Domains:

| domain | what it covers |
|---|---|
| `spec.` | the IR alone, from `validate_dp_spec.py` |
| `struct.` | Phase A structural checks against the pinned DSL |
| `reach.` | Phase E — the transform's reach to models and the network, decided before it runs |
| `runtime.` | user code that executed and misbehaved |
| `closure.` / `policy.` | closure layout, the snapshot and lock, the policy boundary |
| `semantic.` | the distribution read-back and wrong-answer tells |
| `pin.` | the supervisor pinning and compiling the closure |
| `env.` | the machine, the kernel, the network |
| `publish.` | publish, verify, artifact |
| `blocker.` | anything only the user can resolve |
| `concession.` | something the skills discourage, done anyway, disclosed |
| `heal.` / `meta.` | bookkeeping |

### `severity` and `owner` are different questions

`severity` says whether it gates: `error` fails the stage, `warning` does not
gate but must reach this record and be weighed before anything is claimed,
`info` is data rather than judgement (row counts, read-back rows, the Phase A
blind-spot list).

`owner` says **who can fix it**: `agent` (self-heal the generated code), `user`
(a ruling, a credential, an answer nobody else has), `environment` (neither —
retry). It is `owner`, not `severity`, that decides whether the user hears about
a diagnostic at all. A `severity: error` owned by the agent is absorbed
silently; a `severity: warning` with a `concession.` code is spoken out loud.

A producer may relax a `severity` downward (`error` → `warning`), never upward.
A producer may **never** override `owner` — it comes from the registry and only
from the registry, because a demoted `owner` silences a blocker where a relaxed
`severity` merely loses a warning. A diagnostic whose ownership genuinely
changes is **re-emitted under a new code**, never mutated: an `environment`
failure that has exhausted its retries is re-emitted as
`blocker.caps_exhausted` with `owner: user`, because an unfixable environment is
something the user must hear about even though a retryable one is not.

When the remedy is "supply a credential" the diagnostic is `owner: user`
(`env.credential_missing`), not `environment`. The user has to act, so the user
hears it.

### `origin` — how strong is this claim

| `origin` | meaning |
|---|---|
| `tool_computed` | a script in this repo computed it |
| `supervisor_reported` | the supervisor authored the claim; verbatim payload attached |
| `agent_observed` | the agent inferred it from what it could see |
| `llm_authored` | generator prose — analysis, not measurement |
| `unbound` | the schema defines the field and no producer exists yet |

**`origin` records who authored the claim, not who wrote the file.** Agent
transcription launders authorship in neither direction: a traceback the
supervisor emitted stays supervisor-authored however it reached this record, and
an agent's inference stays agent-authored however confidently it is phrased.

An agent-written diagnostic may claim `supervisor_reported` **only if both** of
these hold:

1. `evidence.supervisor_detail` carries the **verbatim, unedited**
   supervisor-authored payload — an error body or traceback returned in a tool
   result, or the durable operation/requirement status returned by
   `inspect_workflow`. A paraphrase, a summary or a reconstruction disqualifies
   it.
2. `path` names the producing tool, so a reader can find the payload:
   `tool:inspect_workflow.operation`.

Otherwise `agent_observed`. **When it is unclear, it is `agent_observed`** —
this is the fail-closed rule expressed as a field, and it is what stops an
unbacked hunch from reaching `environment_suspect`.

### `path` — address the field, by identity

```
v2:models[scored_candidates].key
v2:open_questions[fx_rates].target
closure:transform/main.py:214
closure:models.py:scored_candidates.verdict
tool:inspect_workflow.operation
```

Three prefixes and no others: `spec:`, `closure:`, `tool:`. A subscript is the
entry's **own identity** — `id`, else `name`, else `decision_id`, else `model`,
and only when an entry has none of those, `#<0-based index>`. Never a bare index
for an entry that has an identity: array indices move when a list is edited, and
a moving address is a UI that highlights the wrong field. `path` is `""` only
for a whole-run diagnostic that has no address.

### `evidence`

A JSON object, always present, always serializable, never more than about 4 KB.
Reserved keys with fixed meaning: `expected`, `actual`, `found`, `allowed`,
`count`, `rows`, `columns`, `traceback`, `stdout_excerpt`, `exit_code`,
`command`, `alternative`, `model`, `table`, `supervisor_detail`.

**Redaction is mandatory.** Every producer runs `message` and `evidence` through
the same credential-value pattern `validate_dp_spec.py` uses and replaces a
match with `<redacted>`. A check that prints the secret it found turns a
contained file leak into a transcript leak.

## The report envelope

Any tool whose primary output is diagnostics emits:

```jsonc
{
  "schema": "nxd-diagnostic-report-v2",
  "tool":   "self_check",
  "target": "…/closure",
  "ok":     false,
  "counts": { "error": 1, "warning": 0, "info": 12 },
  "spec_hash": "sha256:…",
  "diagnostics": [ /* nxd-diagnostic-v2 records */ ]
}
```

`tool` is a closed enum of four values. Three name a script; the fourth names
the agent:

| `tool` | written by | stages it may carry |
|---|---|---|
| `validate_dp_spec` | `"$JOB_HELPER_DIR/scripts/validate_dp_spec.py" --json` | `s0_spec` |
| `self_check` | `self_check.py --json` at the closure root | `s1_structure`, `s2_transform`, `s3_closure` |
| `dp_diagnostics` | `"$JOB_HELPER_DIR/scripts/dp_diagnostics.py"` | any |
| `loop` | **the agent**, hand-constructed from tool results | `s4_pin` … `s8_answer` |

`loop` exists because stages 4–8 have no script producer: the agent observes a
build error or a row count and constructs the report itself. Naming that
honestly is the point — a `loop` report is agent-constructed and is **visibly
weaker evidence** than a computed one. Labelling it `dp_diagnostics` would
disguise an observation as a measurement. `record append` rejects a report whose
`tool` cannot produce the stage it was handed.

## The build record, field by field

```jsonc
{
  "schema": "nxd-build-record-v2",
  "workflow": "candidate-scoring",
  "data_product": "candidate_scoring",
  "closure_path": "/abs/path/to/closure",
  "compiled_from": "sha256:…",          // == dp-blueprint.lock.json spec_hash
  "compiler_version": {
    "plugin": "0.29.0",
    "generator_skill": "nxd-generate-data-product",
    "dp_spec_version": 2,
    "canonicalization": "nxd-dp-spec-canon-v2"
  },
  "generated_at_unix_ms": 1769904000000,
  "generator_model": "claude-opus-5",
  "stages":      { /* nine keys, always all present */ },
  "review_rounds": [], /* reserved schema field; workflow v2 never appends here */
  "attempts":    [ /* every heal, remap, regenerate, retry */ ],
  "concessions": [ /* discouraged things done, and whether disclosed */ ],
  "blockers":    [ /* open_questions discovered late */ ],
  "readback":    { /* the distribution read-back as data */ },
  "evidence":    { /* observed facts, each with its origin */ },
  "caps":        { /* the bounds, counted */ },
  "narrative":   { /* LLM prose, labelled as such */ }
}
```

`compiled_from` is the whole point of the file existing next to a lock: it names
the exact plan revision this closure was compiled from. Content tracking is one
**whole-spec** hash — never per-section — and it buys four things: skipping
regeneration when nothing changed, tamper-evidence when the spec was edited
after approval, provenance, and a countable regenerate cap.

**Source-data staleness is a separate axis and never merges with this one.** How
old the input data is has nothing to do with whether the plan and the closure
agree; it is recorded in `evidence.source_state` and excluded from every
predicate below.

### Lifecycle

1. **At generation, before self-check**: `record init` writes the envelope,
   `compiled_from`, `compiler_version`, and every stage as `not_reached` —
   except `s0_spec`, which it fills in the same pass by validating the
   **snapshot** (not the live IR, because the snapshot is what was compiled).
2. **Step 7**: closure-root `self_check.py --json --record build-record.json` merges
   `s1_structure`, `s2_transform`, `s3_closure`. Phase C validates this file's
   own presence and its `compiled_from`, so the record must exist before Phase C
   runs — which is why step 1 is unconditional.
3. **Stages 4–8** are merged by the loop as they happen, via `record append`.
4. **Every** heal, regenerate, remap and retry appends to `attempts[]` *before*
   re-running.
5. **After capture**, exactly one conversation review for that capture generation
   appends to the job-level `review-record.json`. `build-record.json` remains
   unchanged. An accepted behavior-affecting finding requires reset, local
   correction, self-check, recapture, and a fresh review of the new generation.

`record init` is the only writer of `s0_spec`, and re-validating after a
write-back does not update it: `s0_spec` describes the snapshot the closure was
built from and is frozen with it. The live IR having moved is `plan_moved`, not
an `s0_spec` regression.

### `stages`

Keyed by the nine stage ids, every key present at all times.

```jsonc
"s2_transform": {
  "status": "passed",       // passed | passed_with_warnings | failed | not_reached | skipped
  "ordinal": 2,
  "at_unix_ms": 1769904012345,
  "origin": "tool_computed",
  "diagnostics": [ /* nxd-diagnostic-v2 */ ],
  "detail": { "models_counted": 4, "optional_tables_absent": ["mapper_reviews"], "unverified": 1 }
}
```

Status mapping is the same rule everywhere: no errors → `passed`; no errors and
at least one warning → `passed_with_warnings`; any error → `failed`.

**`not_reached` is a distinct status from both `passed` and `failed`, and a
record full of it is normal and honest.** `self_check.py` exits on the first
failing phase, so later phases produce no signal at all — and "no signal" must
never be recorded as "fine". A check that was not reached emits nothing;
producers must not synthesize the diagnostics a stage would have produced.

`skipped` is legal only for `s7_publish` (no static artifact requested) and
`s8_answer` (no query asked).

### `readback` — the distribution read-back as data

```jsonc
"readback": {
  "distribution": [
    { "model": "scored_candidates", "column": "gate_g1",
      "values": [ { "value": "UNKNOWN", "count": 856 } ],
      "uniform": true, "origin": "tool_computed" }
  ],
  "absent": [
    { "source": "verdict_thresholds", "column": "verdict",
      "declared_missing": ["different_role", "needs_more_info"], "origin": "tool_computed" }
  ]
}
```

The read-back stays **non-gating** — that does not change here. What changes is
that it is recorded as data instead of printed as prose and scrolled past, and
that it is surfaced next to concessions. A `uniform: true` column is the
sharpest available tell that a fully green build will answer wrongly; a uniform
entry with no matching explanation in `concessions[]` is exactly what
`concession.readback_uniform_unexplained` is for.

### `concessions[]`

```jsonc
{
  "code": "concession.assert_weakened",
  "class": "discouraged",
  "stage": "s2_transform",
  "path": "closure:transform/main.py:214",
  "what": "kept the most recent row per candidate instead of failing on 12 duplicate ids",
  "why": "the source feed carries genuine duplicate applications",
  "alternative_rejected": "fail the build and surface the 12 rows",
  "attempt": 3,
  "disclosed": false,
  "at_unix_ms": 1769904031000,
  "origin": "llm_authored"
}
```

`class` is always `"discouraged"`. There is no forbidden concession — a
forbidden invariant is never taken, it is escalated as a blocker.

`disclosed` flips to `true` **only when the agent has actually said it to the
user**, in words, in the conversation. Until then the product is not
materialized, however green every stage is. **A green run carrying an
undisclosed concession is the worst state in this design, because it reads as
materialized** — the record says pass, the user was never told what pass cost,
and nothing in the artifact reveals the difference.

### `blockers[]`

```jsonc
{
  "code": "blocker.open_question",
  "open_question_id": "fx_rates",
  "question": "Which EUR→USD rate, over what date range?",
  "blocks": ["total_opex"],
  "disposition": "blocked",       // blocked | deferred | answered
  "stage": "s6_run",
  "path": "v2:open_questions[fx_rates]",
  "discovered": "build_time",     // pre_build | build_time
  "written_back": true,           // written into the LIVE dp-blueprint.md
  "at_unix_ms": 1769904090000,
  "origin": "agent_observed"
}
```

**A build-time blocker is an **Open Questions** entry discovered late, and there
is no second mechanism.** The spec section already has the right vocabulary — a
question, a `blocks:` list, and a `blocked | deferred | answered` disposition —
and it already carries the rule that a measurement you must not propose belongs
there rather than being invented into `criteria` or `decisions`. Inventing a
parallel "build issues" channel would split the user's queue in two.

`written_back: true` means the entry was added to the live `dp-blueprint.md`. That
**un-approves** the spec by design: the canonical hash moves, `compiled_from` no
longer matches the live IR, and the materialization predicate correctly reports
not-materialized. The elicitation contract is a loop, not a pre-build-only gate,
and the same "needs your input" queue serves both ends of it.

`written_back: false` is legal — it is the normal state between recording the
blocker and performing the edit, and the state a crash leaves behind. Nothing is
concealed by the gap, because a `disposition: "blocked"` entry already makes the
predicate false either way.

### `evidence` — observed facts, each with its origin

```jsonc
"evidence": {
  "phase_b_row_counts": { "origin": "agent_observed",
    "note": "scratch DuckDB dry run — NOT the published product",
    "models": [
      { "table": "scored_candidates", "row_count": 856 },
      { "table": "mapper_reviews", "row_count": 0, "materialized": false, "optional": true }
    ] },
  "published_row_counts": { "origin": "supervisor_reported",
    "source": "verified.json:evidence.model_tables",
    "models": [ { "dataset": "main", "table": "scored_candidates", "row_count": 856 } ] },
  "publish": { "origin": "supervisor_reported",
    "workflow": "candidate-scoring", "publish_seq": 3,
    "trust": "artifact_verified", "artifact_status": "available" },
  "endpoint": { "origin": "agent_observed", "reachable": true, "described_models": 4 },
  "required_capture": { "origin": "agent_observed",
    "fields": [ { "field": "github_url", "required_by": "scored_candidates",
                  "missing_rows": 14, "sample_keys": ["a_812", "a_930"] } ] },
  "source_state": { "origin": "agent_observed",
    "cursor_field": "created_at", "watermark": "2026-07-30T00:00:00Z",
    "note": "SOURCE DATA STALENESS — a separate axis. Never participates in materialization." },
  "supervisor_detail": { "origin": "supervisor_reported",
    "path": "tool:inspect_run.run.stdout_tail",
    "note": "verbatim path-redacted traceback quoted from inspect_run; per-attempt identity remains unbound" }
}
```

Each sub-block carries its own `origin`, governed by the same relay criterion as
a diagnostic — a sub-block claiming `supervisor_reported` must be quoting a
verbatim supervisor-authored payload and must name where it came from in
`source`. `published_row_counts` and `publish` qualify: both are read unedited
out of `verified.json`. `endpoint` and `static_artifact` do not — those are the
agent's own observations *about* supervisor output, so they stay
`agent_observed` even though a supervisor was involved. This is not pedantry: an
over-claimed origin is a route to retrying a closure that is genuinely broken.

**`phase_b_row_counts` and `published_row_counts` are separate keys and must
never be merged.** One is a dry run against a temporary database; the other is
what shipped. Collapsing them lets a dry-run count stand in as evidence that the
product has rows.

`required_capture` is the **outcome** half of the spec's
`fields[].required_capture: true` declaration. The spec says which fields the
model is wrong without; this says how many rows arrived without one. The plan
half never learns the count.

### `caps`

```jsonc
"caps": {
  "remap_per_question": 2, "regenerate_total": 3, "retry_environmental_total": 3,
  "regenerates_used": 1,
  "remaps_used": { "q1": 2, "q3": 0 },
  "retries_used": 0,
  "exhausted": []
}
```

**Counted from `attempts[]`, never estimated.** The remap and regenerate bounds
already existed as prose in the skills; what changes is that the agent can check
them instead of self-policing them. `retry_environmental_total` bounds the one
kind of attempt that consumes neither of the others — without it an
environmental failure could retry forever and never reach the user. On
exhaustion the next occurrence is re-emitted as `blocker.caps_exhausted` with
`owner: user`.

A cap is a **bound on retrying, not a verdict on the closure.** Exhausting the
retry cap does not make the closure known-bad; it means the user hears about it
instead of the agent looping in silence.

## Where conversation review lives

The `review_rounds` member in `build-record.json` is a required, reserved empty
array for schema compatibility. Workflow v2 must not append review data to it:
doing so after capture would mutate the source digest that the supervisor is
protecting.

Conversation review instead uses
`…/nxd-jobs/<workflow>/review-record.json`, outside every captured closure. The
ledger has schema `nxd-conversation-review-ledger-v1`, names the workflow, and
appends exactly one round per capture generation. A round can be `complete`,
`timed_out`, or `needs_user` — never `skipped` — and records the deadline,
elapsed time, claims, evidence, adjudication, proposed effect, and any user
decision. `accepted` means verified, not authorized. See
[workflow-v2.md](workflow-v2.md) and
[adversarial-review.md](../../nxd-generate-data-product/reference/adversarial-review.md)
for the authoritative dispatch, projection, reset, and remediation contract.

The following illustrates the external ledger envelope for a completed review
with one rejected, non-applied behavior finding. A rejected claim needs a
citation, but does not need a user decision because it authorizes no change.

```json
{
  "schema": "nxd-conversation-review-ledger-v1",
  "workflow": "candidate-scoring",
  "review_rounds": [
    {
      "status": "complete",
      "started_at_unix_ms": 1769904000000,
      "ended_at_unix_ms": 1769904010000,
      "budget_ms": 120000,
      "findings": [
        {
          "id": "review-001",
          "claim": "The closure drops records required by the approved plan.",
          "evidence": [
            "closure:transform/main.py",
            "closure:dp-blueprint.approved.md#models"
          ],
          "classification": "behavior_affecting",
          "proposed_effect": "Change the transform to retain the dropped records.",
          "applied_files": [],
          "state": "not_applied"
        }
      ],
      "adjudications": [
        {
          "finding_id": "review-001",
          "disposition": "rejected",
          "citation": "The approved plan explicitly excludes those records."
        }
      ],
      "user_decision": null,
      "deferred_finding_ids": []
    }
  ]
}
```

## `attempts[]` — the part that makes claims checkable

```jsonc
{
  "attempt": 3,
  "kind": "heal",              // generate | regenerate | remap | heal | retry
  "stage": "s2_transform",
  "started_at_unix_ms": 1769904030000,
  "origin": "agent_observed",
  "diagnosis": {
    "code": "runtime.assert_failed",
    "path": "closure:transform/main.py:214",
    "summary": "per-candidate uniqueness assert fired: 12 duplicate ids"
  },
  "changed": [
    { "file": "transform/main.py", "what": "deduplicate on ashby_candidate_id before the join" }
  ],
  "spec_hash_before": "sha256:…",
  "spec_hash_after":  "sha256:…",
  "rerun": {
    "stage": "s2_transform",
    "status": "passed",
    "diagnostics_cleared": ["runtime.assert_failed"],
    "diagnostics_new": []
  },
  "exit": "healed_with_concessions",
  "concessions": ["concession.assert_weakened"]
}
```

### INVARIANT-D2: a compiler does not edit your source

> For every attempt with `kind` in `heal`, `retry` or `remap`,
> **`spec_hash_before` must equal `spec_hash_after`.**

The self-heal loop may change generated code. It may **never** change the IR. If
green is only reachable by changing the plan — narrowing the population to dodge
a bad join, dropping a model whose grain will not resolve, relaxing a threshold
— that is a **spec edit requiring re-approval**, not a heal.

A heal attempt that moved the hash is rejected by the record writer, which emits
`blocker.spec_edit_required`, sets `exit: "blocked"`, and routes it to the user.
This is the single place where "a compiler does not edit your source" stops
being advice and becomes a check. `kind: "regenerate"` is the one kind allowed
to carry different hashes — regenerating after an approved spec edit is exactly
the legitimate path.

### The ordering rule for a blocked attempt

There is one legitimate way a heal moves the *live* hash: the `open_questions`
write-back, which un-approves the spec on purpose. Without a fixed order, one
implementation samples `spec_hash_after` before the write-back and another after
— and the second fires `blocker.spec_edit_required`, which is an accusation that
the agent cheated, on a correctly blocked exit.

So the sequence is fixed:

1. Record `heal.attempt_started`.
2. The attempt fails on a blocker.
3. Append the attempt with `exit: "blocked"` and
   `spec_hash_before == spec_hash_after ==` the **pre-write-back** hash. They are
   equal truthfully: the attempt itself never edited the IR.
4. Append the `blockers[]` entry.
5. *Then* edit the live `dp-blueprint.md` and set `written_back: true`.

The write-back is a separate, separately recorded event. It is carried by
`blockers[].written_back`, never by `attempts[]`. The divergence it creates
between the live IR and the lock is visible only through `lock verify --spec`
and is reported only as `plan_moved` — never as `blocker.spec_edit_required`.
The two are disjoint by construction: `blocker.spec_edit_required` means *an
attempt's own hashes differed*; `plan_moved` means *the live IR has moved away
from the snapshot the closure was built from*.

## The stage ladder

Classify a failure by **which stage failed**, never by parsing exception text.
The stages already differ in what they have access to, and that difference is
the classifier.

| stage | what runs | offline? | what a failure means |
|---|---|---|---|
| `s0_spec` | `validate_dp_spec.py` — the IR alone | yes | blocker, or a gap the agent can fill |
| `s1_structure` | self-check Phases A + E — AST vs the pinned DSL, then the reach gate; nothing executed | yes | malformed code, or a transform reaching a model / undeclared network; self-heal |
| `s2_transform` | self-check Phase B — the transform executes for real, scratch DuckDB, no kernel, no network | yes | **user-code runtime error, unambiguously the code** |
| `s3_closure` | self-check Phases C + D | yes | structural / governance; self-heal |
| `s4_pin` | the supervisor pins and compiles the closure | no | **a code fault Phase A cannot see** |
| `s5_serve` | provision / serve — kernel, deps, endpoint | no | usually environment |
| `s6_run` | the transform on the supervisor | no | mixed |
| `s7_publish` | publish / verify / artifact | no | mixed |
| `s8_answer` | describe / query | no | green build, wrong answer |

**Stages 0–3 are offline and deterministic, so a failure there is never
environmental.** That is not a heuristic — those stages touch no kernel, no
network and no supervisor. Phase B in particular is the sharpest instrument in
the pack for "a poorly generated data product that will not do its work":
*Phase B executes for real, so what it reports is what will happen.*

## The three caveats

Written down here so they are not rediscovered the hard way.

1. **Stage 4 masquerades as environment.** Phase A *cannot execute the builders*
   — a closure can pass Phase A in full and still fail when the supervisor runs
   trusted validation through the returned `start_requirement` action. A failed
   validation operation in `inspect_workflow` is **not** presumptive evidence of
   a bad environment. `pin.build_failed` therefore ships with `owner: "agent"`
   in the registry, by construction rather than by evidence.
2. **Phase B covers only `transform/main.py`.** A green Phase B says nothing
   about `spec.py` or `models.py`. Its printed `unverified:` list is its own
   declared blind spot for dynamic constructs, and those lines are emitted as
   `struct.unverified` (`severity: info`) so the blind spot lands in this record
   rather than in a scrollback.
3. **Stage 8 failures produce a fully green build.** Everything passed and the
   answer is still wrong. The distribution read-back exists precisely to make
   that visible — a uniform classification column is the tell — and it stays
   non-gating. What changes is that this record captures and surfaces it, next
   to concessions.

## Classification fails closed

> When the evidence does not settle whether a failure is environmental, default
> to **not** environmental.

Misclassifying a real bug as "the environment" is what ships a broken data
product flagged green. The cost of the opposite error is one wasted heal
attempt. Those are not comparable, so the tie goes to the code.

The discriminating test is: **"would re-running this closure unchanged in a
healthy environment pass?"** Answering *yes* requires `supervisor_reported`
evidence meeting the relay criterion above. Absent that, the answer is *no*, the
`owner` is `agent`, and `meta.classification_unsettled` is recorded so the
honesty is visible in the file rather than only in someone's head.

## Ownership classes

| class | `owner` | handling | typed exits |
|---|---|---|---|
| structural (`struct.`, `closure.`, `policy.`) | `agent` | must self-heal, bounded | `healed`, `healed_with_concessions`, `caps_exhausted`, `blocked` |
| runtime user code (`runtime.`) | `agent` | must self-heal, bounded | same |
| runtime environment (`env.`, some `publish.`) | `environment` | retry, or ask for a credential; **does not count against materialization** | `retry_environmental` |
| blocker (`blocker.`, and `spec.` findings the agent must not invent) | `user` | write back into `open_questions`; the spec un-approves | `blocked` |
| concession (`concession.`) | `agent` | green **with a disclosed concession** | `healed_with_concessions` |

Bounds are counted from `attempts[]`, not estimated: **remap ≤ ~2 per question**,
**regenerate ≤ ~3 total**. Non-convergence must be reported — never looped on
silently, never abandoned silently.

## The concession split: forbidden vs discouraged

A "concession" is the agent reaching green by doing something the skills
discourage. That splits in two, and collapsing the halves is how a heal loop
ends up relitigating an absolute.

### FORBIDDEN — never do it, even to get green. Escalate instead.

These are absolutes in `nxd-generate-data-product`. A heal loop does not get to weigh them
against a red build:

| doing this | escalates as |
|---|---|
| hand-writing `deployment-spec.yaml` / `manifest.yaml` / `models.yaml` | `blocker.forbidden_handwritten_yaml` |
| hand-rolling a durable watermark instead of `transform_state` | `blocker.forbidden_manual_watermark` |
| `write_disposition="replace"` while yielding a delta | `blocker.forbidden_replace_disposition` |
| loosening an assert into restating its own arithmetic | `blocker.forbidden_assert_restates_arithmetic` |
| reaching green only by changing the plan | `blocker.spec_edit_required` |

The last one is INVARIANT-D2 again, from the other direction: it is a spec edit
requiring re-approval, not a heal, and the hash comparison catches it
mechanically.

### DISCOURAGED — permissible, but the run is green *with a disclosed concession*

Recorded in `concessions[]` and **said out loud to the user**. The codes:
`concession.assert_weakened`, `concession.type_coerced`,
`concession.incidental_column_dropped`, `concession.sample_capped`,
`concession.dependency_repinned`, `concession.derived_model_left_inert`,
`concession.unverified_construct_accepted`,
`concession.readback_uniform_unexplained`, `concession.retry_reduced_scope`,
and `concession.other`.

`concession.other` requires a non-empty `message` and an
`evidence.alternative`. It exists so that "I did something the skills discourage
and there is no code for it" is still recordable. **Silence is never the
fallback.**

## Typed heal exits

Every attempt ends in exactly one of five exits.

| exit | meaning |
|---|---|
| `healed` | the re-run of the failing stage passed, no concession |
| `healed_with_concessions` | passed, `concessions[]` grew; `disclosed` must flip before anything is claimed |
| `caps_exhausted` | the bound was reached; re-emit as `blocker.caps_exhausted` with `owner: user`, keep attempt details in the record, and report the user-visible impact and next action |
| `blocked` | a blocker was hit; the user is asked and the spec un-approves. Recorded **before** the write-back, with equal hashes |
| `retry_environmental` | the failure was `owner: environment` with supervisor-reported evidence meeting the relay criterion; consumes the retry cap and never a remap or a regenerate |

An `owner: environment` failure whose evidence does *not* meet the relay
criterion is `agent_observed`, lands in `unsettled`, and is **healed as code —
never retried**.

## Materialization is a predicate, not a status

There is no `materialized` value on the spec's `status`. Adding one would put an
outcome back into the IR. It is computed over the lock, this record, and — when
reachable — the live IR:

**Fully materialized** =

- the lock is intact (snapshot present and byte-matching, status `approved`), and
- `record.compiled_from == lock.spec_hash`, and
- the live spec's canonical hash still equals `lock.spec_hash`, and
- stages `s0`–`s6` are `passed` or `passed_with_warnings`; `s7_publish` is one
  of those or `skipped`; `s8_answer` is not `failed`, and
- every concession is `disclosed`, and
- no blocker has `disposition: "blocked"`, and
- every `heal` / `retry` / `remap` attempt has equal hashes.

`evidence.source_state` never appears in it. Merging staleness in would make a
correctly built product read as broken because its input is a day old.

### The distinguishable not-materialized states

`dp_diagnostics.py materialized` returns
`{"materialized": false, "state": …, "why": [...]}`. First match wins, in this
order:

| state | condition | what to do |
|---|---|---|
| `needs_user` | any blocker with `disposition: "blocked"` | **ask** |
| `plan_moved` | live hash ≠ `lock.spec_hash` — the plan changed after the build | **regenerate** |
| `code_wrong` | hashes match and an offline stage (`s0`–`s3`) failed — never environmental | **self-heal** |
| `unsettled` | hashes match, offline green, a stage ≥ `s4` failed with no supervisor-reported evidence | treat as `code_wrong` |
| `environment_suspect` | hashes match, offline green, every failing diagnostic at stage ≥ `s5` is `owner: environment` **and** supervisor-reported | **retry** — the closure is not known-bad |
| `undisclosed_concession` | otherwise green, some `disclosed: false` | **disclose, then re-evaluate** |
| `awaiting_answer` | otherwise green, `s8_answer` failed | **refine** — the build is green and the answer is wrong |
| `in_progress` | a required stage is `not_reached` and nothing failed | continue |

**`needs_user` outranks `plan_moved` deliberately.** The two co-occur on the
commonest path there is: a build-time blocker is written back into
`open_questions`, which moves the live hash *as a consequence of* the blocker.
Reporting `plan_moved` first would tell the agent to regenerate — against a spec
that still carries the unanswered question that stopped the build. `why[]` lists
every matching condition, so the divergence is deprioritized, never hidden.

### Call it `materialized`, never `correct`

This pack is already blunt about why: *SELF-CHECK OK means the closure is
structurally sound and the transform ran, nothing more*, and *never let a green
exit stand in for "the numbers are right"*. `materialized` means the approved
plan was compiled, the compiled artifact ran, and it published. It says nothing
about whether the numbers are right.

## User-facing narration boundary

The record is internal evidence, not a chat transcript. The owning loop renders
workflow and status updates through
[user-facing-language.md](user-facing-language.md): translate the recorded
impact into a plain-language next action, and never copy `message`, `path`,
`code`, or raw tool output into chat. Automatic repairs remain internal;
meaningful progress, approvals or clarifications, declined or cancelled
approval, blockers, concessions, retries, and final outcomes may be
communicated. This boundary changes no record field or classification rule.

Supervisor evidence may retain an internal detail such as
`workflow/validation_failed` for classification and later inspection; it stays
in the record and is not copied into user chat.

Use `materialized`, never `correct`: a green run means the approved plan was
compiled, ran, and published. It does not establish that the numbers are right.

## The CLI

Stdlib-only Python, `--json` everywhere, exit codes `0` ok / `1` findings /
`2` could not read.

```bash
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" hash         <spec.md>
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" canonicalize <spec.md>
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" emit         <canonical.json>
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" schema       [--json|--diagnostic|--record]
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" lock write   <spec.md> <closure-dir> [--proposal <proposal.json>]   # v3 only; rejected for v2
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" lock verify  <closure-dir> [--spec <spec.md>]
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" record init   --record <path> --lock <path> [--spec-report <report.json>]
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" record append --record <path> --stage <id> --from <report.json>
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" record query  --record <path> \
       [--stage …] [--owner …] [--severity …] [--code …] [--unresolved] [--attempt N]
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" materialized  --record <path> [--lock <path>] [--spec <path>]
python3 self_check.py [--json] [--record build-record.json]  # closure root
```

`record query` is the one to reach for instead of re-reading logs: what failed,
at which stage, who owns it, and what changed between attempts are all queries
over this file.

Two verification commands, and both are run at Step 7 because they check
different things:

- `self_check.py` compares the snapshot's **bytes** against
  `lock.snapshot_sha256`. It needs nothing but `hashlib`, which is why it can
  run inside the closure with a minimal interpreter. It emits
  `closure.canonical_hash_deferred` (`info`) naming the other command, so a
  reader can never mistake the byte check for the canonical one.
- `dp_diagnostics.py lock verify` computes the **canonical** hash. With
  `--spec <path>` it compares the live IR, which is the only way `plan_moved`
  becomes visible.

## What is honestly weak in this record

Most of this file is mechanical. Some of it is not, and saying so up front beats
letting a reader assume otherwise.

- **`narrative` is LLM prose, and says so in the file.** It carries
  `origin: "llm_authored"` and a disclaimer field. The "referenced in the source
  but not extracted" judgement, for instance, is the generator's *analysis* —
  neither the approved plan nor a mechanical fact. Consumers must not treat it
  as evidence.
- **Two fields inside `attempts[]` are prose.** `diagnosis.summary` and
  `changed[].what` are the agent's account of what it thought was wrong and what
  it did about it. `diagnosis.code`, `diagnosis.path`, both hashes, `rerun` and
  `exit` are mechanical and checkable; those two are not. The entry's `origin`
  is at entry granularity for schema simplicity, so this paragraph is the
  disclosure.
- **`concessions[].what` / `.why` / `.alternative_rejected` are LLM prose too.**
  The *existence* of a concession and its `code` are checkable; the account of
  it is not.
- **Supervisor tracebacks now have a producer; per-attempt identity does not.**
  In workflow v2, call `mcp__nxd-desktop__inspect_workflow` once with the failed
  workflow and current operation or requirement identity; its bounded diagnostic
  can fill `supervisor_detail` with `origin: "supervisor_reported"` and a
  `tool:inspect_workflow.operation` path. In a feature-off or non-enrolled
  compatibility runtime only, `mcp__nxd-desktop__inspect_run` with the failed
  `run_id` supplies `run.stdout_tail` and the matching
  `tool:inspect_run.run.stdout_tail` path. See [failure-handling.md](failure-handling.md)
  § After a failed supervisor operation: inspect once, then classify.
  **Per-attempt supervisor identity is still `origin: "unbound"`**, as is stage
  attribution: the supervisor emits no `code`/`stage`/`severity`/`owner`, so the
  stage remains an agent inference over supervisor-authored evidence. Those
  fields exist so a producer can bind to them later without a schema change.

The design move is deliberate: **define the schema now, bind the producer
later.** Every field carries its origin, so an agent-inferred value is visibly
weaker evidence than a supervisor-reported one — which is exactly the
fail-closed posture the classification rules require.
