# Context and resume — reattaching to a local desktop product across sessions

## Contents

- [What persists, what dies](#what-persists-what-dies)
- [Is the closure still the plan? — the mechanical check](#is-the-closure-still-the-plan--the-mechanical-check)
- [Reattach playbook — resume first](#reattach-playbook--resume-first)
- [When resume is not possible — rebuild fallback](#when-resume-is-not-possible--rebuild-fallback)
- [Credential recovery for SENSITIVE closures](#credential-recovery-for-sensitive-closures)
- [Optional session ledger](#optional-session-ledger)
- [User-facing narration](#user-facing-narration)

This is the **context** half of the job loop: how a later turn or a later
session recovers a product it did not build in this session. The
task-scheduling half — routing, step order, caps, fan-out — lives in
[scheduling.md](scheduling.md).

This file covers reattachment, not a way around construction controls. For a
new local construction on a runtime that advertises workflow-v2 execution,
follow [workflow-v2.md](workflow-v2.md) and its returned capability, consent,
capture, retained-review, and admission actions. A false or unavailable
capability is a blocker for that construction, not permission to call a legacy
builder.

## What persists, what dies

Draw the line clearly, because the recovery path depends on it:

**Durable (survives the session):**

- The **closure directory** at `…/nxd-jobs/<workflow>/closure/` on the file-writing
  surface — the source copy, `spec.py`, `models.py`, `transform/`, and the
  generated record files: `dp-blueprint.approved.md`, `dp-blueprint.lock.json`,
  `build-record.json`, `README.md`, and — for a v3 closure —
  `dp-blueprint.proposal.approved.json`. This is the one key a later session always
  has.
- The **live `dp-blueprint.md`**, *beside* the closure at
  `…/nxd-jobs/<workflow>/dp-blueprint.md` — the hand-edited plan, with its drafting
  history, rejected options and open questions. It is upstream of the closure,
  not a file inside it. **A job started before v0.38.0 has this file as
  `dp-spec.md`.** Unlike the closure's artifacts, it gets no automatic fallback:
  it is a path you pass, not one a verifier resolves. If `dp-blueprint.md` is
  absent, look for `dp-spec.md` beside the closure and `git mv` / rename it
  before Step 1b — do **not** re-author the plan, which would discard exactly
  the drafting history this file exists to keep.
- The supervisor's **published catalog** — every workflow ever built and
  published on this machine, queryable without booting anything via
  `list_data_products`.
- The **approved spec snapshot** and **`nxd_decisions`** inside the closure — the
  frozen plan and the machine-queryable ruling ledger. The ledger classifies every
  ruling on two axes, `status` (settled?) and `provenance` (authored by whom?), so
  a resuming session can query which rulings a *previous* session authored instead
  of inheriting them as though the user had supplied them.

**Ephemeral (dies with the session):**

- The **`semantic_endpoint`** and **`bearer_token`**. The bearer is minted per
  session, kept only in memory, and never persisted. A remembered endpoint
  without a live token is worthless, and no tool re-mints a token for a token you
  already hold — you re-acquire the pair by resuming.

## Is the closure still the plan? — the mechanical check

A later session does **not** read prose to work out where things stand. The
closure carries its own answer, and the check is two commands.

| file at the closure root | what it is |
|---|---|
| `dp-blueprint.approved.md` | a byte copy of the approved `dp-blueprint.md` this closure was compiled from |
| `dp-blueprint.lock.json` | that copy's v3 canonical hash (v2 for an existing legacy closure), snapshot hash, and compiler version. A closure built before v0.38.0 has this as `dp-spec.lock.json`; both verifiers fall back to that name, and the snapshot and proposal filenames come from inside the lock, so a legacy closure resolves without being renamed |
| `dp-blueprint.proposal.approved.json` | v3 closures: the typed proposal snapshot, byte-hashed into the lock. Phase C fails if it is missing or does not match |
| `build-record.json` | what happened: stages, attempts, concessions, blockers, the read-back |

```bash
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" lock verify <closure> --spec <workflow>/dp-blueprint.md
# --lock resolves a pre-v0.38.0 closure's dp-spec.lock.json on its own; --spec does NOT
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" materialized --record <closure>/build-record.json \
    --lock <closure>/dp-blueprint.lock.json --spec <workflow>/dp-blueprint.md
```

`lock verify` answers *"is this closure's snapshot intact, and does the live
`dp-blueprint.md` still hash to it?"* `materialized` answers *"was the approved plan
compiled, run and published, with nothing still open?"* — and when the answer is
no, it names **which** no. That name is the next move:

| result | what it means | do |
|---|---|---|
| `materialized: true` | the approved plan compiled, ran and published | **resume** — reattach below; there is nothing to rebuild |
| `needs_user` | a blocker is still open in the record | **ask** the one smallest question, then regenerate |
| `plan_moved` | the live `dp-blueprint.md` no longer hashes to the lock | **regenerate** — this closure was built from an older plan |
| `code_wrong` | the plan matches and an offline check failed | **heal the code** — an offline failure is never environmental |
| `unsettled` | the plan matches, a later stage failed, no supervisor-reported evidence | **treat it as `code_wrong`** — fail closed |
| `environment_suspect` | every failing diagnostic is a supervisor-reported environment fault | **retry** — the closure is not known-bad |
| `undisclosed_concession` | otherwise green, but something the user was never told | **tell them**, then re-evaluate |
| `awaiting_answer` | green build, wrong answer | **refine** (Step 6) |
| `in_progress` | a required stage was never reached | **continue** where it stopped |

Neither command reaches that table if `--spec` names a file that is not there,
and a pre-v0.38.0 job is exactly that case: its live IR is still `dp-spec.md`.
The two commands fail differently, so recognize both —

| command | what you see | do |
|---|---|---|
| `materialized` | exits **2**, `could not hash <path>: No such file or directory` on stderr, no verdict | **look for `dp-spec.md`** beside the closure and rename it, then re-run |
| `lock verify` | exits **1**, `closure.live_spec_unparseable` | same |

Only if there is genuinely no plan beside the closure does Step 1b author one.
Re-authoring over a job whose plan is merely under the old name discards the
drafting history, rejected options and open questions that file exists to keep.

Two rules hang off the verdict table. **`plan_moved` is a regenerate, never a heal** —
the closure is not broken, it is stale, and editing generated code to match a
moved plan is the one thing a compiler must never do. And **none of these names
are said out loud**: the user hears "I'm reattaching to it", "the plan changed
since I built this, so I'm rebuilding it", or "I need one thing from you"
([failure-handling.md](failure-handling.md)).

`materialized` also never means *correct*. It means the approved plan compiled,
ran and published — nothing about whether the numbers are right.

## Reattach playbook — resume first

When the user has an existing product but no live endpoint or token — the
typical new session — **reattach, do not rebuild.** The supervisor exposes a
cross-session catalog and a fast re-serve; use them:

1. **List.** Call `mcp__nxd-desktop__list_data_products`. It boots nothing —
   no kernel, no Python runtime, no endpoint — and returns every published
   workflow with its `workflow` id, `definition_id`, `publish_seq`,
   `published_at_unix_ms`, the model tables with row counts, and an
   `artifact_status`. Call this **first** in a fresh session.
2. **Resume.** If the workflow appears with `artifact_status: available`, call
   `mcp__nxd-desktop__resume_data_product` with that `workflow` id (pass it
   verbatim). It reuses the durable published artifact and returns a fresh
   `semantic_endpoint` plus a session `bearer_token` — the same shape the
   compatibility `build_data_product` path returns — in seconds, with **no**
   regeneration. Then
   render the pinned release with `nxd-render-static-artifact` before describing or
   querying it. Every ruling encoded when the product was built is preserved.
3. **Describe, then answer.** Call `mcp__nxd-desktop__describe_models` with the
   returned endpoint/token before mapping any question — the authoritative
   measure and dimension names live there, not in the `list_data_products` table
   (those are lowercase physical `dataset.table` identities, not the semantic
   catalog). Then `run_semantic_query`.

Resuming the **same** workflow again is idempotent — it returns the same live
endpoint. Resuming or building a **different** workflow replaces the current
endpoint, so any earlier one from this session stops answering.

`resume_data_product` can return `supervisor_busy`: another session or a
detached CLI runtime owns the local runtime. Retry shortly, or run
`nxd-desktop-supervisor stop` to release a detached runtime, then resume again.
If it returns `workflow_not_found`, nothing is published under that id — the
error data lists the `available_workflows`; pick one, call `list_data_products`
to re-check, or treat it as a fresh build.

## When resume is not possible — rebuild fallback

Rebuild is the fallback, not the default. Reach for it only when the published
artifact is genuinely gone. On an enrolled workflow-v2 runtime, reset and
reconstruct through the v2 sequence instead of bypassing capture and review.
The `build_data_product` command in this section is retained only for an
explicitly feature-off or non-enrolled compatibility runtime:

- `list_data_products` reports the workflow as `collected` (the published data
  was garbage-collected) or `release_unreadable` (the durable record is
  damaged — see the entry's `error` field), or
- `resume_data_product` returns `artifact_unavailable` (garbage-collected or
  failed integrity revalidation).

In those cases, rebuild from the closure with the **same** definition path and
the **same** workflow id:

```
build_data_product(definition="<abs path to the closure>", workflow="<workflow-id>")
```

Use that legacy command only after confirming the runtime is feature-off or the
workflow is not enrolled in v2; it must not be used to bypass an enrolled
workflow's construction actions. Reusing the workflow id is what makes this a
reopen of one product rather than
the creation of a second one. Rebuild is sound because the closure is
deterministic and embeds its own copy of the source: the rebuilt product carries
identical rulings and identical rows, including any landed decisions model. It
costs a full build; use [user-facing-language.md](user-facing-language.md) for
the user-facing wording.

If the closure path itself is gone, say so and treat the request as a fresh
build from source — do not guess a definition path or probe the filesystem for
candidate closures.

## Credential recovery for SENSITIVE closures

If the closure carries a `SENSITIVE` marker, it reaches a live database or API
through `infra-profile.yaml`, and a rebuild (only rebuild — resume never touches
credentials) can fail two different ways with the same symptom:

- **The file is present** and the rebuild fails at connection time: the
  credential expired or rotated. Ask the user for the new value, replace the
  `value:` entry that `SENSITIVE` names, and rebuild.
- **The file is absent** — the usual case for a closure obtained as a clone or
  copy, because `.gitignore` excludes it by design. This is not a broken
  closure, and you **cannot** reconstruct the file from `SENSITIVE`: that marker
  lists only credential *key names*, while `infra-profile.yaml` also carries the
  non-secret connection topology (host, port, database, schema for a database;
  base URL for an API) and the `duckdb` / `python-compute` / `<connector>-source`
  service skeleton. Ask the user for the whole file — or for the topology plus
  their own credential — and rebuild against `nxd-generate-data-product`'s
  `reference/database-source.md` / `api-source.md`. Never invent a host or a
  credential to fill the gap, and never echo a credential in chat.

## Optional session ledger

For a build that spans turns or sessions you MAY keep a lightweight ledger,
scoped to one workflow under `.context/nxd-jobs/<workflow>/`, the same way
`nxd-build-data-product`'s `reference/state-and-resume.md` scopes a build. It
is optional — a single-turn loop needs none of it — and it holds only what the
durable catalog cannot reconstruct:

- the `workflow` id and the absolute closure path (the durable key pair),
- the last catalog you saw from `describe_models` (so a resumed session can
  detect a changed model), and
- open remap / regenerate TODOs, one row each, never blocking — mirror the
  append-a-TODO discipline in `state-and-resume.md`.

Do not restate attempts, caps, concessions or blockers here: `build-record.json`
already carries them, mechanically and per attempt, and a second hand-written
copy is exactly the drift this design removed.

Never write the bearer token to this or any file. It is a tool parameter only.

## User-facing narration

This file owns the mechanical distinction between resume, rebuild, and the
durable or session-only state. Use [user-facing-language.md](user-facing-language.md)
for the wording:

- Resume is a genuine reattachment to the existing published result. Do not
  describe it as a rebuild.
- Rebuild is a new run from the saved closure and plan. State its real time and
  resource cost; do not describe it as a reattachment.
- The connection is session-only. The saved closure and published catalog are
  what persist. Do not promise that a connection or credential will persist.
- If a saved copy cannot be refreshed while the published data remains
  queryable, use the reference's saved-copy failure wording and keep the two
  states separate.
