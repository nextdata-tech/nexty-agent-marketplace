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

The grant above remains the **standalone field-mapper harness** contract:
`map_inputs` requires a user-authored `Grant`, and a matching `Grant.check` is
required before the harness dispatches a model call. It is not, by itself, a
Desktop supervisor authorization.

The public Desktop construction surface is now workflow-v2 only. The current
trusted generator-validation path supports non-mapper closures only and rejects
mapper closures before admission with `validation/mapper_closure_unsupported`;
formal mapper requirements are likewise reported as `workflow/unsupported_mapper`.
Do not describe a separate Desktop mapper build, mapper-status tool, or
approval route as available. Use this section for the standalone harness
contract, or report the Desktop mapper closure as unsupported until a workflow-v2
mapper handler is shipped.

If a future Desktop mapper handler is supported, `contracts/mapper_grant.json`,
a mapper request file, and their fields are **untrusted scope proposals**. The
supervisor derives the actual mapper subject from the definition and is the only
component that can turn that subject into an approval. An agent must not author `approved`,
`granted_by`, receipt, signature, or approval-id claims in an attempt to make a
proposal authoritative; those claims are rejected rather than treated as user
consent. A green Phase G proves only that the static harness grant check found a
binding artifact. It is not proof of human authorization in Desktop.

That future handler would use a supervisor-owned loopback review surface and a
native OS presence decision. Before it creates a writer, state database, run,
or provider client, the supervisor would:

1. freeze and pin the candidate definition;
2. derive the mapper subject and content manifest from the actual frozen bytes;
3. create an opaque request ID and token-free loopback status URL;
4. deliver a one-time browser capability out of band in the URL fragment;
5. render the supervisor-derived scope and receive approve/decline from the
   browser; and
6. require a fresh supervisor-owned OS dialog decision.

Only the matching browser capability, OS decision, subject, manifest, and
budget can produce one-use admission. The MCP peer never receives the
capability. The browser click alone is insufficient. The OS dialog is local
presence confirmation, not cryptographic proof of the user's identity.

The current supervisor has no Desktop mapper admission handler, so it does not
currently use MCP form elicitation for this path. If a future handler is added,
its MCP `initialize` request/context would not be an admission input. Do not
require protocol `2025-06-18` form elicitation or claim that the approval
surface has no second step. A second LLM turn is not an approval mechanism.

For the documented handler contract, an accepted request admits that exact
subject for the current supervisor session. An unchanged retry reuses that
session approval without another interaction; a changed spec or proposed scope
gets a new subject and must be confirmed again. The supervisor re-derives the
subject immediately before admission, so a definition changed while it was open fails with
`mapper_subject_changed` rather than running under the earlier confirmation.

**Every non-accept outcome would fail closed.** Browser decline, OS decline,
cancelled or expired requests, malformed or failed transport, binding mismatch,
and any unsupported approval-surface state return
`kind: mapper_approval_required` with `run_admitted: false`. Their
`confirmation` values are `declined`, `cancelled`, `expired`,
`failed`, `unsupported`, or the corresponding binding error as applicable.
The current diagnostic reports `credential_isolation: not_enforced`: the
transform inherits the MCP process environment, so this gate is not a claim
that provider egress is brokered or contained. For example:

```json
{
  "kind": "mapper_approval_required",
  "confirmation": "cancelled",
  "run_admitted": false,
  "credential_isolation": "not_enforced"
}
```

Do not retry by adding approval fields or treat an API key in the child
environment as a workaround. `credential_isolation: not_enforced` means the
current diagnostic makes no claim that provider egress is brokered or contained.
Approval records are session-local: they do not persist signed receipts or
execution attestations, and they do not enforce cumulative call/token/cost
budgets across build attempts.

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
`data/` beside agent-managed exports, protected by the "preserve a file
connector's supplied export exactly" convention. A wiped `data/mapper_reviews/`
is *loss* — the reviews are gone and the cells re-infer — never *corruption*,
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
