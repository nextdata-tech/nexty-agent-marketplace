"""END-TO-END: dlt -> duckdb -> field mapper -> gate -> dlt -> duckdb.

This is the data chain with the platform parts stubbed out, so it can be proven
rather than described. Its sibling `transform_main.py` proves the other half —
the same closure registered with `@data_product.on_transform()` and invoked by
nxd's own runner.

    python3 examples/e2e/run_e2e.py            # replay, no API calls
    python3 examples/e2e/run_e2e.py --live     # real Anthropic calls

What is real here:

- **dlt** (1.28.x) really loads rows into a real **duckdb** file, twice: once for
  the mapper's inputs, once for its judgements.
- The **field mapper** is the shipped package — same `map_inputs`, same
  validator, same evidence checker, same resolver, same coverage gate.
- The **long-form/wide split** lands three required real tables, plus the
  replace-loaded `mapper_review_outcomes` audit projection when durable
  reviews are present. The wide table and audit sidecars come from the same
  in-memory bundle, so they cannot disagree.

What is stubbed, and why that is honest:

- `nxd.data_product.on_transform` wants a platform `ExecutionContext` carrying
  driver-resolved output ports. Standing that up needs a cluster. The
  `_ExecutionContextStub` below supplies only what this closure reads —
  `model_tables` — so the dlt/duckdb/mapper chain is exercised for real while the
  platform boundary stays a seam. The nxd import is still performed, so a real
  signature change here fails loudly instead of rotting.

The load-bearing detail this file exists to prove is the ORDER:

    run 1  ->  land base models (the mapper's inputs)
    read   ->  base rows + durable mapper_reviews
    map    ->  ONE call per input, the only network in the closure
    gate   ->  may refuse; if it does, NOTHING from run 2 lands
    run 2  ->  land proposals, evidence, review outcomes when present, and the
                wide projection

Two runs, not one. A single run would either judge rows that are not landed yet,
or land judgements before the gate has spoken. See the atomicity caveat at the
bottom: this is exactly where publication is NOT atomic today.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass, replace as dc_replace
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent.parent
sys.path.insert(0, str(PKG_ROOT))

import dlt  # noqa: E402
import duckdb  # noqa: E402

from nxd.experimental.field_mapper import __version__  # noqa: E402
from nxd.experimental.field_mapper import (  # noqa: E402
    Grant,
    MapperInput,
    MapperSpec,
    evaluate_coverage,
    map_inputs,
    resolve,
)
from nxd.experimental.field_mapper.errors import FieldMapperError  # noqa: E402
from nxd.experimental.field_mapper.records import (  # noqa: E402
    EVIDENCE_COLUMNS,
    PROPOSAL_COLUMNS,
    REVIEW_OUTCOME_COLUMNS,
    ValueType,
    reviews_from_csv,
)
from nxd.experimental.field_mapper.schema import ABSENT_SENTINEL, compile_schema  # noqa: E402

# Imported for the signature check described in the module docstring: if the
# platform's transform entrypoint moves, this file should fail at import rather
# than quietly drift out of date.
from nxd import data_product  # noqa: E402,F401

BASE_MODEL = "invoice_documents"
MAPPER_MODELS = ("mapper_proposals", "mapper_evidence", "invoice_terms")
REVIEW_OUTCOME_MODEL = "mapper_review_outcomes"

#: dlt infers column types from the data it sees, so a column that is null in
#: every row of a load is NOT MATERIALIZED at all. On a clean run that silently
#: drops `error_code`, `error_detail`, `value_bool` and `value_timestamp` from
#: the landed proposals table — verified: querying the table after a green run
#: showed all four absent.
#:
#: That breaks the record contract in the worst direction. A consumer joining on
#: `error_code` gets a "column not found" failure rather than the nulls it
#: expects, and it only happens on the runs where NOTHING WENT WRONG — so the
#: schema is unstable exactly when the pipeline looks healthiest.
#:
#: These hints pin the columns that are legitimately null on a clean run.
_PROPOSAL_HINTS: dict[str, Any] = {
    "error_code": {"data_type": "text", "nullable": True},
    "error_detail": {"data_type": "text", "nullable": True},
    "value_string": {"data_type": "text", "nullable": True},
    "value_int": {"data_type": "bigint", "nullable": True},
    "value_float": {"data_type": "double", "nullable": True},
    "value_bool": {"data_type": "bool", "nullable": True},
    "value_timestamp": {"data_type": "timestamp", "nullable": True},
}

_EVIDENCE_HINTS: dict[str, Any] = {
    # These three are null on the landed-text path specifically: the extractor
    # columns are populated only where an external extractor produced the text,
    # and `source_model` only for model-sourced evidence. Found by the
    # conformance check below, not by reading the record definition.
    "source_model": {"data_type": "text", "nullable": True},
    "extractor": {"data_type": "text", "nullable": True},
    "extractor_version": {"data_type": "text", "nullable": True},
    "page": {"data_type": "bigint", "nullable": True},
    "char_start": {"data_type": "bigint", "nullable": True},
    "char_end": {"data_type": "bigint", "nullable": True},
    "quote": {"data_type": "text", "nullable": True},
    "source_row_key": {"data_type": "text", "nullable": True},
    "source_field_name": {"data_type": "text", "nullable": True},
    "document_hash": {"data_type": "text", "nullable": True},
    "text_hash": {"data_type": "text", "nullable": True},
}

_REVIEW_OUTCOME_HINTS: dict[str, Any] = {
    "bound_value_hash": {"data_type": "text", "nullable": True},
    "proposal_value_hash": {"data_type": "text", "nullable": True},
    "proposal_input_snapshot_id": {"data_type": "text", "nullable": True},
    "proposal_mapper_spec_id": {"data_type": "text", "nullable": True},
    "effective_value_hash": {"data_type": "text", "nullable": True},
    "winner_review_id": {"data_type": "text", "nullable": True},
}


@dataclass
class _ExecutionContextStub:
    """The slice of the platform context this closure actually reads.

    Deliberately minimal. A stub that reimplements the platform is a second
    implementation to keep in sync; this one only answers "what is the physical
    table name for this model", which is the single thing the transform needs.
    """

    path: str
    schema: str
    model_tables: dict[str, str]


def _fake_source_rows() -> list[dict[str, Any]]:
    """Stand-in for a real upstream extract.

    The text is deliberately messy in the ways real landed text is messy — a
    label on its own line, an inconsistent currency format, one document where
    the value is simply absent — because a clean fixture would prove only that
    the happy path works.
    """
    return [
        {
            "invoice_id": "INV-1001",
            "vendor": "Acme Freight",
            "page_text": (
                "ACME FREIGHT — INVOICE\n"
                "Invoice Reference: INV-1001\n"
                "Ship date: 2026-03-02\n"
                "Line total\n"
                "USD 1,240.50\n"
                "Payment terms: net 30 days from receipt.\n"
            ),
        },
        {
            "invoice_id": "INV-1002",
            "vendor": "Borealis Logistics",
            "page_text": (
                "BOREALIS LOGISTICS\n"
                "Invoice Reference: INV-1002\n"
                "Line total\n"
                "USD 87.00\n"
                "Payment terms: net 45 days.\n"
            ),
        },
        {
            "invoice_id": "INV-1003",
            "vendor": "Cormorant Shipping",
            "page_text": (
                "CORMORANT SHIPPING\n"
                "Invoice Reference: INV-1003\n"
                "Line total\n"
                "USD 512.75\n"
                # No payment terms line at all. The mapper must land
                # evidence_absent here rather than inventing a plausible 30.
            ),
        },
    ]


def _mapper_inputs_from_rows(rows: Sequence[tuple]) -> list[MapperInput]:
    """Landed `(invoice_id, vendor, page_text)` rows -> MapperInputs.

    Shared with `transform_main.py` rather than duplicated: two copies of this
    would drift, and a divergence between the direct runner and the real
    transform would be invisible until one of them lied about what it proved.

    `landed_text` is deliberately separate from `media`. It is the haystack the
    substring check runs against; merging the two is the circularity the whole
    design exists to prevent.
    """
    return [
        MapperInput(
            input_id=invoice_id,
            identity={"invoice_id": invoice_id},
            fields={"invoice_id": invoice_id, "vendor": vendor},
            landed_text=page_text,
            document_class="invoice",
        )
        for invoice_id, vendor, page_text in rows
    ]


def _build_spec() -> MapperSpec:
    """The mapper spec, as DATA.

    Not a dict literal in a transform: design §12 calls that
    policy-as-transform-literal. It is written to a contract file and loaded
    back, which is what a real data product does.
    """
    spec_path = HERE / "contracts" / "invoice_terms_mapper.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        json.dumps(
            {
                "description": "Read an invoice reference and payment terms out of landed invoice text.",
                "instruction": (
                    "Each source document is the text of one invoice.\n\n"
                    "Read the following:\n\n"
                    "invoice_ref: the invoice reference exactly as printed.\n\n"
                    "total_usd: the line total in US dollars, as a number with "
                    "no currency symbol and no thousands separator.\n\n"
                    "payment_terms_days: the number of days in the payment "
                    "terms. If the document does not state payment terms, "
                    "return the absent sentinel — do not infer a customary "
                    "value.\n\n"
                    "Cite the verbatim span you read each value from."
                ),
                "target_fields": [
                    {
                        "name": "invoice_ref",
                        "value_type": "string",
                        "min_length": 3,
                        "max_length": 32,
                        "required": True,
                        "min_evidence": 1,
                    },
                    {
                        "name": "total_usd",
                        "value_type": "float",
                        "minimum": 0.0,
                        "maximum": 1000000.0,
                        "required": True,
                        "min_evidence": 1,
                    },
                    {
                        "name": "payment_terms_days",
                        "value_type": "int",
                        "minimum": 1,
                        "maximum": 365,
                        "required": False,
                        "min_evidence": 1,
                    },
                ],
                "grain": {
                    "identity_fields": ["invoice_id"],
                    "canonical_sort": ["invoice_id"],
                    "duplicate_policy": "reject",
                    "source_locators": [],
                    "identity_bearing_inputs": ["invoice_id"],
                },
                "cardinality": {
                    "min_rows": 3,
                    "max_rows": 3,
                    "expected_per_input": 1,
                },
                "thresholds": {
                    # One of three documents genuinely has no payment terms, so
                    # a third of the payment_terms_days cells will be absent.
                    # Declaring 0.34 rather than 0.0 is the spec admitting a
                    # known property of the corpus instead of blocking on it.
                    "max_degrade_share": 0.0,
                    "max_error_rate": 0.0,
                    "max_unverified_share": 0.0,
                    "max_absent_share": 0.34,
                    "max_validation_retries": 2,
                },
                "input_adapter": "landed_text",
                "model": "claude-haiku-4-5-20251001",
                "effort": "low",
                "accepts_media": [],
                "spec_version": "1",
            },
            indent=2,
        )
        + "\n"
    )
    spec = MapperSpec.load(spec_path)
    spec = spec.with_wire_schema(compile_schema(spec))
    # Stamp the harness version BEFORE the grant is derived. `map_inputs` stamps
    # it internally, so a grant bound to the unstamped hash refuses the very run
    # it was written for. The first draft of this file hit exactly that and the
    # consent gate caught it — the right failure, for the right reason.
    return dc_replace(spec, harness_version=__version__)


def _build_grant(spec: MapperSpec) -> Grant:
    grant_path = HERE / "contracts" / "invoice_terms_grant.json"
    grant_path.write_text(
        json.dumps(
            {
                # Bound to THIS spec hash. Editing the instruction, the model, or
                # the accepted media types changes the hash and this grant stops
                # matching — deliberately, so a human re-consents to what runs.
                "mapper_spec_id": spec.mapper_spec_id,
                "provider": "anthropic",
                "model": spec.model,
                "purpose": "End-to-end proof: read invoice terms from landed text.",
                "input_fields": ["invoice_id", "vendor", "page_text"],
                "document_classes": ["invoice"],
                "pii_category": "none",
                "recurring": False,
                "max_calls": 20,
                "max_tokens": 200000,
                "max_usd": 2.0,
                "granted_by": "e2e-harness",
                "granted_at": "2026-07-31T00:00:00Z",
            },
            indent=2,
        )
        + "\n"
    )
    return Grant.load(grant_path)


class _ReplayCaller:
    """Deterministic stand-in for the model, keyed by invoice id.

    Answers are correct for 1001/1002 and deliberately ABSENT for 1003's payment
    terms, so the run exercises `evidence_absent` and the absent-share threshold
    rather than only the happy path.
    """

    def __call__(self, *, item, spec, wire_schema, violations=()):  # noqa: ANN001
        answers = {
            "INV-1001": {
                "invoice_ref": "INV-1001",
                "total_usd": 1240.50,
                "payment_terms_days": 30,
                "_ev": {
                    "invoice_ref": "Invoice Reference: INV-1001",
                    "total_usd": "USD 1,240.50",
                    "payment_terms_days": "Payment terms: net 30 days from receipt.",
                },
            },
            "INV-1002": {
                "invoice_ref": "INV-1002",
                "total_usd": 87.00,
                "payment_terms_days": 45,
                "_ev": {
                    "invoice_ref": "Invoice Reference: INV-1002",
                    "total_usd": "USD 87.00",
                    "payment_terms_days": "Payment terms: net 45 days.",
                },
            },
            "INV-1003": {
                "invoice_ref": "INV-1003",
                "total_usd": 512.75,
                # The real absent sentinel from schema.py, not a plausible
                # invention. This document genuinely states no payment terms.
                "payment_terms_days": ABSENT_SENTINEL,
                "_ev": {
                    "invoice_ref": "Invoice Reference: INV-1003",
                    "total_usd": "USD 512.75",
                },
            },
        }
        a = answers[item.identity["invoice_id"]]
        ev = a["_ev"]
        # FLAT {field: {value, evidence}} — one object per field at the top
        # level. The first draft invented a {"rows": [{"fields": {...}}]}
        # wrapper, which the compiled wire schema does not describe.
        parsed = {
            name: {
                "value": a[name],
                "evidence": (
                    [{"quote": ev[name], "source_field_name": "page_text"}]
                    if name in ev
                    else []
                ),
            }
            for name in ("invoice_ref", "total_usd", "payment_terms_days")
        }
        # The callback contract accepts the parsed mapping directly. Keeping
        # replay on this public shape means the example does not reach into the
        # private transport result types just to model a recorded response.
        return parsed


def _live_caller(spec: MapperSpec):
    """Real Anthropic calls through the supported public adapter."""
    from nxd.experimental.field_mapper import make_call

    # `make_call` owns provider construction, credential resolution, structured
    # response parsing, and the shared budget ledger. The example deliberately
    # does not import the provider SDK or the private transport client: this is
    # the same public seam a generated transform is expected to use.
    grant = _build_grant(spec)
    return make_call(spec=spec, grant=grant, allow_env=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true", help="make real API calls")
    ap.add_argument("--keep", action="store_true", help="keep the duckdb file")
    args = ap.parse_args(argv)

    run_dir = HERE / "_run"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    db_path = run_dir / "warehouse.duckdb"
    os.environ["DLT_DATA_DIR"] = str(run_dir / "dlt-data")

    ctx = _ExecutionContextStub(
        path=str(db_path),
        schema="invoices",
        model_tables={m: m for m in (BASE_MODEL,) + MAPPER_MODELS + (REVIEW_OUTCOME_MODEL,)},
    )

    pipeline = dlt.pipeline(
        pipeline_name="field_mapper_e2e",
        pipelines_dir=str(run_dir / "dlt-pipelines"),
        destination=dlt.destinations.duckdb(credentials=str(db_path)),
        dataset_name=ctx.schema,
    )

    # ---- run 1: land the mapper's inputs --------------------------------
    print("\n[1/6] dlt: landing base model ...")

    @dlt.resource(name=ctx.model_tables[BASE_MODEL], write_disposition="replace")
    def invoice_documents():
        yield from _fake_source_rows()

    pipeline.run([invoice_documents()])
    con = duckdb.connect(str(db_path))
    landed = con.execute(
        f"SELECT invoice_id, vendor, page_text FROM {ctx.schema}.{BASE_MODEL} "
        f"ORDER BY invoice_id"
    ).fetchall()
    print(f"      landed {len(landed)} row(s) into {ctx.schema}.{BASE_MODEL}")

    # ---- read back and build MapperInputs -------------------------------
    print("[2/6] building mapper inputs from LANDED rows ...")
    inputs = _mapper_inputs_from_rows(landed)

    spec = _build_spec()
    grant = _build_grant(spec)
    print(f"      mapper_spec_id {spec.mapper_spec_id}")

    # ---- map -------------------------------------------------------------
    mode = "LIVE" if args.live else "replay"
    print(f"[3/6] mapping ({mode}) ...")
    caller = _live_caller(spec) if args.live else _ReplayCaller()
    try:
        # The ledger intentionally refuses a closure-root-looking path under
        # the home directory. Keep the database at `_run`, but put the
        # attempt ledger under an explicit run-scoped child that the runtime
        # cleanup rules recognize.
        mapper_run_dir = run_dir / "run" / "mapper"
        result = map_inputs(
            inputs,
            spec=spec,
            grant=grant,
            call=caller,
            run_dir=str(mapper_run_dir),
        )
    except FieldMapperError as exc:
        print(f"\n  BLOCKED before landing: [{exc.error_code}] {exc}")
        return 2

    counts = result.status_counts()
    print(f"      {len(result.proposals)} proposal(s): {counts}")

    # ---- resolve ---------------------------------------------------------
    print("[4/6] resolving proposals against durable reviews ...")
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
        # The CLI passes this; omitting it here made the copied gate WEAKER
        # than the one the harness actually ships — an ok cell could carry
        # fewer evidence atoms than its field declares and still resolve.
        # PER FIELD. `min(...)` re-checked a spec declaring 2 and 0 at 0,
        # making the resolve-time backstop weaker than the run-time check it
        # re-checks.
        min_evidence_per_ok_cell={
            f.name: f.min_evidence for f in spec.target_fields
        },
    )
    print(f"      {len(resolution.wide_rows)} wide row(s), {len(reviews)} review(s)")

    # ---- the gate --------------------------------------------------------
    # Mirrors the CLI's gate exactly (`cmd_run`), including folding the §7
    # structural asserts into the SAME report: a build blocks for one reason or
    # for six, and an operator should see all of them at once rather than fix a
    # threshold only to discover a bijection failure on the next run.
    print("[5/6] coverage gate ...")
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

    for status, share in sorted(report.shares.items()):
        if share:
            print(f"      {status}: {share:.1%}")
    for line in report.reasons:
        print(f"      - {line}")
    if report.blocked:
        # BEFORE run 2, never after. Blocking after the landing would publish a
        # build the gate refused.
        print("\n  BLOCKED: nothing landed.")
        return 2
    print("      passed; judgements may land")

    # ---- run 2: land the judgements --------------------------------------
    print("[6/6] dlt: landing judgements ...")

    @dlt.resource(
        name=ctx.model_tables["mapper_proposals"],
        write_disposition="replace",
        columns=_PROPOSAL_HINTS,
    )
    def mapper_proposals():
        # `as_row()` already returns a dict. Zipping it against the column
        # tuple zipped names against KEYS and landed a table whose every row
        # was the column names — a silent corruption that type checks, loads
        # cleanly, and is only visible by querying what landed.
        yield from (p.as_row() for p in result.proposals)

    @dlt.resource(
        name=ctx.model_tables["mapper_evidence"],
        write_disposition="replace",
        columns=_EVIDENCE_HINTS,
    )
    def mapper_evidence():
        yield from (e.as_row() for e in resolution.evidence)

    @dlt.resource(
        name=ctx.model_tables[REVIEW_OUTCOME_MODEL],
        write_disposition="replace",
        columns=_REVIEW_OUTCOME_HINTS,
    )
    def mapper_review_outcomes():
        yield from resolution.review_outcome_rows

    @dlt.resource(name=ctx.model_tables["invoice_terms"], write_disposition="replace")
    def invoice_terms():
        # A PROJECTION of the same bundle the sidecar came from — never computed
        # separately, which is what makes it impossible for the wide table and
        # the provenance to disagree.
        yield from resolution.wide_rows

    resources = [mapper_proposals(), mapper_evidence()]
    if resolution.review_outcome_rows:
        resources.append(mapper_review_outcomes())
    resources.append(invoice_terms())
    pipeline.run(resources)

    # ---- prove it, from the database, not from memory --------------------
    print("\n--- landed tables " + "-" * 42)
    con = duckdb.connect(str(db_path))
    for model in (BASE_MODEL,) + MAPPER_MODELS:
        n = con.execute(
            f"SELECT count(*) FROM {ctx.schema}.{ctx.model_tables[model]}"
        ).fetchone()[0]
        print(f"  {ctx.model_tables[model]:<20} {n:>4} row(s)")

    # ---- schema conformance ----------------------------------------------
    # The record contract is a promise about COLUMNS, not just rows. dlt drops
    # all-null columns unless hinted, so this check is what keeps the landed
    # schema honest on exactly the runs where nothing went wrong.
    print("\n--- record-contract conformance " + "-" * 28)
    schema_problems: list[str] = []
    contract_models = [
        ("mapper_proposals", PROPOSAL_COLUMNS),
        ("mapper_evidence", EVIDENCE_COLUMNS),
    ]
    if resolution.review_outcome_rows:
        contract_models.append((REVIEW_OUTCOME_MODEL, REVIEW_OUTCOME_COLUMNS))
    for model, want in contract_models:
        got = {
            r[0]
            for r in con.execute(
                f"SELECT column_name FROM information_schema.columns "
                f"WHERE table_schema = '{ctx.schema}' AND table_name = '{model}'"
            ).fetchall()
        }
        missing = [c for c in want if c not in got]
        if missing:
            schema_problems.append(f"{model} missing {missing}")
            print(f"  FAIL {model}: missing {missing}")
        else:
            print(f"  ok   {model}: all {len(want)} contract column(s) present")

    print("\n--- invoice_terms (wide) " + "-" * 35)
    cols = [
        r[0]
        for r in con.execute(
            f"SELECT column_name FROM information_schema.columns "
            f"WHERE table_schema = '{ctx.schema}' AND table_name = 'invoice_terms' "
            f"AND column_name NOT LIKE '\\_%' ESCAPE '\\' ORDER BY ordinal_position"
        ).fetchall()
    ]
    rows = con.execute(
        f"SELECT {', '.join(cols)} FROM {ctx.schema}.invoice_terms ORDER BY 1"
    ).fetchall()
    print("  " + " | ".join(cols))
    for row in rows:
        print("  " + " | ".join("" if v is None else str(v) for v in row))

    print("\n--- provenance: every cell, joined to its evidence " + "-" * 9)
    prov = con.execute(
        f"SELECT p.target_row_key, p.field, p.value_status, e.verify_status, e.quote "
        f"FROM {ctx.schema}.mapper_proposals p "
        f"LEFT JOIN {ctx.schema}.mapper_evidence e "
        f"  ON e.target_row_key = p.target_row_key AND e.field = p.field "
        f"ORDER BY p.target_row_key, p.field"
    ).fetchall()
    for row_key, field, status, verify, quote in prov:
        q = "-" if quote is None else '"' + str(quote) + '"'
        v = verify if verify else "-"
        print(
            "  "
            + str(row_key)[:10].ljust(11)
            + str(field).ljust(20)
            + str(status).ljust(17)
            + str(v).ljust(11)
            + q
        )
    con.close()

    print(
        "\nCAVEAT: that was TWO pipeline.run calls, so publication is not atomic "
        "across them.\nA crash between them leaves landed inputs with no "
        "judgements. CONTRACT §7.7 wants a\nfault-injection test proving "
        "otherwise; it does not exist yet."
    )
    if not args.keep:
        shutil.rmtree(run_dir, ignore_errors=True)
    else:
        print(f"\nduckdb kept at {db_path}")
    if schema_problems:
        # Non-zero, or the check is decoration: the pipeline "succeeded" and
        # the landed tables still break the record contract.
        print(f"\nFAILED: {len(schema_problems)} schema conformance problem(s)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
