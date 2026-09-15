"""A REAL NXD transform that runs the field mapper. This one executes.

    python3 examples/e2e/transform_main.py            # replay, no API calls
    .venv-live/bin/python examples/e2e/transform_main.py --live

The difference from `run_e2e.py`, and the reason both exist:

- `run_e2e.py` proves the DATA CHAIN — dlt lands rows, the mapper judges them,
  the gate decides, dlt lands the judgements. It calls `map_inputs` directly.
- **this file** proves the PLATFORM ENTRYPOINT — the closure is registered with
  `@data_product.on_transform()` and invoked by nxd's own
  `data_product.run_transform(...)`, so nxd's registry, argument binding, and
  task wrapper are all exercised for real rather than stubbed.

What is genuinely real here:

- `nxd.core` v0.41.x is imported and drives the call. The `ExecutionContext` is
  built by `ExecutionContext.from_json`, nxd's own deserializer — not a stub
  object shaped to look like one. If nxd changes the context schema, this file
  fails to construct rather than quietly drifting.
- `TransformTask` resolves the `context` kwarg through
  `FullContextArgumentProvider`, the same provider a platform run uses.
- dlt and duckdb are real, and so is the whole mapper.

- **The output port is a real `DuckDbOutput`**, resolved from the context by
  nxd's `TransformInputOutputPortArgumentProvider` and injected into the closure
  BY PARAMETER NAME — the same binding a platform run performs. The closure
  reads `duckdb.path`, `duckdb.schema` and `duckdb.model_tables` from it rather
  than from the environment, and asks `full_table_name()` for every physical
  table name.

  This needs the monorepo source on `PYTHONPATH`, because the installed wheel is
  v0.41.26 and `local/duckdb/storage` did not exist yet there. `bootstrap.py`
  handles it; see the README.

What is NOT real, stated plainly:

- No kernel, no transaction, no `transform_state`, no provisioning. The context
  is hand-built rather than produced by the kernel, so this proves the
  ENTRYPOINT and the STORAGE BINDING, not the orchestration that would supply
  them in a cluster.
- The DuckDB file is created by this script, not provisioned by a driver.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))

# BEFORE any `import nxd`. The installed wheel predates `local/duckdb/storage`,
# so without this the DuckDbOutput port cannot be bound at all and the run would
# prove strictly less while looking identical.
from bootstrap import describe, use_monorepo_nxd  # noqa: E402

use_monorepo_nxd()

import dlt  # noqa: E402
import duckdb as duckdb_lib  # noqa: E402

from nxd import data_product  # noqa: E402

try:
    from nxd.core.context import DuckDbOutput  # noqa: E402
except ImportError as exc:  # pragma: no cover - environment-dependent
    # Expected and documented, so it exits with an instruction rather than a
    # traceback: this script binds a real DuckDB output port, which the
    # installed wheel cannot construct. `bootstrap` has already noted on stderr
    # that it fell back to the wheel; this says what to do about it.
    raise SystemExit(
        "This proof binds a real DuckDbOutput port, which the installed nxd "
        "wheel does not provide (it predates local/duckdb/storage).\n"
        "Run it against a monorepo checkout:\n\n"
        "    NXD_MONOREPO_ROOT=/path/to/nxd python3 "
        "examples/e2e/transform_main.py\n\n"
        f"(underlying import error: {exc})"
    ) from exc

from nxd.experimental.field_mapper import (  # noqa: E402
    evaluate_coverage,
    map_inputs,
    resolve,
)
from nxd.experimental.field_mapper.errors import FieldMapperError  # noqa: E402
from nxd.experimental.field_mapper.records import ValueType, reviews_from_csv  # noqa: E402

# The data-chain pieces are imported from the sibling runner rather than
# duplicated: two copies of the fixture data and the replay caller would drift,
# and a divergence between them would be invisible until one of them lied.
from run_e2e import (  # noqa: E402
    BASE_MODEL,
    MAPPER_MODELS,
    REVIEW_OUTCOME_MODEL,
    _REVIEW_OUTCOME_HINTS,
    _EVIDENCE_HINTS,
    _PROPOSAL_HINTS,
    _ReplayCaller,
    _build_grant,
    _build_spec,
    _fake_source_rows,
    _live_caller,
    _mapper_inputs_from_rows,
)

#: Where the closure puts its warehouse. A platform run gets this from the
#: output port; with no port available (see the module docstring) the transform
#: is told through the environment, which is at least explicit.
LIVE_ENV_VAR = "FIELD_MAPPER_E2E_LIVE"


def _context_json(db_path: Path) -> str:
    """The execution context, in nxd's own wire shape.

    A platform run gets this from the kernel with the port resolved by the
    driver. Hand-building it is the one thing still stubbed — but the SHAPE is
    nxd's, parsed by `ExecutionContext.from_json`, and the port is a real
    `local/duckdb/storage` service that nxd materialises into a `DuckDbOutput`.
    A schema change on nxd's side fails here rather than drifting.

    Note `service` is a sibling of `context`, not a key inside it — copied from
    nxd's own `test_context.py` rather than guessed.
    """
    return json.dumps(
        {
            "data_product": "invoice-terms",
            "inputs": [],
            "output_ports": [
                {
                    "name": "duckdb",
                    "service": "local/duckdb/storage",
                    "context": {
                        "type_hint": "DuckDbOutput",
                        "models": [],
                        "data": {
                            "path": str(db_path),
                            "schema": "invoices",
                            # The model -> physical table map the closure reads.
                            # A platform run gets these from the driver; the
                            # transform never hardcodes a table name either way.
                            "model_tables": {
                                m: m
                                for m in (BASE_MODEL,) + MAPPER_MODELS + (REVIEW_OUTCOME_MODEL,)
                            },
                        },
                    },
                }
            ],
        }
    )

#: Set by the closure so the caller can assert on what happened. A platform run
#: would surface this through the task result and the landed tables; here it is
#: how the harness checks the transform did what it claimed.
OUTCOME: dict[str, Any] = {}


@data_product.on_transform()
def ingest(duckdb: DuckDbOutput) -> None:
    """Land invoice documents, judge them with the mapper, land the judgements.

    Registered with nxd's real decorator and invoked by nxd's real
    `run_transform`. The `duckdb` argument is bound BY PARAMETER NAME to the
    output port named `duckdb` in the context, and materialised as a real
    `DuckDbOutput` by nxd's `TransformInputOutputPortArgumentProvider` — the
    same binding a platform run performs. Every physical table name comes from
    `duckdb.model_tables` / `full_table_name()`, never from a literal here.

    The ORDER is the load-bearing part, and it is why this cannot be one
    `pipeline.run`:

        run 1  ->  land base models (the mapper's inputs)
        read   ->  base rows + durable reviews
        map    ->  one call per input, the only network in this closure
        gate   ->  may refuse; if it does, NOTHING from run 2 lands
        run 2  ->  land proposals, evidence, and the wide projection

    A single run would either judge rows that are not landed yet, or land
    judgements before the gate has spoken.
    """
    # Everything physical comes FROM THE PORT.
    db_path = Path(duckdb.path)
    schema = duckdb.schema
    tables = duckdb.model_tables
    live = os.environ.get(LIVE_ENV_VAR) == "1"
    run_dir = db_path.parent

    OUTCOME["port_path"] = duckdb.path
    OUTCOME["port_schema"] = duckdb.schema
    OUTCOME["port_tables"] = dict(duckdb.model_tables)

    os.environ["DLT_DATA_DIR"] = str(run_dir / "dlt-data")
    pipeline = dlt.pipeline(
        pipeline_name="field_mapper_transform",
        pipelines_dir=str(run_dir / "dlt-pipelines"),
        destination=dlt.destinations.duckdb(credentials=str(db_path)),
        dataset_name=schema,
    )

    # ---- run 1: land the mapper's inputs ---------------------------------
    @dlt.resource(name=tables[BASE_MODEL], write_disposition="replace")
    def invoice_documents():
        yield from _fake_source_rows()

    pipeline.run([invoice_documents()])

    # `duckdb_lib`, not `duckdb`: the port argument shadows the library name
    # inside this closure, which is precisely why the import is aliased.
    con = duckdb_lib.connect(str(db_path))
    landed = con.execute(
        f"SELECT invoice_id, vendor, page_text "
        f"FROM {duckdb.full_table_name(BASE_MODEL)} ORDER BY invoice_id"
    ).fetchall()
    con.close()

    # ---- map -------------------------------------------------------------
    spec = _build_spec()
    grant = _build_grant(spec)
    inputs = _mapper_inputs_from_rows(landed)
    caller = _live_caller(spec) if live else _ReplayCaller()

    result = map_inputs(
        inputs,
        spec=spec,
        grant=grant,
        call=caller,
        # Keep attempt ledgers below an explicit run-scoped directory. The
        # mapper refuses a home-directory path that looks like a durable
        # closure root, because rebuild cleanup cannot safely own it.
        run_dir=str(run_dir / "run" / "mapper"),
    )

    # ---- resolve ---------------------------------------------------------
    reviews_file = HERE / "reviews" / "mapper_reviews.csv"
    reviews = (
        reviews_from_csv(reviews_file.read_text(encoding="utf-8"))
        if reviews_file.exists()
        else []
    )
    resolution = resolve(
        result.proposals,
        reviews,
        result.evidence,
        fields=spec.field_names,
        field_types={f.name: ValueType(f.value_type) for f in spec.target_fields},
        # PER FIELD. `min(...)` re-checked a spec declaring 2 and 0 at 0,
        # making the resolve-time backstop weaker than the run-time check it
        # re-checks.
        min_evidence_per_ok_cell={
            f.name: f.min_evidence for f in spec.target_fields
        },
    )

    # ---- the gate --------------------------------------------------------
    report = evaluate_coverage(
        result.cells,
        max_degrade_share=spec.thresholds.max_degrade_share,
        max_error_rate=spec.thresholds.max_error_rate,
        max_absent_share=spec.thresholds.max_absent_share,
        max_unverified_share=spec.thresholds.max_unverified_share,
        raise_on_block=False,
    )
    try:
        resolution.assert_bijection()
        resolution.assert_value_hashes()
        resolution.assert_review_audit_completeness()
        resolution.assert_cardinality(
            min_rows=spec.cardinality.min_rows,
            max_rows=spec.cardinality.max_rows or len(resolution.row_keys),
        )
    except FieldMapperError as exc:
        report.blocked = True
        report.reasons.append(f"structural assert failed: {exc}")

    OUTCOME["status_counts"] = result.status_counts()
    OUTCOME["blocked"] = report.blocked
    OUTCOME["reasons"] = list(report.reasons)

    if report.blocked:
        # RAISED, not returned. A transform that discovers its output is
        # untrustworthy must fail the task — returning quietly would let the
        # platform mark the run successful with nothing landed, which reads as
        # "no new data" rather than "the build was refused".
        raise RuntimeError(
            "field mapper blocked the build; nothing landed:\n  "
            + "\n  ".join(report.reasons)
        )

    # ---- run 2: land the judgements --------------------------------------
    # Fault injection for `--prove-atomicity`. Placed here rather than inside a
    # resource so the crash lands squarely BETWEEN the two committed loads,
    # which is the window CONTRACT §7.7 asks about.
    if os.environ.get(CRASH_ENV_VAR) == "1":
        raise _InjectedCrash("simulated process death between the two loads")

    @dlt.resource(
        name=tables["mapper_proposals"],
        write_disposition="replace",
        columns=_PROPOSAL_HINTS,
    )
    def mapper_proposals():
        yield from (p.as_row() for p in result.proposals)

    @dlt.resource(
        name=tables["mapper_evidence"],
        write_disposition="replace",
        columns=_EVIDENCE_HINTS,
    )
    def mapper_evidence():
        yield from (e.as_row() for e in resolution.evidence)

    @dlt.resource(
        name=tables[REVIEW_OUTCOME_MODEL],
        write_disposition="replace",
        columns=_REVIEW_OUTCOME_HINTS,
    )
    def mapper_review_outcomes():
        yield from resolution.review_outcome_rows

    @dlt.resource(name=tables["invoice_terms"], write_disposition="replace")
    def invoice_terms():
        yield from resolution.wide_rows

    resources = [mapper_proposals(), mapper_evidence()]
    if resolution.review_outcome_rows:
        resources.append(mapper_review_outcomes())
    resources.append(invoice_terms())
    pipeline.run(resources)
    OUTCOME["landed"] = True


#: Set to make the closure raise immediately before its SECOND `pipeline.run`.
#: Fault injection for the atomicity proof; unset in every normal run.
CRASH_ENV_VAR = "FIELD_MAPPER_E2E_CRASH_BEFORE_RUN2"


class _InjectedCrash(RuntimeError):
    """A simulated process death between the two loads. Not a real failure."""


def _prove_atomicity() -> int:
    """CONTRACT §7.7: what does a crash between the two loads leave behind?

    The gate proof (`--prove-block`) shows a REFUSED build publishes nothing.
    That is a different property from this one: here the gate PASSED, run 1
    committed, and the process dies before run 2. Nothing in the design prevents
    that, so the question is not "is it atomic" — it is not — but "what exactly
    is a consumer left reading, and is that state detectable?"

    Reports rather than asserts a hoped-for outcome. A test that asserted
    atomicity would have to fail, and a test that asserted nothing would be
    decoration; this pins the ACTUAL post-crash state so a future change that
    alters it shows up as a diff.
    """
    run_dir = HERE / "_atomicity"
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    db_path = run_dir / "warehouse.duckdb"
    os.environ[LIVE_ENV_VAR] = "0"
    os.environ[CRASH_ENV_VAR] = "1"

    print("\n[nxd] run_transform  (atomicity proof: crash before run 2)")
    try:
        data_product.run_transform(_context_json(db_path))
        print("  FAIL: the injected crash did not fire")
        return 1
    except _InjectedCrash:
        print("  crashed between the loads, as injected")
    finally:
        os.environ.pop(CRASH_ENV_VAR, None)

    con = duckdb_lib.connect(str(db_path))
    present = {
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'invoices'"
        ).fetchall()
    }
    base_rows = (
        con.execute(f"SELECT count(*) FROM invoices.{BASE_MODEL}").fetchone()[0]
        if BASE_MODEL in present
        else 0
    )
    con.close()
    shutil.rmtree(run_dir, ignore_errors=True)

    judgements = sorted(present & set(MAPPER_MODELS))
    print(f"  run-1 rows landed:   {base_rows}")
    print(f"  run-2 tables present: {judgements or 'none'}")

    # The state that actually exists, named plainly.
    if base_rows and not judgements:
        print(
            "\n  CONFIRMED NON-ATOMIC, and this is the shape of it:\n"
            "    inputs are landed and judgements are absent. A consumer joining\n"
            "    invoice_documents to mapper_proposals gets ZERO rows — not\n"
            "    wrong values, and not a partially-judged population.\n"
            "\n  Detectable: the judgement tables are missing entirely, so a\n"
            "  consumer fails loudly rather than reading a half-built table.\n"
            "  A re-run replace-loads both sides and repairs it.\n"
            "\n  NOT SAFE if a previous successful run left judgement tables\n"
            "  behind: those are STALE relative to the inputs that just landed,\n"
            "  and nothing marks them so. That is the real exposure, and it is\n"
            "  why CONTRACT §7.7 wants one transaction across both loads."
        )
        return _prove_stale_judgements_survive()
    if judgements:
        print(
            "\n  UNEXPECTED: judgement tables exist after a crash before run 2.\n"
            "  Either the injection fired late or dlt committed early."
        )
        return 1
    print("\n  UNEXPECTED: run 1 landed nothing; the injection fired too early.")
    return 1


def _prove_stale_judgements_survive() -> int:
    """The dangerous half: a crash AFTER a previous successful run.

    Run once cleanly so judgement tables exist. Then change the inputs, run
    again, and crash between the loads. The new inputs are landed; the OLD
    judgements are still sitting there, now describing rows that no longer
    exist. Nothing in the schema says so.
    """
    import dataclasses

    _re = sys.modules[__name__]
    run_dir = HERE / "_atomicity_stale"
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    db_path = run_dir / "warehouse.duckdb"
    os.environ[LIVE_ENV_VAR] = "0"

    print("\n  --- second scenario: crash after a PREVIOUS successful run ---")
    data_product.run_transform(_context_json(db_path))
    con = duckdb_lib.connect(str(db_path))
    first = con.execute(
        "SELECT count(*), min(invoice_ref) FROM invoices.invoice_terms"
    ).fetchone()
    con.close()
    print(f"  run A landed cleanly: {first[0]} judged row(s)")

    # Drop an input, so run B's landed population is genuinely smaller than the
    # judgements run A left behind. Identity is untouched: the replay answer
    # table is keyed by invoice_id, and renaming them would fail the run for an
    # unrelated reason (KeyError) instead of proving anything about staleness.
    original = _re._fake_source_rows

    def _fewer() -> Any:
        return [dict(r) for r in original()][:1]

    # The spec declares min_rows 3, so a one-input run would BLOCK on
    # cardinality before ever reaching the injected crash — and prove nothing.
    # Relaxing it here keeps the scenario about atomicity rather than about the
    # gate, which `--prove-block` already covers.
    original_spec = _re._build_spec

    def _relaxed() -> Any:
        spec = original_spec()
        return dataclasses.replace(
            spec,
            cardinality=dataclasses.replace(spec.cardinality, min_rows=1),
        )

    _re._fake_source_rows = _fewer
    _re._build_spec = _relaxed
    os.environ[CRASH_ENV_VAR] = "1"
    try:
        data_product.run_transform(_context_json(db_path))
        print("  FAIL: the injected crash did not fire on run B")
        return 1
    except _InjectedCrash:
        print("  run B crashed between the loads, as injected")
    finally:
        os.environ.pop(CRASH_ENV_VAR, None)
        _re._fake_source_rows = original
        _re._build_spec = original_spec

    con = duckdb_lib.connect(str(db_path))
    inputs_now = [
        r[0]
        for r in con.execute(
            f"SELECT invoice_id FROM invoices.{BASE_MODEL} ORDER BY 1"
        ).fetchall()
    ]
    judged_now = [
        r[0]
        for r in con.execute(
            "SELECT invoice_ref FROM invoices.invoice_terms ORDER BY 1"
        ).fetchall()
    ]
    orphaned = con.execute(
        f"SELECT count(*) FROM invoices.invoice_terms t "
        f"LEFT JOIN invoices.{BASE_MODEL} d ON d.invoice_id = t.invoice_ref "
        f"WHERE d.invoice_id IS NULL"
    ).fetchone()[0]
    con.close()
    shutil.rmtree(run_dir, ignore_errors=True)

    print(f"  inputs now:     {inputs_now}")
    print(f"  judgements now: {judged_now}")
    print(f"  judgement rows with no matching input: {orphaned}")

    if orphaned and len(judged_now) > len(inputs_now):
        print(
            f"\n  CONFIRMED, and this is the exposure CONTRACT §7.7 names:\n"
            f"    invoice_terms still holds {len(judged_now)} judged rows while\n"
            f"    only {len(inputs_now)} input(s) are landed. {orphaned} of those\n"
            f"    judgements describe inputs that are GONE, and nothing in the\n"
            f"    schema marks them stale.\n"
            f"\n  Worse than the first scenario, because it is NOT detectable by\n"
            f"  absence: invoice_terms is present, populated, and internally\n"
            f"  consistent. A consumer reading it alone sees a plausible table\n"
            f"  that is partly obsolete — the survivors and the orphans look\n"
            f"  identical.\n"
            f"\n  Mitigation available today: join judgements to inputs on the\n"
            f"  identity column and drop non-matching rows, or filter on the\n"
            f"  execution_id every proposal already carries — a stale row's\n"
            f"  execution_id is not the latest one.\n"
            f"\n  Real fix: both loads in ONE transaction. dlt gives that per\n"
            f"  pipeline.run, not across two, and the mapper needs two because\n"
            f"  it must read landed inputs before it can judge them."
        )
        return 0
    print(
        f"\n  UNEXPECTED: expected orphaned judgements, got "
        f"{orphaned} orphan(s) across {len(judged_now)} judged row(s) "
        f"and {len(inputs_now)} input(s)."
    )
    return 1


def _prove_block() -> int:
    """The gate must REFUSE, and nothing from run 2 may exist when it does.

    A gate that has never been seen to block is not a proven gate. This drives
    the same real `run_transform`, with `max_absent_share` tightened to 0 so the
    one genuinely-absent payment term breaches it, then checks the DATABASE
    rather than the return value: `invoice_documents` (run 1) must be present
    and every judgement table (run 2) must be absent.

    That ordering is the whole reason the closure is two `pipeline.run` calls.
    If the gate ran after publication, this check would find the judgement
    tables sitting there under a build that was refused.
    """
    import dataclasses

    # Patch THIS module's binding, not `run_e2e`'s. `_build_spec` was imported
    # by name at load time, so the closure holds a reference in this module's
    # namespace; rebinding it in run_e2e leaves the closure calling the
    # original. (First attempt did exactly that and the gate silently did not
    # block — a monkeypatch that misses looks identical to a check that passes.)
    _re = sys.modules[__name__]

    run_dir = HERE / "_blockproof"
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    db_path = run_dir / "warehouse.duckdb"
    os.environ[LIVE_ENV_VAR] = "0"

    original = _re._build_spec

    def _strict() -> Any:
        spec = original()
        return dataclasses.replace(
            spec,
            thresholds=dataclasses.replace(spec.thresholds, max_absent_share=0.0),
        )

    _re._build_spec = _strict
    print("\n[nxd] run_transform  (block proof: max_absent_share=0)")
    try:
        data_product.run_transform(_context_json(db_path))
        print("  FAIL: the transform returned; the gate did not block")
        return 1
    except RuntimeError as exc:
        print(f"  blocked, as required: {str(exc).splitlines()[0]}")
    finally:
        _re._build_spec = original

    con = duckdb_lib.connect(str(db_path))
    present = {
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'invoices'"
        ).fetchall()
    }
    con.close()
    shutil.rmtree(run_dir, ignore_errors=True)

    leaked = sorted(present & set(MAPPER_MODELS))
    print(f"  run-1 table present: {BASE_MODEL in present}")
    print(f"  run-2 tables leaked: {leaked or 'none'}")
    if BASE_MODEL not in present:
        print("  FAIL: run 1 did not land, so this proves nothing about run 2")
        return 1
    if leaked:
        print("  FAIL: a blocked build published judgements")
        return 1
    print("\nOK: the gate refused and NOTHING from run 2 landed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the mapper as a real nxd transform.")
    ap.add_argument("--live", action="store_true", help="make real API calls")
    ap.add_argument("--keep", action="store_true", help="keep the duckdb file")
    ap.add_argument(
        "--prove-block",
        action="store_true",
        help=(
            "tighten max_absent_share to 0 so the genuinely-absent payment term "
            "breaches it, then assert the transform RAISED and no judgement "
            "table exists"
        ),
    )
    ap.add_argument(
        "--prove-atomicity",
        action="store_true",
        help=(
            "kill the closure between the two pipeline.run calls and report "
            "what the database is left holding (CONTRACT §7.7)"
        ),
    )
    args = ap.parse_args(argv)
    if args.prove_block:
        return _prove_block()
    if args.prove_atomicity:
        return _prove_atomicity()

    run_dir = HERE / "_transform_run"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    db_path = run_dir / "warehouse.duckdb"
    os.environ[LIVE_ENV_VAR] = "1" if args.live else "0"

    context_json = _context_json(db_path)

    # Which nxd answered decides what a green run proves, so it is printed
    # rather than assumed: on the older wheel the DuckDbOutput binding does not
    # exist and the run would fail rather than silently prove less.
    print(f"\n[nxd] {describe()}")
    print(f"[nxd] run_transform  ({'LIVE' if args.live else 'replay'})")
    try:
        data_product.run_transform(context_json)
    except RuntimeError as exc:
        print(f"\n  TRANSFORM RAISED (the gate refused):\n    {exc}")
        return 2

    print("[nxd] transform returned cleanly")
    # Read back off the PORT the closure was handed, which is the thing being
    # proven: nxd resolved it and injected it by parameter name.
    print(f"      port path:     {OUTCOME.get('port_path')}")
    print(f"      port schema:   {OUTCOME.get('port_schema')}")
    print(f"      port tables:   {sorted(OUTCOME.get('port_tables') or {})}")
    print(f"      status counts: {OUTCOME.get('status_counts')}")

    # ---- assert on what LANDED, not on what the closure said -------------
    con = duckdb_lib.connect(str(db_path))
    print("\n--- landed tables " + "-" * 42)
    problems: list[str] = []
    for model in (BASE_MODEL,) + MAPPER_MODELS:
        try:
            n = con.execute(f"SELECT count(*) FROM invoices.{model}").fetchone()[0]
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{model}: not queryable — {exc}")
            print(f"  FAIL {model}: {exc}")
            continue
        print(f"  {model:<20} {n:>4} row(s)")
        if n == 0:
            problems.append(f"{model}: landed zero rows")

    print("\n--- invoice_terms (wide) " + "-" * 35)
    for row in con.execute(
        "SELECT target_row_key, invoice_ref, total_usd, payment_terms_days "
        "FROM invoices.invoice_terms ORDER BY 1"
    ).fetchall():
        print("  " + " | ".join("" if v is None else str(v) for v in row))
    con.close()

    if not args.keep:
        shutil.rmtree(run_dir, ignore_errors=True)
    else:
        print(f"\nduckdb kept at {db_path}")

    if problems:
        print(f"\nFAILED: {len(problems)} problem(s)")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(
        "\nOK: the mapper ran inside a real nxd transform, invoked by nxd's own "
        "run_transform,\nand its judgements landed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
