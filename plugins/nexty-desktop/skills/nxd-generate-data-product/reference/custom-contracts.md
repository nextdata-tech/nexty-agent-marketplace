# Explicit custom contracts in desktop closures

## Contents

- What belongs in a custom contract
- CSV-first support boundary
- Generated layout and public DSL wiring
- Verification scripts
- Preflight checks

## What belongs in a custom contract

The active `dp-blueprint.md` is v3 prose-first Markdown. A user expresses guarantees
as natural-language **Expectations** under an Input and **Promises** under an
Output; there is no user-authored `Contracts`, `Delivery`, or standalone policy
payload. The compiler derives the internal contract inventory from those
expectations and promises. The v3 lock records that inventory and the self-check
compares it with actual `spec.py` wiring.

The settled handoff carries what generation needs:

| handoff value | what it decides here |
|---|---|
| `name` | the verifier filename under `contracts/` |
| `model` | which model the verifier reads |
| `phase` | whether the verifier runs before or after the transform |
| `guarantee` | the user's words; the contract's `.description(...)` |
| `rule` | the executable body |
| `fields` | which columns the rule reads |

The handoff must state the attachment point and phase. The extraction layer may
derive those fields from the prose, but must show the interpretation in the
echo-back and ask an Open Question when the attachment is ambiguous. An input
expectation and an output promise must be attached at the phase stated in the
approved proposal.

**A contract never replaces a Step-3b assert.** They check different things: a
contract checks what the user guaranteed about a value, an assert checks the
source-vs-derived relationship, which no verifier can see. Dropping an assert
because a contract covers the same column removes the only check that would
catch a wrong derivation. Contract names are unique across **both** sections —
the name selects the verifier filename, so a collision silently overwrites.

**Authority is settled in the handoff.** A type, nullability, key or enum
inferred from profiling is a real constraint but is **not** a custom contract:
it belongs in the model and an ordinary `.promise(model)`. Do not promote one
into `contracts/` — that relabels your own inference as the user's guarantee.

If a guarantee is missing a threshold, accepted set, time zone, tolerance or
reconciliation population, that gap belongs back in the spec as an **Open
Questions** entry, not filled in here. Do not work around it by choosing a
number at codegen time.

## CSV-first support boundary

The local desktop runtime supports executable **custom input expectations only for a declared
CSV source-aligned input**. Every desktop source-aligned input — custom or not
— must use `.source(_csv)`, where `_csv` binds exactly to
`/infra-profile/desktop-local#/services/csv-source`. That unlabeled service
uses `nxd:local/file/storage:0.1.0` and is also the transform secret via
`.secrets([_csv])`. Labeled CSV services are supported only as transform
secrets on this runtime; do not bind one through `.input(...).source(...)`.

The input expectation receives `LocalFileInput` and runs **before** the DLT
transform. It reads only its declared `model_paths` from the pinned export.
DLT separately loads that same export during the transform; a verifier does
not receive a DLT loader or connection context.

For a true database or API source, do not pretend the same local runtime can
run a custom input expectation. Explain that input custom verification is not
supported on the CSV-first desktop path and either obtain a CSV export or omit
the executable expectation. Never emit a decorative script that is not wired
to an input.

An output promise may be generated for another connector only when the actual
output storage and contract compute context are known to support the verifier.
That runtime dependency is a ruling: land it as a `## decisions` row in the
spec, with `provenance: agent_authored`, so it travels as reviewable data.
Otherwise explain the gap rather than claiming the promise will execute.

## Generated layout and public DSL wiring

Add one script per contract the spec declares — no more, no fewer:

```
contracts/
├── expectations/
│   └── accepted-currency.py       # exactly one CSV input verifier
└── promises/
    └── order-total-reconciles.py  # exactly one DuckDB output verifier
```

Do not create a `contracts/` directory, placeholder script, or empty
`__init__.py` without an explicit custom contract. A `script(...)` executes the
whole file and the custom name does not select one function, so sharing one
phase-wide script can run unrelated checks. Do not duplicate the ordinary
schema promises: every physical output model still keeps `.promise(model)`.

For a CSV input, define `_csv` and `_compute` once in `spec.py`:

```python
_csv = "/infra-profile/desktop-local#/services/csv-source"
_compute = "/infra-profile/desktop-local#/services/python-compute"
```

The transform explicitly uses one host-owned trigger:

```python
.transform(
    script("transform/main.py")
    .compute(_compute)
    .secrets([_csv])
    .when(simple_sensor(startup=False, when="any"))
)
```

Attach an input expectation to the declared source-aligned CSV input before
the transform. Attach an output promise to the DuckDB output after the
transform, while keeping its schema promise:

```python
.input(
    "orders-source",
    source_aligned_input()
    .source(_csv)
    .config({"model_paths": {"orders": "orders/orders.csv"}})
    .expectation(
        custom("accepted-currency")
        .description("User-stated: currency must be EUR or USD.")
        .model(orders)
        .verify(script("contracts/expectations/accepted-currency.py").compute(_compute))
    ),
)
.output(
    data_product_output()
    .promise(orders)
    .promise(
        custom("order-total-reconciles")
        .description("User-stated: output line totals reconcile to order totals.")
        .model(orders)
        .verify(script("contracts/promises/order-total-reconciles.py").compute(_compute))
    )
    .port("duckdb", storage(_duckdb))
)
```

`compute()` is called on the `script(...)` verifier spec, inside
`verify(...)`; `custom(...).verify(...).compute(...)` is not a valid chain.
Use each name exactly once across input expectations and output promises.

The profile declares:

```yaml
- name: csv-source
  driver: nxd:local/file/storage:0.1.0
  attributes: []
```

Keep `csv-source-path` relative. It selects the pinned export root; it is not
a secret value and no absolute host path may enter the closure.
Each CSV source-aligned input also declares `model_paths` relative to that
root, one exact `model: model/model.csv` mapping per attached model. The local
file context uses this mapping; do not scan a root directory or hard-code a
path inside a verifier.

## Verification scripts

Verifier entrypoints must be synchronous `def` functions. The desktop runtime does not await
an `async def` verifier, so the closure self-check rejects coroutine entrypoints.

Each script has exactly one `@data_product.on_verify()` verifier and ends with
`if __name__ == "__main__": data_product.verify()`. It must be parseable and
has one small `verify` function for its named custom contract; make its
diagnostics name the contract, model, and observed/expected values.

Use concrete runtime contexts. **Every context and result type comes from the
one module** — there is no `nxd.core.contract`, and importing it is the natural
guess that fails at verification time, after the transform has already run:

```python
from nxd import data_product
from nxd.core.context import (
    DuckDbOutput,        # output promise context
    LocalFileInput,      # CSV input expectation context
    VerifyResult,
    VerifyResultEnum,
)
```

A CSV expectation takes `LocalFileInput`, reads only `source.path_for("model")`
with `csv.DictReader`. An output promise takes `DuckDbOutput`, resolves its table
through `output.full_table_name("model")`, and opens `output.path` read-only with
`duckdb`. Resolve **every** table that way, including a second one the check
joins to — never spell a table name as a literal.

`VerifyResult`'s second argument is `context: Optional[dict[str, Any]]` — a
**dict, not a message string**. A string is accepted by Python and reaches a
reviewer as an unreadable blob, so make it structured and make it name the
observed-vs-expected values:

```python
    if rows:
        return VerifyResult(
            VerifyResultEnum.FAILED,
            {
                "contract": "order-total-reconciles",
                "model": "orders",
                "guarantee": "output line totals reconcile to order totals",
                "observed_offending_rows": len(rows),
                "expected_offending_rows": 0,
                "examples": repr(rows[:3]),
            },
        )
    return VerifyResult(
        VerifyResultEnum.PASS,
        {"contract": "order-total-reconciles", "model": "orders",
         "observed_offending_rows": 0},
    )
```

The current enum values are `PASS`, `WARNING`, and `FAILED`; use `FAILED` for a
broken user guarantee. A bare `pass`, `...`, or unconditional PASS is a
decorative verifier and fails preflight.

**Write the predicate against the landed types, not the authored ones.** A
column whose CSV carries an empty cell lands as `VARCHAR`, so `TRIM()` on a
column dlt typed as `TIMESTAMP`, or `upper_bound + 1` on one it typed as
`VARCHAR`, fails with a DuckDB binder error at verification time rather than a
failed promise. Cast the reference model's numeric columns in the transform (see
[derived-models.md](derived-models.md) § "Reading the sources yourself") so the
landed type matches what `models.py` declares, and the verifier's SQL can be
written plainly.

**Prove the predicate is non-vacuous before shipping it.** Run it against the
landed rows, then against a deliberately broken copy, and confirm it returns
zero rows and then non-zero. A contract that cannot fail is worse than no
contract: it reports PASS forever and reads as evidence.

Verifiers only inspect their supplied input/output context and return the
runtime's supported pass/fail result. They never mutate tables, repair rows,
call an LLM, hide a failure behind a warning, embed secrets, or resolve a path
outside the closure. An aggregate, reconciliation, or freshness check must use
the runtime context actually supplied by the corresponding contract phase; if
that context cannot provide the needed rows or clock, record it as unsupported
instead of writing a fake check.

## Preflight checks

The closure self-check must fail when any custom-contract invariant is broken:

- a referenced `contracts/**/*.py` file is absent, decorative, outside the
  closure, cannot be parsed, has anything other than one registered verifier,
  or does not invoke `data_product.verify()` under its main guard;
- a custom contract has no literal unique name, no non-empty `.description(...)`,
  no `.model(...)`, or no verifier;
- any desktop source-aligned input is not `.source(_csv)` with `_csv` bound to
  the exact unlabeled desktop-local `csv-source`;
- the DuckDB output port is bound to a service other than `duckdb`;
- `csv-source-path` is missing, absolute, or escapes the closure, or a
  `model_paths` entry resolves to no CSV under it;
- `infra-profile.yaml` omits a service `spec.py` references, binds one to the
  wrong driver, or is not named `desktop-local`;
- the verifier script path escapes the closure; or
- a contract script imports `nxd.core.contract`, which does not exist — the
  contexts and result types are in `nxd.core.context`; or
- a contract script carries a literal secret.

**What it does NOT check**, so the gap is stated rather than assumed: it does
not verify *where* a `custom(...)` is attached — a promise wired inside
`.input(...)`, or an expectation on the output, passes — and it approximates
"exports the expected callable" by counting `@data_product.on_verify()`
decorators rather than resolving the symbol.

The self-check remains structural/offline. A pass proves the generated closure
is parseable and wired, not that a live Desktop contract execution succeeded.
