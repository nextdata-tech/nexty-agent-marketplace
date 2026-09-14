# Closure record surfaces — snapshot, lock, build record, README

## Contents

- [What the generator emits, and why](#what-the-generator-emits-and-why)
- [1. Byte-copy the approved spec](#1-byte-copy-the-approved-spec)
- [2. Write the lock](#2-write-the-lock)
- [3. Open the build record](#3-open-the-build-record)
- [4. Render `README.md`](#4-render-readmemd)
- [Required-capture fields: the plan half and the observed half](#required-capture-fields-the-plan-half-and-the-observed-half)
- [`contracts/<name>.md` — one per model still to build](#contractsnamemd--one-per-model-still-to-build)

## What the generator emits, and why

`dp-blueprint.md` is the plan, and it lives **beside** the closure: hand-edited, with
its drafting history, its rejected options and its open questions. The closure
needs the plan too — a cold reader, an export handoff or a later session has only
the closure — but it must never *depend on a file outside itself* to get it. For
workflow-v2, the supervisor copies and hashes the approved spec during capture.

For a new local construction, generation is not admission. When the connected
desktop runtime advertises workflow-v2 execution, the owning job loop must use
`get_workflow_capabilities` → `prepare_workflow` → consent → capture → retained
review → `start_requirement` → `start_run` as described in
`nxd-run-job-loop/reference/workflow-v2.md`. A generated closure or a local
self-check cannot bypass that sequence.

| file | what it is | written by |
|---|---|---|
| `dp-blueprint.approved.md` | byte-identical copy of the approved `dp-blueprint.md` | supervisor capture |
| `dp-blueprint.proposal.approved.json` | exact typed interpretation approved by the user (v3 only) | supervisor capture from inline `typed_proposal` |
| `dp-blueprint.lock.json` | its canonical hash, snapshot hash, and compiler version | supervisor capture |
| `build-record.json` | what happened: stages, attempts, concessions, blockers | supervisor capture |
| `README.md` | the reopen recipe, and a credentials block when one is needed | this skill, from the template below |
| `contracts/<name>.md` | the contract for a model still to be built | this skill, from the template below |

Nothing under `closure/` is hand-authored plan text or reserved metadata. The
shellless agent must not run the materialization commands below before capture;
the supervisor owns the reserved snapshots, hashes, lock, record, and trusted
checker. Self-containment used to be
a prose discipline — *copy the rulings in, never point at the spec* — enforced by
nothing at all. It is now a **hash-checkable snapshot**: the self-check compares
the copy's bytes against `lock.snapshot_sha256`, so a plan edited inside the
closure after approval is *detected* rather than trusted.

The normative schema for `build-record.json` and for the diagnostic record every
stage emits is **nxd-run-job-loop**'s `reference/build-record.md`. Read it there.
This file is only the emission procedure; restating a schema in two places is how
two schemas start to differ.

## 1. Byte-copy the approved spec (supervisor capture implementation)

```bash
cp "…/nxd-jobs/<workflow>/dp-blueprint.md" "<closure>/dp-blueprint.approved.md"
```

**Byte-identical, never re-serialized.** No reformatting, no re-wrapping, no
normalization, no "while I am here" fix. The snapshot is evidence, and evidence
rewritten on the way in cannot be compared against the hash computed from it.

Three preconditions, all hard:

- The live spec's `status:` is `approved`. Copying a `proposed` spec would
  certify a plan the user never approved. `lock.spec_status_at_copy` records what
  was true at copy time, and the self-check fails a snapshot that was not.
- `"$JOB_HELPER_DIR/scripts/validate_dp_spec.py"` passes against that spec. A spec that does not
  validate is not a settled plan.
- The copy happens **after** the approval read-back gate, at generation. That is
  precisely what keeps the gate's bright line intact — "nothing under `closure/`"
  before approval still holds, because there is nothing under `closure/` yet.

## 2. Write the lock (supervisor capture implementation)

```bash
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" lock write <spec.md> <closure-dir> \
  --proposal <workflow>/dp-blueprint.proposal.json  # v3 only: required there, rejected for v2
```

`dp-blueprint.lock.json` carries the v3 canonical `spec_hash` and typed proposal hash
for new prose-first plans (or the v2 canonical `spec_hash` for an existing
closure), raw
`snapshot_sha256`, `spec_status_at_copy`, and compiler version. It deliberately
stores **no path back to the live IR**: the workflow id plus the
`…/nxd-jobs/<workflow>/dp-blueprint.md` convention recovers it.

What the hash buys, concretely: regeneration is skippable when nothing changed;
*"once approved, the spec is frozen for that build"* stops being honour-system
and becomes tamper-evident; provenance is recoverable; and the regenerate cap
becomes countable rather than estimated. Source **data** staleness is a wholly
separate axis — it lives in `build-record.json` `evidence.source_state` and never
merges with this one, because a correctly built product whose input is a day old
is not a broken product.

## 3. Open the build record (supervisor capture implementation)

```bash
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" record init \
    --record <closure>/build-record.json \
    --lock   <closure>/dp-blueprint.lock.json
```

Run it **before** the self-check: Phase C checks that the record exists and that
its `compiled_from` equals `lock.spec_hash`, so the record has to be there first.
`record init` writes the envelope, `compiled_from`, `compiler_version` and every
stage as not-yet-reached — except the spec stage, which it fills in the same pass
by validating **the snapshot** (the bytes the closure was compiled from), not the
live IR.

From here the record is **generated, never hand-authored**.
`self_check.py --record build-record.json` merges its own stages; the loop
appends the supervisor's validation, run, publication and query stages as they
happen; and every heal, regenerate, remap and retry appends to `attempts[]`
*before* the re-run. Under workflow v2, adversarial review claims,
adjudications and user decisions belong in the adjacent job-level
`review-record.json`, never in the captured closure's record; see
`nxd-run-job-loop/reference/build-record.md`.
There is no section for you to fill in, and no prose to keep in sync — which is
the whole point: the outcomes are a pure product of the build, so nobody should
be transcribing them.

## 4. Render `README.md`

`README.md` at the closure root carries **exactly two things**: the reopen recipe
and, when a source holds live credentials, the credentials block. No plan
sections, no outcomes, no rulings, nothing to hand-copy — roughly twenty lines.
The self-check gates its existence because the reopen recipe is the one thing a
cold reader needs that is neither plan nor outcome. Write it so it stands alone:
a cold reader may not have this skill loaded.

```markdown
# <dp-name>

Workflow id:   <workflow-id>
Closure path:  <abs path to this dir>
(Together these are the durable key across sessions. Bearer tokens never persist.)

The plan this closure was built from is `dp-blueprint.approved.md`, bound by
`dp-blueprint.lock.json`. What happened while building it is `build-record.json`.

## Reopen

Reattach first. A rebuild is the fallback, taken only when the published
artifact is genuinely gone.

For an enrolled workflow on a runtime with workflow-v2 execution enabled, use
the returned v2 actions and never call `build_data_product` for new
construction. The legacy rebuild shown below is compatibility-only for an
explicitly feature-off or non-enrolled runtime. If a new construction is
v2-capable but a capability or enrollment check fails, stop and report that
blocker rather than bypassing capture and review.

For a multi-source CSV/file closure that uses directory companion declarations,
use a desktop supervisor with directory-companion support before step 1. Do not
fall back to an undeclared export root.

1. `list_data_products` — is this workflow published, and is `artifact_status`
   `available`?
2. `resume_data_product(workflow="<workflow-id>")` — reattaches to the published
   artifact in seconds and returns a fresh endpoint and bearer, with no rebuild.
   In a feature-off/non-enrolled compatibility runtime, fallback only on
   `collected` / `artifact_unavailable`:
   `build_data_product(definition="<abs path to this dir>", workflow="<workflow-id>")`
   — a full rebuild, sound because this closure is deterministic and embeds its
   source.
3. `describe_models` — with the endpoint and bearer the resume (or rebuild) returned.
4. `run_semantic_query` — same endpoint and bearer.

## Credentials (omit this section entirely when there are none)

This closure holds a live credential in plaintext.
- File: `infra-profile.yaml` — service <service-name>, keys: <key names only>
- Rotate: replace the `value:` entries and rebuild the data product.
- Do not commit or hand-zip this directory with the credential intact.
  `.gitignore` covers `infra-profile.yaml`. To share the product, use the
  supervisor's `export_data_product` tool: it strips every credential fail-closed
  and emits an `IMPORT.md` naming what the recipient must refill.
```

Omit the credentials section **entirely** for a file/CSV closure — an empty "no
credentials here" line invites a later editor to fill it in. And never a
credential **value**, here or in any other generated file: keys and paths only.

## Required-capture fields: the plan half and the observed half

A source field a downstream model, gate or verdict **consumes** is
required-capture. Find them by reading backward from every derived model and
every gating/verdict rule to the source fields they read. The concept has two
halves, and after this change they live in different files:

- **The plan half** is declared in the spec —
  `models[].fields[].required_capture: true` in `dp-blueprint.md`, carried into the
  closure by the byte copy. It is part of what the user approved.
- **The observed half** is generated — `build-record.json`
  `evidence.required_capture`, one entry per field with `required_by`,
  `missing_rows` and `sample_keys`. Nobody authors it.

"Referenced in the source but not extracted" is an **incomplete extraction**, not
a valid missing value: record which rows lack it and surface it for recovery,
never pass it along as absent. A missing required field disables the downstream
step **without erroring** — it runs, produces nothing, and no assert fires, which
is exactly why nothing else catches it. The scoring-side consequence
(`listed_uncaptured` versus `not_stated`) is in
[derived-models.md](derived-models.md).

## `contracts/<name>.md` — one per model still to build

The naming invariant requires every promised model to appear in
`PHYSICAL_MODELS`, so a promised-but-unbuilt model fails Phase A. When the logic
is genuinely deferred to a later session the model is therefore **not yet
`.promise`d**, the spec marks its entry `deferred: true`, and its contract is
carried as `contracts/<name>.md` inside the closure. That file is what makes the
model buildable by a cold reader; `.promise` it only once it is authored.

**Prefer authoring the inert derived model instead** — its `semantic_model`, an
empty-bodied `@dlt.resource`, an entry in `PHYSICAL_MODELS`, and the contract
expressed as schema plus Step-3b asserts. That shape is executable *and*
satisfies the naming invariant. Use the contract file only when the logic cannot
be written yet.

Either way the contract resolves **inside** the closure. A `../`-rooted pointer
to a design doc outside it is a dangling reference the moment the closure is
exported or moved, and the self-check fails it.

```markdown
# contract — <derived-model-name>

## inputs
<which base/derived models and columns this model reads>

## rule
<the input→output mapping: the derivation, gates, thresholds, calibration —
everything a later session needs to author the transform, with nothing left to
re-derive>

## output schema
<the columns this model must promise, with types — what models.py declares>

## verdict set
<the allowed values for any classification/verdict column; omit if none>
```
