# Task scheduling — routing, sequencing, caps, and fan-out

## Contents

- [Route the request before doing work](#route-the-request-before-doing-work)
- [Step order and dependency edges](#step-order-and-dependency-edges)
- [Bounded-loop caps](#bounded-loop-caps)
- [One data product in flight](#one-data-product-in-flight)
- [Subagent fan-out](#subagent-fan-out)
- [Main-thread workflow-v2 scheduling](#main-thread-workflow-v2-scheduling)
- [Main-thread review checkpoint](#main-thread-review-checkpoint)

This is the **task-scheduling** half of the job loop: which path a request
takes, the order the steps run in, what forces a re-run, how many times the loop
may retry, and where independent work may run in parallel. The **context**
half — reattaching to a product across sessions, what persists, what dies — lives
in [context-and-resume.md](context-and-resume.md).

Workflow and status wording is canonical in
[user-facing-language.md](user-facing-language.md). This file defines routing,
sequencing, caps, fan-out, and structured handoff facts; it does not add another
narration policy.

## Route the request before doing work

Classify the request before provisioning, generation, or querying. Prefer the
smallest path that can give an honest answer:

| Situation | Route |
|---|---|
| **Explicit deployed/platform product** — the user names a remote DP, cluster, or platform endpoint | Hand off to `nxd-query-data-product`. Do not create a local replacement. |
| **Existing local product with endpoint/token but no workflow id** | Explicit query-only exception: state that the static artifact is unavailable, then call `describe_models` and answer through `run_semantic_query`. |
| **Existing local product, but no endpoint/token** — the typical new session, since the bearer is per-session and never persisted | **Reattach, don't rebuild.** Use `list_data_products` for discovery only, then `resume_data_product`, render the release with `nxd-render-static-artifact`, then describe/query. A fresh endpoint and bearer arrive in seconds with no regeneration. Rebuild is the fallback only when the published artifact is gone. |
| **Endpoint + bearer + workflow** | Render the pinned static artifact first, then describe/query. The artifact is release-scoped and does not consume the bearer. |
| **Share / hand off a product** — the user wants to give it to another person or machine | Call `mcp__nxd-desktop__export_data_product` with the same `definition` path used to build it. Read-only and on demand — not part of the build/query loop. It zips the closure, strips credentials fail-closed, and emits a guided `IMPORT.md` for the recipient to rebuild. Full playbook: [handoff-export.md](handoff-export.md). |
| **In-scope source data** — attached/exported CSVs, another local file (JSON/JSONL/Parquet), a connected workspace folder, pasted tabular data, a spreadsheet, an accessible live database connection, or an off-mesh REST API the user describes | Preserve the source, infer a model, generate a local closure when no suitable local product exists, then answer through the supervisor. An ordinary single file source may be copied unchanged into the generated closure's required export layout; a database or API source is described (host/URL, credentials-availability, table/endpoint list), never fabricated, and its connection details pass through to generation exactly as the user gave them — **a live credential stays in the owning main thread and is injected host-side**. Never modify a supplied original. |
| **No product and no source** | Ask one concise question naming the missing thing: the local data file/folder or an existing product to query. Do not manufacture a dataset, create a throwaway database, or probe Cowork uploads/workspaces with Bash in hope of finding one. |
| **Trivial, non-durable calculation** — for example, arithmetic over values pasted in the request, with no request to analyze or reuse data | Answer directly. Do not start a supervisor or build a product. |
| **Local analysis requested but runtime unavailable** | Stop before fallback work. State that the local analysis runtime is unavailable, identify the missing MCP connection or host-local runtime prerequisite, and point to `nxd-desktop-setup.sh` / the Desktop connection repair. Do not substitute SQLite, raw SQL, pandas, or shell aggregation. |

Treat ambiguous requests conservatively. If a question could mean either a
one-off calculation or analysis of an unseen source, ask which data or product
should answer it. If data is in scope and the user asks for a recurring,
shareable, or multi-question analysis, prefer the reusable local-product path.

## Step order and dependency edges

The loop runs Steps 1–6 plus Step 4a in `SKILL.md`. The order is not arbitrary — each step
consumes the previous step's artifact, so a change upstream forces the
downstream steps that depend on it to re-run:

```
gather (1)  → infer (2)      (questions + source shape drive the model)
infer (2)   → generate (3)   (the semantic model is placed into the closure)
generate(3) → build (4)      (the closure is the --definition the supervisor pins)
build/resume → artifact (4a) (the pinned release is rendered once, offline)
artifact(4a) → describe/query(5) (release page precedes query vocabulary)
query (5)   → refine (6)     (a wrong answer routes back to 2/3 or stays in 5)
```

When a refine cycle (Step 6) changes an upstream artifact, re-run only the
downstream steps that actually depend on the change, and say which ones before
doing so:

- A **query-level** fix (wrong selection, missing dimension) re-runs Step 5 only;
  it does not rerender the same release.
- A **model/DP-level** fix (missing metric, wrong grain, a new derived model)
  re-runs Steps 2/3 → 4 → 4a → 5 with the **same** workflow id.

After any same-workflow rebuild discard cached resource URIs and the former
current artifact file, render the new publish sequence in Step 4a, then
re-`describe_models` before mapping again — never map against a remembered catalog.

## Bounded-loop caps

The loop is bounded at every level. Keep them all bounded and report
non-convergence rather than looping forever or giving up silently:

- **Query-level remap** (cheapest) — the model is right but the selection was
  wrong or a dimension was missing. Re-describe, re-map, re-query through MCP.
  Cap at **~2 remaps per question**.
- **Model / DP-level regenerate** — the inferred model is wrong, or the question
  needs a column/grain that does not exist yet (a filtered figure, a ratio, a
  monthly rollup, a classification). The latter is a **derived model**, not a
  query tweak: go back to Step 2/3, have `nxd-generate-data-product` materialize the
  ruling, rebuild through MCP with the **same** workflow id. Cap at **~3
  regenerate cycles total**.
- **Environmental retry** — a failure the closure cannot fix, evidenced by a
  supervisor-reported error. Cap at **~3 retries**. It consumes neither of the
  bounds above, which is exactly why it needs one of its own: without it an
  environment fault could retry forever and never reach the user.

**These caps are counted, not estimated.** Every attempt — generate, regenerate,
remap, heal, retry — is appended to the closure's `build-record.json`
`attempts[]` before the re-run, and `caps` in that record carries the bounds
alongside `regenerates_used`, `remaps_used` and `retries_used`. Read them instead
of keeping a tally in your head:

```bash
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" record query --record <closure>/build-record.json --unresolved
```

If the loop does not converge within the caps, keep attempt details in the
record and report the user-facing impact and next action from
[user-facing-language.md](user-facing-language.md). Failure classification and
recovery remain in [failure-handling.md](failure-handling.md); do not turn an
internal retry history into a chat transcript.

## One data product in flight

Keep **one data product in flight at a time** while iterating. This is not a
limit on how many *sources* one data product may have — a data product may draw
on several labeled sources — only on how many products you build or serve at
once.

The supervisor enforces the same shape from its side: a session serves **one
workflow at a time**. Building or resuming a *different* workflow replaces the
current endpoint, so any earlier endpoint from this session stops answering.
Resuming or rebuilding the **same** workflow id is the regenerate/reopen
primitive and returns the same product.

## Subagent fan-out

Independent, read-only work in the loop MAY be dispatched to parallel subagents
so their intermediate reads stay out of the main conversation. This is
**permitted, not required** — a single-source, single-question loop needs none
of it. These are explicit instructions to dispatch **built-in** subagents; do
not add, select, or rely on a custom/plugin agent definition. Fan out only when
the work is genuinely independent:

- **Step 2, multi-source profiling** — when a data product draws on several
  sources, each source's profile (`schema.json`) is independent. In an
  activated workflow-v2 session, the main thread profiles each source and
  carries every source's label forward; do not delegate profiling because the
  main thread owns the workflow-v2 sequence and its policy handoff.
- **Step 5, multi-question answering** — dispatch a built-in read-only query
  subagent for each independent question *only when* the governed
  `describe_models` and `run_semantic_query` MCP tools are available directly
  to that child. Do not pass an endpoint or bearer in its prompt, return, or
  narration. If those tools are not available to the child, the main thread
  runs the governed queries sequentially and presents the rows; it never falls
  back to raw SQL, pandas, or shell aggregation.

Two hard boundaries on fan-out:

- **Build/serve (Step 4) is single-flight and stays on the main thread.** The
  supervisor serves one workflow at a time; concurrent builds would fight over
  the runtime lock and replace each other's endpoints. Never fan out a build.
- **Every subagent inherits the invariants** — no raw SQL / pandas / shell
  aggregation fallback, describe before mapping, label previews as previews. A
  subagent that cannot answer through the governed query returns that, it does
  not reach for a fallback.

## Main-thread workflow-v2 scheduling

Profiling (Step 2) and code generation (Step 3) are the loop's heaviest context
consumers: source sample reads, model inference, and authoring `spec.py` /
`models.py` / `transform/main.py` plus the typed proposal and authored README.
The reserved v3 record files are supervisor capture outputs. None of the
authoring touches the supervisor — it is pure file authoring against a durable
closure directory — but an activated workflow-v2 session keeps
both steps in the owning main conversation. This preserves the thread that owns
the policy, workflow revision, capture, and review relay. No child may perform a
workflow MCP action or return a generation handoff for the main thread to trust.

Do not delegate or fan out Steps 2–3, even when the source is large or there are multiple
sources. The only permitted conversation child in the workflow-v2 construction
path is the single retained-capture review described below.

1. **Main thread: inference.** Invoke `nxd-build-semantic-data-product` in its
   inference mode, profile each source into `schema.json`, derive the semantic
   model into `semantic-model-plan.json`, and surface any result-changing gap.
   Do not write the runnable closure or invoke the generator during this step.
2. **Main thread: the policy read-back.** With the profile in hand, run the
   Step 1a read-back for any result-changing gap — including one the profile
   surfaced — and wait for the user's approval. This user turn is the
   orchestrator's; it never happens inside a subagent.
3. **Main thread: generation.** Invoke `nxd-generate-data-product` with the
   already-computed model and **verbatim approved policy**. It authors the
   executable closure and returns control to the same main thread. If helper
   tools exist, Step 7 checks are optional evidence only; the supervisor
   materializes trusted metadata and checks during capture. The main thread then
   performs host-path verification and capture. If it finds a result-changing
   gap the approved policy does not resolve — either a policy element absent
   from the enumeration or a profiling finding that makes an approved element
   ambiguous or conditional — stop and perform a fresh read-back and
   generation-only bounce in the same workflow; do not delegate the bounce. The
   generation handoff returns `gap_found` when that bounce is required.

**The main thread verifies the handoff path before capture.** Never pass an
unverified path to the supervisor: confirm the authored executable files and
connector inputs resolve on the supervisor's **host** surface. Supervisor
capture verifies the complete closure file set: `spec.py`, `models.py`,
`infra-profile.yaml`, `transform/main.py`, `requirements.txt`,
`dp-blueprint.approved.md`, `dp-blueprint.lock.json`, `build-record.json`,
`README.md`, the connector companion artifact where the type has one — and, for
a credentialed source, `SENSITIVE` and `.gitignore`. Do not require
agent-authored copies of `dp-blueprint.approved.md`,
`dp-blueprint.proposal.approved.json`, `dp-blueprint.lock.json`,
`build-record.json`, or `self_check.py`; supervisor capture materializes and
verifies those reserved surfaces. A path that does not resolve host-side is a
handoff failure, not a capture input.

**Credential boundary — a live credential never enters a conversation child.**
For a database or REST API source, the main thread writes the real credential
into `infra-profile.yaml` only after authoring and before capture;
`SENSITIVE`, `.gitignore`, and `chmod 0600` remain required. A review child
receives only the sanitized request and retained paths, never a credential or
credential-looking value. The live connectivity check runs on the host after
injection and must not be replaced by a child-side dry run.

## Main-thread review checkpoint

Step 3b belongs to the main thread after main-thread generation.
Finish the generator handoff before capture, then never mutate the captured closure.
The activated v2 contract requires exactly one built-in read-only `Agent` or
`Task` review per capture generation over the supervisor-returned retained
paths. The owning/main thread must dispatch that one conversation child (a
`general-purpose` subagent is acceptable); it must not load
`nxd-review-closure` with `Skill` and review inline, and the supervisor never
launches the reviewer. The child prompt carries only the retained review inputs
and sanitized request, tells the child to load the reviewer skill, and includes
the canonical dispatch marker in [workflow-v2.md](workflow-v2.md). There is no
complexity-based skip or mutable-path duplicate.

Preserve every user question and supplied procedure under the
`sanitized_original_request` contract, inventory and replace every credential,
and stop if complete sanitization cannot be established. The reviewer returns
claims only and never edits, builds, serves, transforms, or talks to the user.
The exact marker, 120-second deadline, external `review-record.json` ledger,
bounded `report_requirement` projection, remediation loop, and wire fields are
canonical in [workflow-v2.md](workflow-v2.md) and
[adversarial-review.md](../../nxd-generate-data-product/reference/adversarial-review.md).

An accepted behavior-changing finding requires reset, local correction, optional
agent-side evidence when tools exist, recapture, and one fresh review for the new generation. Evidence
from sibling captures or abandoned workflows never combines. Step 4 proceeds
only when the supervisor reports the review requirement satisfied and returns
the validation action; no local ledger or self-check asserts completion.
