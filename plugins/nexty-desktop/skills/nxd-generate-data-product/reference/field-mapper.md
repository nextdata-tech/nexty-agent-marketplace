# Field mapper — mapping from inside a transform

The one sanctioned way a transform may call a model, and the way a **packaged**
data product infers. Agent-side judging that lands as CSV before the build
(llm-judgments.md) is the exploration lane — right while the rubric is still
moving, wrong as a shipping shape, because it leaves the procedure outside the
artifact. This path is gated by consent rather than trusted, and the gate is the
price of having the logic travel with the closure.

## Contents

- [When to use it (and when not to)](#when-to-use-it-and-when-not-to)
- [Importing it in a closure](#importing-it-in-a-closure)
- [Authoring the spec and the grant](#authoring-the-spec-and-the-grant)
- [Desktop supervisor approval boundary](#desktop-supervisor-approval-boundary)
- [Evidence modes — what each one proves](#evidence-modes--what-each-one-proves)
- [Reviews: `data/mapper_reviews/`](#reviews-datamapper_reviews)
- [Two dlt runs, in this order](#two-dlt-runs-in-this-order)
- [Spend: the self-check really pays](#spend-the-self-check-really-pays)
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

It brings real cost, and the cost is worth stating plainly: a consent grant the
user must author, a supervisor approval interaction, and live model calls (with
real spend) during the self-check and every build.

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

A mapper build has two execution prerequisites that fail *outside* your
generated code — the `anthropic` SDK and an API key visible to the MCP child
process — and several that fail inside it before any model is contacted. Key
visibility is only a reachability observation: it is not a Desktop approval or
a credential-isolation guarantee. All prerequisites are checkable offline in
under a second.

**Run the preflight in [mapper-preflight.md](mapper-preflight.md) before every
mapper build**, and report its `MAPPER RUN STATUS` block before and after the
attempt. It is mandatory, it never calls the model, and it exists because a real
build spent a full cycle to discover a missing SDK, then another to discover a
wrong `MapperInput` call — both free to catch beforehand.

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

Write the mapper spec as JSON under `contracts/` (for example
`contracts/mapper_spec.json`) and load it with `MapperSpec.load`. A spec inlined
as a Python literal has no stable id, so nothing can be consented to.

Compute the id the grant must carry:

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

**Consent is the user's act.** You cannot author a grant on the user's behalf,
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
  only; models by exact id, limited to the priced ids of the Claude haiku,
  sonnet, opus and fable families (dated aliases included, anything unpriced
  is `workflow/mapper_model_unpriced`); at most 1000 calls, 2,000,000 tokens
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
| `workflow/mapper_scope_invalid` | missing, malformed or loosely typed grant; an unknown key; both grant paths present; spec missing |
| `workflow/mapper_approval_claims_rejected` | the grant carries `approved`, `approval`, `approved_by`, `approval_id`, `approval_hash`, `user_approved`, `decision`, `receipt` or `signature` |
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
   `approval: {state: "awaiting_user", expires_at}`. That expiry is the
   earlier of the policy's approval window and the grant's `expires_at`. It
   contains no URL, token, capability or key.

A decline, cancel, timeout, interrupted prompt or unavailable dialog returns
the requirement to pending with a `workflow/approval_*` code. The user can be
asked again, but only when they want to be. The recovery table for every code
is in the job loop's `reference/workflow-v2.md` § Mapper approval. The OS
dialog confirms local presence. It is not cryptographic proof of the user's
identity, and it is the only approval surface: no chat reply, MCP call, file
or form counts as approval.

**Declared data scope is not enforced.** The grant's `input_fields`,
`document_classes` and `pii_category` are declared by the author and reviewed
by the user. The dialog labels them "Declared by the author (reviewed, not
enforced)". The supervisor does not restrict what the transform sends to the
model, and the harness's own `Grant.check` runs inside the closure, so it is
not a boundary either. Keep the declared list honest, because it is what the
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

**Widening needs a fresh approval.** A new model, higher ceilings, a later
expiry or `recurring: true` changes the grant. That means recapture, a fresh
review and a new OS approval. With `recurring: false`, an approval is spent by
the first publication it admits. After that, admission reports it as
`workflow/approval_expired`. Recapturing a changed closure moves the subject
and invalidates the approval on purpose.

**Validation does not spend.** Trusted validation runs the scratch build
against a stub broker backend with no network and no charge. Stub values are
placeholders, so contract or expectation failures confined to columns the
mapper spec declares as outputs become the advisory
`validation/mapper_output_unverified`. Every other contract failure still
fails. Scratch evidence proves wiring, not the quality of mapped values.

## Evidence modes — what each one proves

| Mode / path | Evidence kind | What it actually proves |
|---|---|---|
| `structured` over text already landed in the closure | `verified` | The quoted span is a substring of landed text; re-checkable offline from `text_hash` + offsets. |
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
        fields={"document_id": document_id},    # other context the model may read
        landed_text=text,                       # the substring haystack
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

`target_row_key` is a content-derived hash, not the source row ID. Keep the
`MapResult` proposals/evidence together and resolve from that bundle; never
reconstruct a join from a model response's guessed row key.

**`MapperInput` takes no arbitrary keyword per source column.** There is no
`MapperInput(document_id=...)`, and there is no `deps` argument to `map_inputs` —
an older revision of `mapper/CONTRACT.md` described one, and it never existed.

The four slots are not interchangeable:

| Slot | Holds | Why it cannot be merged |
|---|---|---|
| `input_id` | this record's handle | keys the ledger and the recorded responses |
| `identity` | the spec's `identity_fields` | what the grant and `input_snapshot_id` bind to — empty means nothing binds |
| `fields` | other context the model may read | un-bound: changing it does not revoke a review |
| `landed_text` | text already landed in the closure | the only surface `verify_quote` checks a quote against |

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
