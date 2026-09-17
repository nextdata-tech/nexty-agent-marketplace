# Field mapper — Layer-1 contract

Status: **normative.** Normative for everything under
`src/nxd-generate-data-product/mapper/`: this file is what the code and the
acceptance fixtures are held to, and where it and the implementation disagree,
one of the two is a bug — report it rather than silently diverging. The
architecture record, which explains *why* the design is shaped this way, is
the repository document `docs/architecture/field-mapper.md`.
It describes the same system but does not govern it.

Two `nxd_decisions` axes landed after the design was written and bear on this
machinery: `provenance` (a required second axis, vocabulary `user_confirmed` |
`agent_authored` | `source_derived` | `deferred`) and `evidence_kind` (`fact` /
`inference`, on a *deterministic* score-explanation row). Consequences are
carried in §2.4 and open question 9: `evidence_kind` and this contract's
`verify_status` answer different questions and must never be populated from
each other.

**Layer-1 is what does not vary between experiments.** Prompt text, field
grouping, input-adapter shape, and evidence granularity are Layer 2 and are
deliberately absent here — they live in the landed mapper spec, versioned by
`mapper_spec_id`.

## Contents

- [1. Module layout](#1-module-layout)
- [2. Record schemas](#2-record-schemas)
- [3. `value_status` semantics](#3-value_status-semantics)
- [4. What blocks the build](#4-what-blocks-the-build)
- [5. Identifiers and hashing](#5-identifiers-and-hashing)
- [6. Resolution order](#6-resolution-order)
- [7. Consistency asserts](#7-consistency-asserts)
- [8. Transport contract](#8-transport-contract)
- [9. Ledger contract](#9-ledger-contract)
- [10. Open questions](#10-open-questions)

---

**Standalone grant boundary.** This Layer-1 contract governs the standalone
field-mapper harness: `map_inputs` still requires a user-authored `Grant`, and
`Grant.check` remains the pre-dispatch guard for that harness. It does not mint
or prove a human authorization for a Desktop supervisor build; that is a
separate supervisor-owned admission boundary.

## 1. Module layout

Imported as `nxd.experimental.field_mapper`, from the installed `nxd` package.
The modules below are not carried in the skill's zip — the runtime provides
them. There is exactly one copy of the harness, in the nxd monorepo, which is
where it is tested, version-stamped and packaged. The skills repo carried a
second copy for a time so the consent gate's tests had a real harness to run
against; it is gone, and those tests resolve the monorepo copy instead, skipping
where that checkout is unreachable.

```
field_mapper/
  __init__.py        version stamp + the only names Layer 2 may import
  spec.py            mapper spec model, canonicalization, mapper_spec_id
  identity.py        target_row_key derivation, input_snapshot_id, observation_id
  records.py         proposals/reviews/evidence/outcomes + CSV serialization
  schema.py          mapper spec -> Anthropic JSON Schema (+ what it cannot express)
  validate.py        range/enum/type/evidence checks, retry policy, degrade accounting
  transport.py       anthropic SDK call, budget, backoff, cancellation, heartbeat
  ledger.py          append-only run ledger (hashes only)
  grant.py           consent-grant load + match, refused before any model call
  resolver.py        proposals + reviews + evidence -> effective rows + review outcomes
  errors.py          the exception taxonomy value_status maps from
  __main__.py        CLI: preflight | canary | run | resolve | verify
```

### Why this differs from the suggested decomposition

Four splits were added; none is cosmetic.

**`identity.py` split out of `spec.py`.** Row identity is the axis the original
design got wrong (§3 of the design), and it is consumed by *four* modules —
records, resolver, validate, ledger. Leaving `target_row_key` derivation inside
`spec.py` makes every one of those import the spec module to hash a row key, and
invites the drift where the resolver derives a key one way and the record writer
another. Row identity is the contract's load-bearing primitive; it gets its own
module with its own tests, and `spec.py` imports *it*.

**`schema.py` split out of `transport.py`.** The design's hardest transport fact
is the negative one: JSON Schema on this API supports neither numeric range
(`minimum`/`maximum`) nor string length (`minLength`), and is rejected outright
when combined with citations. That means every constraint the spec declares has
to be routed to one of two places — into the wire schema, or into
`validate.py` — and the routing decision is exactly where a "we thought the API
enforced it" bug lives. Making it a module forces the routing table to be
explicit and testable offline with no network. It also isolates the one-time
schema-compilation cost (24h server-side cache, keyed on schema bytes) so
schema churn is visible as a cost line, not a mystery latency.

**`grant.py` split out of `__main__.py`.** §9 of the design requires the grant
check to happen **before** source content or credentials are read. If that check
lives in the CLI, an in-process caller (`nxd-run-job-loop` invoking `map()`
directly, which is the stated Layer-1 use) bypasses it entirely. It is a library
gate, not a CLI gate.

**`errors.py` split out.** `value_status` is a projection of an exception
taxonomy; §4's table is only enforceable if the mapping from raised exception to
status is in one place. Otherwise `transport.py` decides some statuses,
`validate.py` decides others, and the "systemic failure blocks, row-level absence
does not" boundary erodes at the first new error type.

### Public surface

Layer 2 (generated code, skills, job-loop closures) may import **only** these:

| Symbol | Module | Purpose |
|---|---|---|
| `map_inputs(inputs, *, spec, grant, run_dir, call, ...) -> MapResult` | `field_mapper` | the N→M primitive |
| `make_call(*, spec, grant, secrets=None, allow_env=False, provider=None, provider_model=None, provider_cwd=None) -> callable` | `field_mapper` | lazy, budgeted provider seam; explicit secret first, then allowlisted environment fallback **only when `allow_env=True` is passed**; creates the client only on first dispatch |
| `MapperInput(input_id, identity, ...)` | `field_mapper` | one source record handed to the mapper |
| `MapperSpec.load(path)` / `.mapper_spec_id` | `spec` | landed spec → runtime object |
| `MapperProposal` / `MapperReview` / `MapperEvidence` / `ReviewOutcome` | `records` | mapped values, human state, evidence, and review resolution |
| `ValueStatus` | `records` | the enum in §3 |
| `resolve(proposals, reviews, evidence=None, *, fields, row_keys=None, field_types=None, min_evidence_per_ok_cell=0) -> Resolution` | `resolver` | wide projection + sidecar + review outcomes, one bundle |
| `Resolution.wide_rows` / `.provenance` / `.review_outcome_rows` / `.assert_bijection()` / `.assert_value_hashes()` / `.assert_cardinality(...)` / `.assert_review_audit_completeness()` | `resolver` | §7 |
| `PreflightEstimate` / `estimate(inputs, spec)` | `transport` | §10 of the design |
| `Grant.load(path)` / `Grant.check(spec, *, input_fields=(), document_classes=(), now=None)` | `grant` | §9 |
| `FieldMapperError` and subclasses | `errors` | so callers can catch by class, not string |
| `target_row_key_for_input(*, identity, fields, identity_fields, source_locators=(), ordinal=None)` | `mapper` | derive a row key the way `map_inputs` does — the only way to attribute a proposal back to its source row |

Everything else is private. `transport.Client` is deliberately **not** public —
Layer 2 must use `make_call` so provider construction, credential resolution,
and the per-run budget ledger stay behind the sanctioned seam.

#### The exact call, and the exact construction

Both signatures are normative and are what the installed package accepts. An
earlier revision of this table documented `map_inputs(inputs, *, spec, deps)`;
**no `deps` argument, object, or module has ever existed** in the
implementation. The documented surface was the error, and it is corrected here
rather than by wrapping the working function — see
`docs/architecture/field-mapper.md`.

`run_dir` is the mapper's **ephemeral ledger root**, not the closure root and not
`~`. Derive it as a run-scoped child of the transform directory (for example,
`Path(run_dir) / "run" / "mapper"`) and pass that child to `map_inputs`. When a
path under the user's home has no run-directory marker (`runs`, `run`, `tmp`,
`temp`, `scratch`, or `var`), the current ledger guard raises `ValueError`
before the first provider dispatch; callers must treat that as a systemic
refusal. A future stable `error_code` for this path guard must not be inferred
from the exception text.

```python
from pathlib import Path

result = map_inputs(
    inputs,                 # Sequence[MapperInput]
    spec=spec,              # MapperSpec.load(...)
    grant=grant,            # Grant.load(...)
    run_dir=str(Path(run_dir) / "run" / "mapper"),  # ephemeral ledger root
    call=call,              # the injected model callable
)
```

`MapperInput` takes **no arbitrary keyword per source column.** `input_id` and
`identity` are required; everything else is optional:

```python
MapperInput(
    input_id=str(document_id),              # the mapper's handle for this record
    identity={"document_id": document_id},  # the spec's declared identity fields
    fields={"document_id": document_id},    # everything else the adapter exposes
    landed_text=text,                       # the substring haystack — NOT model output
    document_class="invoice",
)
```

`MapperInput(document_id=...)` raises
`TypeError: MapperInput.__init__() got an unexpected keyword argument 'document_id'`.
The four slots are not interchangeable and collapsing them breaks a specific
guarantee each: `input_id` is the handle, `identity` is what the grant and
`input_snapshot_id` bind to, `fields` is un-bound context, and `landed_text` is
the only surface `verify_quote` can check a quote against. Putting the document
identity **only** in `fields` leaves `identity` empty, so nothing binds.

Generated code must **not** introspect these signatures to decide how to call
them. A mapper that adapts itself to whatever is installed converts a loud,
immediate `TypeError` into a silent behavioural difference between two runtimes.

#### Nothing returned carries the input identity back

`target_row_key` is a content-derived hash (§5), **not** your `input_id`.
`MapperProposal`, `MapperEvidence` and `Resolution` are all keyed by it, and
none of them carries `identity` — so a caller that needs to attribute a
proposal back to its source row must derive the key itself, with the same
projection `map_inputs` used:

```python
from nxd.experimental.field_mapper.mapper import target_row_key_for_input

row_key_to_source = {
    target_row_key_for_input(
        identity=item.identity,
        fields=item.fields,
        identity_fields=spec.grain.identity_fields,
        source_locators=spec.grain.source_locators,
    ): item.input_id
    for item in mapper_inputs
}
```

Take `identity_fields` and `source_locators` from **the spec's grain**, never
from a repeated literal, or the projection drifts from the one that produced the
keys and every proposal fails to resolve.

**This recipe assumes `duplicate_policy` is `reject` or `merge_by_rule`.** Under
`ordinal_suffix` the emission ordinal participates in the key (§5), so one
`input_id` no longer maps to one key and the lookup above silently resolves
nothing. That policy needs a projection that passes `ordinal=` per emitted row —
and a `dict` keyed by row key stops being the right shape. Every `samples/*/spec.json`
uses `reject`, so no fixture exercises this.

**`ordinal_suffix` is a documented trap, not a supported alternative** (open
question 8): a source reorder changes the key and mass-invalidates every review
bound to it. Read this caveat as "here is why the recipe does not cover that
policy", not as an invitation to adopt it.

Treating `target_row_key` as the business key is the failure this section
exists to prevent: downstream asserts then reject every row for belonging to an
entity that does not exist, and the message points at the data rather than at
the key.

`map_inputs` returns a `MapResult` carrying proposals + evidence + ledger handle
**in one in-memory bundle** (design §7). There is no API that returns proposals
without their evidence, because that API is how the orphan-evidence bug gets
written.

#### `allow_env` defaults to False — pass it explicitly

The environment fallback is **opt-in**. A closure that omits `allow_env` gets no
ambient credential, however visible the key is in the child's environment, and
fails at the first dispatch with `credential_missing` — a credential error for a
credential that is present. Pass it explicitly when the supervisor supplies the
key through the environment:

```python
call = make_call(spec=spec, grant=grant, allow_env=True)
```

`provider` also defaults to `None`, but that is not a hole: provider and model
selection are **bound to the grant**. Omit it and `grant.provider` is used; pass
one that disagrees and `make_call` raises `GrantError` ("provider override ...
does not match the consented provider") rather than honouring it. The same holds
for `provider_model` against `grant.model` ("provider model override ... does
not match the consented model"). Both arguments are retained for
compatibility, not as an override channel — consent is not something a caller
can widen at the call site. The pack's own `examples/e2e/run_e2e.py` calls
`make_call(spec=spec, grant=grant, allow_env=True)` with no provider for exactly
this reason.

### Provider adapter contract

`make_call` returns a synchronous callable. Generated code must not return a
coroutine, import a provider SDK, construct `transport.Client`, or pass through
an SDK response object. The callable receives these keyword-only arguments:

| Argument | Type | Meaning |
|---|---|---|
| `item` | `MapperInput` | one source record and its landed text |
| `spec` | `MapperSpec` | the runtime-bound mapper spec |
| `wire_schema` | mapping | compiled structured-output schema |
| `violations` | sequence | bounded validation feedback for a correction turn |

It returns a parsed mapping shaped like the compiled wire schema. `map_inputs`
unwraps a transport `CallResult` when present and validates that mapping; a
missing parsed body becomes `error_code = schema_reject`, never
`evidence_absent`. `SystemicError` subclasses block the run, while `CellError`
subclasses are recorded for the row and handled by the mapper's bounded retry
and coverage gate. Error details are sanitized; stable machine codes, not
provider payloads or credentials, are persisted.

---

## 2. Record schemas

Four landed models. Long-form records are authoritative; the wide model is a
projection (design §2). `mapper_review_outcomes` is a replace-loaded audit
projection of the durable reviews for the current published population.

Column type names are the `nxd.spec` types (`string()`, `int64()`, `float64()`,
`bool()`, `timestamp(unit=DurationUnit.Microseconds)`), since these land as
base models through the normal dlt reader loop. In a generated `models.py`,
import `DurationUnit` from `nxd.core.yaml_schemas` and pass the unit explicitly;
`timestamp()` is not a valid zero-argument constructor.

### 2.1 `mapper_proposals`

One row per `(target_row_key, field)` produced by this execution. **Replace-loaded
every build** — it is this run's output, not durable state.

| Column | Type | Null? | Key | Description |
|---|---|---|---|---|
| `target_row_key` | `string()` | no | PK | Content/source-derived row identity (§5). Never an emission ordinal — except under `duplicate_policy = ordinal_suffix`, where §5 admits it after the identity fields are exhausted. |
| `field` | `string()` | no | PK | Target field name. Must be declared in the spec's target fields. |
| `value_string` | `string()` | **yes** | | Typed value slot. Exactly one `value_*` column is non-null when `value_status = ok`; **all are null** for every other status. |
| `value_int` | `int64()` | **yes** | | ditto |
| `value_float` | `float64()` | **yes** | | ditto |
| `value_bool` | `bool()` | **yes** | | ditto |
| `value_timestamp` | `timestamp(unit=DurationUnit.Microseconds)` | **yes** | | ditto |
| `value_type` | `string()` | no | | Which slot is authoritative: `string`\|`int`\|`float`\|`bool`\|`timestamp`. Populated even when the value is null, so the resolver knows the column's type without consulting the spec. |
| `value_hash` | `string()` | no | | `sha256` over the canonical value encoding (§5). Stable for null: the null-of-type digest, not the empty string. |
| `value_status` | `string()` | no | | The enum in §3. |
| `error_code` | `string()` | **yes** | | Non-null iff `value_status = error`. Stable machine token (`credential_missing`, `transport_exhausted`, `refusal`, `schema_reject`, `dependency_missing`). Never free text. |
| `error_detail` | `string()` | **yes** | | Human-readable, redacted. Never contains input content or credentials. |
| `attempt_count` | `int64()` | no | | Attempts spent on this cell, including the successful one. `0` when no call was made (e.g. blocked before dispatch). |
| `attempt_id` | `string()` | **yes** | | Identifies the *final* attempt in the ledger. Null when `attempt_count = 0`. |
| `needs_review` | `bool()` | no | | See §3 and open question 3. Derived, not model-asserted. |
| `evidence_count` | `int64()` | no | | Number of `mapper_evidence` rows for this cell. `0` is legal only for statuses where the spec declares evidence optional. |
| `observation_id` | `string()` | no | | Content hash of key+value+provenance+response (§5). |
| `input_snapshot_id` | `string()` | no | | Deterministic hash of the inputs this cell was derived from (§5). |
| `mapper_spec_id` | `string()` | no | | Canonical spec hash (§5). |
| `execution_id` | `string()` | no | | Nondeterministic, harness-supplied. **Never a business key.** Present for ledger join only. |
| `emission_ordinal` | `int64()` | no | | Display-only and NOT stable across runs. Not part of any key under `reject` or `merge_by_rule`; under `ordinal_suffix` it participates in `target_row_key` (§5), which is why that policy makes review binding fragile. |

Uniqueness: `(target_row_key, field)`. A duplicate is a hard build failure, not a
last-write-wins — duplicate emission means the spec's row identity is
under-determined, which is the failure mode §3 of the design exists to catch.

> **Why five typed columns rather than one `value` string.** The design's §4 rule
> is "typed value columns are nullable; never a string sentinel in a
> numeric/date/boolean column." A single string column satisfies the letter (no
> sentinel in a numeric column, because there is no numeric column) and defeats
> the purpose — every consumer casts, and a bad cast becomes a query-time error
> instead of a build-time one. Five nullable slots keep the type in the schema
> where a metric can be defined over it.

### 2.2 `mapper_reviews`

Durable human state. **Never replace-loaded away.** One row per review act; rows
are immutable and append-only (new batch file per review session, per the
`llm-judgments.md` batch convention). The physical location is
`data/mapper_reviews/batch-NNN.csv` inside the closure — decided, with its
tradeoff recorded, in open question 5.

| Column | Type | Null? | Key | Description |
|---|---|---|---|---|
| `review_id` | `string()` | no | PK | Content hash of the whole review row. Makes the batch file idempotent to re-land. |
| `target_row_key` | `string()` | no | | Binds to the proposal's row. |
| `field` | `string()` | no | | Binds to the proposal's field. |
| `verdict` | `string()` | no | | `confirmed` \| `rejected` \| `overridden`. |
| `override_value_string` | `string()` | **yes** | | Populated iff `verdict = overridden`. Same five-slot typing as proposals. |
| `override_value_int` | `int64()` | **yes** | | ditto |
| `override_value_float` | `float64()` | **yes** | | ditto |
| `override_value_bool` | `bool()` | **yes** | | ditto |
| `override_value_timestamp` | `timestamp(unit=DurationUnit.Microseconds)` | **yes** | | ditto |
| `override_value_type` | `string()` | **yes** | | Non-null iff `verdict = overridden`. |
| `bound_value_hash` | `string()` | **yes** | | The `value_hash` this review examined. Null iff `verdict = overridden` **and** the reviewer is overriding an absent proposal. |
| `bound_input_snapshot_id` | `string()` | no | | The `input_snapshot_id` in force when reviewed. |
| `bound_mapper_spec_id` | `string()` | no | | The `mapper_spec_id` in force when reviewed. |
| `reviewer` | `string()` | no | | Identity. `human` or a named reviewer; never a model name — a model does not review. |
| `reviewed_at` | `timestamp(unit=DurationUnit.Microseconds)` | no | | Supplied by the reviewer's tooling, not by the transform. Not `now()` at build time. |
| `note` | `string()` | **yes** | | Free text, reviewer-authored. |

Uniqueness: `review_id`. **Not** `(target_row_key, field)` — a later review of the
same cell is a new row, and the resolver picks the latest *valid* one (§6).

**Binding and auto-invalidation.** A review applies to a proposal only when all
three bindings match: `bound_value_hash == proposals.value_hash`,
`bound_input_snapshot_id == proposals.input_snapshot_id`,
`bound_mapper_spec_id == proposals.mapper_spec_id`. Any mismatch → the review is
`stale` and does **not** apply. Stale reviews are never deleted and never
silently reapplied; the resolver surfaces them (§6) so the reviewer can see what
came unbound and why.

The one asymmetry: an `overridden` review with a null `bound_value_hash` binds on
input + spec only. That is deliberate — a human overriding a cell the model
never produced (`evidence_absent`) has no value hash to bind to, and forcing one
would make the "human fills the gap" case unrepresentable.

### 2.3 `mapper_review_outcomes`

One replace-loaded row for **every** durable review considered by a resolve.
This is an audit projection, not a replacement for `mapper_reviews`: the
original verdict, reviewer, timestamp, and requested override remain in the
append-only review record. The outcome records whether that decision reached the
published projection and retains both sides of the binding for diagnosis.

| Column | Type | Null? | Description |
|---|---|---|---|
| `review_id` | `string()` | no | Join to the immutable review act. |
| `target_row_key` | `string()` | no | Review target. |
| `field` | `string()` | no | Review target field. |
| `verdict` | `string()` | no | Original human verdict. |
| `outcome` | `string()` | no | `applied`, `rejected`, or `ignored`. |
| `reason` | `string()` | no | Stable explanation for the outcome. |
| `stale_reasons` | `string()` | no | Complete binding failures, empty when not stale. |
| `bound_value_hash` | `string()` | yes | Value hash the reviewer saw. |
| `bound_input_snapshot_id` | `string()` | no | Input snapshot the reviewer saw. |
| `bound_mapper_spec_id` | `string()` | no | Mapper definition the reviewer saw. |
| `proposal_value_hash` | `string()` | yes | Value hash observed in the resolved run, if the target exists. |
| `proposal_input_snapshot_id` | `string()` | yes | Input snapshot observed in the resolved run. |
| `proposal_mapper_spec_id` | `string()` | yes | Mapper definition observed in the resolved run. |
| `effective_value_hash` | `string()` | yes | Hash published for the cell when a resolution row exists. |
| `winner_review_id` | `string()` | yes | Winning review for an applied or superseded decision. |
| `reviewer` | `string()` | no | Original reviewer identity. |
| `reviewed_at` | `timestamp(unit=DurationUnit.Microseconds)` | no | Original review timestamp. |

`applied` means the valid review was the winner, including a human rejection
that intentionally nulls the effective cell. `ignored` means the binding was
valid but a later valid review won. `rejected` means the review could not be
used: stale binding, missing target, undeclared field, or incompatible override
type. Every review has exactly one outcome row; no decision is silently dropped.

This projection proves deterministic review resolution and publication
accounting. It does not authenticate the reviewer or replace the supervisor's
separate pre-call user-authorization gate.

### 2.4 `mapper_evidence`

One-to-many per proposal. Replace-loaded with the proposals it belongs to — the
two are always produced and landed together, from the same in-memory bundle.

| Column | Type | Null? | Key | Description |
|---|---|---|---|---|
| `target_row_key` | `string()` | no | PK | FK to the proposal. |
| `field` | `string()` | no | PK | FK to the proposal. |
| `evidence_ordinal` | `int64()` | no | PK | 0-based, stable within a cell for a given execution. Ordering is by the model's citation order. |
| `locator_kind` | `string()` | no | | `landed_text` \| `page_region` \| `source_field`. Determines which locator columns are meaningful. |
| `source_model` | `string()` | **yes** | | For `landed_text`/`source_field`: which landed model the text came from. |
| `source_row_key` | `string()` | **yes** | | For `landed_text`/`source_field`: identity of the row within that model. |
| `source_field_name` | `string()` | **yes** | | For `source_field`: the column read. |
| `document_hash` | `string()` | **yes** | | For `landed_text`/`page_region`: hash of the source document. |
| `page` | `int64()` | **yes** | | For `landed_text`/`page_region`: 1-based page number. |
| `char_start` | `int64()` | **yes** | | For `landed_text`: offset into the *landed normalized text*, not the raw document. |
| `char_end` | `int64()` | **yes** | | ditto, exclusive. |
| `quote` | `string()` | no | | Verbatim span as cited. Never paraphrase. |
| `verify_status` | `string()` | no | | `verified` \| `evidence_unverified` \| `verify_failed`. See below. |
| `extractor` | `string()` | **yes** | | Name of the text extractor that produced the landed text. Null when `locator_kind = source_field`. |
| `extractor_version` | `string()` | **yes** | | ditto. |
| `text_hash` | `string()` | **yes** | | Hash of the landed normalized text the quote was checked against. Null when unverifiable. |

Uniqueness: `(target_row_key, field, evidence_ordinal)`.

`verify_status` semantics:

- `verified` — the whitespace/case-normalized `quote` is a substring of the
  landed normalized text identified by `text_hash`, at `[char_start, char_end)`.
  **This is the only value that may claim substring verification.** Matching is
  normalization-only: collapse runs of whitespace, casefold. Never fuzzy, never
  edit-distance, never embedding similarity.
- `evidence_unverified` — no canonical landed text exists for this locator
  (scanned page with no OCR artifact, `page_region` provenance). The build does
  **not** claim verification. Legal, surfaced, counted against a per-spec
  threshold.
- `verify_failed` — landed text exists and the quote is *not* a substring of it.
  This is a hallucinated citation. It propagates to the proposal's
  `value_status` (§3) — it is never silently downgraded to `evidence_unverified`.

**The check is against landed text, never against a document blob and never
against text the model itself returned in the same response.** Validating a quote
against model-returned text is circular and proves nothing (design §5).

> **Not `evidence_kind`, and not `nxd_decisions.provenance`.** `evidence_kind`
> (`fact` / `inference`) sits on the *deterministic*
> score-explanation row, answering **how firmly the source supports one reading**.
> `verify_status` answers a mechanical question instead — **did a substring check
> run against landed text, and did it pass**. A hallucinated citation is
> `verify_failed` regardless of how defensible the reading would have been, and an
> honest inference over a correctly-quoted span is `verified` even though its
> `evidence_kind` would be `inference`. Three vocabularies, three grains
> (per-evidence-atom / per-explanation-row / per-ruling); never populate one from
> another. If this harness later emits explanation rows, `evidence_kind` is an
> additional column, not a rename of this one.

---

## 3. `value_status` semantics

Enum, exactly five values. Any other value is a schema violation.

| Value | Precise meaning | Typed value | Evidence | `needs_review` | Build |
|---|---|---|---|---|---|
| `ok` | The model returned a value; it passed type, range, and enum validation; every required evidence atom is present and `verified` (or `evidence_unverified` within the spec's allowance). | exactly one non-null slot | ≥ spec minimum | `false` | ok |
| `evidence_absent` | The model was asked, responded within contract, and reported that the source does not state this field. Not an error. Not a failure. The honest reading of a silent source. | **all null** | 0 required; a "where I looked" atom may be present | `false` by default; `true` if the spec marks the field required | ok |
| `validation_failed` | A value was returned but did not survive the harness's checks after the retry budget was exhausted — out of declared range, not in the declared enum, uncastable to the declared type, or its evidence came back `verify_failed`. The model's answer is discarded; it is never landed as a value. | **all null** | atoms retained for triage, incl. the failing one | `true` | ok **within threshold** |
| `error` | The harness could not obtain a well-formed answer for reasons that are not about this row's content: missing credential, missing dependency, transport failure after backoff, API refusal, or a schema the API rejected. `error_code` names which. | **all null** | 0 | `true` | **see §4** |
| `skipped` | The cell was never dispatched, because the run terminated (budget ceiling, deadline, cancellation, or a blocking `error` elsewhere) before reaching it. Distinguishes "the source is silent" from "we never asked." | **all null** | 0 | `true` | **blocks** |

`skipped` is an addition to the design's four-value list. It is required because
without it, a run that stops at 60% coverage lands 40% of its cells as
`evidence_absent` — indistinguishable from a genuinely silent source, which is
precisely the "100%-sentinel green build" the design forbids. `skipped` makes
partial coverage visibly partial.

Rules that hold for every non-`ok` status:

- **All five typed value slots are null.** There is no "partial" value. A row
  that failed validation does not land the value that failed it.
- **Excluded mechanically from default metrics.** Metric views filter
  `value_status = 'ok'`; a coverage metric reports the non-`ok` share alongside
  any headline number, exactly as `needs_review` share is reported for
  classification (`nxd-run-job-loop/SKILL.md`). A metric that silently averages
  over non-`ok` rows is a contract violation.
- **`needs_review` is derived, never model-asserted.** The model has no input to
  it. It is computed by `validate.py` from status + spec requiredness. See open
  question 3 for how it surfaces to the reviewer.

---

## 4. What blocks the build

The boundary is **systemic failure blocks; row-level absence does not.** Stated
as conditions evaluated after mapping completes, before landing:

| Condition | Blocks? | Rationale |
|---|---|---|
| Any cell `evidence_absent` | no | The source is silent. That is a finding, not a fault. |
| Any cell `validation_failed` | no, until threshold | The model got this cell wrong; the harness caught it. Working as designed. |
| `validation_failed` share > spec's `max_degrade_share` | **yes** | Past some share, "the harness caught it" becomes "the spec doesn't work." Threshold value is open question 1. |
| `evidence_absent` share > spec's `max_absent_share` (when declared) | **yes** | Guards against a prompt/adapter change that silently stops finding anything. Optional — a genuinely sparse source declares no ceiling. |
| Any cell `error` whose `error_code` is systemic | **yes** | The set is **derived** from the `SystemicError` hierarchy in `errors.py` (`SYSTEMIC_ERROR_CODES`), never hand-listed at the gate, so the two cannot drift apart: `credential_missing`, `dependency_missing`, `schema_reject`, `budget_exceeded`, `cancelled`, `model_not_found`, `grant_missing`, `spec_invalid`. One missing credential means every cell was unattempted; equally, a cancelled or budget-exhausted run must not land as a complete one. `coverage_blocked` is excluded, because it is what this gate *raises* — feeding it back would make the decision self-referential. |
| `error` share (transport/refusal) > spec's `max_error_rate` | **yes** | Transport failure above rate is systemic per design §4. |
| `error` share below that rate | no | An isolated 529 that exhausted its backoff is a row-level fact. |
| Any cell `skipped` | **yes** | The run did not complete. Landing a partial run as if complete is the coverage lie. |
| Any evidence `verify_failed` | no directly | It propagates: the owning cell becomes `validation_failed`, and the degrade threshold governs. |
| `evidence_unverified` share > spec's `max_unverified_share` | **yes** | Past a threshold, "we don't claim verification" describes the whole dataset, and the evidence obligation has silently lapsed. |
| Bijection assert fails (§7) | **yes** | Structural. |
| Grant missing or mismatched | **yes**, before any model call — see the note below on what "before" does and does not cover | Design §9. Refused at the dispatch boundary. |
| Preflight estimate exceeds grant ceiling | **yes**, before the first call | Design §10. |
| Remaining budget cannot meet required coverage | **yes**, mid-run, deterministically | Better to fail than to land a silently truncated run. |

Blocking is a raised `FieldMapperError` subclass that fails the transform. It is
never a landed row with a `blocked` status — a blocked build lands nothing,
because a partially-landed governed model is worse than no model.

### What the grant gate does and does not guarantee

Earlier revisions of this document claimed refusal happens "before any source
read". **That is stronger than what the code does, and the claim is withdrawn.**

What actually holds: the grant is checked in `map_inputs` before any content is
sent to a model and before the API key is resolved. Nothing reaches a provider,
and no money is spent, without a matching grant.

The grant binds the model **as the spec names it** — the alias, not the resolved
snapshot (open question 7). A snapshot rollover behind that alias is invisible to
the grant and to `mapper_spec_id`; the ledger's per-call `response.model` is what
makes it auditable after the fact.

What does not hold: callers may already have read source bytes off local disk by
then. `Fixture.load()` hydrates inputs — including `landed_text` from disk —
while building the argument it then passes to `map_inputs`, so a *mismatched* or
*expired* grant is refused after that local read, not before it. Only a wholly
**missing grant file** is refused before it, and only because loading the fixture
fails outright.

This is scoped deliberately rather than fixed, because the two readings protect
different things. The gate is a **consent boundary on disclosure** — it governs
what leaves the process for a third-party model, which is where the consent
question actually bites. It is *not* an access-control boundary on the local
filesystem: the caller already had those bytes, chose to read them, and could
have read them with no harness involved. Promising otherwise would be a
guarantee this library is not positioned to enforce.

Consequence worth stating plainly for anyone building on this: **do not treat
grant refusal as a substitute for filesystem permissions.** If a caller must not
read a document at all, that has to be enforced before the harness is invoked.
A grant governs disclosure to the model, nothing more.

---

## 5. Identifiers and hashing

Four identifiers plus two hashes. All digests are `sha256`, lowercase hex,
truncated to 32 chars for column width, with the full digest in the ledger.

| Id | Determinism | Derivation | Role |
|---|---|---|---|
| `execution_id` | **nondeterministic**, harness-supplied | UUIDv4 from the caller | Execution identity. Joins to the ledger. **Never a business key, never part of any uniqueness assert, never a partition key on a landed model.** |
| `input_snapshot_id` | deterministic | `sha256` over the canonical serialization of every input the spec declares as identity-bearing, in the spec's declared canonical sort order | What was read. Binds reviews. |
| `mapper_spec_id` | deterministic | `sha256` over the canonicalized spec (§below) | Which instruction/config. Binds reviews. Also the schema-cache key. |
| `observation_id` | deterministic | `sha256` over `(target_row_key, field, value_hash, evidence digest, response_hash)` | The observation itself. Two runs producing the same value from the same input with the same citations produce the same `observation_id`. |
| `target_row_key` | deterministic | `sha256` over the spec's declared identity fields + stable source locators, canonicalized | Row identity. §3 of the design. |
| `value_hash` | deterministic | `sha256` over `(value_type, canonical value encoding)`; null values hash the type-tagged null, not `""` | Review binding. |

**Spec canonicalization** (`spec.py`), in order:

1. Serialize to a plain dict with keys sorted lexicographically at every level.
2. Normalize instruction text: strip trailing whitespace per line, normalize line
   endings to `\n`, no other rewriting — the rubric text is the experiment
   variable and must not be silently altered.
3. Include: instruction text, target field list with declared types/ranges/enums,
   grain declaration, cardinality bounds, canonical sort order, duplicate policy,
   evidence requirements, thresholds, model id, and the wire schema bytes.
4. Exclude: anything nondeterministic — `execution_id`, timestamps, file paths,
   the API key, and any comment or description field explicitly marked
   non-semantic.
5. UTF-8 encode, `sha256`.

The exclusion list is a contract, not an optimization: if a path leaks into the
spec hash, `mapper_spec_id` changes when the closure moves directory and every
human review in the dataset auto-invalidates at once.

**`target_row_key` derivation** requires the spec to declare, and rejects the
spec if it does not (design §3):

- identity fields (which input fields determine a target row)
- canonical sort order
- duplicate-disambiguation policy (`reject` | `ordinal_suffix` | `merge_by_rule`)
- expected / min / max cardinality
- stable source locators where identity fields alone are insufficient

`emission_ordinal` participates in `target_row_key` **only** under
`duplicate_policy = ordinal_suffix`, and only after the identity fields are
exhausted — and that policy carries an explicit warning that review binding is
fragile under it, because reordering the source changes the key.

---

## 6. Resolution order

`resolver.resolve()` produces the wide rows and the provenance sidecar from a
single in-memory bundle. Never two passes, never a re-read of a landed table
mid-run.

Per `(target_row_key, field)`:

1. Collect all `mapper_reviews` rows for the cell. Partition into **valid**
   (all bindings match, per §2.2) and **stale**.
2. Among valid reviews, take the one with the greatest `reviewed_at`; ties break
   on `review_id` lexicographically (deterministic, arbitrary, documented).
3. If that review's verdict is `overridden` → the effective value is the
   override, `effective_source = human_override`, and the model's proposal is
   retained in provenance but not in the wide row. **Human override has
   declared precedence over any model proposal.**
4. If `confirmed` → the effective value is the proposal's value,
   `effective_source = model_confirmed`.
5. If `rejected` → the cell is effectively `validation_failed` regardless of the
   proposal's status, `effective_source = human_rejected`, value null.
6. If there is no valid review → the effective value is the proposal's,
   `effective_source = model_proposed`, and `needs_review` carries through.
7. Stale reviews contribute to neither the value nor the status. They are
   emitted into the sidecar with `stale_reason` ∈ `value_changed` |
   `input_changed` | `spec_changed` | `target_not_found` so the reviewer
   can see exactly what came unbound.

The provenance sidecar carries `value_hash`, `effective_source`, `value_status`,
`observation_id`, `evidence_count`, and the stale-review list. It is built in the
same call as the wide rows, from the same objects — not joined afterwards. The
review-outcome projection is emitted from that same `Resolution`; it is not
inferred from the published wide table after the fact.

---

## 7. Consistency asserts

Run before landing; any failure blocks (§4).

1. **Bijection.** Every governed wide cell maps to exactly one effective
   mapped-value record, and every effective record maps to exactly one wide
   cell. No orphans in either direction.
2. **Evidence completeness.** Every `ok` cell has ≥ the spec's minimum evidence
   atoms; every evidence row's `(target_row_key, field)` resolves to an existing
   proposal. **A human override is exempt**: `min_evidence` is a floor on what
   the MODEL must cite, and an override is `ok` because a person stated it, not
   because a model cleared the harness's checks. Applying the floor there would
   block the build on an override of a cell the model never proposed — the case
   §6 exists to support — and contradict the unconditional precedence §6 grants
   an override.
3. **Value-hash agreement.** The sidecar's `value_hash` equals the hash
   recomputed from the wide row's value. Catches the class where the projection
   and the provenance drift.
4. **Cardinality.** Emitted row count is within the spec's declared min/max; the
   per-input cardinality matches the declared shape. **Never** a Tier-1 "output
   count == source count" assert — that is unsatisfiable for N→M and is the
   specific assert `derived-models.md` will need amending for.
5. **Key uniqueness.** `(target_row_key, field)` unique in proposals;
   `(target_row_key, field, evidence_ordinal)` unique in evidence.
6. **Status/value coherence.** `value_status = ok` ⟺ exactly one non-null typed
   slot. Every other status ⟹ all slots null. `error_code` non-null ⟺
   `value_status = error`.
7. **Publication atomicity.** Fault-inject between table resources in the
   acceptance suite to prove the multi-table publication is atomic. **Do not
   assume one `pipeline.run` gives multi-table atomicity** — the design says so
   explicitly, and the assert exists to test the assumption rather than restate it.
8. **Review audit completeness.** `len(review_outcomes) == len(reviews)` and
   every `review_id` is unique. A published run must expose the outcome for
   every durable review, including stale, invalid, missing-target, and
   superseded reviews.

---

## 8. Transport contract

Transport is the `anthropic` SDK. **The model is declared by the spec**, and its
capabilities are NOT universal — this section originally read "Model:
`claude-opus-5`", which silently made one model's dialect the contract.

**Model capability gating.** Reasoning controls — adaptive `thinking` and
`output_config.effort` — exist only on the 4.6 generation and later.
`transport.supports_reasoning_controls(model)` prefix-matches
`_REASONING_CONTROL_MODELS` and gates **both** thinking branches plus `effort`.
Sending any of them to an older model is a 400 that blocks the whole run: the API
refuses before executing, so the same request fails on every cell.

Both defects this gate originally had are now fixed, and the second was
confirmed empirically rather than argued:

- **Unknown no longer means silently no.** `Client._capabilities()` probes the
  Models API once per run and prefers its answer, falling back to the table when
  the probe cannot answer — no SDK, no key, a non-Anthropic provider, or an
  unrecognised model (verified: a bogus model id returns `None`, it does not
  raise). When the two disagree the run says so on the heartbeat rather than
  degrading quietly. The table stays as the no-network path because `preflight`
  and dry runs must work with neither SDK nor key.
- **Two leaves, not one boolean.** `ModelCapabilities` carries
  `adaptive_thinking` and `effort` separately. The live probe shows they
  genuinely diverge:

  | model | `thinking` | adaptive | `effort` |
  |---|---|---|---|
  | `claude-haiku-4-5` | supported | **False** | **False** |
  | `claude-sonnet-5` | supported | True | True |
  | `claude-opus-5` | supported | True | True |

  So haiku accepts a `thinking` parameter while refusing `output_config.effort`.
  One boolean forced the harness to either send `effort` to a model that 400s on
  it, or omit thinking from one that accepts it.

  Not yet exploited: haiku's non-adaptive `thinking` is still omitted rather than
  sent, because a non-adaptive block needs a `budget_tokens` this layer has no
  basis to choose, and a wrong one truncates mid-object.

`_validate_thinking_effort_pairing` encodes an Opus-specific rule (disabled
above `high` effort) and applies it to every model. Harmless today because it
only refuses, but it should be scoped to models that actually emit the controls.

Verified live: a haiku spec 400'd on the ungated path and succeeds through the
gate. The `claude-haiku-4-5` E2E run in `examples/e2e/` exercises it.

**Fixed by this contract:**

- `output_config={"format": {"type": "json_schema", "schema": <compiled>}}`.
  Schema must set `additionalProperties: false` and list `required`.
- **No `temperature`, `top_p`, or `top_k`.** Rejected with 400 on this model and
  rejected by design review independently. Do not add them back "for
  determinism" — they never guaranteed it.
- No `budget_tokens`. Depth is `output_config.effort`, declared by the spec.
- The API key may reach the transform via `.secrets([...])` in `spec.py`, with
  the allowlisted `ANTHROPIC_API_KEY` environment variable as an **opt-in**
  fallback for a CLI or explicitly configured local run: it applies only when
  the caller passes `allow_env=True`, which is not the default. An explicit
  secret wins over it. The key is read
  once into the client and **never** written to the ledger, a record, a log
  line, an error message, or a `repr`.

**Constraints the harness must own, because the API cannot express them:**

JSON Schema here supports `type`, `enum`, `const`, `anyOf`, `$ref`, and string
`format`. It does **not** support `minimum`, `maximum`, `multipleOf`,
`minLength`, or `maxLength`. Therefore `schema.py` routes each spec constraint:

| Spec constraint | Enforced by |
|---|---|
| field type | wire schema (`type`) |
| closed value set | wire schema (`enum`) |
| numeric range | **`validate.py`** — retry on violation |
| string length | **`validate.py`** — retry on violation |
| evidence substring | **`validate.py`** — retry on violation |
| required fields | wire schema (`required`) |

This is where retry earns its place: a range violation is a *recoverable*
model error that the API cannot catch, so re-asking with the violation named is
a legitimate and bounded correction. Retry is **not** a way to paper over
transport failure.

**Retry policy** — two distinct budgets, never conflated:

- *Validation retries*: `max_validation_retries` (spec-declared, default 2). Each
  retry re-sends with the specific violation named. Exhaustion →
  `validation_failed`. Every attempt is ledgered.
- *Transport retries*: for 429 / 500 / 529 / connection errors only, exponential
  backoff with jitter, honoring `retry-after`. Exhaustion → `error` with
  `error_code = transport_exhausted`. **Never** retried: 400, 401, 403, 404 —
  these are systemic and must surface immediately rather than burn budget.

**Four transport outcomes**, not three. The fourth is easy to miss:

1. success → parse, validate
2. exception (4xx/5xx/network) → `error`
3. malformed/unparseable content despite structured output → `error`,
   `error_code = schema_reject`
4. **`stop_reason == "refusal"`** → HTTP 200 with empty or partial content.
   `stop_details` may be null; branch on `stop_reason`, never on `stop_details`.
   → `error` with `error_code = refusal`. Code that reads `content[0]`
   unconditionally breaks here, and it breaks *silently* on a 200.

**Thinking and `max_tokens`.** On a reasoning-capable model (`claude-opus-5` and
its generation) thinking is **on by default** — omitting the parameter runs
adaptive — and `max_tokens` caps thinking *plus* response text together. On a
model without reasoning controls the `thinking` parameter is not merely
defaulted, it is **unknown**, so the harness omits it entirely rather than
sending `{"type": "disabled"}`; sending the disabled form was itself a 400, the
same one the adaptive gate exists to prevent.

A `max_tokens` sized snugly around the expected JSON will truncate mid-object and
surface as a parse failure that looks like a model defect. `transport.py` sizes
`max_tokens` with explicit headroom over the schema's expected output and treats
`stop_reason == "max_tokens"` as `error`/`schema_reject`, never as a partial
answer to salvage.

The default `max_tokens=16_000` is sized for a thinking-on model. On a
non-reasoning model it is a harmless overshoot in the request — but **not** in
the cost estimate, where `per_call_output = max_tokens × retries` makes it the
single largest error term: the live haiku run estimated 16,000 output tokens
against 127 actual. The printed figure is a worst-case bound, not a forecast.

**Budget and execution** (design §10), all declared by the spec, all enforced
before the first call and re-checked before each:

- preflight estimate: call count, input tokens, spend, wall time
- hard ceilings aligned to the grant; exceeding → block
- bounded canary run before the full population
- declared concurrency, rate-limit backoff, deadline, cancellation
- fail when remaining budget cannot meet required coverage — deterministically,
  not when the money runs out
- explicit cap on supported input size (checkpointing is prohibited)
- progress heartbeat so the supervisor readiness gate survives a long run

**Cancellation** leaves undispatched cells as `skipped` (§3), which blocks the
build. A cancelled run must not be landable as a complete one.

**Untrusted input.** All input text is data, never instruction. It is delivered
in a user-turn content block, never interpolated into the system prompt, never
into the schema. Source-identity reconciliation runs *before* mapping; a mismatch
quarantines the input rather than mapping it. Extraction and rubric-scoring are
separate contract types even though they share this transport code — sharing
transport must not become sharing a prompt surface.

---

## 9. Ledger contract

Append-only, JSONL, one line per attempt. Lives under the **ephemeral run
directory**, never the closure root, never `~`.

Per line: `execution_id`, `attempt_id`, `target_row_key`, `field`,
`attempt_index`, `mapper_spec_id`, `input_snapshot_id`, prompt hash, wire-schema
hash, **input content hash** (never content, never base64), exact model snapshot
from `response.model`, API version, request parameters excluding secrets,
response hash, `stop_reason`, token usage, latency, outcome, and the resulting
`value_status`.

Invariants:

- **The API key can never appear.** Asserted by a test that greps the whole
  written ledger for the key value and for any `sk-ant-` prefix.
- No input content, no base64, no quotes from source documents — hashes and
  controlled references only. At 10k × 1MB PDFs, verbatim logging is a second PII
  store and ~10GB before responses.
- Retention, encryption, access control, redaction, crash consistency, and
  export behavior are declared per spec. Crash consistency means: a torn final
  line is detectable and discardable, and the ledger is never read back as
  authoritative state.

**Replay is parser/validator replay, and nothing more.** Re-running a stored
response through the parser and validator tests the parser and the validator. It
does **not** test a changed prompt — that response was generated under the old
prompt. Any claim that prompt iteration costs zero API calls is false for
behavioral comparison, which is the entire Layer-2 loop. `ledger.py` exposes
`replay_validation(...)` and deliberately exposes no function named or shaped
like `replay_prompt`.

---

## 10. Open questions

Carried forward from design §15, plus what this contract surfaced. Five are now
**decided** (1, 4, 5, 6, 7) and are recorded here as decisions rather than
deleted, so the reasoning that produced them survives. Four (2, 3, 8, 9) still
stand — each states why it does not gate use of the shipped harness.

1. **Threshold values. DECIDED: no defaults — the spec must declare them.**
   A too-loose default makes §4 decorative; a too-tight one makes every first run
   block, and a wrong default is worse than an absent one. `Thresholds` therefore
   has no defaults for `max_degrade_share`, `max_error_rate` and
   `max_unverified_share`, and `__post_init__` rejects `None` for each;
   `max_absent_share` stays genuinely optional, because a sparse source
   legitimately declares no ceiling. The consent gate's spec shape requires a
   `thresholds` key for a JSON to count as a mapper spec at all, so a spec that
   forgot them is not silently ungated — it is not a spec.

2. **Concurrency and rate-limit policy, concretely.** Requests in flight,
   per-minute ceiling, whether the ceiling is spec-declared or discovered from
   `x-ratelimit-*` headers, and how concurrency interacts with the deterministic
   budget check (a concurrent overshoot can exceed a ceiling that a serial check
   would have caught). *Blocks:* `transport.py`. Also unresolved: whether the
   Batches API (50% cost, ≤24h) is in scope — it changes the wall-time story for
   10k rows completely, but its results arrive unordered and it rejects the
   `fallbacks` parameter. *Does not gate use:* the shipped transport is serial,
   which is correct and merely slow. Concurrency is a throughput question, not a
   correctness one.

3. **What `needs_review` actually is.** The design flags that value bucket /
   sidecar status / `nxd_decisions` status are not interchangeable. The shipped
   evidence: `needs_review` in `derivation-plan.md` and `derived-models.md` is a
   **value in a dimension** (an explicit bucket a row lands in), while
   `nxd_decisions.status` is `proposed`/`confirmed`/`blocked`. This contract
   models it as a boolean column, which matches *neither*. *Blocks:* `records.py`
   final schema and the metric view. *Needs:* a decision — most likely that the
   boolean is internal and the reviewer-facing surface is a dimension whose
   values are the `value_status` enum, with an `nxd_decisions` row at
   `status = proposed` / `provenance = agent_authored` for the mapper spec as a
   whole. Not resolvable unilaterally. *Does not gate use:* the boolean is
   internal to this harness, and the reviewer-facing surface is the `value_status`
   dimension — settling the vocabulary is an upstream alignment, not a blocker on
   mapping.

9. **Which `nxd_decisions` axes the mapper spec and its rows carry.**
   `provenance` is a required second axis on every `nxd_decisions` row
   (`user_confirmed` | `agent_authored` | `source_derived` | `deferred`),
   orthogonal to `status`, and the Phase D self-check fails a ledger missing
   either. The mapper spec itself is clearly one row — `agent_authored` when the
   agent wrote the instruction text, `user_confirmed` when the user supplied the
   rubric verbatim — and **`provenance` never moves when a review lands**;
   confirmation moves `status`. What is *not* settled: whether the mapper's
   per-cell proposals also owe an `nxd_decisions` row each (almost certainly not
   — that is what `mapper_proposals` is for), and whether a `human_override`
   review flips the spec-level row's `provenance` (it must not, by the
   "provenance never moves" rule, but the override is a genuinely user-authored
   *value*, which is the case that rule was not written against). Separately,
   `mapper_evidence.verify_status` is **not** `evidence_kind`: that
   column answers `fact` vs `inference` for a deterministic band, whereas
   `verify_status` answers whether a substring check ran and passed. Disjoint
   vocabularies, and §2.4 must never be populated from the other.
   *Blocks:* the `nxd_decisions` rows `__main__.py` emits, and Phase D passage.
   *Does not gate use:* this is the decisions-ledger semantics settling, and it
   constrains what the mapper records about itself, not whether it may map.

4. **PDF evidence. DECIDED: covered by `evidence_mode`, not by a PDF library.**
   The three evidence paths are distinct and each is honest about what it proves:
   `evidence_mode: "citations"` has the API extract `cited_text` spans
   server-side from the document, with page locations — structurally
   non-fabricable by the answering model, landing `api_cited`; the substring
   check (`verified`) requires text that has **already landed** in the
   `landed_text` model, of any modality (extracted PDF text, an ASR transcript);
   and media sent direct without citations stays `evidence_unverified` by design.
   Scanned PDFs are not citable, and image citations do not exist. **The one
   residual:** `count_pdf_pages` is stdlib-only and returns `None` for encrypted
   or object-stream-compressed PDFs, so the *cost estimator* degrades to
   unpriceable on those. The mapping path is unaffected. No PDF library is added
   to fix an estimator.

5. **Where `mapper_reviews` physically lives. DECIDED:
   `data/mapper_reviews/batch-NNN.csv`, inside the closure.** Three reasons.
   (a) The closure self-containment invariant makes any outside-the-closure
   location a violation of an already-shipped rule — the generate-dp self-check
   fails `../`-rooted references precisely to stop durable state escaping the
   closure. (b) It survives `write_disposition="replace"`, because dlt
   replace-loads the full glob every run. (c) The edit surface is **adding a new
   batch file, never editing an existing one**, hand-authored or tool-written, so
   review history is append-only like the ledger. **Stated tradeoff:** durable
   human state then sits inside `data/` beside agent-managed exports, protected
   by convention only — the "preserve a file connector's supplied export exactly"
   rule is that convention. The compensating control is the review-binding
   machinery itself: a wiped `data/mapper_reviews/` is *loss* (reviews gone,
   cells re-infer), never *corruption* — `bound_value_hash`,
   `bound_input_snapshot_id` and `bound_mapper_spec_id` make it impossible for a
   review to silently attach to the wrong value.

6. **Harness packaging and version stamping. DECIDED: shipped inside the `nxd`
   package.** A closure that maps imports `nxd.experimental.field_mapper` from
   the package the runtime already installs. Nothing is copied.

   This supersedes an earlier decision to vendor the harness out of the
   installed skill directory into the closure root. That route never ran on the
   platform: the desktop supervisor stages only `transform/main.py` into its
   isolated data directory, so a vendored `field_mapper/` was absent at
   execution and every build died on `ModuleNotFoundError` after passing every
   static gate.

   **Do not vendor a copy.** The consent gate denies the retired spelling by
   name (`grant.vendored_harness`): a vendored harness answers for its own spec
   hash, so no grant bound to it means anything, and the remedy is deleting the
   directory rather than consenting to it. A copy under a DIFFERENT name is
   still undetected — see `reference/self-check.md` § "What Phase G cannot see".

   Drift is still not silent: `harness_version` is an input to
   `mapper_spec_id`, so a release that changes the harness moves every spec id,
   the existing grants stop
   binding, and the consent gate demands fresh ones. The version appears in the
   ledger and in the spec-hash input list for the same reason.

7. **Does the grant bind the model snapshot? DECIDED: the alias, as the spec
   names it.** A snapshot rollover behind an alias is a platform property no
   local gate can observe — a spec pins a model, not a platform — and binding the
   snapshot would invalidate every grant on every rollover and block builds for a
   change the user never made. **Stated limit:** two runs with the same
   `mapper_spec_id` may therefore have run on different snapshots. The
   compensating control is the ledger, which records the exact `response.model`
   per call, so the drift is auditable after the fact even though it is not
   preventable before it.

8. **What happens to a review when only `emission_ordinal` changed.** Under
   `duplicate_policy = ordinal_suffix`, a source reorder changes `target_row_key`
   and mass-invalidates reviews with `stale_reason = input_changed` — technically
   correct, practically a reviewer losing all their work to a no-op source
   change. *Blocks:* nothing immediately (the policy can be forbidden), but it
   determines whether `ordinal_suffix` is a supported policy or a documented
   trap. *Does not gate use:* `ordinal_suffix` is a documented trap today, and
   the default `reject` policy avoids it entirely.
