# The pre-handoff self-check script

## Contents

- What the phases are
- What this script does NOT cover
- The JSON report and the build record
- Where an expected value may come from
- The script
- Reading a failure

## What the phases are

The dry-run for Step 7 of nxd-generate-data-product, in six phases. They run in
the order **A → E → G → B → C → D**, not in name order — E and G both run before
B because B executes the transform, and a verdict about what the transform may
reach or spend is worthless once it already has. That ordering is the point of
those two phases rather than an accident of how they were added.

- **Phase A — structural check of `models.py` and `spec.py`.** Parses both files
  with `ast` and checks them against the pinned DSL surface in
  [reference/nxd-spec-api.md](nxd-spec-api.md). Nothing is imported and nothing
  is executed, because the `nxd` wheel is an internal package on a private index
  and is NOT installable here — a real import raises
  `ModuleNotFoundError: No module named 'nxd'`.
- **Phase E — reach gate.** Static, import-level scan of `transform/main.py`,
  plus a model-SDK-only scan of every `contracts/**/*.py` verifier, enforcing that **the transform never calls a listed model SDK** and
  reaches the network only through the connector `spec.py` declares. **It runs
  before Phase B, and that ordering is the gate.** Phase B imports
  `transform.main` and calls `ingest(...)`; a scan sitting in Phase D's position
  would report "denied" after the transform had already opened the socket,
  already called the model, already spent the money. The decision and its
  `sys.exit(1)` both precede the import. Two families of finding: a
  **connector-shape mismatch** (importing `dlt.sources.rest_api` while declaring
  only `csv-source` — reading from a source the closure's own spec does not
  name), and **import reach** (a model-provider SDK, or raw transport with no
  network-shaped connector declared). Both are subject to one exception: a
  closure that declares `api-source` or `db-source` is waived on the transport
  family, because dlt's REST and SQL sources are built on
  `httpx`/`requests`/`urllib3` and a rule that fires on every correct API closure
  gets deleted rather than obeyed. The SDK denial is *not* waived by it — no
  connector type licenses calling a model from a transform. The declared type is
  read from `spec.py`'s **string literals via the AST**, never from its source
  text: a service path inside a `#` comment or a docstring is not a declaration
  and must not collect the waiver, and reading nodes rather than text also makes
  quote style irrelevant and lets the absolute `https://…/infra-profile/…` form
  parse. When *no* service reference can be read at all — an unparseable or
  silent `spec.py` — the connector is **unknown**, not "none": the transport
  check warns and is skipped rather than denying every transport on the strength
  of a parse failure. The SDK denial still applies, because it is waived by
  nothing. This invariant was prose-only until now, on the belief that the
  desktop venv was closed; it is not (`requests`, `httpx`, `httpcore`, `urllib3`
  all arrive transitively via `dlt` and `mcp`; `urllib` and `socket` are stdlib),
  so a closure could call a model and nothing structural stopped it.
  **What it cannot see is in "What this script does NOT cover" — read that
  before you treat a green Phase E as proof the transform is offline.**
- **Phase G — consent gate.** Static, and inert on the closures that do not
  import the field-mapper harness as `nxd.experimental.field_mapper`. When one
  does, it fails the closure unless `contracts/` carries a grant binding each
  mapper spec kept there **by hash**, so a rubric edited after consent was given
  stops matching and the user is asked again. A closure that instead *vendors* a
  copy of the harness is denied outright as `grant.vendored_harness` — no grant
  rescues that, which is the point of denying it by name rather than routing it
  through the grant oracle. It runs here, before Phase B, for the same reason Phase E does
  and one of its own: Phase B *executes* the transform, and a mapper transform
  with a resolvable key spends real money there, so a consent verdict delivered
  afterwards would describe consent already spent. **What it cannot see is in
  "What Phase G cannot see"** — a green Phase G is not proof that no unconsented
  mapping happened. This is a static artifact-binding check, not a protected
  Desktop human-authorization check: the current supervisor must independently
  admit the mapper build before it can create a run.
- **Phase B — dry-run of the transform** against a scratch DuckDB: the
  supervisor's execution minus the kernel. When the transform declares
  `transform_state`, the helper invokes it a second time against the same
  scratch database after a strict JSON state fold, and requires unchanged table
  materialization and row counts. The first run's row counts remain the only
  Phase B count evidence; the second run is a state-boundary verification.
- **Phase C — closure-record gate** (Step 6a). A closure can be structurally
  valid and still be an insufficient handoff, and this is the phase that catches
  it. What changed with the snapshot design: sufficiency is now a
  **hash-checkable property**, not a prose discipline. The approved plan is
  byte-copied into the closure at generation as `dp-blueprint.approved.md`, and the
  phase checks:
  - `dp-blueprint.approved.md` is present at the closure root;
  - `dp-blueprint.lock.json` is present, parses, and carries schema
    the matching `nxd-dp-spec-lock-v2` or `nxd-dp-spec-lock-v3` envelope;
  - the snapshot's raw bytes hash to the lock's `snapshot_sha256` — the **tamper
    check**. The snapshot is evidence, and evidence edited after it was written
    is not evidence. This is the mechanical half of "once approved, the spec is
    frozen for that build", which used to be honour-system;
  - the lock records `spec_status_at_copy: approved` — a snapshot of an
    unapproved spec is a build nobody signed off;
  - `build-record.json` is present, parses, carries schema
    `nxd-build-record-v2`, and its `compiled_from` equals the lock's
    `spec_hash` — the record must describe a build of *this* plan;
  - `README.md` is present (the reopen recipe and the credential key names —
    the one thing a cold reader needs that is neither plan nor outcome);
  - no closure file references a contract/design doc by a `../`-rooted path
    that escapes the closure. The snapshot **is** scanned: a `../`-rooted
    reference inside the approved plan is exactly the dangling pointer this
    design removes. No carve-out is needed anywhere, because the plan is
    *copied* rather than *pointed at*;
  - and — when `infra-profile.yaml` carries a populated `attributes:` list, i.e.
    a live credential in plaintext — that `.gitignore` and `SENSITIVE` exist and
    that `.gitignore` names `infra-profile.yaml`. Unchanged. The credential check
    reports missing FILES only; it never reads or echoes an attribute value,
    because a check that prints the secret it found turns a contained file leak
    into a transcript leak.

  Phase C checks the snapshot's **bytes**, while the shared canonicalizer
  (v2 or v3, dispatched on `dp_spec_version`) computes
  the canonical hash without a YAML dependency. Step 7 still runs
  `python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" lock verify <closure> --spec
  <dp-blueprint.md>` to compare the live IR and the approved snapshot, and Phase C
  emits an informational diagnostic naming that command so a reader of the JSON
  can never mistake the two checks.
  (Note: the naming invariant that Phase A enforces requires every physical
  model to be either a required `.promise(model)` or an explicitly listed
  `OPTIONAL_EMPTY_MODELS` entry registered with `.model(model)`. Optionality is
  only for physical absence after a zero-row resource; it is not a deferred
  contract. A deferred contract belongs to a model not yet exposed, carried in
  `contracts/<name>.md` — see Step 6a — until the model is authored.)
- **Phase D — policy-boundary gate.** Checks that rulings are landed data the
  user can edit, not literals in transform code: `nxd_decisions`, if promised, is
  a **base** model backed by `data/` with a `status` column **and a `provenance`
  column**, each restricted to its fixed vocabulary, and no parameter in a landed
  policy CSV is duplicated as a literal in `transform/main.py`. A ledger
  generated from a Python literal *describes* the code instead of driving it —
  editing a row changes nothing, and the two silently diverge. `status` and
  `provenance` are checked independently because they answer different questions
  — *settled?* and *authored by whom?* — and a ledger carrying only the first
  cannot distinguish a weight the user supplied from a threshold you invented to
  make an underspecified rubric executable. The prose rules this enforces are in
  [derivation-plan.md](derivation-plan.md); Phase D is what makes them fire.

After the phases, the script prints a **distribution read-back** over every
derived model's classification columns. It is not a phase and it never fails the
run — it exists so a fabricated gate or verdict is visible rather than hidden
behind a green exit. Relay it (see "Reading a failure"). With `--record` it is
also written to the build record as data, because a read-back that only ever
existed in a scrollback is a read-back nobody can check later.

One script, one command, one exit code. What to check and how to read a failure
is in SKILL.md; the installed `nxd-run-job-loop/scripts/self_check.py` is the
runnable script, and this file is its operating contract.

The success banner says `C (context-completeness)` and that label is now a
deliberate half-truth: Phase C's meaning changed with the snapshot design, but
the line is keyed on byte-for-byte by `evals/run.py`'s deterministic check and by
every scenario checker, so the string is frozen and the script carries a comment
at the emitting line saying so. Accuracy of a label loses to stability of a
contract. Do not "fix" it.

## What this script does NOT cover

Read this before you trust a green result. Phase A is a *structural* check, not
a runtime one, and the gap is real:

- **It checks signatures against a pinned surface (`nxd` v0.41.139), not against
  the installed wheel.** If the DSL has drifted since that pin, Phase A enforces
  the pin, not reality. Re-derive the changed signature and update
  `nxd-spec-api.md` and this script together — the drift protocol is in that
  file's "Version pin and drift" section.
- **It cannot execute the builders.** Everything `nxd.spec` validates at build
  time is unexercised: value-shape errors inside a role blob, `FieldRef`
  resolution for `metric(of=...)`, and the supervisor's spec→YAML compilation at
  create time. **A closure can pass Phase A in full and still fail when the
  supervisor pins it.**
- **It cannot see dynamic constructs.** A schema entry built from a variable, a
  comprehension, or a `**` spread is reported as `unverified:
  <model>.<column>` and is NOT checked. The printed unverified list is the
  honest scope boundary — read it.
- **It says nothing about whether the semantics are right.** Whether a column
  should be a dimension, whether a metric's aggregation answers the question,
  whether the key is the real grain — no static check can know. Only closed-set
  violations (misspelled kwargs, unknown `Agg` members, unknown dtypes,
  misplaced roles, broken naming invariant) fail.

Phase B executes for real, so what it reports is what will happen — but it
covers only `transform/main.py`. A green Phase B says nothing about `spec.py` or
`models.py`, and the `unverified:` list is its own declared blind spot. Both are
recorded rather than merely printed: the blind spot goes into the report as
`struct.unverified` diagnostics, so it survives the scrollback.

Phase C's byte check is not the canonical hash. It answers *"was the in-closure
copy edited after it was written?"*, not *"did the plan change?"* — that second
question compares the live `dp-blueprint.md` against the lock and needs
`dp_diagnostics.py lock verify --spec`, which Step 7 runs separately.

## The JSON report and the build record

The default output is unchanged prose, and it must stay that way — `evals/run.py`
and every scenario checker key on those lines. Two flags add machine-readable
output on top of it:

```bash
python self_check.py --json                              # report to stdout
python self_check.py --json --record build-record.json   # and merge stages 1-3
```

- **`--json`** prints exactly one `nxd-diagnostic-report-v2` object and nothing
  else. The prose is suppressed, so stdout is parseable in full. Every phase
  emits the same `nxd-diagnostic-v2` shape — `{schema, stage, code, severity,
  owner, origin, path, message, evidence, fix?}` — differing only in which stage
  produced it: `s1_structure` (Phase A), `s2_transform` (Phase B and the
  read-back), `s3_closure` (Phases C and D, which share a stage because both are
  offline, both are agent-owned, and both self-heal; the `code` and `path` tell
  them apart).
- **`--record <path>`** merges those three stages into an existing
  `build-record.json` in place, along with the read-back and the Phase B row
  counts. The record is created by `dp_diagnostics.py record init` **before**
  this script runs — Phase C gates its presence — so a missing record is a real
  fault and is reported, never papered over by writing a fresh one here.

Exit codes: `0` clean, `1` a phase found something, `2` the closure could not be
read at all. The third is separate on purpose — "I looked and it is wrong" and "I
could not look" are different results, and a traceback for the second reads as a
broken checker rather than a closure you are standing outside of.

Three properties are worth knowing because they are what make the report
trustworthy rather than decorative:

- **A phase that did not run is `not_reached`, never `passed`.** The script still
  stops at the first failing phase — Phase B cannot run over malformed code — but
  under `--json` it emits the report *before* exiting, and the phases that never
  ran carry `status: "not_reached"` plus a `meta.stage_not_reached` diagnostic. A
  phase that produced no signal at all did not agree with you.
- **`severity` does not decide who hears about it; `owner` does.** Everything
  this script emits is `owner: agent` — structural faults and user-code runtime
  faults are yours to self-heal — except `closure.lock_status_not_approved` and
  the three consent codes `grant.missing`, `grant.spec_mismatch` and
  `grant.expired`, which are `owner: user`. Only the user can approve a spec,
  and only the user can consent to sending the closure's content to a model: you
  cannot author a grant on their behalf, decide that a drifted rubric is still
  acceptable, or extend an expiry. The agent absorbs the rest and reports one
  plain line of outcome.
- **Phase B row counts are recorded under their own key, `phase_b_row_counts`,
  and are never merged with published counts.** One is a dry run against a
  temporary database; the other is what shipped. Collapsing them would let a
  scratch count stand in as evidence that the product has rows.
- **Optional physical tables are absent by design when their resource yields no
  rows.** Phase A requires `OPTIONAL_EMPTY_MODELS` to be a literal subset of
  `PHYSICAL_MODELS`, rejects optional models promised with `.promise(...)`, and
  requires them to be registered with `.model(...)`. Phase B records an absent
  optional table as zero rows and skips it in the distribution read-back; a
  missing required table still emits `runtime.model_table_missing`. A catalog
  entry remains visible through `describe_models`, but a semantic query whose
  physical source is absent must surface that unavailable table rather than
  fabricate a zero or a placeholder record. When rows materialize, the same
  model and view are queryable without changing `spec.py`.

This script is copied into the closure and run there with a bare interpreter, so
it **cannot import `"$JOB_HELPER_DIR/scripts/dp_diagnostics.py"`**. The code table at the top is an
inlined literal subset of that module's registry, and
`evals/tests/test_self_check_diagnostic_vocab.py` is what stops the two drifting.
Sharing the code by import would be wrong even where it is possible.

**What Phase E cannot see.** It is an *import-level* check over the closure's
executed Python: `transform/main.py`, plus every `contracts/**/*.py` verifier for
the model-SDK family only. A green Phase E means **no *listed* denied module name
appears in an `import` statement in those files** — nothing stronger. It is not a
sandbox, it does not mean the transform is offline, and it is not a claim that no
model was called. Specifically:

- **The deny list is enumerated, so it is not exhaustive.** It names the SDKs an
  author actually reaches for — `anthropic`, `anthropic_bedrock`, `openai`,
  `cohere`, `mistralai`, `ollama`, `groq`, `together`, `replicate`,
  `google.generativeai`, `google.genai`, `vertexai`, `litellm`,
  `huggingface_hub`, `langchain_anthropic`, `langchain_openai`, `llama_index` —
  plus the raw transports. A provider shipping under a name nobody added here
  passes. Report a green Phase E as "no *listed* model-provider SDK", never as
  "no model call".
- **A green Phase E is not "this closure does not infer", and is not meant to
  be.** A packaged closure that infers does it through
  `nxd.experimental.field_mapper` under Phase G's consent grant — that is the
  sanctioned seam, and Phase E passes it by design (the harness's own
  `import anthropic` is function-local and invisible to an import-level walk).
  What Phase E denies is a *raw* provider SDK in the transform, which reaches a
  model while routing around the grant check, the supervisor approval boundary
  and the sanitized credential handling. Denied-here and inferring-legitimately
  are different questions; Phase G answers the second.
- **`subprocess` and `os.popen` are a documented gap, by decision — not a rule.**
  `subprocess.run(["curl", …])` reaches anything. Neither spelling is denied:
  `os` is imported by nearly every correct transform for `os.path`, so denying
  that root would fire on almost every valid closure, while `os.popen` and
  `os.system` are attribute accesses an import-level check cannot see at all.
  Denying `subprocess` alone would stop the naive spelling and miss the other
  three, which reads as coverage the gate does not have. Recorded as a hole
  rather than half-closed.
- **`dlt.sources.filesystem` is not a shape fingerprint, by decision.** The
  connector-shape family keys on the two NETWORK-shaped verticals — `rest_api`
  and `sql_database` — so an api-source or db-source closure importing
  `filesystem` reads local files its spec never declared and passes. A
  `filesystem` import is far more likely to be incidental than a `rest_api` one,
  and a rule that fires on correct closures gets deleted rather than obeyed.
- **The bare stdlib parents `http` and `urllib` are a documented gap, for the
  same reason.** `http.client` and `urllib.request` are denied, but `import http`
  emits only `"http"`, which does not match either — and dlt's own dependency
  chain has already loaded the submodule, so `http.client.HTTPSConnection(…)`
  works off the parent import alone. Denying the bare roots is not available:
  `denied_hit` prefix-matches on dot boundaries, so `urllib` would also catch
  `urllib.parse`, which is pure string manipulation with no network and is used
  by shipped example transforms (`company_dividends`, `wttr_loader`,
  `customer_purchases`). A rule that fires on correct closures gets deleted
  rather than obeyed. Denying `http` alone would close one spelling of two and
  read as coverage the gate does not have.
- **`mcp` is an open transport, by decision — and it is the widest hole here.**
  It is not flagged, because `mcp` ships in the fixed desktop venv and is how a
  closure talks to the supervisor; denying it would fail closures doing exactly
  what the platform intends. But `mcp` is a general-purpose client library that
  connects to *any* server it is pointed at, including a model endpoint, and it
  is the reason `httpx` is in the venv at all. Permitting `mcp` permits
  everything `mcp` can reach.
- **Reach that is not an import.** `pandas.read_json("https://…")` and a DuckDB
  `INSTALL httpfs; SELECT * FROM 'https://…'` both fetch over the network with no
  denied import anywhere. Neither is visible at the import layer and neither is
  fixable there. Out of scope, and knowingly so.
- **Reach behind a wrapper.** A helper module in the closure that opens a
  `socket` and is imported by `transform/main.py` as a local name is invisible:
  Phase E scans the transform and the verifiers as *text*, and does not follow
  imports or reason about what a function does. It is a name check, not semantic
  detection.
- **Contract verifiers are scanned for model SDKs only — not for transport, by
  decision.** A verifier under `contracts/**/*.py` runs on the local desktop runtime
  after the data has landed, so a model SDK there is the same risk as one in the
  transform and is denied identically and unconditionally. Raw transport is *not*
  checked in verifiers: the `network_declared` waiver is derived from `spec.py`'s
  connector declaration, which describes how the **transform** gets its data, and
  handing that waiver to a verifier would grant reach the declaration never
  claimed. Denying transport in verifiers outright is the other half of the
  choice and is not taken here. Recorded as a hole rather than half-closed —
  a verifier importing `httpx` passes Phase E.
- **`importlib`, `__import__`, or a name built at runtime.** A dynamic import is
  not an `ast.Import` node.
- **An allowed import used for a denied purpose.** A declared `api-source` is
  waived on `httpx`; that waiver is unconditional once declared, so a closure
  that declares `api-source` and points `httpx` at a model endpoint passes.
- **A forged declaration.** The connector type comes from `spec.py`, which the
  same generation pass authors as the transform. Nothing independent stamps it.
  The shape check therefore verifies *self-consistency between two files the same
  author wrote* — it catches the closure that drifted, not the one that lied.
  (Reading the declaration from the AST closes a *different* hole: a service path
  in a comment is not a declaration at all. It does nothing about a spec.py that
  declares `api-source` and means it.)

Phase E raises the cost of reaching a model from *nothing* to *deliberately
routing around a named check*. That is what it is worth; do not report it as
more.

**What Phase G cannot see.** Phase G is the *consent* gate, and it is a
different question from Phase E's. Phase E asks whether the transform reaches a
model at all; Phase G applies only to the one sanctioned way it may — a closure
that **imports the field-mapper harness** from the installed package as
`nxd.experimental.field_mapper` — and asks whether a grant under `contracts/`
binds each mapper spec found there. A closure that imports nothing under that
path never triggers it, and `phase G ok` says so in as many words. The retired
spelling — a harness copied into the closure root and imported as
`import field_mapper` — is not a route into this gate either; it is denied by
name as `grant.vendored_harness`, described below.
`nxd.experimental.field_mapper` is deliberately **not** in Phase E's
`MODEL_ROOTS`: it would fail every legitimate mapper closure, and the two gates
answer different questions. Green Phase G means a binding consent artifact
exists. In a Desktop supervisor flow it does **not** prove a human
authorization: the static grant/request files are untrusted scope proposals,
and only protected supervisor confirmation can admit a run. Specifically not:

- **The hash is computed by the harness, not by this gate.** Phase G
  subprocesses `python -m nxd.experimental.field_mapper spec-id` rather than
  reimplementing the binding, because two copies of a consent rule is how a
  gate ends up enforcing something other than what it claims. The copy that
  answers here is the copy the transform imports under Phase B, so the binding
  and the mapping cannot disagree with each other. **That consistency is not
  integrity.** The oracle runs as a subprocess with the closure root on
  `sys.path`, so a closure that ships its own `nxd/experimental/field_mapper/`
  at its root shadows the installed package in BOTH places and answers for its
  own spec under whatever rule it likes. Self-attestation survived the harness
  moving out of the closure; it now takes a directory rather than an edit.
  What also remains is spec authorship: the gate binds whatever spec the
  closure presents. Phase G catches the closure that **drifted** — the spec
  edited after the user said yes, which is the common failure — never the one
  that **lied** by minting its own spec/grant pair.
- **Binding is to the specs on disk, not to the spec the call passes.** Phase G
  hashes every spec-shaped JSON under `contracts/` and demands a grant for each;
  nothing inspects which spec path the transform actually hands to `map_inputs`.
  A closure carrying a granted decoy spec under `contracts/` while its transform
  loads a different spec passes this gate. At run time the harness's own
  `Grant.check` still compares the running spec's hash against the grant it is
  handed — that is where an honest closure fails, and a closure minting its own
  spec/grant pair is the "lied, not drifted" case no static gate here catches.
- **The trigger is an import-level name, in any module under `transform/` or at
  the closure root.** The scan walks `transform/**/*.py` plus the closure root's
  own `*.py`, so moving the import into a helper module — under `transform/` or
  beside `models.py` — is not an exemption. The root glob is non-recursive (a
  full-tree walk buys little here and costs a walk of every `data/` and
  `contracts/` subtree on every run), so an import inside a root *subpackage* —
  `helpers/util.py`, with the transform doing `import helpers.util` — does not
  fire the gate. Unlike the routes below, that is an ordinary refactor rather
  than a closure that lied, which makes it the one hole here a careful author
  could reach by accident. An
  `importlib.import_module("nxd.experimental.field_mapper")`,
  `transport.py`'s body pasted inline, or a **copy of the harness vendored
  under a name the gate does not know** never fires this gate either. Note the
  scope of that last one, which changed: the RETIRED spelling — a
  `field_mapper/` at the closure root imported as `import field_mapper`, which
  was the sanctioned contract before the harness shipped inside `nxd` — IS
  detected now, and denied by name as `grant.vendored_harness`. What stays
  undetected is the same copy under any OTHER name, because this gate matches
  import names, never content. Such a closure passes **Phase E as well**: the
  harness's `import anthropic` is function-local inside `transport.py`, and
  Phase E walks only the transform and the verifiers, not the packages they
  import. Both gates green is not proof that no unconsented mapping happened.
- **Static binding is not runtime coverage.** `input_fields` and
  `document_classes` overreach, and the `max_calls`/`max_tokens`/`max_usd`
  ceilings, are properties of a *run*. They are enforced by `Grant.check` and
  `RunBudget` inside the harness at call time. Phase G passes no runtime
  arguments and watches no individual call.
- **One gated call is not every call.** A transform that calls `map_inputs`
  (satisfying the gate) and *also* constructs a second `transport.Client`
  beside it is invisible — attribute-level use is beyond an import check, the
  same way `os.popen` is beyond Phase E's.
- **Phase B spends under the grant, for real.** Phase B *executes* the
  transform, and the harness resolves its key from secrets with an environment
  fallback — so with `ANTHROPIC_API_KEY` set, self-checking a mapper closure
  makes live model calls during the dry run. Phase G runs before Phase B so that
  spend happens only under a binding grant; the ceilings on it are the harness's
  job, not this script's.
- **The grant binds the model id string, not the snapshot.** A snapshot rollover
  behind the same alias is invisible here, by an upstream decision this gate
  does not preempt.
- **Expiry makes the gate time-dependent, deliberately.** A closure green
  yesterday fails after its grant's `expires_at`. That is consent lapsing, not
  flakiness — re-ask rather than extending the date to make the check quiet.

`grant.unbound` is the one warning: a grant binding no spec in the closure
authorizes nothing and fails nothing, but left on disk it reads as coverage it
does not provide. It is reported only when every spec *did* find its grant —
otherwise the same files are already the subject of a mismatch error.

## Where an expected value may come from

SKILL.md's Step-3b invariant — *never restate the transform's arithmetic as an
assert* — extends to anything you add here, and the extension is the part that
gets violated. **An expected value comes from a landed `data/` file or from the
user's contract in `contracts/`. It never comes from the transform module or
from a copy of the transform's constants.**

Both halves matter, because complying with the first alone is the usual failure:

- `from transform.main import WEIGHTS, VERDICTS` and then asserting a weight is
  in `WEIGHTS` — fails only on a self-typo.
- `WEIGHTS = {...}` re-declared at the top of the checker, copied from the
  transform — no import, identical tautology.
- Recomputing a total with the transform's own formula over the transform's own
  constants — reproduces its arithmetic, including its errors.

None of these can detect a wrong score, a wrong gate, or a classification that
disagrees with the source. They are **internal-consistency checks**, and that is
what they must be called. Never narrate one as independent verification, and
never let a green exit stand in for "the numbers are right" — SELF-CHECK OK
means the closure is structurally sound and the transform ran, nothing more.

The good case is already the shipped one: Step-3b's Tier-1 asserts (declared-key
uniqueness, grain row count vs independently-read source rows) and Tier-2
(signed measure reconciliation in `Decimal`) all take their expected value from
the source, not from the code under test. Once a supplied rubric is landed as
data — see [derivation-plan.md](derivation-plan.md) — a check that reads the
rubric CSV is legitimately independent too.

## The script

The shipped file is `nxd-run-job-loop/scripts/self_check.py` in the installed
skill tree. Copy it into the closure first — there is nothing to run until you
do — then run it from the closure root:

```bash
cp "$JOB_HELPER_DIR/scripts/self_check.py" <closure>/self_check.py
cd <closure> && python3 self_check.py --json --record build-record.json
```

Two different things need packages on that interpreter, and only one of them is
the transform's. `self_check.py` imports `duckdb` **itself**, to open the scratch
database and count the landed rows — and it does so *after* Phase B has already
run the transform, so a missing `duckdb` surfaces as a bare traceback on a run
that did real work, with no diagnostic emitted and no build record merged. `dlt`
and `pandas` are the transform's, reached through Phase B's import of it. If any
of the three is absent, pin them for the run — and keep the working directory and
the flags, or the run produces prose and merges nothing:

```bash
cd <closure> && uv run --python 3.12 --with "dlt[duckdb]==1.28.2" \
  --with "duckdb==1.5.4" --with "pandas==2.3.3" \
  python self_check.py --json --record build-record.json
```

That shipped file is now the single source of truth for the self-check runtime behaviour; this reference keeps the phases, boundaries, report contract and failure-reading guidance, not a second executable copy.

The read-back prints **after** `SELF-CHECK OK`, deliberately: it is the last
thing on screen, and it is not what the OK line attests to. Read it before you
report the run — a gate that passed every row, or a verdict that came out
single-valued, is a number you invented, and it is now the last thing you saw
rather than the thing the success banner scrolled past.

## Reading a failure

The row counts printed for derived models are worth reading, not just passing:
a derived table with zero rows, or with exactly as many rows as its source when
the derivation was supposed to expand or collapse, means the derivation ran but
did nothing. The Step-3b asserts should have caught that — if they did not, the
invariant they encode was too weak.

Reading a **Phase B** failure: it executed, so what it reports is what will
happen on the supervisor. There is no kernel and no network in this phase, so a
failure here is never environmental and is never a reason to retry — it is
unambiguously the generated code. A fired assert (`runtime.assert_failed`) is the
transform's own invariant rejecting the data it produced: that is the check
working, not the check being wrong. Fix the derivation, never the assert.
An absent table is acceptable only when the model is in
`OPTIONAL_EMPTY_MODELS`; a required absence is the specific
`runtime.model_table_missing` finding.

Reading a **Phase C** failure: each one names a specific missing or mismatched
record file, and none of them is fixed by hand-editing the closure.
`dp-blueprint.approved.md does not match ... snapshot_sha256` means the in-closure
copy of the plan was edited after it was written — the plan a build was compiled
from is not editable in place, so change the live `dp-blueprint.md`, re-approve, and
regenerate. `compiled_from does not equal ... spec_hash` means the build record
describes a build of a *different* plan than the one snapshotted here; regenerate
rather than reconciling by hand. `spec_status_at_copy` not `approved` is the one
Phase C finding that is not yours to fix: it means the closure was generated from
a spec nobody signed off, and approval is a user act. A missing
`build-record.json` means outcomes have nowhere to land, which would make a green
run indistinguishable from a green run that conceded something — run
`dp_diagnostics.py record init` at generation, before this script.

Reading a **Phase D** failure: it is not a formatting complaint. `nxd_decisions
is promised but is not in BASE_MODELS` means the ledger is generated from a
Python literal, so it documents the code rather than driving it — the row a user
edits has no effect, and the two drift apart the moment either changes. Fix it by
writing `data/nxd_decisions/nxd_decisions.csv` and reading it like any other base
model, never by deleting the model or loosening the check. `value X is landed AND
appears as a literal` means the same value exists in two places that can
disagree: delete the literal and read the row.

`nxd_decisions.csv has no 'provenance' column` means the ledger records whether
each ruling is settled but not who wrote it — so a reviewer cannot separate the
user's weights from the ones you invented. Add the column and classify **every**
row from the fixed set; do not backfill them all as `user_confirmed` to clear the
gate, which is the exact erasure the column exists to prevent. `provenance has
[...]` means a value outside that set: the vocabulary is closed precisely so the
class is queryable, so map your value onto one of the four rather than widening
the set. Which value belongs on which row is in
[derivation-plan.md](derivation-plan.md).

An **ABSENT** line reports a declared value no derived column ever produced. It
is mechanical and says only that: on this data, that branch did not fire. Whether
it *cannot* fire is yours to work out — and if a rule makes a verdict
unreachable (a criterion that can never reach the score its own branch needs),
say so to the user rather than shipping a branch that is dead by construction.

The **distribution read-back** is read the same way, and it is the one the
success banner is most likely to bury. A `UNIFORM` line means a column you built
to distinguish rows does not: a gate that passes every row is not a gate, and a
classification with one value classified nothing. That is a value you supplied,
not one the data produced. Two things follow. **State the distribution to the
user before building** — a uniform gate by name, and what it was supposed to
separate. And do not silently repair it: a gate that cannot fail is a ruling you
authored, so it lands in `nxd_decisions` like any other, or it goes back to the
user as a question. A non-uniform distribution is not a pass either — it is
simply the shape of what you produced, and it is worth one line in the handoff.

Reading a Phase A failure: every message names the file, model, and column. A
kwarg rejection (`join() takes to=, not to_model=`) is a typo — fix the call. A
naming-invariant failure is a diverged name — fix the NAME in every place it
appears (`models.py`, `.promise` or optional `.model`, `PHYSICAL_MODELS`,
`OPTIONAL_EMPTY_MODELS`, `data/<name>/`), never quote around it. If a message contradicts the installed wheel's actual
behaviour, the pin has drifted: re-derive that one signature, and update
`nxd-spec-api.md` and this script together.
