# Incremental transforms on the local desktop runtime

The default Step 3 ingest is a **full replace** every run — that is what makes
reruns idempotent, and it is the right answer for almost every closure. Read this
only when the source is genuinely append-only, every output model is append-safe,
and re-reading the source whole is not acceptable.

## Contents

- [Before you start: the eligibility gate](#before-you-start-the-eligibility-gate)
- [Two mechanisms, never composed](#two-mechanisms-never-composed)
- [The `transform_state` kwarg](#the-transform_state-kwarg)
- [Addressing the bag: `for_model()` always](#addressing-the-bag-for_model-always)
- [Durability: rows and cursor do NOT share fate](#durability-rows-and-cursor-do-not-share-fate)
- [Write first, advance the cursor second](#write-first-advance-the-cursor-second)
- [Every run yields every promised model](#every-run-yields-every-promised-model)
- [Verifying the write: count rows, not table names](#verifying-the-write-count-rows-not-table-names)
- [Desktop specifics](#desktop-specifics)
- [Reading prior data back out of DuckDB](#reading-prior-data-back-out-of-duckdb)
- [Do NOT](#do-not)
- [Worked transform: append-only source](#worked-transform-append-only-source)

> **`transform_state` round-trips on desktop: writes persist and the previous
> run's bag is replayed.** It is the only sanctioned durable store — every
> hand-rolled alternative is banned in [Do NOT](#do-not) — and a raise does not
> roll the rows back with the cursor
> ([Durability](#durability-rows-and-cursor-do-not-share-fate)).

## Before you start: the eligibility gate

Incrementality is not a property of the source alone. **Every promised model the
transform lands must be append-safe**, which means: a row, once written, is never
revised, re-derived, or re-grained by any later run. Check all four before
writing a line:

1. **The source is append-only.** Rows are only ever added, never updated or
   deleted, and carry a monotonically increasing cursor column.
2. **Every base model is a pass-through of that source.** No cleaning or
   dedupe pass whose result for an old row could change.
3. **Every derived model is append-safe too.** This is the clause authors skip.
   A derived model is append-safe only when each output row is a pure function
   of source rows that are themselves already final. It is **not** append-safe
   when it is:
   - an **aggregate or regrain** (daily/weekly rollups, per-entity counts and
     sums) — a late-arriving event for an already-emitted day means the old
     aggregate row is now wrong, and appending a second row for the same
     declared grain duplicates the primary key;
   - a **dedupe or reclassification** whose verdict for an old row can change;
   - anything whose **derivation logic** you may edit in a later refine cycle —
     rows landed under the old logic stay in the table forever, silently mixed
     with rows landed under the new logic.
4. **A refine cycle will not change the derivation.** Desktop re-runs are refine
   cycles on the same workflow, so this is a live risk, not a hypothetical.

If any promised model fails the gate, the correct answer is one of:

- **Keep the whole closure on full replace.** Almost always right. Say so plainly
  to the author, with the failing model named.
- **Split the disposition by model.** Land append-safe base models with
  `write_disposition="append"` and rebuild the non-append-safe derived models
  with `write_disposition="replace"` in the same run, from the full table read
  back out of DuckDB. **Order matters and getting it wrong is silent: run the
  append lane first, then read the table for the rebuild.** The derived model has
  to see this run's appended rows; read it before the append lands and the derived
  model trails the base table by one run's delta forever, on a green run. Note what
  that forces: `prior_counts` is still read **before** the append lane runs, while
  the rebuild's full-table read comes **after** it. That read is not a validation,
  so it does not weaken the rule in
  [Durability](#durability-rows-and-cursor-do-not-share-fate): validation you *can*
  do before a write still goes before it, and the post-write row-count check still
  runs after, as it must. Only the
  base scan is incremental. Build **one** `dlt.pipeline(...)` object and call
  `run()` on it twice, each call with its own disposition and its own resource
  list; the cursor covers only the base models. Every snippet in this file is
  written against a single pipeline object, so a second one puts you outside what
  any of them has been checked against.

Never append to an aggregate or a regrain. The output is duplicate declared-grain
keys or stale arithmetic, on a green run, with no error.

**Do not escape the gate by leaving a promised model out of the landed set.**
Delivering the failing aggregate as a consume-time `semantic_view` — whether you
drop it from `PHYSICAL_MODELS` or simply never add it — makes the gate pass while
the model the closure promised is never landed: nothing writes it, the row-count
check cannot see it, and the naming assert never covers it. A promised aggregate
belongs in `DERIVED_MODELS` — and so in `PHYSICAL_MODELS`, which is
`BASE_MODELS + DERIVED_MODELS` — including when a refine cycle is what adds it. If
a promised model is not append-safe, use one of the two remedies above; it stays a
landed model either way.

## Two mechanisms, never composed

An incremental closure runs **two separate state mechanisms**. They are not layers
of one thing, and wiring one into the other silently corrupts the output.

| | dlt pipeline state | your watermark |
|---|---|---|
| Holds | dlt's own bookkeeping (load ids, schema) | your cursor / offset |
| Lives in | `pipelines_dir` under the run dir | the kernel's per-workflow SQLite via `transform_state` |
| Lifetime | **ephemeral** — one run, thrown away | **durable** — survives into the next run |
| You read it | never | every run |

**The run-local dlt invariant does not change.** `pipelines_dir` still sits under
the run dir, `DLT_DATA_DIR` is still set, and it is still never `~/.dlt`. Do not
try to get incrementality by making dlt's state durable — that is a different,
unsupported mechanism, and dlt's incremental cursors are not what the kernel
replays.

**The write disposition question is separate and it is the dangerous one.** The
default template lands everything with `write_disposition="replace"`, which
rewrites the table from what this run yielded. If you keep `"replace"` **and**
start yielding only the delta, the table shrinks to just the delta — a green run
that silently destroys history. An incremental transform that yields only new
rows must land them with `write_disposition="append"`, and must accept the
consequence: **`"append"` reruns are not idempotent by themselves.** The
`transform_state` cursor is what makes them idempotent — it is the only thing
stopping a rerun from re-yielding rows it already landed. Get the cursor wrong
and you are back in the duplicate-rows bug class.

So: full-replace closures need no cursor. Incremental closures need `"append"`
**plus** a correct cursor. Never `"replace"` plus a delta.

## The `transform_state` kwarg

Declare a parameter literally named `transform_state` on the
`@data_product.on_transform()` function. The kernel injects the previous
successful run's committed bag; whatever the bag holds when the transform returns
is persisted for the next run.

```python
@data_product.on_transform()
def ingest(duckdb: DuckDbOutput, secrets: dict[str, Any], transform_state) -> None:
    ...
```

Two rules on what the bag may hold:

- **Empty on the first run.** There is no prior run, so each model's bag is an
  empty mapping — never `None`, never absent. Read through
  `.get(key, default)` **on the bag `for_model(...)` returns**, never on
  `transform_state` itself, and pick a default that means "take everything from
  the beginning". `transform_state` is the handle, not a bag: never subscript or
  `.get()` it directly. With one declared model the handle is pre-bound, so flat
  access happens to reach the right bag; with two or more the handle is unbound and
  both directions fail silently — a flat write is dropped, and a flat **read**
  returns your default rather than raising, so the cursor looks like a first run
  and the whole source is re-yielded into an `"append"` table. So never write it —
  at any model count, on any run, not just run one — even where it happens to work;
  [Addressing the bag](#addressing-the-bag-for_model-always) has the mechanism.
- **JSON-serializable values only.** The kernel serializes the bag; it does not
  inspect or coerce it. A `numpy.int64` row count or a `pandas.Timestamp`
  read off a DataFrame is **not** JSON-serializable and fails the commit. Cast at
  the boundary: `int(...)`, `float(...)`, `str(...)`, or `.isoformat()` for a
  timestamp. Store strings and numbers, not objects.

## Addressing the bag: `for_model()` always

**Use `transform_state.for_model("<name>")` in every closure you write, even a
single-model one.** Never index the bag flat.

Addressing is a property of the **declared-model list the kernel seeded into the
handle**, not of how many models the closure promises. Desktop always seeds that
list (see [Desktop specifics](#desktop-specifics)), so you get a
`MultiModelTransformState` with `for_model()` / `generic()` available. With
exactly one declared model that handle is also pre-bound, so flat indexing
*happens* to reach the right bag — with two or more it is unbound and flat writes
go nowhere. That is why `for_model()` is the only form worth writing: it is
correct at every count, and the count crosses without warning.

`for_model()` is always available on desktop. Do **not** guard it with
`try`/`except AttributeError` — that branch is unreachable here, and there is no
fallback store to reach for if it were ([Do NOT](#do-not)); a runtime that cannot
hold a cursor keeps the closure on full replace.

**The failure is silent in both directions**, which is why the rule is absolute.
`transform_state["max_event_id"] = 12345` raises nothing either way: on a
pre-bound handle it happens to persist, on an unbound one it persists **nothing**
while the run stays green and every assert passes. The next run then reads an
empty bag, defaults the cursor to "take everything", and re-yields the whole
source into an `"append"` table — the only symptom is a row count growing by the
full source size every run. And the count is easy to cross without noticing:
`PHYSICAL_MODELS = BASE_MODELS + DERIVED_MODELS` means one base plus one derived
already declares two, so a flat cursor that worked becomes a dropped one the
moment a refine cycle adds a derived model. `for_model()` raises `KeyError`
listing the valid names on a typo, so it cannot fail silently — which is why it is
the only form to write.

```python
events = transform_state.for_model("events")
users = transform_state.for_model("users")
shared = transform_state.generic()

events["max_id"] = int(new_event_max)
users["max_id"] = int(new_user_max)
shared["run_count"] = shared.get("run_count", 0) + 1
```

- `for_model(name)` validates `name` against the declared models and raises
  `KeyError` listing the valid names on a typo. A model never sees another
  model's bag.
- `generic()` is one shared, model-less bag persisted under a reserved key. Use
  it for a cursor that spans models (a single source watermark feeding several
  tables). It is available only when the transform runs **once** for the whole
  run — which is always the case on desktop, since the local driver has no
  per-model dispatch (see [Do NOT](#do-not)).

## Durability: rows and cursor do NOT share fate

**On desktop the two halves of a run have opposite failure behavior. Do not
assume a raise rolls back the rows.**

- **The cursor is transactional.** The kernel folds the `transform_state` bags
  into the committed transaction only when every model succeeded. A run that
  raises persists **no** state — the next run reads back the cursor of the last
  run that *succeeded*.
- **The rows are not.** The local DuckDB storage driver implements no transaction
  protocol: there is no staging clone and no rollback. dlt's writes into the
  DuckDB file are **immediate and permanent** the moment `pipeline.run(...)`
  returns, and a later raise cannot undo them.

So a transform that lands rows and *then* raises leaves the rows durably written
while the cursor stays where it was. That is torn state, and on the next run the
stale cursor re-yields those same rows into an `"append"` table — duplicate rows,
green run, no error.

Two consequences you must design for:

1. **Do every check you can before the write, not after.** Validate the batch —
   row shape, key uniqueness, cursor monotonicity, Step 3b data-quality asserts —
   while it is still just a list in memory. A check that raises *before*
   `pipeline.run(...)` costs nothing. A check that raises *after* it has already
   left rows on disk with an unadvanced cursor.
2. **Make the post-write verification tell you what actually landed**, so a
   failure is diagnosable rather than mysterious. It cannot roll anything back;
   its job is to stop the cursor from advancing over a bad write and to name what
   went wrong. See
   [Verifying the write](#verifying-the-write-count-rows-not-table-names).

Recovering from a torn run means dropping the duplicate rows from the DuckDB
table by hand, or rebuilding the closure from scratch with a full replace. Say so
to the author rather than letting them discover it from a row count.

## Write first, advance the cursor second

Because the bag is persisted from whatever it holds **when the transform
returns**, the assignment statement's position in the function does not by itself
decide anything — but the code between it and the return does. Advance the cursor
only after every operation that can fail has succeeded.

**Correct** — validate, land, verify, then advance:

```python
new_max = max(int(r["event_id"]) for r in new_rows)
_assert_batch_ok(new_rows)                             # 1. check BEFORE writing

pipeline.run(resources, write_disposition="append")    # 2. land the data

landed = _count_rows(...)                              # 3. verify what landed
if landed != expected_total:
    raise RuntimeError(...)                            #    (cursor stays put)

events_state["max_event_id"] = new_max                 # 4. only now advance
```

**Wrong** — advance, then land:

```python
events_state["max_event_id"] = new_max                 # cursor moved first
pipeline.run(resources, write_disposition="append")    # if this raises...
```

The wrong version is a hazard for the case that does **not** raise: any code that
advances the cursor and then *conditionally* skips or filters the write — an
early `return` on an empty batch, a partial write in a loop, a swallowed
exception — commits a cursor past rows that were never landed. Those rows are
then permanently skipped, and the run is green. Put the assignment after the
write and after the verification, with nothing between it and the return that can
decide not to write.

The same reasoning forbids advancing the cursor from the *source* rather than
from what was written: set it from the max value actually yielded into
`pipeline.run(...)`, not from the max value observed while scanning the source.

## Every run yields every promised model

**Build the resource list from `PHYSICAL_MODELS`, not from the models that happen
to have new rows.** A model with an empty delta yields an empty resource; it does
not get dropped from the list. In a split-disposition closure each lane carries its
own list — the append lane is a strict subset of `PHYSICAL_MODELS` by design — and
the invariant is on their **union**: every promised model appears in exactly one
lane, every run. "The resource list" below means the lane a model belongs to.

This looks like pointless work and it is the single most important structural
rule in a multi-model incremental transform. Skipping a model with no delta —
the natural, "obviously correct" optimization — is what produces a cursor that
advances over rows that were never written:

> Two promised models, `events` and `users`. On run 2 only `events` has new
> source rows, so the resource list is built with just the `events` resource.
> `users` is never handed to `pipeline.run(...)` at all. But **both** cursors are
> assigned and both bags are committed at the end of the run. `users`' watermark
> now claims rows were landed that no `pipeline.run(...)` ever saw. Those rows
> are skipped on every subsequent run, forever, with a green run and no error.

Yielding every model every run makes the cursor advance and the write structurally
inseparable: a model that is in the resource list is a model whose rows went
through `pipeline.run(...)`. Advance a model's cursor **only** in the same branch
that put its rows into the resource list — never unconditionally at the end.

Do **not** try to detect the skip with the table-name assert. It cannot see it —
that is the next section.

## Verifying the write: count rows, not table names

The default full-replace template asserts on
`pipeline.default_schema.data_table_names()`. **Under `"append"` that assert
cannot detect a promised model that was not written this run**, so it is not a
sufficient verification for an incremental transform.

The reason is that dlt rehydrates its schema from the destination. Once a table
exists in the DuckDB file from any previous run, `data_table_names()` reports it
whether or not this run wrote a single row to it. So on run 2 of the two-model
example above, `data_table_names()` returns **both** `events` and `users`, the
assert compares equal, nothing raises, and both cursors commit. The assert only
ever catches unexpected *extra* tables; it never catches a *missing* write.

The same rehydration has a second consequence, in the opposite direction: on a
genuine **first** run the schema is inferred from what flows through the
pipeline, and a table is registered only once at least one row reaches it. A
first run whose delta is empty for every model therefore produces **no** data
tables at all, `data_table_names()` returns `[]`, and the table-name assert
**fails** — on a run that did nothing wrong.

So the transform ends up with **both checks, not one**. The row-count check is an
**addition**, never a replacement: keep the table-name assert (it still enforces
the naming invariant and still catches semantic views leaking into the output),
scope it to what it can actually prove, and add the row-count check beside it.
Swapping one for the other drops the naming invariant. The `.transform-complete`
touch is still mandatory, but it does **not** stay where the default template
leaves it: the template makes it the last statement after the naming assert,
whereas here it goes after **both** checks and before the cursor advance. Touch it
any earlier and the supervisor's readiness gate can report the build ready before
the row-count check raises — the exact failure this section exists to catch.

```python
# Naming invariant: dlt must never write a table we did not promise. Under
# "append" a table can also be absent because THIS run had nothing new for it
# (and on run one, because nothing has ever been written), so check for
# unexpected extras rather than exact equality.
expected = {duckdb.model_tables[model] for model in PHYSICAL_MODELS}
actual = set(pipeline.default_schema.data_table_names())
if actual - expected:
    raise RuntimeError(
        f"dlt produced unpromised tables {sorted(actual - expected)!r}"
    )

# What actually landed. This is the check the cursor advance depends on:
# row counts are the only signal that distinguishes "wrote the delta" from
# "silently wrote nothing".
for model in PHYSICAL_MODELS:
    landed = _table_row_count(duckdb, model)   # 0 when the table does not exist
    # yielded_by_model[model] counts what THIS run handed to pipeline.run(...) for
    # that model — not its delta. For an appended model those are the same thing;
    # for a replaced derived model it is the whole rebuild (the gate has you read
    # the full table back out of DuckDB), so counting a delta here would raise on
    # a correct run.
    yielded = len(yielded_by_model[model])
    # The expectation depends on THIS model's disposition. An appended model adds
    # to what was already there; a replaced model (a derived model the
    # eligibility gate sent back to "replace") is rewritten from this run alone,
    # so prior rows are gone by design and adding them here would raise on a
    # correct run.
    expected_rows = (prior_counts[model] + yielded) if model in APPEND_MODELS else yielded
    if landed != expected_rows:
        raise RuntimeError(
            f"{model}: expected {expected_rows} rows after "
            f"{'append' if model in APPEND_MODELS else 'replace'}, found {landed}"
        )
```

Three names the snippet expects you to have built. `prior_counts` is read from the
table **before** the write, with the same read-back helper as below, defaulting to
`0` when the table does not exist yet. `yielded_by_model` is the per-model row
lists you handed to `pipeline.run(...)` this run — build it as you assemble the
resources, so the count and the write cannot drift apart. `APPEND_MODELS` is the
subset you land with `write_disposition="append"` — for a single-disposition
closure that is all of `PHYSICAL_MODELS`. The check then holds on the first run
(`0 + n == n`), on an empty delta (`n + 0 == n`), on a replaced derived model
(`landed == n`), and still catches the skipped-model case the table-name assert
cannot see.

## Desktop specifics

- **`transform_state` round-trips.** The kernel routes the local Python compute
  driver through its batch module — the module that seeds and folds incremental
  state — and per-model **state seeding** is wired, so the transform receives a
  `MultiModelTransformState` with `for_model()` available and the previous run's
  bag replayed. (Per-model state seeding is a different mechanism from per-model
  execution *dispatch*, which desktop does not do — see the `.when(...)` entry in
  [Do NOT](#do-not). One bag per model, one invocation for all of them.) Write the
  bag and read it back; there is nothing to enable and nothing to check first. A
  platform acceptance test covers this end-to-end across two builds of one
  workflow — run 1 commits a cursor, run 2 must observe
  it. **The product docs' `transform-state.md` scopes `transform_state` to
  `k8s-compute` and calls it a no-op elsewhere; that caveat does not apply to lean
  desktop.** The desktop's local `python-compute` driver routes through the same batch
  module and persists the bag, so do not conclude from that page that the
  parameter does nothing here — an agent that did exactly that hand-rolled the
  watermark this document bans.
- **Persistence is on by default.** The supervisor always hands the kernel a
  per-workflow database path; there is no flag to set and nothing to enable.
- **One `<workflow_key>.sqlite3` per workflow.** State is scoped to the workflow,
  so it survives refine cycles that reuse the same workflow id — a rebuild of the
  same workflow reads back the previous run's committed state. A *different*
  workflow id starts from an empty bag.
- **There is no cron on the desktop runtime.** Runs happen because the user asks for one. Do
  not write guidance or comments in terms of "each scheduled run" or "nightly" —
  the correct framing is "the next run", whenever that is. A refine cycle is a
  run like any other, and it will read the cursor.
- **A refine cycle does not reset the table.** Editing the transform and
  re-running appends to whatever previous runs already landed, under the old
  logic, with the cursor still in place. If a refine changes what a row should
  contain, the already-landed rows do not change with it — that is why the
  eligibility gate rules out derivations you expect to edit.
- The kernel-side store differs from the platform (SQLite locally rather than
  Postgres), but the API and the semantics are identical. Nothing in the
  transform changes between them.

## Reading prior data back out of DuckDB

`DuckDbOutput` exposes `path`, `schema`, `model_tables` and `full_table_name` —
it has **no** query or execute method. To read what previous runs landed (to
count rows for the verification above, or to check for overlap), open the file
directly. **One rule: no `SELECT max(<cursor>)` off the output table, not even as
a post-write assertion.** Reads that merely see the cursor column are fine, but
write the overlap check so it cannot be mistaken for a watermark: ask whether the
delta's keys are *already present* (`SELECT count(*) … WHERE <cursor> IN (…)`, or
`>= :delta_min`), not what the table's maximum is. The full-table read the
[eligibility
gate](#before-you-start-the-eligibility-gate) requires when you rebuild a
non-append-safe derived model with `"replace"` is also sanctioned. A third read
is sanctioned for a follow-up batch: read the full table to verify that the
previous rows remain and the new rows were added. None of these reads is a
watermark; the cursor lives in `transform_state`, and nowhere else.

**Import the module under an alias.** The output port parameter must be named
exactly `duckdb` (the local DuckDB driver requires that name and it cannot be
renamed), so inside the transform body the parameter **shadows** the `duckdb`
module. A bare `import duckdb` plus `duckdb.connect(...)` in the function body
resolves against the `DuckDbOutput` dataclass and raises
`AttributeError: 'DuckDbOutput' object has no attribute 'connect'`. The same
shadowing makes `except duckdb.CatalogException` unreachable. Alias the module at
import time:

```python
import duckdb as duckdb_lib   # the PORT PARAM is named `duckdb` and shadows it


def _table_row_count(duckdb: DuckDbOutput, model: str) -> int:
    """Rows currently landed for `model`; 0 before the first run writes it."""
    table = duckdb.model_tables[model]
    try:
        with duckdb_lib.connect(duckdb.path, read_only=True) as con:
            row = con.execute(
                f'SELECT count(*) FROM "{duckdb.schema}"."{table}"'
            ).fetchone()
    except (duckdb_lib.IOException, duckdb_lib.CatalogException):
        # IOException: the database FILE does not exist yet (first run).
        # CatalogException: the file exists but this table has not been written.
        return 0
    return int(row[0]) if row and row[0] is not None else 0
```

**Catch both exceptions.** They are raised at different stages and only one of
them is about the table:

- `duckdb.IOException` — `connect(path, read_only=True)` against a path that does
  not exist. This is the **first run**, before anything has ever been written.
  `read_only=True` will not create the file, so this is the normal first-run
  path, not an error condition.
- `duckdb.CatalogException` — the file exists but the table does not, e.g. a new
  model added to an existing closure.

Catching only `CatalogException` (the intuitive choice, since the missing thing
is a table) leaves the first run crashing on the connect.

This is a **read**, and it is the one sanctioned use of `duckdb.connect` in a
transform. The Step 3 ban is on *writing* around the port: no `duckdb.connect`
insert or update, no `CREATE TABLE` / `CREATE VIEW` DDL, no direct file write into
staging. All writes still go through dlt with
`dlt.destinations.duckdb(credentials=duckdb.path)`.

Why the cursor rather than a landed-rows watermark, given that a watermark cannot
disagree with what was committed and so is immune to the
[torn state](#durability-rows-and-cursor-do-not-share-fate) above: that immunity
holds only if a failed load leaves a *prefix* in cursor order. A partial load that
is not a prefix advances the watermark past rows that never landed and skips them
permanently — a silent gap, where the cursor's failure mode is a visible duplicate.
The ban covers the assertion case too because a `max()` that exists in the
transform is one refactor from being read, and the row-count check already proves
the write landed. If the source has no usable cursor column at all, the closure is
not eligible for incrementality: keep it on full replace and say so.

## Do NOT

- **Do NOT use `.when(...)` on desktop.** The local Python compute driver
  discards the kernel's per-model execution payloads, so a `.when(...)` DAG never
  dispatches — the models silently never run. Desktop transforms are always a
  single invocation landing every model. (`generic()` is therefore always
  available on desktop; the inner-orchestration restriction on it cannot arise.)
- **Do NOT reference `NXD_TRANSFORM_STATE_SIDECAR_PATH`.** It is a Databricks-only
  mechanism. It does nothing on desktop and putting it in a closure is a
  correctness lie.
- **Do NOT switch to `write_disposition="append"` just to "get incrementality".**
  `"append"` accumulates whatever you yield, every run, forever. It is correct
  **only** paired with a cursor that guarantees you never yield an already-landed
  row. Without the cursor it is exactly the duplicate-rows bug.
- **Do NOT keep `write_disposition="replace"` while yielding only the delta.**
  That rewrites the table to just the delta and destroys every prior row, with a
  green run and no error.
- **Do NOT append to an aggregate, a regrain, or a dedupe.** Only models that
  pass the [eligibility gate](#before-you-start-the-eligibility-gate) may be
  appended; rebuild the rest with `"replace"` in the same run.
- **Do NOT hand-roll durable state.** No JSON sidecar file, no marker table, no
  `SELECT max(<cursor>)` watermark off the output table, no environment variable
  (dlt's own state has its own bullet below). `transform_state` is the mechanism;
  the DuckDB read-back never holds the cursor — see
  [Reading prior data back out of DuckDB](#reading-prior-data-back-out-of-duckdb)
  for the reads it is for.
- **Do NOT index the bag flat.** Use `for_model("<name>")` at every model count —
  a handle pre-bound to a sole model stops being bound the moment the closure
  declares a second, and one derived model is enough. Both directions fail
  silently; see
  [Addressing the bag](#addressing-the-bag-for_model-always).
- **Do NOT drop a model from the resource list because it has no new rows.**
  Yield every promised model every run, and advance a cursor only in the branch
  that wrote that model's rows.
- **Do NOT treat the table-name assert as proof the write happened.** It cannot
  see a missing write under `"append"`. Count rows.
- **Do NOT delete the table-name assert when you add the row-count check.** They
  prove different things — naming invariant vs. what landed — and the transform
  keeps both. Same for the `.transform-complete` touch: adding verification never
  removes it.
- **Do NOT assume a raise rolls back the rows.** The local DuckDB driver has no
  transaction; rows are permanent the moment `pipeline.run(...)` returns. Check
  before writing.
- **Do NOT `import duckdb` and call `duckdb.connect(...)` inside the transform.**
  The port parameter shadows the module. Alias it: `import duckdb as duckdb_lib`.
- **Do NOT guard the read-back with `except duckdb.CatalogException` alone.** A
  first run raises `IOException` from the connect, before any table lookup.
- **Do NOT make dlt's `pipelines_dir` or `DLT_DATA_DIR` durable.** They stay under
  the run dir. The watermark lives in `transform_state`, nowhere else.
- **Do NOT store non-JSON values** in the bag (numpy/pandas scalars, `datetime`,
  `Decimal`, sets, DataFrames). Cast first.
- **Do NOT default the first-run cursor to "now"** or to the source's current max.
  That skips the entire existing history on run one.

## Worked transform: append-only source

An append-only event export: one CSV directory whose files only ever gain rows
with a monotonically increasing `event_id`. The single promised model `events` is
a pass-through of that source, so it passes the eligibility gate. Every clause of
the Step 3 contract survives — typed `DuckDbOutput`, config from `secrets`,
run-local dlt state, writes through the port, `PHYSICAL_MODELS` from
`.promise(...)`, the naming assert, the `.transform-complete` touch. The changes
are the `transform_state` kwarg addressed through `for_model()`, the row filter,
`"append"` instead of `"replace"`, the row-count verification, and the cursor
advance placed last.

```python
"""<dp-name>: incrementally load the append-only event export into DuckDB."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

# Source-checkout shim: see reference/derived-models.md. Copy it verbatim.

import dlt
import duckdb as duckdb_lib  # aliased: the port param below is named `duckdb`

from nxd import data_product
from nxd.core.context import DuckDbOutput

BASE_MODELS = ("events",)
DERIVED_MODELS = ()
PHYSICAL_MODELS = BASE_MODELS + DERIVED_MODELS


def _table_row_count(duckdb: DuckDbOutput, model: str) -> int:
    """Rows currently landed for `model`; 0 before the first run writes it."""
    try:
        with duckdb_lib.connect(duckdb.path, read_only=True) as con:
            row = con.execute(
                f'SELECT count(*) FROM "{duckdb.schema}"."{duckdb.model_tables[model]}"'
            ).fetchone()
    except (duckdb_lib.IOException, duckdb_lib.CatalogException):
        # IOException: DB file absent (first run). CatalogException: table absent.
        return 0
    return int(row[0]) if row and row[0] is not None else 0


@data_product.on_transform()
def ingest(
    duckdb: DuckDbOutput,
    secrets: dict[str, Any],
    transform_state,
) -> None:
    """Land only events newer than the previous run's committed cursor."""
    source_root = Path(secrets["csv_source"])
    # for_model() always. With one declared model a flat cursor only HAPPENS to
    # reach the right bag; it is dropped SILENTLY the moment the closure declares a
    # second, and one derived model added in a refine cycle is enough.
    events_state = transform_state.for_model("events")
    # First run has no prior state: 0 means "take everything".
    last_seen = int(events_state.get("max_event_id", 0))

    # Read the source ourselves so we can filter and compute the new cursor.
    rows: list[dict[str, Any]] = []
    for path in sorted((source_root / "events").glob("*.csv")):
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                event_id = int(row["event_id"])
                if event_id > last_seen:
                    row["event_id"] = event_id
                    rows.append(row)

    # Validate BEFORE writing. The local DuckDB driver has no transaction, so a
    # raise after pipeline.run(...) leaves the rows on disk with a stale cursor.
    if len({r["event_id"] for r in rows}) != len(rows):
        raise RuntimeError("duplicate event_id within the new batch")

    # Count what is already landed, to verify the append below.
    prior_count = _table_row_count(duckdb, "events")

    # Keep ALL dlt state run-local (next to the staging file) — never ~/.dlt.
    run_dir = Path(duckdb.path).parent
    pipelines_dir = run_dir / "dlt-pipelines"
    pipelines_dir.mkdir(parents=True, exist_ok=True)
    os.environ["DLT_DATA_DIR"] = str(run_dir / "dlt-data")

    pipeline = dlt.pipeline(
        pipelines_dir=str(pipelines_dir),
        destination=dlt.destinations.duckdb(credentials=duckdb.path),
        dataset_name=duckdb.schema,
    )

    # EVERY promised model goes into the resource list every run, even with an
    # empty delta. Dropping a model whose delta is empty would let its cursor
    # advance over rows no pipeline.run(...) ever saw.
    @dlt.resource(name=duckdb.model_tables["events"])
    def new_events():
        yield from rows

    # "append" — NOT "replace": replace would rewrite the table to just this
    # delta and drop every previously landed event. The cursor below is what
    # keeps "append" idempotent across reruns.
    pipeline.run([new_events()], write_disposition="append")

    # Naming invariant: dlt must never write a table we did not promise. Check
    # for EXTRAS, not exact equality — under "append" a promised table is
    # legitimately absent from the schema until a run actually writes rows to it.
    expected = {duckdb.model_tables[model] for model in PHYSICAL_MODELS}
    actual = set(pipeline.default_schema.data_table_names())
    if actual - expected:
        raise RuntimeError(
            f"dlt produced unpromised tables {sorted(actual - expected)!r}"
        )

    # Verify the write by ROW COUNT. The table-name assert above cannot detect a
    # model that was silently not written: dlt rehydrates its schema from the
    # destination, so a table landed by an earlier run is reported either way.
    # "events" is appended, so the expectation is prior + this run's rows. If you
    # later add a derived model the gate sends back to "replace", that model's
    # expectation is this run's rows ALONE — switch to the disposition-aware form
    # in the Verifying-the-write section rather than extending this line, or the
    # check raises on a correct run.
    landed = _table_row_count(duckdb, "events")
    expected_rows = prior_count + len(rows)
    if landed != expected_rows:
        raise RuntimeError(
            f"events: expected {expected_rows} rows after append, found {landed}"
        )

    # Produce-verification marker: the supervisor's readiness gate waits for it.
    # This is fallible I/O, so it runs BEFORE the cursor advance — if the marker
    # cannot be written the run is not complete, and the cursor must stay put.
    (run_dir / ".transform-complete").touch()

    # Advance the cursor LAST: after the write, after the verification, and after
    # every other operation that can fail — with nothing between it and the return
    # that can decide not to write. Only when this run actually yielded rows.
    # int(...) keeps it JSON-serializable.
    if rows:
        events_state["max_event_id"] = int(max(r["event_id"] for r in rows))


if __name__ == "__main__":
    data_product.main()
```

Note the empty-batch case: when `rows` is empty the cursor is left untouched
rather than reset, dlt lands nothing, and the row-count check passes trivially
(`prior_count + 0 == prior_count`). This holds on the first run too, when the
DuckDB file does not exist yet and `_table_row_count` returns `0` on both sides —
which is why the verification counts rows instead of comparing table names.
