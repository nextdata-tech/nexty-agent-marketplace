# self_check.py
import ast, hashlib, inspect, json, os, re, stat, sys, tempfile, time, traceback, types
from dataclasses import dataclass, field
from pathlib import Path

# ------------------------------------------------------------ diagnostics ---
# Every phase emits the same record shape ("nxd-diagnostic-v2") alongside the
# prose it has always printed. Two output paths, and they do not mix:
#   * default  — stdout is byte-for-byte what it was. evals/run.py's
#                deterministic check and every scenario checker key on those
#                lines, so a stray print here breaks graders, not just readers.
#   * --json   — ONE "nxd-diagnostic-report-v2" object on stdout and nothing
#                else, so it can be piped straight into a build record.
#
# This file is COPIED INTO THE CLOSURE and run there with a bare interpreter, so
# it can never import the desktop diagnostics helper. The table below is an inlined
# literal subset of that module's registry; evals/tests/
# test_self_check_diagnostic_vocab.py is what keeps the two from drifting.
# severity and owner come from the table and are never chosen per call site:
# `owner` is the field that decides whether a human hears about a diagnostic at
# all, so a producer that could pick it could silence a blocker.
CODES = {}
def _codes(severity, owner, *codes):
    CODES.update({c: (severity, owner) for c in codes})

_codes("error", "agent",
       "struct.import_not_public_dsl", "struct.model_name_not_literal",
       "struct.model_no_description", "struct.view_empty_schema",
       "struct.view_field_not_metric_field", "struct.unknown_dtype",
       "struct.unknown_agg", "struct.agg_expression_forbidden",
       "struct.metric_in_model", "struct.metric_first_arg_not_agg",
       "struct.bad_kwarg", "struct.join_to_model_kwarg",
       "struct.primary_key_takes_no_args", "struct.metric_of_and_column",
       "struct.description_unreachable", "struct.role_no_description",
       "struct.join_target_missing", "struct.no_primary_key",
       "struct.semantic_tools_forbidden", "struct.bad_infra_profile",
       "struct.bad_script_path", "struct.missing_call", "struct.port_not_duckdb",
       "struct.port_no_storage", "struct.promise_of_view",
       "struct.malformed_service_ref",
       "struct.naming_invariant_promised_vs_models",
       "struct.naming_invariant_promised_vs_physical",
       "struct.base_models_vs_data_dirs",
       "struct.optional_models_invalid", "struct.optional_model_promised",
       "struct.optional_model_not_registered")
_codes("warning", "agent", "struct.key_not_groupable")
_codes("warning", "agent", "struct.model_not_queryable")
_codes("info", "agent", "struct.unverified")
_codes("error", "agent",
       "runtime.import_failed", "runtime.transform_raised",
       "runtime.assert_failed", "runtime.base_models_mismatch",
       "runtime.transform_incomplete", "runtime.model_table_missing",
       "runtime.state_unserializable", "runtime.state_not_persisted",
       "runtime.state_flat_write", "runtime.rerun_row_count_changed")
_codes("info", "agent", "runtime.row_count", "runtime.dry_run_not_runnable")
_codes("error", "agent",
       "closure.spec_snapshot_missing", "closure.lock_missing",
       "closure.lock_unparseable", "closure.lock_snapshot_byte_mismatch",
       "closure.spec_hash_mismatch",
       "closure.build_record_missing", "closure.build_record_invalid",
       "closure.build_record_hash_mismatch", "closure.build_record_merge_failed",
       "closure.readme_missing",
       "closure.escaping_reference",
       "closure.gitignore_missing", "closure.sensitive_missing",
       "closure.gitignore_not_naming_profile",
       "closure.contract_not_wired", "closure.contract_verifier_missing",
       "closure.contract_verifier_malformed", "closure.contract_verifier_inert",
       "closure.contract_verifier_unreferenced",
       "closure.contract_verifier_secret", "closure.contract_duplicate_name",
       "closure.contract_inventory_mismatch",
       "closure.terms_hash_mismatch", "closure.contract_inventory_hash_mismatch",
       "closure.decision_inventory_mismatch",
       "closure.profile_service_missing", "closure.profile_driver_mismatch",
       "closure.profile_name_mismatch", "closure.port_storage_mismatch",
       "closure.input_service_mismatch", "closure.csv_root_invalid",
       "closure.model_path_unresolved")
# The one closure.* code the AGENT cannot fix: a snapshot taken from a spec the
# user never approved is a governance fault, and only the user can approve.
_codes("error", "user", "closure.lock_status_not_approved",
       "closure.contract_phase_unsupported")
_codes("info", "agent", "closure.canonical_hash_deferred",
       "closure.legacy_artifact_superseded")
_codes("error", "agent",
       "policy.decisions_not_base_model", "policy.decisions_csv_missing",
       "policy.decisions_column_missing", "policy.decisions_value_out_of_vocab",
       "policy.literal_duplicates_landed_value")
_codes("info", "agent", "semantic.distribution")
_codes("warning", "agent", "semantic.uniform_column", "semantic.absent_vocabulary")
_codes("info", "agent", "meta.stage_not_reached")
# Phase E, the reach gate. Severities and owners match dp_diagnostics.py and
# dp-spec-authoritative.md; a code that reports here but is absent from this
# table raises KeyError inside diag() and takes the whole self-check with it.
_codes("error", "agent",
       "reach.model_sdk_import", "reach.undeclared_transport",
       "reach.connector_shape_mismatch")
_codes("warning", "agent", "reach.connector_undeclared")
# Phase G, the consent gate. Same KeyError hazard as reach.* above.
#
# Three of these are owner: user, which is unusual — every other agent-fixable
# code in this table is owned by the agent. Consent is not a code defect: the
# agent cannot author a grant on the user's behalf, cannot decide that a drifted
# rubric is still acceptable, and cannot extend an expiry. Those three are
# questions FOR the user, exactly like closure.lock_status_not_approved.
_codes("error", "user",
       "grant.missing", "grant.spec_mismatch", "grant.expired")
_codes("error", "agent",
       "grant.invalid", "grant.spec_unreadable", "grant.ungated_map",
       "grant.verifier_maps", "grant.vendored_harness")
_codes("warning", "agent", "grant.unbound")

JSON_MODE = "--json" in sys.argv
RECORD_PATH = None
if "--record" in sys.argv:                 # no argparse: the closure's copy of
    _i = sys.argv.index("--record")        # this script stays small and its
    RECORD_PATH = (sys.argv[_i + 1]        # byte-identical twin stays readable
                   if _i + 1 < len(sys.argv) else "build-record.json")

# The closure's own artifact names. This script is COPIED INTO THE CLOSURE and
# run with a bare interpreter, so the shared diagnostics helper is out of reach
# and its constants have to be inlined here: these are the twins of
# CLOSURE_SNAPSHOT / CLOSURE_LOCK / V3_PROPOSAL_SNAPSHOT, and LEGACY is the twin
# of LEGACY_CLOSURE_LOCK. Before v0.38.0 these three were named after the
# dp-spec; a closure built then is still a valid closure.
CLOSURE_SNAPSHOT = "dp-blueprint.approved.md"
CLOSURE_LOCK = "dp-blueprint.lock.json"
CLOSURE_PROPOSAL = "dp-blueprint.proposal.approved.json"
# Only these two are resolved by NAME. The proposal snapshot is not here on
# purpose: its filename travels inside the lock, so it is resolved from
# lock["proposal_snapshot"] and a legacy entry for it could never fire.
LEGACY = {CLOSURE_SNAPSHOT: "dp-spec.approved.md",
          CLOSURE_LOCK: "dp-spec.lock.json"}


def closure_path(name):
    """Resolve a closure artifact, preferring the current name.

    Falls back to the pre-v0.38.0 spelling, and returns the CURRENT-name path
    when neither exists so a genuinely missing artifact still reports the name
    a fresh closure should have. A name with no legacy spelling degrades to "no
    fallback" rather than raising: this runs inside a user's closure under a
    bare interpreter, where a traceback is the worst possible output.
    """
    current = Path(name)
    if current.is_file():
        return current
    legacy = LEGACY.get(name)
    if legacy and Path(legacy).is_file():
        return Path(legacy)
    return current


DIAGS = []
STAGE_STATE = {"s1_structure": None, "s2_transform": None, "s3_closure": None}
STAGE_DETAIL = {"s1_structure": {}, "s2_transform": {}, "s3_closure": {}}
STAGE_AT = {}
READBACK = {"distribution": [], "absent": []}
ROW_COUNTS = []

# Same pattern the spec validator uses. A check that prints the secret it found
# turns a contained file leak into a transcript leak, and a JSON report is more
# copyable than a scrollback, not less — so message AND evidence go through it.
CREDENTIAL_VALUE_RE = re.compile(
    r"\b(password|passwd|secret|api[_-]?key|token|bearer|private[_-]?key)\b"
    r"\s*[:=]\s*\S+", re.IGNORECASE)

def redact(x):
    if isinstance(x, str):
        return CREDENTIAL_VALUE_RE.sub(lambda m: f"{m.group(1)}=<redacted>", x)
    if isinstance(x, dict):
        return {k: redact(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [redact(v) for v in x]
    return x

def say(*a, **k):
    """Prose stdout. Silent under --json so the report is the whole of stdout."""
    if not JSON_MODE:
        print(*a, **k)

def record_notice(message):
    """Keep record I/O failures visible without contaminating JSON stdout."""
    say(message)
    if JSON_MODE:
        print(message, file=sys.stderr)

def cpath(at):
    return f"closure:{at}" if at else ""

def diag(stage, code, message, *, path="", evidence=None, fix=None):
    sev, owner = CODES[code]
    d = {"schema": "nxd-diagnostic-v2", "stage": stage, "code": code,
         "severity": sev, "owner": owner, "origin": "tool_computed",
         "path": path, "message": redact(message),
         "evidence": redact(evidence or {})}
    if fix:
        d["fix"] = fix
    DIAGS.append(d)
    return d

def close_stage(stage, state, **detail):
    STAGE_STATE[stage] = state
    STAGE_AT[stage] = int(time.time() * 1000)
    STAGE_DETAIL[stage] = detail

def merge_record(path, stages):
    """Merge stages 1-3 into an existing build-record.json, in place.

    The record is created by `dp_diagnostics.py record init` BEFORE this script
    runs — Phase C gates its presence — so a missing record is a real fault and
    is reported rather than papered over by writing a fresh one here.
    """
    p = Path(path)
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        message = (
            f"record: {path} could not be read ({type(exc).__name__}: {exc}) — "
            "stages 1-3 NOT merged. Re-run generator lock/record setup with its "
            "resolved job_helper_dir before self_check.py.")
        record_notice(message)
        diag("s3_closure", "closure.build_record_merge_failed", message,
             path=cpath(path), evidence={"record": path})
        return False
    rec.setdefault("stages", {}).update(stages)
    if READBACK["distribution"] or READBACK["absent"]:
        rec["readback"] = READBACK
    if ROW_COUNTS:
        # Kept under its own key forever. Merging it with published_row_counts
        # would let a scratch-database dry run stand in as evidence that the
        # shipped product has rows.
        rec.setdefault("evidence", {})["phase_b_row_counts"] = {
            "origin": "agent_observed",
            "note": "scratch DuckDB dry run — NOT the published product",
            "models": ROW_COUNTS}
    tmp = p.with_name(p.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        tmp.replace(p)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        message = (
            f"record: {path} could not be written ({type(exc).__name__}: {exc}) — "
            "the previous record is unchanged. Re-run generator lock/record "
            "setup with its resolved job_helper_dir before self_check.py.")
        record_notice(message)
        diag("s3_closure", "closure.build_record_merge_failed", message,
             path=cpath(path), evidence={"record": path})
        return False
    return True

def finish(exit_code):
    """Every exit goes through here, including a failing phase.

    Phases still stop at the first failure — Phase B cannot run over malformed
    code — but the report is emitted BEFORE exiting, with the phases that never
    ran carried as `not_reached`. `not_reached` is a distinct status from
    `passed`: a phase that produced no signal at all did not agree with you.
    """
    stages = {}
    for ordinal, s in ((1, "s1_structure"), (2, "s2_transform"),
                       (3, "s3_closure")):
        if STAGE_STATE[s] is None:
            diag(s, "meta.stage_not_reached",
                 f"{s} did not run and produced no signal at all — an earlier "
                 f"phase failed, or the closure could not be read.")
        ds = [d for d in DIAGS if d["stage"] == s]
        if STAGE_STATE[s] is None:
            status = "not_reached"
        elif STAGE_STATE[s] == "failed":
            status = "failed"
        elif any(d["severity"] == "warning" for d in ds):
            status = "passed_with_warnings"
        else:
            status = "passed"
        stages[s] = {"status": status, "ordinal": ordinal,
                     "at_unix_ms": STAGE_AT.get(s), "origin": "tool_computed",
                     "diagnostics": ds, "detail": STAGE_DETAIL[s]}
    if RECORD_PATH and not merge_record(RECORD_PATH, stages):
        # The record could not carry this failure, so make the process verdict
        # fail as well; the diagnostic already recorded it in DIAGS.
        exit_code = max(exit_code, 1)
    if JSON_MODE:
        counts = {"error": 0, "warning": 0, "info": 0}
        for d in DIAGS:
            counts[d["severity"]] += 1
        try:
            spec_hash = json.loads(
                closure_path(CLOSURE_LOCK).read_text(encoding="utf-8")).get("spec_hash")
        except Exception:
            spec_hash = None
        print(json.dumps({"schema": "nxd-diagnostic-report-v2",
                          "tool": "self_check", "target": str(Path.cwd()),
                          "ok": exit_code == 0, "counts": counts,
                          "spec_hash": spec_hash, "diagnostics": DIAGS},
                         indent=2))
    sys.exit(exit_code)

# ---------------------------------------------------------------- Phase A ---
# Structural check of models.py + spec.py against the pinned nxd.spec surface
# (reference/nxd-spec-api.md, nxd v0.41.139). No imports, no execution.

DTYPES = {
    "string", "boolean", "date", "date32", "date64", "double", "float",
    "float16", "float32", "float64", "int8", "int16", "int32", "int64",
    "uint", "uint8", "uint16", "uint32", "uint64", "number", "vector",
    "binary", "vector_embeddings", "timestamp", "decimal", "duration",
    "time32", "time64", "list", "list_view", "large_list", "large_list_view",
    "map", "dictionary", "struct", "variant",
}
AGGS = {"COUNT", "COUNT_DISTINCT", "SUM", "AVG", "MIN", "MAX", "EXPRESSION"}
KWARGS = {                      # role builder -> allowed keyword names
    "primary_key": set(),
    "dimension": {"name", "pii", "label", "description"},
    "join": {"to", "to_column", "cardinality", "to_data_product"},
    "metric": {"of", "name", "description", "boolean", "extra_dimensions",
               "column"},
    "field": {"roles", "description", "label", "name"},
    "metric_field": {"description", "name"},
}
SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")

errors, unverified, unverified_at = [], [], []
def bad(code, msg, at=""):
    errors.append(msg)
    diag("s1_structure", code, msg, path=cpath(at))

def warn(code, msg, at=""):
    """A real defect that must not fail the closure.

    `bad` fails the phase, which would reject closures that already build,
    publish and answer. A warning still reaches the report and the build record,
    which is what `struct.key_not_groupable` needs: it is invisible at runtime
    (no error, no failed assert, no missing table) but it is not fatal.
    """
    diag("s1_structure", code, msg, path=cpath(at))
    say(f"  warning: {msg}")

def unv(msg, at=""):
    """A construct this static pass cannot see. Recorded, never a failure —
    the printed list is the honest scope boundary, and it belongs in the build
    record rather than in a scrollback nobody keeps."""
    unverified.append(msg)
    unverified_at.append(at)

def call_name(node):
    """Dotted or bare name of a Call's func, or None."""
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None

def spine(node):
    """Every Call in a chained expression, outermost first."""
    out = []
    while isinstance(node, ast.Call):
        out.append(node)
        node = node.func.value if isinstance(node.func, ast.Attribute) else None
    return out

def literal_str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(
        node.value, str) else None

def desc_str(node):
    """Any authored description form: literal, f-string, or concatenation.

    Adjacent parenthesised literals are folded to one Constant by the parser,
    but an f-string is a JoinedStr and a runtime `a + b` is a BinOp. Both are
    legitimately-authored descriptions, so accept them here — the acceptance
    eval's has_description() accepts them too, and the two gates must agree or
    correctly-annotated code fails one of them.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value or None
    if isinstance(node, ast.JoinedStr):
        return ast.unparse(node)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return ast.unparse(node)
    return None

def check_kwargs(call, name, where):
    allowed = KWARGS[name]
    for kw in call.keywords:
        if kw.arg is None:
            unv(f"{where}: **spread into {name}()", where)
            continue
        if kw.arg in allowed:
            continue
        if name == "join" and kw.arg == "to_model":
            bad("struct.join_to_model_kwarg",
                f"{where}: join() takes to=, not to_model=", where)
        else:
            bad("struct.bad_kwarg",
                f"{where}: {name}() has no keyword '{kw.arg}' "
                f"(allowed: {sorted(allowed) or 'none'})", where)
    if name == "primary_key" and call.args:
        bad("struct.primary_key_takes_no_args",
            f"{where}: primary_key() takes no arguments", where)
    if name == "metric":
        if any(k.arg == "of" for k in call.keywords) and \
           any(k.arg == "column" for k in call.keywords):
            bad("struct.metric_of_and_column",
                f"{where}: metric() takes of= or column=, never both", where)
    # Annotation reach, both graded as failures. A description on the WRAPPER
    # is an attribute description: it lands in data_model and never reaches
    # describe_models, so the author believes they documented the concept and
    # did not. A MISSING description is the same defect by omission — and it
    # is the one the benchmark actually measured, so warning here while the
    # eval checks fail it would leave the only mechanical gate green on
    # precisely the defect this guidance exists to prevent.
    if name in ("field", "metric_field") and any(
            k.arg == "description" and desc_str(k.value) for k in call.keywords):
        # A wrapper description is a legal attribute description (it reaches
        # the structural data_model block). The defect is using it INSTEAD of
        # the role's, so only fail when the roles carry none — otherwise this
        # would block a legal API call that is not the mistake.
        roles = [a for a in call.args[1:] if isinstance(a, ast.Call)]
        for kw in call.keywords:          # documented roles=[...] form
            if kw.arg == "roles" and isinstance(kw.value, (ast.List, ast.Tuple)):
                roles += [e for e in kw.value.elts if isinstance(e, ast.Call)]
        describable = [r for r in roles if call_name(r) in ("dimension", "metric")]
        if not any(any(k.arg == "description" and desc_str(k.value)
                       for k in r.keywords) for r in describable):
            # Point at a fix that exists. With no dimension/metric role there
            # is nowhere to move the text to — primary_key()/join() take no
            # description — so the only remedy is to delete it.
            remedy = ("move it inside dimension(...) / metric(...)"
                      if describable else
                      "primary_key()/join() take no description — drop it")
            bad("struct.description_unreachable",
                f"{where}: description= on {name}() never reaches "
                f"describe_models and the role carries none — {remedy}", where)
    if name in ("dimension", "metric") and not any(
            k.arg == "description" and desc_str(k.value)
            for k in call.keywords):
        bad("struct.role_no_description",
            f"{where}: {name}() has no description= — it reaches "
            f"describe_models as a bare name the agent cannot choose on", where)

def check_dtype(node, where):
    """A call in dtype position must be a known data-type constructor."""
    n = call_name(node)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if n not in DTYPES:
            bad("struct.unknown_dtype",
                f"{where}: unknown data type '{n}()' — not in the pinned "
                f"nxd.spec.data_types surface", where)

def walk_roles(node, where, *, in_view):
    """Validate every role/dtype/Agg reference inside one schema entry."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) \
                and sub.value.id == "Agg":
            if sub.attr not in AGGS:
                bad("struct.unknown_agg",
                    f"{where}: Agg.{sub.attr} is not a member "
                    f"(allowed: {sorted(AGGS)})", where)
            elif sub.attr == "EXPRESSION":
                # A real API member, but out of scope for this generation path:
                # its SQL lives in the output PORT model's expressions={...} map,
                # which the desktop closure does not author. Reaching for it here
                # is always an attempt to dodge a derivation — the ruling belongs
                # in the transform as a physical column or row.
                bad("struct.agg_expression_forbidden",
                    f"{where}: Agg.EXPRESSION is outside this generation path — "
                    f"materialize the ruling as a physical column in the "
                    f"transform and aggregate that column with a normal Agg "
                    f"(see reference/derivation-plan.md)", where)
        if not isinstance(sub, ast.Call):
            continue
        n = call_name(sub)
        if n in KWARGS:
            check_kwargs(sub, n, where)
        if n == "metric":
            if not in_view:
                bad("struct.metric_in_model",
                    f"{where}: metric() inside a semantic_model schema — "
                    f"metrics are consume-time only, declare them as "
                    f"metric_field(metric(...)) on a semantic_view", where)
            if sub.args and not (isinstance(sub.args[0], ast.Attribute)
                                 and isinstance(sub.args[0].value, ast.Name)
                                 and sub.args[0].value.id == "Agg"):
                bad("struct.metric_first_arg_not_agg",
                    f"{where}: metric()'s first argument must be an Agg "
                    f"member (e.g. Agg.SUM), not a bare value", where)

def parse_models(src, path):
    """-> ({var: name}, {var: kind}, {name: joins}, {name: has_pk},
           {view_name: base_var})"""
    tree = ast.parse(src, path)
    var_name, var_kind, joins, has_pk, view_bases = {}, {}, {}, {}, {}
    for imp in ast.walk(tree):
        if isinstance(imp, ast.ImportFrom) and (imp.module or "").startswith("nxd"):
            head = imp.module.split(".")
            if not (head[:2] == ["nxd", "spec"] and len(head) <= 3):
                bad("struct.import_not_public_dsl",
                    f"{path}: import from '{imp.module}' — public DSL only "
                    f"(nxd.spec / nxd.spec.data_types)", path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        chain = spine(node.value)
        if not chain:
            continue
        root = chain[-1]
        kind = call_name(root)
        if kind not in ("semantic_model", "semantic_view"):
            continue
        # The name may be positional or the documented name= keyword — the
        # vendored example corpus writes semantic_model(name=..., description=...)
        # throughout, and best_practices.md prefers it. Missing this shape does
        # not just skip the name check, it skips the model's fields entirely.
        name_node = (root.args[0] if root.args else
                     next((k.value for k in root.keywords if k.arg == "name"), None))
        model = literal_str(name_node) if name_node is not None else None
        if model is None or not SNAKE.match(model):
            shown = ast.unparse(name_node) if name_node is not None else "<none>"
            bad("struct.model_name_not_literal",
                f"{path}: {kind}() name must be a lowercase snake_case string "
                f"literal, got {shown}", path)
            continue
        var_name[target.id], var_kind[target.id] = model, kind
        if kind == "semantic_view":
            # semantic_view("<name>", <base>) - the base is the second positional
            # in every documented form. Recorded as the VARIABLE id and resolved
            # to a model name after the file is parsed, since a view may be
            # defined above its base.
            base_arg = (root.args[1] if len(root.args) > 1 else
                        next((k.value for k in root.keywords
                              if k.arg in ("model", "base")), None))
            if isinstance(base_arg, ast.Name):
                view_bases[model] = base_arg.id
        joins.setdefault(model, []); has_pk[model] = False
        # Either authoring form counts: the chained .description(...) is the
        # verified one, but the description= constructor kwarg is pinned in
        # the documented signature and is what the shipped example corpus
        # uses, so rejecting it would fail correctly-authored models.
        if kind == "semantic_model" and not (
                any(call_name(c) == "description" and c.args
                    and desc_str(c.args[0]) for c in chain)
                or any(k.arg == "description" and desc_str(k.value)
                       for k in root.keywords)):
            bad("struct.model_no_description",
                f"{path}: semantic_model('{model}') declares no description "
                f"— both list_models and describe_model show it to the agent",
                f"{path}:{model}")
        in_view = kind == "semantic_view"
        for call in chain:
            if call_name(call) not in ("schema", "fields") or not call.args:
                continue
            schema = call.args[0]
            if not isinstance(schema, ast.Dict):
                unv(f"{model}: .schema() argument is not a dict literal",
                    f"{path}:{model}")
                continue
            if in_view and not schema.keys:
                bad("struct.view_empty_schema",
                    f"{path}: semantic_view('{model}') has an empty schema — "
                    f"this raises at build time", f"{path}:{model}")
            for k, v in zip(schema.keys, schema.values):
                col = literal_str(k) or "<dynamic>"
                where = f"{path}:{model}.{col}"
                if k is None:
                    unv(f"{model}: ** spread in .schema()", f"{path}:{model}")
                    continue
                entry_calls = ([v] if isinstance(v, ast.Call)
                               else list(v.elts) if isinstance(v, ast.Tuple) else [])
                if not entry_calls and not isinstance(v, ast.Tuple):
                    unv(f"{where}: schema value is "
                        f"{type(v).__name__}, not a call or tuple", where)
                    continue
                top = call_name(v) if isinstance(v, ast.Call) else None
                if in_view and isinstance(v, ast.Call) and top != "metric_field":
                    bad("struct.view_field_not_metric_field",
                        f"{where}: a semantic_view field must be "
                        f"metric_field(...), got {top}()", where)
                if top in ("field", "metric_field") and v.args:
                    check_dtype(v.args[0], where)
                elif top in DTYPES or (top and isinstance(v, ast.Call)
                                       and isinstance(v.func, ast.Name)):
                    check_dtype(v, where)
                if isinstance(v, ast.Tuple) and v.elts:
                    check_dtype(v.elts[0], where)
                walk_roles(v, where, in_view=in_view)
                field_roles = {call_name(sub) for sub in ast.walk(v)}
                if not in_view and "primary_key" in field_roles \
                        and "dimension" not in field_roles:
                    # Roles compose. A key carrying ONLY primary_key() never
                    # reaches describe_models, so no query can group by it and
                    # every entity-level question loses its answerable form —
                    # with no error, no failed assert and no missing table to
                    # show for it.
                    warn("struct.key_not_groupable",
                         f"{where}: primary_key() with no dimension(...) on the "
                         f"same field — the key is not groupable, so no query "
                         f"can return which entity a row is about", where)
                if not in_view and "join" in field_roles \
                        and "dimension" not in field_roles \
                        and "primary_key" not in field_roles:
                    # Exactly the same failure wearing a different role. A column
                    # carrying ONLY join(...) is a traversal edge and nothing
                    # else: it is absent from describe_models, so a caller cannot
                    # filter or group this model by the entity it points at. The
                    # rows are reachable only through the OTHER model's metric,
                    # where a filter scopes that model's aggregate rather than
                    # this model's spine — which returns every row and looks like
                    # it worked.
                    warn("struct.key_not_groupable",
                         f"{where}: join(...) with no dimension(...) on the same "
                         f"field — the foreign key is not groupable, so this "
                         f"model cannot be filtered or grouped by the entity it "
                         f"joins to", where)
                for sub in ast.walk(v):
                    if call_name(sub) == "primary_key":
                        has_pk[model] = True
                    if call_name(sub) == "join":
                        tgt = (literal_str(sub.args[0]) if sub.args else
                               next((literal_str(k.value) for k in sub.keywords
                                     if k.arg == "to"), None))
                        if tgt:
                            joins[model].append((tgt, where))
    return var_name, var_kind, joins, has_pk, view_bases

def parse_spec(src, path, var_name, var_kind):
    """-> set of promised model names"""
    tree = ast.parse(src, path)
    promised, modelled, saw = set(), set(), {"data_product": False,
                                             "script": False, "compute": False,
                                             "secrets": False, "port": False}
    if ".semantic_tools(" in src:
        bad("struct.semantic_tools_forbidden",
            f"{path}: .semantic_tools(...) is forbidden on desktop", path)

    # A custom contract's verifier is also a script(...).compute(...), so the
    # transform's rules cannot be applied to every script call in the file. Mark
    # the verifier scripts first: they are the ones reachable from a custom(...)
    # contract, and they are exempt from "must point at transform/main.py" and
    # do not satisfy the transform's own required-call set.
    verifier_scripts = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or call_name(node) != "verify":
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and call_name(sub) == "script":
                verifier_scripts.add(id(sub))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        n = call_name(node)
        if n == "data_product":
            saw["data_product"] = True
            ip = next((literal_str(k.value) for k in node.keywords
                       if k.arg == "infra_profile"), None)
            if ip != "desktop-local":
                bad("struct.bad_infra_profile",
                    f"{path}: data_product(infra_profile=...) must be the "
                    f"literal \"desktop-local\", got {ip!r}", path)
        elif n == "script":
            if id(node) in verifier_scripts:
                # A contract verifier, checked by the Phase C custom-contract
                # gate against contracts/, not by the transform's path rule.
                continue
            saw["script"] = True
            if literal_str(node.args[0] if node.args else None) != "transform/main.py":
                bad("struct.bad_script_path",
                    f"{path}: script() must point at \"transform/main.py\"", path)
        elif n in ("compute", "secrets"):
            # Only the TRANSFORM's .compute()/.secrets() satisfy the required
            # call set. A verifier's .compute(_compute) must not stand in for a
            # transform that never declared one.
            if not any(id(sub) in verifier_scripts
                       for sub in ast.walk(node) if isinstance(sub, ast.Call)):
                saw[n] = True
        elif n == "port":
            saw["port"] = True
            if literal_str(node.args[0] if node.args else None) != "duckdb":
                bad("struct.port_not_duckdb",
                    f"{path}: the output port must be named \"duckdb\"", path)
            if len(node.args) < 2 or call_name(node.args[1]) != "storage":
                bad("struct.port_no_storage",
                    f"{path}: .port(\"duckdb\", ...) second argument must be "
                    f"a storage(...) call", path)
        elif n in ("promise", "model") and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Name) and arg.id in var_name:
                (promised if n == "promise" else modelled).add(var_name[arg.id])
                if n == "promise" and var_kind[arg.id] == "semantic_view":
                    bad("struct.promise_of_view",
                        f"{path}: .promise({arg.id}) — {var_name[arg.id]} is a "
                        f"semantic_view; views are registered with .model(), "
                        f"never promised", path)
            elif any(call_name(c) == "custom" for c in spine(arg)):
                # .promise(custom("x")...verify(...)) — a custom contract, not a
                # models.py name. The root of the chain is the custom() call, so
                # match on the spine rather than the outermost call.
                pass  # validated by the Phase C custom-contract gate
            else:
                unv(f"{path}: .{n}() argument "
                    f"{ast.unparse(arg)} is not a models.py name", path)
    for key, msg in [("data_product", "no data_product(...) call"),
                     ("script", "no script(...) call"),
                     ("compute", "script() has no .compute(...)"),
                     ("secrets", "script() has no .secrets([...])"),
                     ("port", "no .port(...) call")]:
        if not saw[key]:
            bad("struct.missing_call", f"{path}: {msg}", path)
    for ref in re.findall(r'"(/infra-profile/[^"]*)"', src):
        if not re.fullmatch(r"/infra-profile/desktop-local#/services/[a-z0-9-]+", ref):
            bad("struct.malformed_service_ref",
                f"{path}: malformed service reference {ref!r} — expected "
                f"/infra-profile/desktop-local#/services/<name>", path)
    return promised, modelled

def model_constants(src):
    """Read transform model declarations without importing the transform."""
    tree, out = ast.parse(src, "transform/main.py"), {}
    assignments = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and \
                isinstance(node.targets[0], ast.Name) and \
                node.targets[0].id in ("BASE_MODELS", "DERIVED_MODELS",
                                       "PHYSICAL_MODELS",
                                       "OPTIONAL_EMPTY_MODELS"):
            assignments[node.targets[0].id] = node.value

    def resolve_model_value(value, seen=()):
        try:
            literal = ast.literal_eval(value)
        except (TypeError, ValueError):
            if isinstance(value, ast.Name):
                return resolve_model_tuple(value.id, seen)
            if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
                return (resolve_model_value(value.left, seen) +
                        resolve_model_value(value.right, seen))
            raise
        if not isinstance(literal, (tuple, list)) or \
                not all(isinstance(model, str) for model in literal):
            raise ValueError(value)
        return list(literal)

    def resolve_model_tuple(name, seen=()):
        if name in seen or name not in assignments:
            raise ValueError(name)
        return resolve_model_value(assignments[name], seen + (name,))

    for name in ("BASE_MODELS", "DERIVED_MODELS", "PHYSICAL_MODELS",
                 "OPTIONAL_EMPTY_MODELS"):
        if name not in assignments:
            continue
        try:
            # Optionality is intentionally stricter than the legacy model
            # constants: it must be an explicit literal declaration.
            if name == "OPTIONAL_EMPTY_MODELS":
                literal = ast.literal_eval(assignments[name])
                if not isinstance(literal, (tuple, list)) or \
                        not all(isinstance(model, str) for model in literal):
                    raise ValueError(name)
                models = list(literal)
            else:
                models = resolve_model_tuple(name)
        except (TypeError, ValueError):
            if name == "OPTIONAL_EMPTY_MODELS":
                bad("struct.optional_models_invalid",
                    "transform/main.py: OPTIONAL_EMPTY_MODELS must be a "
                    "literal tuple or list of lowercase model names",
                    "transform/main.py")
            else:
                unv(f"transform/main.py: {name} is not a literal",
                    "transform/main.py")
            continue
        if name == "OPTIONAL_EMPTY_MODELS":
            if len(models) != len(set(models)) or \
                    any(not SNAKE.fullmatch(model) for model in models):
                bad("struct.optional_models_invalid",
                    "transform/main.py: OPTIONAL_EMPTY_MODELS must contain "
                    "unique lowercase snake_case model names",
                    "transform/main.py")
                continue
        out[name] = models
    return out

# Exit 2 = could not read, distinct from exit 1 = found something. A traceback
# here reads as a broken checker; the closure is simply not where we are.
try:
    models_src = Path("models.py").read_text(encoding="utf-8")
    spec_src = Path("spec.py").read_text(encoding="utf-8")
    transform_src = Path("transform/main.py").read_text(encoding="utf-8")
except (OSError, UnicodeDecodeError) as exc:
    # UnicodeDecodeError subclasses ValueError, not OSError, so a latin-1
    # models.py walked past this handler and exited 1 with a bare traceback —
    # "found something", by this block's own definition, when nothing was ever
    # read. Same reasoning as the contracts/ reads below.
    print(f"CANNOT READ — {exc}. Run self_check.py from the CLOSURE ROOT: "
          f"models.py, spec.py and transform/main.py must all be present, and "
          f"must be UTF-8.",
          file=sys.stderr)
    finish(2)

var_name, var_kind, joins, has_pk, view_bases = parse_models(models_src, "models.py")
promised, modelled = parse_spec(spec_src, "spec.py", var_name, var_kind)

base_names = {n for v, n in var_name.items() if var_kind[v] == "semantic_model"}
consts = model_constants(transform_src)
physical = set(consts.get("PHYSICAL_MODELS", []))
optional = set(consts.get("OPTIONAL_EMPTY_MODELS", []))
for model, edges in joins.items():
    for tgt, where in edges:
        if tgt not in base_names:
            bad("struct.join_target_missing",
                f"{where}: join(to=\"{tgt}\") — no semantic_model of that name "
                f"in models.py", where)
# Which base models a metric can actually reach. run_semantic_query REQUIRES at
# least one measure, so a declared physical model that backs no semantic_view is landed
# but unreachable: its dimensions never appear in a selection, and the only way
# to touch its rows is through ANOTHER model's metric across a join - where a
# filter scopes that model's aggregate rather than this model's spine, quietly
# returning every row. A bare COUNT view is enough to fix it.
_metric_backed = {var_name[v] for v in view_bases.values() if v in var_name}
for model in sorted((promised | optional) & base_names):
    if model not in _metric_backed:
        warn("struct.model_not_queryable",
             f"models.py: semantic_model('{model}') is a declared physical "
             f"model but backs no "
             f"semantic_view, so no metric reaches it — run_semantic_query "
             f"requires a measure, making this model unqueryable however well "
             f"its dimensions are described. Add a semantic_view with at least "
             f"one metric (a COUNT of its key will do) if any Question or "
             f"Output reads it.",
             f"models.py:{model}")
for model in base_names:
    if not has_pk[model]:
        bad("struct.no_primary_key",
            f"models.py: semantic_model('{model}') declares no primary_key()",
            f"models.py:{model}")

if promised != base_names:
    # Optional physical models are catalog models, not produce-time promises:
    # an absent optional dlt resource must not enter the kernel's promise
    # verifier. Required models retain the historical equality invariant.
    required_models = base_names - optional
    if promised != required_models:
        bad("struct.naming_invariant_promised_vs_models",
            f"naming invariant: semantic_model names {sorted(base_names)} "
            f"with optional {sorted(optional)} != promised names "
            f"{sorted(promised)}", "spec.py")
if physical and promised | optional != physical:
    bad("struct.naming_invariant_promised_vs_physical",
        f"naming invariant: promised names {sorted(promised)} plus optional "
        f"names {sorted(optional)} != PHYSICAL_MODELS {sorted(physical)}",
        "transform/main.py")
if physical and optional - physical:
    bad("struct.optional_models_invalid",
        f"OPTIONAL_EMPTY_MODELS contains names not in PHYSICAL_MODELS: "
        f"{sorted(optional - physical)}", "transform/main.py")
if optional - base_names:
    bad("struct.optional_models_invalid",
        f"OPTIONAL_EMPTY_MODELS must name semantic_model outputs, not views or "
        f"unknown names: {sorted(optional - base_names)}", "transform/main.py")
if optional & promised:
    bad("struct.optional_model_promised",
        f"optional physical models must use .model(), never .promise(): "
        f"{sorted(optional & promised)}", "spec.py")
if optional - modelled:
    bad("struct.optional_model_not_registered",
        f"optional physical models must be registered with .model(): "
        f"{sorted(optional - modelled)}", "spec.py")
if base_names and not (promised & base_names):
    bad("struct.optional_models_invalid",
        "every physical model is optional; a data product must promise at "
        "least one required model", "spec.py")
if "BASE_MODELS" in consts:
    # data/ is NOT universal. A csv-source or file-source closure exports its
    # inputs to data/ and the BASE_MODELS-vs-directories comparison is the
    # invariant. An api-source or db-source closure lands nothing there: the
    # connector reads from the network at transform time and data/ never exists.
    # An unguarded iterdir() raised FileNotFoundError here, which killed the run
    # inside Phase A and meant Phase E — the gate whose entire job is
    # discriminating those two connector families — never executed on the ones it
    # exists for.
    #
    # Deliberately NOT a finding when data/ is absent, even for a csv-source
    # closure: BASE_MODELS is what the transform promises to land, and a
    # csv-source closure that genuinely has no data/ already fails Phase B, which
    # executes and reads it. Adding a Phase A finding here would only move the
    # same failure earlier while risking a false positive on any connector shape
    # not enumerated above.
    if Path("data").is_dir():
        dirs = {d.name for d in Path("data").iterdir() if d.is_dir()}
        base = set(consts["BASE_MODELS"])
        missing = base - dirs
        unexpected = dirs - base
        if unexpected or missing - optional:
            bad("struct.base_models_vs_data_dirs",
                f"BASE_MODELS {sorted(base)} does not match data/ directories "
                f"{sorted(dirs)}; missing optional models are allowed only from "
                f"OPTIONAL_EMPTY_MODELS {sorted(optional)}", "transform/main.py")

for u, at in zip(unverified, unverified_at):
    say(f"unverified: {u}")
    diag("s1_structure", "struct.unverified", f"unverified: {u}", path=cpath(at))
if errors:
    say("\nPHASE A FAILED — structural check of models.py / spec.py:")
    for e in errors:
        say(f"  - {e}")
    close_stage("s1_structure", "failed",
                errors=len(errors), unverified=len(unverified))
    finish(1)
close_stage("s1_structure", "passed",
            models_counted=len(base_names), unverified=len(unverified))
say(f"phase A ok — {len(base_names)} semantic_model, "
    f"{len(modelled - base_names)} semantic_view, "
    f"{len(unverified)} unverified entries")

# ---------------------------------------------------------------- Phase E ---
# Reach gate. RUNS BEFORE PHASE B, and that placement is the whole point: Phase B
# imports transform.main and calls ingest(). A scan that sits after it reports
# "denied" once the transform has already opened the socket, already called the
# model, already spent the money. The decision must precede the import, so this
# block sits above Phase B and exits before sys.path.insert(0, ".").
#
# What it enforces: the transform never calls a model THROUGH A RAW PROVIDER SDK,
# and reaches the network only through the connector it declares. The gate is
# about the seam, not about inference as such: a packaged closure is expected to
# infer, and does it through nxd.experimental.field_mapper under Phase G's
# consent grant, which keeps the procedure inside the closure, the credential
# outside it, and the approval in front of the user. A direct `import anthropic`
# has none of those properties, which is why it stays denied here.
# That invariant used to be prose only,
# on the belief that the desktop venv was closed. It is not — requests, httpx,
# httpcore and urllib3 all arrive transitively via dlt and mcp, and urllib and
# socket are stdlib. A closure can call a model today; nothing structural stops
# it. This is the structure.
#
# Static, import-level. transform/main.py for both families below, plus a
# model-SDK-only scan of contracts/**/*.py. Two families of finding:
#   (a) CONNECTOR-SHAPE MISMATCH — an import contradicting the connector type
#       spec.py declares. A closure that declares csv-source and imports
#       dlt.sources.rest_api reads its input from somewhere its own declaration
#       does not name.
#   (b) IMPORT REACH — a denied root: a model-provider SDK, or a raw transport.
# Both are subject to the same connector exception: the transport a declared
# api-source legitimately needs is not a finding on that closure.
# Findings carry their code, like Phase D's: the message is for the human in the
# scrollback, the code is what the build record and any consumer key on.
eerrors = []

def eerr(code, msg, at="transform/main.py"):
    eerrors.append((code, msg, at))

# The declared connector type comes from spec.py's service references, read from
# the AST — every ast.Constant string node, and nothing else. NOT a regex over
# the source text: a regex counts a service path mentioned inside a `#` comment
# or a docstring, so a spec.py whose only occurrence of api-source is
#
#     # I could have used "/infra-profile/desktop-local#/services/api-source"
#
# would declare api-source and collect the transport waiver below from a line
# Python never evaluates. The waiver is the permissive branch of this gate, so
# granting it from a comment is granting it to anyone who can type one.
#
# Reading Constant nodes also makes quote style irrelevant — '...' and "..." are
# the same node — which the regex got wrong in the other direction by only ever
# matching double quotes.
#
# Labeled instances per reference/multi-source.md ("db-source-orders") are
# matched by prefix, so a multi-source closure declares each of its types.
CONNECTOR_KINDS = ("csv-source", "file-source", "db-source", "api-source")
# Both documented spellings: the preferred relative path and the absolute
# https://<host>/infra-profile/... form the platform also accepts.
SERVICE_REF = re.compile(
    r"^(?:https?://[^/]+)?/infra-profile/[^/]+#/services/(?P<svc>[A-Za-z0-9_-]+)$")

def declared_connectors(src):
    """Connector kinds spec.py actually declares, from string LITERALS only.

    An unparseable spec.py declares nothing and warns — see below. It is not this
    gate's job to report a syntax error; Phase A owns that finding, and it has
    already run and exited by the time control reaches here.
    """
    try:
        tree = ast.parse(src, "spec.py")
    except SyntaxError:
        return set(), False
    kinds, saw_ref = set(), False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        m = SERVICE_REF.match(node.value.strip())
        if not m:
            continue
        saw_ref = True
        svc = m.group("svc")
        for kind in CONNECTOR_KINDS:
            if svc == kind or svc.startswith(kind + "-"):
                kinds.add(kind)
    return kinds, saw_ref

declared_sources, saw_service_ref = declared_connectors(spec_src)

# A closure whose spec.py yields NO parseable service reference at all is a
# different situation from one that declares a non-network service: the first is
# "this gate could not read the declaration", the second is "the declaration says
# no network". Treating the first as the second denies every transport on the
# strength of a parse failure, which is a verdict this gate has not earned. Warn
# and let the closure through the transport branch — the model-SDK denial below
# is unconditional and still applies, because it is waived by nothing.
if not saw_service_ref:
    _m = ("phase E could not read any /infra-profile/.../services/<name> "
          "reference from spec.py, so the declared connector type is unknown. The "
          "model-provider SDK denial still applies; the transport check is "
          "skipped rather than guessed.")
    say(f"warning: {_m}")
    diag("s1_structure", "reach.connector_undeclared", _m, path=cpath("spec.py"))

# THE CONNECTOR EXCEPTION. dlt's REST and SQL sources are built on
# httpx/requests/urllib3 and open sockets by design, and a db-source over a
# networked engine does the same. A closure that DECLARES a network-shaped
# source is waived on the transport family — that waiver is what keeps the gate
# usable, because a rule that fires on every correct api-source closure gets
# deleted rather than obeyed. Model-provider SDKs are NOT waived by it: no
# connector type licenses calling a model from a transform.
# `not saw_service_ref` joins the waiver for the reason above: an unreadable
# declaration is not evidence of a non-network one.
network_declared = bool(declared_sources & {"api-source", "db-source"}) \
    or not saw_service_ref

# (a) Import fingerprints that contradict a declared connector type. Keyed on the
# two NETWORK-shaped dlt verticals this skill's own reference docs tell the author
# to use: rest_api for api-source (reference/api-source.md), sql_database for
# db-source (reference/database-source.md). An import of one while declaring only
# the other is a shape mismatch: the closure reads from a source its spec does not
# name.
#
# `dlt.sources.filesystem` is deliberately NOT a third entry, and that is a
# recorded gap rather than an oversight: a filesystem import in an api/db closure
# reads local files the spec never declared, but it is far more likely to be
# incidental than a rest_api import is, and firing on it would fire on correct
# closures. Listed in the doc's "What Phase E cannot see" with the other holes.
SHAPE = {
    "dlt.sources.rest_api": ("api-source",
                             "a REST API source"),
    "dlt.sources.sql_database": ("db-source",
                                 "a database source"),
}

# (b) Denied roots. Model-provider SDKs are denied unconditionally — no connector
# type licenses calling a model from a transform. Raw transports are denied
# unless a network-shaped connector is declared.
#
# TWO ROOTS ARE DELIBERATELY NOT DENIED, and both are holes rather than
# non-issues. They are recorded here and in the doc's "What Phase E cannot see"
# so the next reader inherits the decision instead of re-litigating it:
#
#   subprocess / os.popen — `subprocess.run(["curl", ...])` reaches anything, and
#   os is imported by nearly every transform for os.path, so denying the root
#   would fire on almost every correct closure while `os.popen` specifically is
#   an attribute access this import-level check cannot see anyway. Denying
#   `subprocess` alone would catch the naive spelling and miss `os.popen`,
#   `os.system`, and `shutil` shelling out — a check that stops one of four
#   spellings reads as coverage it does not have. Documented gap, not a rule.
#
#   mcp — an OPEN TRANSPORT by decision. mcp is in the fixed desktop venv and is
#   how a closure talks to the supervisor, so denying it would fail closures
#   doing exactly what the platform intends. But it is a hole and not a small
#   one: mcp is a general-purpose client library that connects to ANY server it
#   is pointed at, including a model endpoint, and it is the reason httpx is in
#   the venv at all. Permitting mcp permits everything mcp can reach.
#
# The list is enumerated, so it is not exhaustive and never will be — a provider
# that ships under a name nobody added here passes. It denies the SDKs an author
# actually reaches for; it is not a proof of no inference. Say that when
# reporting a green Phase E.
MODEL_ROOTS = {
    # first-party provider SDKs
    "anthropic", "anthropic_bedrock", "openai", "cohere", "mistralai", "ollama",
    "groq", "together", "replicate",
    # google ships the current SDK as `google.genai` and the older one as
    # `google.generativeai`; `vertexai` is the same models via GCP.
    "google.generativeai", "google.genai", "vertexai",
    # aggregators and framework provider-bindings — the same call with a wrapper
    # in front of it, which is exactly how this denial gets routed around
    "litellm", "huggingface_hub", "langchain_anthropic", "langchain_openai",
    "llama_index",
}
# Raw transport. Denied only when no network-shaped connector is declared, so a
# legitimate api-source/db-source closure is unaffected by every entry here.
#
# The second group is the one a first pass misses. `requests` and `httpx` are the
# spellings someone writes when they are not thinking about this gate; the
# layers UNDER them are what a dlt-shaped closure reaches for without inventing
# anything. `dlt.sources.helpers.requests` in particular is not an evasion — it
# is dlt's own re-export, the spelling its docs teach, and it has `.post`. A
# deny list that stops `import requests` while permitting the library's
# documented alias for the same object is a list that only catches the naive
# author, which is not what this gate claims to be.
#
# Still enumerated, still not a proof. `anyio`/`asyncio` are here because their
# open_*_connection primitives are transport by any reading, not because the
# modules are otherwise suspicious — a closure importing asyncio for unrelated
# reasons in a csv-source transform is already doing something worth a look.
TRANSPORT_ROOTS = {
    # what an author writes directly
    "httpx", "requests", "aiohttp", "urllib.request", "urllib3",
    "socket", "http.client",
    # the layers underneath, reachable without naming any of the above
    "httpcore", "h11", "anyio", "asyncio",
    # dlt's own re-exports — the spelling its documentation teaches
    "dlt.sources.helpers.requests", "dlt.sources.helpers.rest_client",
}

def imported_roots(src, path):
    """Every dotted module name transform/main.py imports, statically.

    Import-level only. A helper that wraps a socket behind a local function, a
    URL handed to pandas.read_json, an `INSTALL httpfs` inside a DuckDB string —
    none of those are imports and none of them are visible here. See the doc's
    "What this script does NOT cover".

    Note the ImportFrom expansion: `from google import genai` arrives as
    module="google", names=["genai"], and neither part alone is a denied root.
    Emitting "google.genai" as well as "google" is what makes that form reachable
    by the same root list as `import google.genai`.
    """
    out = set()
    for node in ast.walk(ast.parse(src, path)):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out

def denied_hit(mod, roots):
    """A dotted module is a hit if it IS a denied root or lives under one.

    Prefix-matched on dot boundaries so `socket` catches `socket.socket` but
    `socketserver` and `requests_oauthlib` are not collateral.
    """
    return next((r for r in roots
                 if mod == r or mod.startswith(r + ".")), None)

t_imports = imported_roots(transform_src, "transform/main.py")

# Gated on `saw_service_ref` for the same reason the transport family is: when
# spec.py carried no readable declaration, `declared_sources` is empty, so
# `needs not in declared_sources` is trivially true and the closure would be
# denied on the strength of a parse failure — while the message asserted
# "spec.py declares <no connector service>", the very thing the warning one line
# earlier says the gate could not determine. A verdict this gate has not earned.
for mod, (needs, human) in (SHAPE.items() if saw_service_ref else ()):
    if any(denied_hit(m, {mod}) for m in t_imports) and \
            needs not in declared_sources:
        eerr("reach.connector_shape_mismatch",
            f"transform/main.py imports {mod} — {human} — but spec.py declares "
            f"{sorted(declared_sources) or 'no connector service'}. The closure "
            f"reads from a source its own declaration does not name. Either "
            f"declare the {needs} service in spec.py and infra-profile.yaml "
            f"(SKILL.md connector-types table), or drop the import.")

# Report once per DENIED ROOT, not once per importing name: `from openai import
# OpenAI` yields both "openai" and "openai.OpenAI" and both hit the same root, so
# keying the message on the root collapses them to one line. Purely
# presentational — one hit and two hits are the same verdict.
for root in sorted(r for r in MODEL_ROOTS
                   if any(denied_hit(m, {r}) for m in t_imports)):
    eerr("reach.model_sdk_import",
        f"transform/main.py imports {root!r} — a model-provider SDK directly. A "
        f"transform may infer, but only through the sanctioned seam: "
        f"nxd.experimental.field_mapper, called via make_call, under a consent "
        f"grant (reference/field-mapper.md). A raw SDK import routes around the "
        f"grant check, the supervisor approval boundary, and the sanitized "
        f"credential handling — so nobody receiving this closure can see what "
        f"content leaves it or authorize the call. While the product is still "
        f"being explored, judging agent-side and landing the rows as CSV "
        f"(reference/llm-judgments.md) is the cheaper lane and needs no grant.")
if not network_declared:
    for root in sorted(r for r in TRANSPORT_ROOTS
                       if any(denied_hit(m, {r}) for m in t_imports)):
        eerr("reach.undeclared_transport",
            f"transform/main.py imports {root!r} — raw network transport — and "
            + (f"spec.py declares {sorted(declared_sources)}, none of which "
               f"reaches the network. " if declared_sources else
               "spec.py declares no connector service at all. ")
            + f"A CSV or file closure reads what the "
            f"connector already exported to data/. If this closure really "
            f"needs to fetch, declare an api-source and go through "
            f"dlt.sources.rest_api (reference/api-source.md).")

# Contract verifiers are the SECOND class of executed Python in the closure, and
# a verifier that imports a model SDK and calls it is the identical risk this
# gate exists to deny in the transform. Walked from the filesystem with rglob
# rather than from spec.py's wiring, matching the unreferenced-file walk later in
# this script: an unwired-but-present verifier still ships, and the nesting under
# contracts/expectations/ and contracts/promises/ is covered by the same walk.
#
# ONLY the model-SDK denial is applied here, and unconditionally. TRANSPORT_ROOTS
# is deliberately left out: `network_declared` is computed from spec.py's
# connector declaration, which is a statement about how the TRANSFORM gets its
# data. A verifier runs after the data has landed and reads it from the closure's
# own tables, so it has no claim on that waiver — but denying transport in
# verifiers outright is a rule this gate has not yet earned evidence for, so the
# transport family is a recorded gap for verifiers rather than a half-applied
# rule (see the doc's "What Phase E cannot see").
#
# Nothing here executes a verifier, so unlike the transform scan the placement is
# not ordering-critical — self_check never imports contracts/, only the local
# desktop runtime does. Phase E is the right home because it keeps the reach domain in
# one place, not because it runs before Phase B.
for vpath in sorted(p for p in Path("contracts").rglob("*.py")
                    if p.name != "__init__.py"):
    try:
        v_imports = imported_roots(vpath.read_text(encoding="utf-8"), str(vpath))
    except (OSError, UnicodeDecodeError, SyntaxError):
        # UnicodeDecodeError subclasses ValueError, not OSError, so a verifier
        # that is valid Python under a non-UTF-8 coding declaration escaped this
        # handler and took the whole self-check with it — a bare traceback, no
        # reach.* code, no close_stage, no record merge. Exactly the failure mode
        # the rest of this script is written to avoid.
        #
        # An unreadable or unparseable verifier is Phase C's finding to report,
        # not this gate's. Silence here means "could not scan", which the doc
        # records; inventing a reach verdict from a parse failure would be a
        # verdict this gate has not earned.
        continue
    for root in sorted(r for r in MODEL_ROOTS
                       if any(denied_hit(m, {r}) for m in v_imports)):
        eerr("reach.model_sdk_import",
            f"{vpath} imports {root!r} — a model-provider SDK. A contract "
            f"verifier decides pass/fail from data that has already landed; it "
            f"never calls a model. A verifier that asks a model is not a "
            f"check — it re-decides the answer on every run, so the same rows "
            f"can pass today and fail tomorrow.", str(vpath))

if eerrors:
    say("\nPHASE E FAILED — reach gate (no raw provider SDK; infer through the seam):")
    seen = set()
    for ecode, e, eat in eerrors:
        if e in seen:
            continue
        seen.add(e)
        say(f"  - {e}")
        diag("s1_structure", ecode, e, path=cpath(eat))
    # Phase A already closed s1_structure as `passed`. Re-close it as `failed`:
    # the stage is a verdict on the closure, not on the phase that happened to
    # run first, and a record saying s1 passed while carrying reach.* errors
    # would be a record that contradicts itself.
    close_stage("s1_structure", "failed", errors=len(seen))
    finish(1)
# When spec.py carried no readable service ref, `network_declared` was granted
# above rather than derived — an unreadable declaration is not evidence of a
# non-network one. That is the right default, but the success line must not then
# claim a transport check it never performed: `import requests` in the transform
# passes silently under it, and a reader who was told "no transport import"
# would have been told something false.
_transport_checked = saw_service_ref
say(f"phase E ok — no denied model-SDK import in transform/main.py"
    + (f", no undeclared transport there, and its imports are consistent with "
       f"the declared connector {sorted(declared_sources) or ['(none)']}"
       if _transport_checked else
       ". TRANSPORT WAS NOT CHECKED: "
       "spec.py declared no readable service ref, so the transport family was "
       "waived rather than tested — this line is silent on whether the "
       "transform opens a socket")
    + f"; no model-SDK import in any contracts/**/*.py verifier either. This is "
    f"an import-level name check over the transform plus the verifiers: no "
    f"*listed* model-provider SDK — the list is enumerated, not exhaustive"
    # Gated for the same reason the head is. Left unconditional, this clause
    # re-asserted "no undeclared transport" inside the very branch that had just
    # disclaimed the check: an unreadable spec.py plus `import requests` printed
    # both sentences at once, and the second one was false.
    + (" — and no undeclared transport from the listed roots in the transform "
       "(verifiers are NOT scanned for transport)."
       if _transport_checked else
       ". The transport family was not evaluated at all.")
    + f" A wrapped socket, a URL passed "
    f"to a reader, DuckDB httpfs, subprocess, and the mcp client are all "
    f"invisible or permitted here (see 'What Phase E cannot see').")

# === PHASE-G-BEGIN ===
# ---------------------------------------------------------------- Phase G ---
# The consent gate: a closure that VENDORS the field-mapper harness maps only
# under a grant binding each mapper spec found in the closure. Statically that
# is the specs on disk under contracts/, never the path the transform hands to
# `map_inputs` — the gap is recorded in "What Phase G cannot see".
#
# Why a gate at all. `field_mapper/grant.py` says, in its own module docstring,
# that it "is a userland convention, not an enforceable security boundary" and
# that something outside it is what fails a closure mapping without a matching
# grant. For a long time nothing did. A consent record that no one checks is a
# file, not a consent record — the spec drifts after the user said yes, the old
# grant keeps sitting in contracts/, and every run afterwards is authorized by
# an artifact describing a rubric the user never saw.
#
# ORDERING — the forced half. This block must DECIDE before Phase B's
# `sys.path.insert(0, ".")` / `from transform.main import ...` / `ingest(...)`,
# for Phase E's reason and one more. Phase B EXECUTES the transform, and the
# mapper resolves its API key from secrets with an environment fallback: on a
# machine with ANTHROPIC_API_KEY set, self-checking a mapper closure makes live
# model calls during Phase B — real disclosure of the closure's source content
# and real spend. A consent verdict delivered after that is a report about
# consent already violated. test_phase_g_decides_before_phase_b_imports pins the
# byte offsets so this cannot decay into a post-hoc note.
#
# ORDERING — the chosen half. G runs after E rather than before because it
# reuses E's `imported_roots`/`denied_hit`/`t_imports`, and because E is purely
# static while G subprocesses the installed harness. More privileged runs later.
#
# Letter G, not F: the run order is already A, E, B, C, D, so "next letter"
# communicates nothing, and D is taken by the policy boundary below. G is for
# grant.
import subprocess                          # noqa: E402 — only Phase G shells out
MAPPER_ROOT = "nxd.experimental.field_mapper"
gerrors, gwarnings = [], []
# Bound here rather than inside the discovery branch: the success line reports
# `len(matched)`, and a NameError in the reporting path of a gate that just
# passed would turn a green closure into a crash.
matched = {}

def gerr(code, msg, at="", ev=None, fix=None):
    gerrors.append((code, msg, at, ev, fix))

# The trigger is an import-level module name in ANY module under transform/ OR
# at the closure root, matched on dot boundaries by the same helper Phase E uses
# — so `import nxd.experimental.field_mapper`, `from nxd.experimental.field_mapper
# import map_inputs`, `from nxd.experimental.field_mapper.records import
# reviews_from_csv` and `from nxd.experimental import field_mapper` all fire.
# Dot-boundary matching is what keeps this narrow: the root is the full dotted
# path, so the `import nxd` and `from nxd.spec import ...` that EVERY closure
# carries are not collateral, and neither is a sibling like
# `nxd.experimental.semantic`. Scanning only main.py would let an honest
# refactor — the import moved to transform/helpers.py, main.py importing that —
# through green, and it is the same closure mapping the same content.
#
# The closure root is scanned because a root module is importable from the
# transform (the transform executes with the closure root as its working
# directory) and so runs under Phase B exactly like a transform/ one. A
# `glue.py` sitting beside models.py holding the import, with transform/main.py
# doing `import glue`, maps for real. Root is globbed NON-recursively: a
# full-tree walk buys little here and costs a walk of every data/ and
# contracts/ subtree on every run. The residual — a root SUBPACKAGE holding the
# import — is stated in the no-obligation success line rather than left for a
# reader to discover.
#
# The mapper root is deliberately NOT added to MODEL_ROOTS. It would fail every
# legitimate mapper closure at Phase E, which denies model SDKs outright and
# offers no grant-shaped exception. The mapper's own `import anthropic` is
# function-local inside `transport.py` and invisible to Phase E's AST walk
# anyway; the two gates answer different questions and stay separate.
t_modules = {"transform/main.py": (t_imports, transform_src)}
for _tp in sorted(list(Path("transform").rglob("*.py")) + list(Path(".").glob("*.py"))):
    _rel = _tp.as_posix()
    if _rel in t_modules:
        continue
    try:
        _tsrc = _tp.read_text(encoding="utf-8")
        t_modules[_rel] = (imported_roots(_tsrc, _rel), _tsrc)
    except (OSError, SyntaxError):
        continue        # Phase A and Phase B own an unreadable transform module.
mapper_import_file, mapper_import = next(
    ((rel, m) for rel, (mods, _s) in sorted(t_modules.items())
     for m in sorted(mods) if denied_hit(m, {MAPPER_ROOT})),
    (None, None))

# The pre-package spelling. The harness used to be COPIED into the closure root
# and imported as a top-level `field_mapper`, and that was the sanctioned
# contract, so every closure authored before it moved into `nxd` has this shape
# on disk. Left unmatched it is the worst case this gate has: the import
# resolves at Phase B (the closure root is on sys.path), the transform maps for
# real against the env-fallback key, and the gate says "no consent obligation"
# on the way past. Phase E does not cover it either — the harness's
# `import anthropic` is function-local and invisible to its AST walk.
#
# Denied by name rather than sent through the grant oracle: a vendored copy is
# not a thing to consent to, it is a thing to delete. A grant cannot make it
# right, because the vendored harness answers for its own spec hash.
LEGACY_MAPPER_ROOT = "field_mapper"
legacy_file, legacy_import = next(
    ((rel, m) for rel, (mods, _s) in sorted(t_modules.items())
     for m in sorted(mods) if denied_hit(m, {LEGACY_MAPPER_ROOT})),
    (None, None))
if legacy_file:
    gerr("grant.vendored_harness",
         f"{legacy_file} imports '{legacy_import}' — a copy of the field-mapper "
         f"harness vendored into the closure. That was the contract before the "
         f"harness shipped inside the nxd package, and it no longer runs on the "
         f"platform: the supervisor stages only transform/main.py, so the "
         f"vendored package is absent at execution. It also answers for its own "
         f"spec hash, so no grant bound to it means anything.",
         legacy_file,
         fix="delete the vendored field_mapper/ directory and import "
             "`nxd.experimental.field_mapper` instead; the grant's "
             "mapper_spec_id must then be recomputed with "
             "`python -m nxd.experimental.field_mapper spec-id <spec.json>`")

# The verifier scan is UNCONDITIONAL and not grant-waivable — a grant authorizes
# mapping in a transform, never in a contract verifier. This closes a hole Phase
# E structurally cannot: its verifier scan checks MODEL_ROOTS only, and the
# mapper reaches a model through that function-local import, so a verifier that
# maps passes Phase E while doing exactly what Phase E's own message forbids —
# re-deciding pass/fail on every run, so the same landed rows can pass today and
# fail tomorrow.
for vpath in sorted(p for p in Path("contracts").rglob("*.py")
                    if p.name != "__init__.py"):
    try:
        gv_imports = imported_roots(vpath.read_text(encoding="utf-8"), str(vpath))
    except (OSError, SyntaxError):
        # Same reasoning as Phase E's verifier scan: an unreadable verifier is
        # Phase C's finding. "Could not scan" is not a consent verdict.
        continue
    # BOTH spellings. The legacy scan above walks t_modules — transform/ plus a
    # non-recursive closure root — and contracts/ is in neither, so a verifier
    # carrying the retired `import field_mapper` would match nothing anywhere.
    # Before the harness moved into the package MAPPER_ROOT *was* "field_mapper"
    # and this loop caught it; narrowing the root to the dotted path alone would
    # have retired the coverage along with the spelling. A verifier that maps is
    # the worst case either way — it re-decides pass/fail against a live model
    # on every run — so it is denied whichever way it reaches the harness.
    v_hit = next((h for m in gv_imports
                  if (h := denied_hit(m, {MAPPER_ROOT, LEGACY_MAPPER_ROOT}))),
                 None)
    if v_hit:
        # The hit, not MAPPER_ROOT: interpolating the constant would name the
        # dotted path in a finding raised by a legacy import.
        gerr("grant.verifier_maps",
             f"{vpath} imports {v_hit!r}. A contract verifier decides "
             f"pass/fail from data that has already landed; it never calls a "
             f"model. No grant authorizes this — consent covers mapping in the "
             f"transform, and a verifier that maps re-decides the answer every "
             f"run.", str(vpath))

if mapper_import:
    # --- Discover specs and grants under contracts/, BY SHAPE. -------------
    # By required-key shape rather than by filename: a closure names these files
    # whatever it likes, and a gate keyed on `mapper_spec.json` is bypassed by
    # renaming a file — which is not a threat model worth honouring, but is a
    # very easy way to get a false GREEN on an honest closure that just used a
    # different name.
    SPEC_KEYS = {"instruction", "target_fields", "grain", "cardinality",
                 "thresholds", "input_adapter"}
    GRANT_KEYS = {"mapper_spec_id", "provider", "model", "purpose"}
    # `mapper_spec_id` alone makes a file a grant CANDIDATE. Requiring the full
    # GRANT_KEYS set to even recognise one made a defective grant indistinguish-
    # able from no grant: drop `purpose` and the file fell out of grant_files,
    # the `elif not grant_files` branch fired, and the gate told the user
    # "contracts/ carries no consent grant" while the grant sat in contracts/.
    # That is the one grant.* message that can contradict what is on disk, and
    # it carries next_action: confirm — so a one-key typo the agent could fix
    # stopped the loop to ask a human to re-consent to a rubric they had already
    # consented to. Recognise first, then judge: a candidate that fails the full
    # shape is grant.invalid (owner: agent), and grant.missing keeps its literal
    # meaning of no grant artifact at all.
    spec_files, grant_files = [], []
    malformed_grants = []
    for jpath in sorted(Path("contracts").rglob("*.json")):
        try:
            doc = json.loads(jpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        if SPEC_KEYS <= set(doc):
            spec_files.append(jpath)
        elif GRANT_KEYS <= set(doc):
            grant_files.append((jpath, doc))
        elif "mapper_spec_id" in doc:
            missing = sorted(GRANT_KEYS - set(doc))
            malformed_grants.append(jpath)
            gerr("grant.invalid",
                 f"{jpath} carries mapper_spec_id, so it is a consent grant, "
                 f"but it is missing {missing}. Every one of "
                 f"{sorted(GRANT_KEYS)} is required: a grant without a stated "
                 f"purpose cannot be reviewed by the person who gave it, and "
                 f"one that names no provider or model does not say what the "
                 f"content was authorized to reach.", str(jpath),
                 ev={"missing_keys": missing},
                 fix=f"add {missing} to {jpath}; the grant itself does not need "
                     f"re-consenting, only completing")

    # A transform that imports the package but never references `map_inputs` is
    # the bypass grant.py names in its own docstring: `transport.Client` is
    # reachable directly, and it takes no grant. `map_inputs` is the only entry
    # point where `Grant.check` runs — its `grant` parameter is required, not
    # optional — so its absence means the consent path was routed around.
    # AST-level, Name or Attribute, so both `map_inputs(...)` and
    # `field_mapper.map_inputs(...)` count — across every transform module, for
    # the same reason the trigger spans them. `map_inputs as mi` binds the call
    # to a different name, so the import aliases are collected too: an alias is
    # recorded in the AST, and reading it beats calling a consented closure
    # ungated on a spelling.
    _refs, _gated_aliases = set(), {"map_inputs"}
    for _rel, (_mods, _msrc) in sorted(t_modules.items()):
        try:
            _tree = ast.parse(_msrc, _rel)
        except SyntaxError:
            _refs.add("map_inputs")  # Phase A already owns the parse failure.
            continue
        for node in ast.walk(_tree):
            if isinstance(node, ast.Name):
                _refs.add(node.id)
            elif isinstance(node, ast.Attribute):
                _refs.add(node.attr)
            elif (isinstance(node, ast.ImportFrom) and node.module
                  and denied_hit(node.module, {MAPPER_ROOT})):
                _gated_aliases |= {a.asname for a in node.names
                                   if a.name == "map_inputs" and a.asname}
    if not (_refs & _gated_aliases):
        gerr("grant.ungated_map",
             f"{mapper_import_file} imports {mapper_import!r} but no transform "
             f"module references map_inputs. map_inputs is the only entry point "
             f"that checks the grant before reading source content or resolving "
             f"a key; reaching transport.Client directly maps with no consent "
             f"check at all.",
             mapper_import_file)

    if not spec_files:
        gerr("grant.spec_unreadable",
             f"{mapper_import_file} imports {mapper_import!r} but no mapper "
             f"spec JSON was found under contracts/. Whatever form the spec "
             f"takes elsewhere — another directory, or inlined as a Python "
             f"literal — it is invisible here, and an inline literal "
             f"additionally has no stable mapper_spec_id to consent to.",
             "contracts/",
             fix="write the mapper spec to contracts/<name>_spec.json and load "
                 "it with MapperSpec.load")
    elif not grant_files and not malformed_grants:
        # `and not malformed_grants` so this cannot fire alongside the
        # grant.invalid above and tell the user, in the same report, both that a
        # grant is defective and that no grant exists. The defective one is
        # already reported and is agent-fixable; adding a second finding that
        # asks for a fresh human yes would be the contradiction this branch is
        # least able to afford.
        #
        # owner: user. Consent is the user's act — the agent cannot author a
        # grant on the user's behalf, which is the whole point of a grant.
        gerr("grant.missing",
             f"{mapper_import_file} imports {mapper_import!r} and "
             f"{len(spec_files)} mapper spec(s) are present, but contracts/ "
             f"carries no consent grant. The mapper sends the closure's source "
             f"content to a model; that needs a recorded human yes bound to "
             f"the spec by hash.", "contracts/",
             ev={"specs": [str(p) for p in spec_files]},
             fix="ask the user, then write the grant with the mapper_spec_id "
                 "printed by `python -m nxd.experimental.field_mapper "
                 "spec-id <spec.json>`")
    else:
        # --- Recompute each spec's bound id and pair a grant to it. --------
        # The id is computed by SUBPROCESSING THE INSTALLED HARNESS rather than
        # reimplementing the hash here. Two copies of a binding rule is how a
        # gate ends up enforcing something other than what it claims: the
        # harness the transform will import under Phase B is what must answer.
        # It resolves from the same interpreter running this script, so the
        # copy that binds and the copy that maps cannot drift apart.
        bound = {}
        for spath in spec_files:
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "nxd.experimental.field_mapper",
                     "spec-id", str(spath)],
                    capture_output=True, text=True, timeout=60)
                if proc.returncode != 0:
                    # LAST line of stderr, not the whole of it. A broken package
                    # exits with a full traceback, and pasting that into a
                    # finding buries the one informative line under frames from
                    # runpy — while also putting the word "Traceback" into a
                    # report whose readers use it to mean "the tool itself
                    # crashed".
                    _last = [ln for ln in proc.stderr.strip().splitlines()
                             if ln.strip()]
                    raise ValueError(_last[-1].strip()[:200] if _last
                                     else "non-zero exit")
                bound[str(spath)] = json.loads(proc.stdout.strip())["mapper_spec_id"]
            except Exception as exc:
                # A broken harness package is a FINDING, not a traceback. The
                # whole self-check dying on a subprocess failure would take
                # every other phase's verdict with it.
                gerr("grant.spec_unreadable",
                     f"could not compute the bound mapper_spec_id for {spath}: "
                     f"{type(exc).__name__}: {exc}. The field-mapper "
                     f"package could not answer for its own spec, so no grant "
                     f"can be matched to it.", str(spath))
        for spath, spec_id in sorted(bound.items()):
            hit = next((g for g, doc in grant_files
                        if doc.get("mapper_spec_id") == spec_id), None)
            if hit is None:
                # `<derived>` is the samples-only escape hatch in the harness's
                # fixture loader, where it is substituted with whatever spec is
                # loaded. In a closure that is not a binding at all — it
                # authorizes anything — so it is called out by name rather than
                # folded into the generic mismatch.
                placeholder = [str(g) for g, doc in grant_files
                               if doc.get("mapper_spec_id") == "<derived>"]
                if placeholder:
                    gerr("grant.invalid",
                         f"{placeholder[0]} carries "
                         f'"mapper_spec_id": "<derived>" — the harness fixture '
                         f"placeholder, which binds to whatever spec it is "
                         f"handed and therefore authorizes anything.",
                         placeholder[0],
                         fix="replace it with the literal id from "
                             "`python -m nxd.experimental.field_mapper "
                             "spec-id`")
                    continue
                bad = [str(g) for g, doc in grant_files
                       if not re.fullmatch(r"[0-9a-f]{32}",
                                           str(doc.get("mapper_spec_id", "")))]
                if bad:
                    gerr("grant.invalid",
                         f"{bad[0]} carries a mapper_spec_id that is not a "
                         f"32-character lowercase hex hash, so it cannot bind "
                         f"any spec.", bad[0])
                    continue
                if not grant_files:
                    # Every grant in the closure was malformed, so each is
                    # already reported as grant.invalid. Saying "the grants
                    # present authorize other spec hashes" here would name a set
                    # that is empty, and would send the user to re-consent when
                    # the actual repair is completing a file the agent can fix.
                    continue
                gerr("grant.spec_mismatch",
                     f"no grant binds {spath} (bound id {spec_id}). The grants "
                     f"present authorize other spec hashes — the rubric, "
                     f"fields, thresholds or model changed since consent was "
                     f"given, which is exactly when the user should be asked "
                     f"again.", str(spath),
                     ev={"expected": spec_id,
                         "found": sorted({str(doc.get("mapper_spec_id"))
                                          for _, doc in grant_files})},
                     fix="re-ask the user and rewrite the grant against the "
                         "current spec-id")
                continue
            matched[str(spath)] = (str(hit), spec_id)

        # --- Run the harness's OWN check on each bound pair. ---------------
        # `Grant.check` is the rule of record. Calling it means the gate cannot
        # drift from the consent rules it claims to enforce. No runtime
        # arguments are passed: input_fields and document_classes are properties
        # of a RUN, so only the statically decidable subset — hash, model,
        # corroboration model, expiry — is exercised here.
        for spath, (gpath, spec_id) in sorted(matched.items()):
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "nxd.experimental.field_mapper",
                     "grant-check", spath, gpath],
                    capture_output=True, text=True, timeout=60)
                verdict = json.loads(proc.stdout.strip())
            except Exception as exc:
                gerr("grant.spec_unreadable",
                     f"could not check {gpath} against {spath}: "
                     f"{type(exc).__name__}: {exc}", gpath)
                continue
            for problem in verdict.get("problems", []):
                kind, detail = problem.get("kind"), problem.get("message", "")
                if kind == "expired":
                    gerr("grant.expired",
                         f"{gpath} has expired: {detail} Consent lapses on "
                         f"purpose — a closure green yesterday failing today is "
                         f"the grant doing its job, not flakiness.", gpath)
                elif kind in ("model_mismatch", "corroboration_mismatch"):
                    gerr("grant.spec_mismatch",
                         f"{gpath} binds {spath}'s hash but not its models: "
                         f"{detail} Consent is per model — sending the same "
                         f"content to a second model is a separate disclosure.",
                         gpath)
                elif kind == "spec_mismatch":
                    gerr("grant.spec_mismatch",
                         f"{gpath} does not authorize {spath}: {detail}", gpath)
                else:
                    gerr("grant.invalid",
                         f"{gpath} was rejected by the harness: {detail}", gpath)

        # A grant matching no spec is a WARNING, not an error: it fails nothing
        # and authorizes nothing, but a stale consent artifact left in contracts/
        # is the thing a later reader mistakes for coverage.
        #
        # "Stale" is only meaningful when the closure's consent is OTHERWISE
        # complete. If some spec failed to bind, the very same grant files are
        # already the subject of a spec_mismatch/invalid error, and calling them
        # stale as well is one problem reported from both ends — "no grant binds
        # this spec" beside "this grant binds no spec" invites fixing whichever
        # sentence reads more tractably instead of the single mismatch. So the
        # warning fires only when every spec found its grant.
        _all_bound = bool(bound) and len(matched) == len(bound) and not gerrors
        for gpath, doc in (grant_files if _all_bound else []):
            if str(gpath) not in {g for g, _ in matched.values()}:
                gwarnings.append(
                    (str(gpath),
                     f"{gpath} is a consent grant that binds no spec in this "
                     f"closure (it authorizes "
                     f"{doc.get('mapper_spec_id')!r}). Stale consent left on "
                     f"disk reads as coverage it does not provide."))

for _gp, _gm in gwarnings:
    diag("s1_structure", "grant.unbound", _gm, path=cpath(_gp))

if gerrors:
    say("\nPHASE G FAILED — consent gate (a mapper maps only under a "
        "matching grant):")
    gseen = set()
    for gcode, gmsg, gat, gev, gfix in gerrors:
        if gmsg in gseen:
            continue
        gseen.add(gmsg)
        say(f"  - {gmsg}")
        diag("s1_structure", gcode, gmsg, path=cpath(gat),
             evidence=gev, fix=gfix)
    # Same move as Phase E: Phase A closed s1_structure as `passed`, and a
    # record claiming s1 passed while carrying grant.* errors contradicts itself.
    close_stage("s1_structure", "failed", errors=len(gseen))
    finish(1)

if not mapper_import:
    say("phase G ok — no module under transform/ or at the closure root "
        "imports the field-mapper harness, so no consent obligation was "
        "found. Absence of a finding is not proof of absence: the scan does "
        "not descend into root SUBPACKAGES, so a helper at helpers/util.py "
        "holding the import reaches here silently, and neither this gate nor "
        "Phase E sees an importlib call or an inlined copy of the "
        "harness source. "
        "Whether the closure reaches a model by some other route is Phase "
        "E's question, not this one's.")
else:
    say(f"phase G ok — {mapper_import_file} imports {mapper_import!r}; "
        f"{len(matched)} mapper spec(s) under contracts/ each bound by a "
        f"grant, checked by the harness's own Grant.check"
        + (f"; {len(gwarnings)} unbound grant(s) also present" if gwarnings
           else "")
        + ". Statically decidable consent over the specs ON DISK only — "
          "nothing here inspects which spec path the transform passes to "
          "map_inputs, the hash is computed BY the audited package, and field "
          "coverage, document classes and spend ceilings are enforced at run "
          "time inside the harness, not here (see 'What Phase G cannot see').")
# === PHASE-G-END ===

# ---------------------------------------------------------------- Phase B ---
# Dry-run of transform/main.py against a scratch DuckDB. This one EXECUTES.
# Everything it reports is what will happen on the supervisor, so a failure
# here is unambiguously the generated code — never the environment. There is
# no kernel and no network in this phase to blame.

@dataclass
class DuckDbOutput:
    path: str; schema: str; model_tables: dict; models: dict = field(default_factory=dict)

GENERIC_STATE_KEY = "__nxd_generic__"


class FakeTransformState(dict):
    """A small offline model of the current MultiModelTransformState handle.

    The self-check runs outside the kernel, so it cannot use the real provider.
    Keep the important runtime boundaries here: a single-model handle is
    pre-bound, multi-model flat writes remain readable for this invocation but
    are not captured, ``for_model`` validates declared names, and ``generic``
    is created lazily under the reserved wire key.
    """

    def __init__(self, declared, prior=None):
        prior = json.loads(json.dumps(prior or {}, allow_nan=False))
        self._declared = list(declared)
        self._bags = {m: dict(prior.get(m, {})) for m in self._declared}
        self._generic = None
        if GENERIC_STATE_KEY in prior:
            self._generic = dict(prior[GENERIC_STATE_KEY])
        self._bound = self._declared[0] if len(self._declared) == 1 else None
        if self._bound is not None:
            dict.__init__(self, self._bags[self._bound])
            self._bags[self._bound] = self
        else:
            dict.__init__(self)
        self.dropped_flat_write = False

    def for_model(self, name):
        if name not in self._bags:
            raise KeyError(f"unknown model {name!r}; declared: {sorted(self._bags)}")
        if name == self._bound:
            return self
        return self._bags[name]

    def generic(self):
        if self._generic is None:
            self._generic = {}
        return self._generic

    def models(self):
        return list(self._declared)

    def _note_flat_write(self):
        if self._bound is None:
            self.dropped_flat_write = True

    def __setitem__(self, key, value):
        # Match the runtime's dict-like flat handle: the value is visible during
        # this call, but no declared model captures it for folding.
        self._note_flat_write()
        dict.__setitem__(self, key, value)

    def update(self, *args, **kwargs):
        values = dict(*args, **kwargs)
        if values:
            self._note_flat_write()
        dict.update(self, values)

    def setdefault(self, key, default=None):
        self._note_flat_write()
        return dict.setdefault(self, key, default)

    def persist(self):
        snapshot = {m: dict(self._bags[m]) for m in self._declared}
        if self._generic is not None:
            snapshot[GENERIC_STATE_KEY] = dict(self._generic)
        # The kernel's wire boundary is strict JSON; NaN/Infinity are not valid
        # persisted state even though Python's default encoder accepts them.
        return json.loads(json.dumps(snapshot, allow_nan=False))

berrors, btracebacks = [], []
def berr(code, msg, at="", ev=None, tb=""):
    berrors.append(msg)
    btracebacks.append(tb)
    ev = dict(ev or {})
    if tb:
        ev["traceback"] = tb
    diag("s2_transform", code, msg, path=cpath(at), evidence=ev)

def dry_run_waived(exc, network_declared):
    """Is this dry-run failure a KNOWN LIMIT rather than a closure defect?

    A db-source/api-source closure reads its connection out of `secrets`, and
    the offline harness has no credential to give it, so the first `secrets[...]`
    raises KeyError before the transform does anything. That is expected, and
    reference/api-source.md tells the author not to code around it.

    It is waived ONLY on that pairing. A CSV closure declaring no network source
    has no such excuse: a missing secret there is a real fault and must still
    fail Phase B. Keyed on the connector, never on the exception type alone.
    """
    return isinstance(exc, KeyError) and bool(network_declared)


def fail_b():
    say("\nPHASE B FAILED — transform dry-run (this EXECUTED: what it reports "
        "is what will happen):")
    for e, tb in zip(berrors, btracebacks):
        say(f"  - {e}")
        if tb:
            say("".join(f"      {ln}\n" for ln in tb.strip().splitlines()), end="")
    close_stage("s2_transform", "failed", errors=len(berrors))
    finish(1)

nxd = types.ModuleType("nxd"); core = types.ModuleType("nxd.core")
ctx = types.ModuleType("nxd.core.context"); ctx.DuckDbOutput = DuckDbOutput; dp = types.SimpleNamespace(
    on_transform=lambda *a, **k: (lambda fn: fn), main=lambda: None)
nxd.data_product, nxd.core, core.context = dp, core, ctx
# The stub stands in for `nxd` so the dry run needs no installed SDK. But a bare
# ModuleType has no `__path__`, and a module without one cannot have submodules:
# once this lands in sys.modules it SHADOWS the real package, so any
# `nxd.<anything>` the transform imports dies as "'nxd' is not a package" — an
# error about the stub, reported against the closure. That matters for the field
# mapper, which lives at `nxd.experimental.field_mapper` and is imported by
# exactly the closures Phase G has just cleared to run. Borrow the real
# package's search path when it is installed, so genuine submodules resolve
# through it while the stubbed names above still win by being set here directly.
#
# The closure root goes on sys.path FIRST: the resolution has to see the same
# path the transform will, or a closure-root package is invisible to the lookup
# and present at import — the ordering that made this look like a missing
# dependency rather than a shadowed one.
sys.path.insert(0, ".")
import importlib.util                    # noqa: E402 — only this shim needs it
_real_path = None
if "nxd" in sys.modules:
    # Already imported: read the live module's own path rather than calling
    # find_spec, which raises ValueError on an entry with no __spec__. Giving up
    # here would overwrite a provably-importable package with the path-less stub
    # two lines below — reintroducing the exact shadowing this shim exists to
    # remove, in the one case where the real package was known to be present.
    _real_path = getattr(sys.modules["nxd"], "__path__", None)
else:
    try:
        _spec = importlib.util.find_spec("nxd")
    except (ImportError, ValueError):
        # A broken or partially-installed `nxd` must not take the dry run down
        # before it starts; a path-less stub is the pre-existing behaviour.
        _spec = None
    _real_path = _spec.submodule_search_locations if _spec is not None else None
if _real_path:
    nxd.__path__ = list(_real_path)
sys.modules.update({"nxd": nxd, "nxd.core": core, "nxd.core.context": ctx})
# A labeled multi-source closure carries its transform-only exports in
# data-<label>/ roots rather than the ordinary data/ root. The supervisor names
# the immutable snapshot explicitly, so the dry run must do the same instead
# of relying on the current working directory.
os.environ["NXD_TRANSFORM_ROOT"] = str(Path.cwd().resolve())
try:
    from transform.main import BASE_MODELS, PHYSICAL_MODELS, ingest  # noqa: E402
except Exception as exc:
    berr("runtime.import_failed",
         f"transform/main.py: import failed — {type(exc).__name__}: {exc}",
         "transform/main.py", tb=traceback.format_exc())
    fail_b()
try:
    uses_transform_state = "transform_state" in inspect.signature(ingest).parameters
except (TypeError, ValueError) as exc:
    berr("runtime.transform_raised",
         f"transform/main.py: could not inspect ingest signature — {exc}",
         "transform/main.py", tb=traceback.format_exc())
    fail_b()

def _real_directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def _real_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


source_roots = []
if Path("data").exists() or Path("data").is_symlink():
    source_roots.append(Path("data"))
source_roots.extend(sorted(
    path for path in Path(".").glob("data-*")
    if _real_directory(path) or path.is_symlink()
))

# A labeled root is a transform-only source export. Keep each root's
# ownership intact: flattening all child names into one set can make two labels
# appear valid when they actually claim the same model, or let a symlink/empty
# root reach the transform dry-run. The supervisor applies the same refusal
# rules when it copies a declared companion tree.
labeled_roots = [
    root for root in source_roots
    if root.name.startswith("data-") and root.name != "data-"
]
root_models: dict[Path, set[str]] = {}
model_owners: dict[str, Path] = {}
for source_root in source_roots:
    if not _real_directory(source_root):
        berr("closure.csv_root_invalid",
             f"source export root must be a real directory, not a symlink: "
             f"{source_root}", str(source_root))
        continue
    try:
        children = list(source_root.iterdir())
    except OSError as exc:
        berr("closure.csv_root_invalid",
             f"cannot inspect source export root {source_root}: {exc}",
             str(source_root))
        continue
    model_dirs = set()
    for child in children:
        if child.is_symlink():
            berr("closure.csv_root_invalid",
                 f"source export contains a symlink: {child}", str(child))
            continue
        if not _real_directory(child):
            continue
        model_dirs.add(child.name)
        prior = model_owners.setdefault(child.name, source_root)
        if prior != source_root:
            berr("closure.csv_root_invalid",
                 f"model directory {child.name!r} is claimed by both "
                 f"{prior} and {source_root}", str(child))
    root_models[source_root] = model_dirs

for source_root in labeled_roots:
    label = source_root.name.removeprefix("data-")
    if not root_models.get(source_root):
        berr("closure.csv_root_invalid",
             f"labeled export root {source_root} is empty or has no model "
             "directory; omit it instead of declaring it", str(source_root))
    for member in source_root.rglob("*"):
        if member.is_symlink():
            berr("closure.csv_root_invalid",
                 f"labeled export tree contains a symlink: {member}",
                 str(member))
    path_file = Path(f"csv-source-{label}-path")
    if not _real_regular_file(path_file):
        berr("closure.csv_root_invalid",
             f"labeled export root {source_root} requires a regular "
             f"{path_file} companion file", str(path_file))
        continue
    try:
        relative_path = path_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        berr("closure.csv_root_invalid",
             f"cannot read {path_file}: {exc}", str(path_file))
        continue
    candidate = Path(relative_path)
    if relative_path != source_root.name or candidate.is_absolute() \
            or ".." in candidate.parts:
        berr("closure.csv_root_invalid",
             f"{path_file} must name its relative labeled root "
             f"{source_root.name!r}, got {relative_path!r}", str(path_file))

if labeled_roots:
    manifest = Path("companion-files")
    if not _real_regular_file(manifest):
        berr("closure.csv_root_invalid",
             "labeled export roots require a regular root-level "
             "companion-files manifest", "companion-files")
    else:
        try:
            declared = [
                line.strip() for line in manifest.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
        except (OSError, UnicodeError) as exc:
            declared = []
            berr("closure.csv_root_invalid",
                 f"cannot read companion-files: {exc}", "companion-files")
        expected = sorted(root.name for root in labeled_roots)
        if declared != expected or declared != sorted(set(declared)):
            berr("closure.csv_root_invalid",
                 f"companion-files must declare each non-empty labeled root "
                 f"once, got {declared!r}, expected {expected!r}",
                 "companion-files")
        for entry in declared:
            declared_root = Path(entry)
            if (declared_root.is_absolute() or ".." in declared_root.parts
                    or not _real_directory(declared_root)):
                berr("closure.csv_root_invalid",
                     f"companion-files entry is not a real relative directory: "
                     f"{entry!r}", "companion-files")
            if declared_root.is_symlink():
                berr("closure.csv_root_invalid",
                     f"companion-files entry is a symlink: {entry!r}",
                     "companion-files")

DIRS = sorted({model for models in root_models.values() for model in models})
# BASE models are backed by the ordinary data/ root or by labeled data-<label>/
# roots. Derived models are landed by the transform and appear in
# PHYSICAL_MODELS with no source directory of their own.
base_models = set(BASE_MODELS)
missing_base_models = base_models - set(DIRS)
unexpected_base_models = set(DIRS) - base_models
if unexpected_base_models or missing_base_models - optional:
    berr("runtime.base_models_mismatch",
         f"base models must match source export directories: "
         f"BASE_MODELS {sorted(base_models)} != {sorted(set(DIRS))}; "
         f"missing optional models are allowed only from "
         f"OPTIONAL_EMPTY_MODELS {sorted(optional)}", "transform/main.py",
         {"expected": sorted(set(DIRS)), "actual": sorted(base_models),
          "optional": sorted(optional)})
if not set(BASE_MODELS) <= set(PHYSICAL_MODELS):
    berr("runtime.base_models_mismatch",
         f"base models must be promised: "
         f"{sorted(set(BASE_MODELS) - set(PHYSICAL_MODELS))} are in BASE_MODELS "
         f"but not in PHYSICAL_MODELS", "transform/main.py")
if berrors:
    fail_b()
run = Path(tempfile.mkdtemp())
# model_tables comes from PHYSICAL_MODELS, never the data/ listing: a map built
# from directories KeyErrors the moment a derived model resolves its table name.
out = DuckDbOutput(path=str(run / "data.duckdb"), schema="main",
                   model_tables={m: m for m in PHYSICAL_MODELS})
# A db-source/api-source closure reads its connection out of `secrets`, and the
# harness has no credential to give it — so `secrets["base_url"]` raises before
# the transform does anything. That is a KNOWN LIMIT of the offline dry run, not
# a defect in the closure, and reference/api-source.md tells the author not to
# code around it with a profile-reading fallback.
#
# It must therefore not fail Phase B, because failing Phase B also skips Phases
# C, D and E — the closure-record, policy-boundary and reach-gate checks. Those
# are exactly the checks a credentialed, network-shaped closure most needs, and
# losing them silently to an expected KeyError is how a missing contract wiring
# or a hardcoded policy value reaches a build.
dry_run_runnable = True
dry_run_secrets = (
    {"csv_source": str(Path("data").resolve())}
    if Path("data").is_dir() else {}
)


def run_transform(state=None):
    """Execute one transform invocation with the same argument boundary."""
    global dry_run_runnable
    state_kwargs = {}
    if state is not None:
        state_kwargs["transform_state"] = state
    try:
        ingest(duckdb=out, secrets=dry_run_secrets, **state_kwargs)
    except KeyError as exc:
        if not dry_run_waived(exc, network_declared):
            berr("runtime.transform_raised",
                 f"transform/main.py: KeyError: {exc}",
                 "transform/main.py", tb=traceback.format_exc())
        else:
            dry_run_runnable = False
            diag("s2_transform", "runtime.dry_run_not_runnable",
                 f"the dry run could not execute: this closure declares "
                 f"{sorted(declared_sources) or ['a network source']} and reads "
                 f"{exc} out of `secrets`, which the offline harness cannot supply. "
                 f"Phase B is NOT RUNNABLE here and reports nothing about the "
                 f"transform - verify it with check_data_product, which runs the "
                 f"real closure under the supervisor's interpreter. Phases C, D and "
                 f"E still run below.",
                 path=cpath("transform/main.py"),
                 evidence={"missing_secret": str(exc),
                           "declared_sources": sorted(declared_sources)})
    except AssertionError as exc:
        # A fired assert is the transform's OWN invariant rejecting the data it
        # produced. That is the check working, not the check being wrong.
        berr("runtime.assert_failed",
             f"transform/main.py: an assert fired during the dry run — {exc}",
             "transform/main.py", tb=traceback.format_exc())
    except Exception as exc:
        berr("runtime.transform_raised",
             f"transform/main.py: {type(exc).__name__}: {exc}",
             "transform/main.py", tb=traceback.format_exc())


first_state = FakeTransformState(PHYSICAL_MODELS) if uses_transform_state else None
run_transform(first_state)
if berrors:
    fail_b()
import duckdb
con = duckdb.connect(out.path, read_only=True) if dry_run_runnable else None
absent_optional_models = set()
actual_tables = None
first_materialized = {}
if dry_run_runnable:
    try:
        actual_tables = {
            row[0] for row in con.execute("SHOW TABLES").fetchall()
        }
    except Exception:
        # The per-model query below carries the useful exception and keeps the
        # existing required-table diagnostic stable.
        pass

def record_absent_optional(model):
    absent_optional_models.add(model)
    say(f"{model}: 0 rows (optional table absent)")
    ROW_COUNTS.append({"table": model, "row_count": 0,
                       "materialized": False, "optional": True})
    diag("s2_transform", "runtime.row_count",
         f"{model}: 0 rows (optional table absent)",
         path=cpath(f"transform/main.py:{model}"),
         evidence={"model": model, "count": 0, "materialized": False,
                   "optional": True})

def is_missing_table_error(exc):
    message = str(exc).lower()
    return "does not exist" in message or "not found" in message

for m in (PHYSICAL_MODELS if dry_run_runnable else ()):  # unquoted main.<name> — the invariant, physically
    if actual_tables is not None and m in optional and m not in actual_tables:
        record_absent_optional(m)
        first_materialized[m] = False
        continue
    try:
        n_rows = con.execute(f"SELECT COUNT(*) FROM main.{m}").fetchone()[0]
    except Exception as exc:
        if actual_tables is None and m in optional and is_missing_table_error(exc):
            record_absent_optional(m)
            first_materialized[m] = False
            continue
        berr("runtime.model_table_missing",
             f"main.{m} is a declared physical model but is not queryable after "
             f"the transform — "
             f"{type(exc).__name__}: {exc}", "transform/main.py", {"model": m})
        continue
    say(m, n_rows)
    first_materialized[m] = True
    ROW_COUNTS.append({"table": m, "row_count": n_rows})
    diag("s2_transform", "runtime.row_count", f"{m}: {n_rows} rows",
         path=cpath(f"transform/main.py:{m}"),
         evidence={"model": m, "count": n_rows})
if dry_run_runnable and not (run / ".transform-complete").exists():
    berr("runtime.transform_incomplete",
         "the transform returned without writing .transform-complete — it did "
         "not finish", "transform/main.py")
if berrors:
    fail_b()

# A stateful transform is the one case where a single successful dry run is not
# enough: the cursor contract is about the boundary between runs. Reuse the
# same scratch database and run directory, but carry only the JSON-folded state
# from the first invocation. Do not duplicate the first run's row-count evidence
# in ROW_COUNTS; the second pass is a verification of unchanged rerun behavior.
if dry_run_runnable and uses_transform_state:
    first_counts = {
        entry["table"]: entry["row_count"]
        for entry in ROW_COUNTS
    }
    landed_rows = any(count > 0 for count in first_counts.values())
    if first_state.dropped_flat_write:
        berr("runtime.state_flat_write",
             "transform_state was indexed flat while multiple physical models "
             "are declared; the write is visible only during this invocation and "
             "is not captured for persistence — use for_model() or generic()",
             "transform/main.py")
    try:
        persisted_state = first_state.persist()
    except Exception as exc:
        persisted_state = None
        berr("runtime.state_unserializable",
             f"transform_state could not be JSON-serialized after the first run — "
             f"{type(exc).__name__}: {exc}", "transform/main.py",
             tb=traceback.format_exc())
    if landed_rows and not any(bool((persisted_state or {}).get(key))
                               for key in PHYSICAL_MODELS) \
            and not (persisted_state or {}).get(GENERIC_STATE_KEY):
        berr("runtime.state_not_persisted",
             "the transform landed rows but persisted no transform_state value; "
             "a successful incremental run must advance a JSON-serializable "
             "cursor (use a sentinel even when a successful batch is empty)",
             "transform/main.py")
    if berrors:
        fail_b()

    con.close()
    (run / ".transform-complete").unlink(missing_ok=True)
    second_state = FakeTransformState(PHYSICAL_MODELS, prior=persisted_state)
    run_transform(second_state)
    if berrors:
        fail_b()
    con = duckdb.connect(out.path, read_only=True)
    second_actual_tables = {
        row[0] for row in con.execute("SHOW TABLES").fetchall()
    }
    second_counts = {}
    second_materialized = {}
    for m in PHYSICAL_MODELS:
        if m in optional and m not in second_actual_tables:
            second_counts[m] = 0
            second_materialized[m] = False
            continue
        try:
            second_counts[m] = con.execute(
                f"SELECT COUNT(*) FROM main.{m}"
            ).fetchone()[0]
            second_materialized[m] = True
        except Exception as exc:
            berr("runtime.model_table_missing",
                 f"main.{m} is a declared physical model but is not queryable "
                 f"after the verification rerun — {type(exc).__name__}: {exc}",
                 "transform/main.py", {"model": m, "run": 2})
    if second_state.dropped_flat_write:
        berr("runtime.state_flat_write",
             "transform_state was indexed flat during the verification rerun "
             "while multiple physical models are declared; use for_model() or "
             "generic()", "transform/main.py")
    if second_counts != first_counts or second_materialized != first_materialized:
        berr("runtime.rerun_row_count_changed",
             f"the unchanged verification rerun changed materialization or row "
             f"counts: first={first_materialized}/{first_counts}, "
             f"second={second_materialized}/{second_counts}", "transform/main.py",
             {"first_counts": first_counts, "second_counts": second_counts,
              "first_materialized": first_materialized,
              "second_materialized": second_materialized})
    if not (run / ".transform-complete").exists():
        berr("runtime.transform_incomplete",
             "the verification rerun returned without writing .transform-complete",
             "transform/main.py")
    try:
        second_state.persist()
    except Exception as exc:
        berr("runtime.state_unserializable",
             f"transform_state could not be JSON-serialized after the verification "
             f"rerun — {type(exc).__name__}: {exc}", "transform/main.py",
             tb=traceback.format_exc())
    if berrors:
        fail_b()
    say(f"state round-trip ok — transform dry-run EXECUTED twice; unchanged "
        f"rerun preserved {first_counts}")
if not dry_run_runnable:
    close_stage("s2_transform", "skipped",
                reason="dry_run_not_runnable", unverified=len(unverified))
    say(f"phase B NOT RUNNABLE — this closure needs a credential the offline "
        f"harness cannot supply, so the transform was never executed and "
        f"nothing below is evidence about it. models.py/spec.py were still "
        f"checked STRUCTURALLY; {len(unverified)} unverified entries listed "
        f"above. Verify the transform with check_data_product. Continuing to "
        f"phases C, D and E.")
else:
    close_stage("s2_transform", "passed",
                models_counted=len(PHYSICAL_MODELS),
                optional_tables_absent=sorted(absent_optional_models),
                state_round_trip=bool(uses_transform_state),
                unverified=len(unverified))
    say(f"phase B ok — transform dry-run EXECUTED"
    f"{' twice for state round-trip' if uses_transform_state else ''}; "
    f"models.py/spec.py checked "
    f"STRUCTURALLY against the pinned nxd v0.41.139 DSL surface (not "
    f"executed — no nxd wheel installable here); {len(unverified)} "
    f"unverified entries listed above. A spec fault only the real wheel or "
    f"the supervisor's spec compilation can raise still reaches handoff.")

# ---------------------------------------------------------------- Phase C ---
# Closure-record gate (Step 6a). The closure must be a SUFFICIENT handoff, and
# after this change that is a HASH-CHECKABLE property rather than a prose
# discipline: the approved spec is byte-copied in as dp-blueprint.approved.md, the
# lock carries its hash and the compiler version, and build-record.json says
# which spec the closure was compiled from. A structurally valid closure can
# still be uncontinuable if the plan it was built from lives in ../../some-doc.md
# — copied, never pointed at, is what removes that failure mode.
cerrors = []
def cerr(code, msg, at="", ev=None):
    cerrors.append(msg)
    diag("s3_closure", code, msg, path=cpath(at), evidence=ev)

# Resolve the closure's own artifacts once, before anything reports on them, so
# every Phase C diagnostic names the file that is actually there.
#
# The LOCK is resolved by name (current, then the pre-v0.38.0 dp-spec.* spelling
# — a closure built then is still valid). The SNAPSHOT is not: its filename
# travels inside the lock, which is what dp_diagnostics resolves, and the lock
# schema allows an in-closure sub-path. Guessing the name here instead would let
# the two verifiers disagree about the same closure — `lock verify` passing
# while Phase C reports a missing snapshot, and C8 silently skipping the plan.
# The name fallback is the LAST resort, for a closure whose lock is unreadable.
lockp = closure_path(CLOSURE_LOCK)
lock_name = lockp.name


def _lock_snapshot_ref(path):
    """The snapshot as the lock declares it. Returns (path or None, escaped)."""
    try:
        ref = str(json.loads(path.read_text(encoding="utf-8")).get("snapshot") or "")
    except Exception:
        return None, False
    if not ref:
        return None, False
    candidate = Path(ref)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None, True
    try:
        candidate.resolve().relative_to(Path.cwd().resolve())
    except (OSError, RuntimeError, ValueError):
        return None, True
    return candidate, False


_snap_ref, _snap_escaped = _lock_snapshot_ref(lockp)
snap = _snap_ref if _snap_ref is not None else closure_path(CLOSURE_SNAPSHOT)
snap_name = str(snap)
if _snap_escaped:
    cerr("closure.escaping_reference",
         "the lock's snapshot reference points outside the closure.", lock_name)

# C11 first, so it is in the report whatever else happens: Phase C compares the
# snapshot's BYTES, which is sufficient inside the closure (the bytes are the
# ones the canonical hash was computed from) and needs nothing but hashlib. The
# semantic comparison against the live IR is a separate v2 lock verification.
diag("s3_closure", "closure.canonical_hash_deferred",
     f"Phase C checked the snapshot's raw bytes against {lock_name}. The "
     "canonical (semantic) hash and the comparison against the live dp-blueprint.md "
     "are NOT checked here — re-run generator canonical lock verification with its "
     "resolved job_helper_dir for that.",
     path=cpath(lock_name))

# C1 / C2 — the approved plan and its lock must both be in the closure.
snap_bytes = snap.read_bytes() if snap.exists() else None
if snap_bytes is None:
    # snap_name, not the constant: when the lock declared the snapshot, that is
    # the name the closure is missing, and _verify_v3_lock reports the same one.
    # The constant would send a legacy closure's reader after the wrong file.
    cerr("closure.spec_snapshot_missing",
         f"{snap_name} is missing from the closure root — the closure "
         "carries no copy of the approved plan it was compiled from, so a cold "
         "reader cannot tell what it was supposed to build. Byte-copy the "
         "approved dp-blueprint.md in at generation (Step 6a).", snap_name)
lock = None
is_v3_lock = False
v3_approved_contracts = set()
v3_pre_transform_contracts = set()
if not lockp.exists():
    cerr("closure.lock_missing",
         f"{CLOSURE_LOCK} is missing from the closure root — without it the "
         "snapshot is an unattributed copy: no hash, no compiler version, "
         "nothing to check it against. Run `dp_diagnostics.py lock write`.",
         CLOSURE_LOCK)
else:
    try:
        lock = json.loads(lockp.read_text(encoding="utf-8"))
        if not isinstance(lock, dict):
            raise ValueError("not a JSON object")
        is_v3_lock = lock.get("schema") == "nxd-dp-spec-lock-v3"
        if lock.get("schema") not in {"nxd-dp-spec-lock-v2", "nxd-dp-spec-lock-v3"}:
            raise ValueError(f"unsupported lock schema {lock.get('schema')!r}")
        lock_keys = ({
            "schema", "spec_hash", "proposal_hash", "canonicalization", "snapshot", "snapshot_sha256",
            "proposal_snapshot", "proposal_snapshot_sha256", "spec_status_at_copy", "dp_spec_version",
            "name", "workflow", "terms_hash", "contract_inventory_hash", "locked_decisions_hash", "delivery_profile",
            "source_basename", "compiler_version", "copied_at_unix_ms",
        } if is_v3_lock else {
            "schema", "spec_hash", "canonicalization", "snapshot", "snapshot_sha256",
            "spec_status_at_copy", "dp_spec_version", "name", "workflow",
            "source_basename", "contract_names", "compiler_version", "copied_at_unix_ms",
        })
        if set(lock) != lock_keys:
            raise ValueError("lock keys do not match the complete v2/v3 envelope")
        expected_version = 3 if is_v3_lock else 2
        expected_canon = "nxd-dp-spec-canon-v3" if is_v3_lock else "nxd-dp-spec-canon-v2"
        if lock.get("dp_spec_version") != expected_version:
            raise ValueError(f"dp_spec_version is not {expected_version}")
        if lock.get("canonicalization") != expected_canon:
            raise ValueError(f"canonicalization is not {expected_canon}")
        if is_v3_lock and lock.get("delivery_profile") != "desktop-local-duckdb-semantic-query":
            raise ValueError("delivery_profile is not the fixed desktop-local DuckDB semantic-query profile")
        compiler = lock.get("compiler_version")
        if not isinstance(compiler, dict) or set(compiler) != {"plugin", "generator_skill", "self_check"}:
            raise ValueError("compiler_version is not the complete v2/v3 lock shape")
        if any(not isinstance(compiler[key], str) or not compiler[key]
               for key in ("plugin", "generator_skill", "self_check")):
            raise ValueError("compiler_version values must be non-empty strings")
        if is_v3_lock and compiler.get("self_check") != "nxd-self-check-v3":
            raise ValueError("v3 compiler_version.self_check is not nxd-self-check-v3")
    except Exception as exc:
        lock = None
        cerr("closure.lock_unparseable",
             f"{lock_name} could not be read as a lock file — "
             f"{type(exc).__name__}: {exc}", lock_name)

# C3 — tamper check. The snapshot is EVIDENCE; evidence edited after it was
# written is not evidence. This is the mechanical half of "once approved, the
# spec is frozen for that build", which used to be honour-system.
if lock is not None and snap_bytes is not None:
    snapshot_version_match = re.search(
        rb"^dp_spec_version:\s*(\d+)\s*$", snap_bytes, re.MULTILINE
    )
    snapshot_version = int(snapshot_version_match.group(1)) if snapshot_version_match else None
    expected_snapshot_version = 3 if is_v3_lock else 2
    if snapshot_version != expected_snapshot_version:
        cerr("closure.spec_hash_mismatch",
             f"{snap_name} declares dp_spec_version={snapshot_version!r}, "
             f"but the lock envelope is for version {expected_snapshot_version}.",
             snap_name,
             {"expected": expected_snapshot_version, "actual": snapshot_version})
    if is_v3_lock:
        for field in ("name", "workflow"):
            match = re.search(rb"^" + field.encode("ascii") + rb":\s*(.+?)\s*$", snap_bytes, re.MULTILINE)
            snapshot_value = match.group(1).decode("utf-8") if match else None
            if snapshot_value != lock.get(field):
                cerr("closure.spec_hash_mismatch",
                     f"{snap_name} {field} does not match the v3 lock.",
                     snap_name,
                     {"expected": lock.get(field), "actual": snapshot_value})
    got = hashlib.sha256(snap_bytes).hexdigest()
    want = lock.get("snapshot_sha256")
    if got != want:
        cerr("closure.lock_snapshot_byte_mismatch",
             f"{snap_name} does not match {lock_name} "
             f"snapshot_sha256 — the in-closure copy was edited after it was "
             f"written. The plan a build was compiled from is not editable "
             f"in place: change the live dp-blueprint.md, re-approve, regenerate.",
             snap_name, {"expected": want, "actual": got})
    if is_v3_lock:
        proposal_path = Path(str(lock.get("proposal_snapshot", "")))
        proposal_name = proposal_path.name or CLOSURE_PROPOSAL
        try:
            proposal_path.resolve().relative_to(Path.cwd().resolve())
            inside_closure = True
        except (OSError, RuntimeError, ValueError):
            inside_closure = False
        if proposal_path.is_absolute() or ".." in proposal_path.parts or not inside_closure:
            cerr("closure.escaping_reference",
                 "the v3 typed proposal snapshot points outside the closure.",
                 lock_name)
            proposal_path = Path("")
        proposal_bytes = proposal_path.read_bytes() if proposal_path.is_file() else None
        if proposal_bytes is None:
            cerr("closure.spec_snapshot_missing",
                 "the v3 typed proposal snapshot is missing from the closure.",
                 proposal_name)
        elif hashlib.sha256(proposal_bytes).hexdigest() != lock.get("proposal_snapshot_sha256"):
            cerr("closure.lock_snapshot_byte_mismatch",
                 f"{proposal_name} does not match its lock hash.",
                 proposal_name)
        else:
            try:
                proposal = json.loads(proposal_bytes.decode("utf-8"))
                _v3_contracts = [
                    item for item in proposal.get("proposal", {}).get("contracts", [])
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                ]
                v3_approved_contracts = {item["id"] for item in _v3_contracts}
                # Phase travels on the compiled contract, and the runtime can only
                # execute a pre_transform one for a declared CSV source-aligned
                # input. Keep the split so the support check below can name ids.
                v3_pre_transform_contracts = {
                    item["id"] for item in _v3_contracts
                    if item.get("phase") == "pre_transform"
                }
                proposal_payload = {
                    key: proposal[key] for key in proposal
                    if key not in {"hashes", "proposal_hash"}
                }
                proposal_hash = "sha256:" + hashlib.sha256(json.dumps(
                    proposal_payload, sort_keys=True, ensure_ascii=False,
                    separators=(",", ":")
                ).encode("utf-8")).hexdigest()
                if proposal_hash != lock.get("proposal_hash"):
                    cerr("closure.spec_hash_mismatch",
                         "the typed proposal hash does not match the v3 lock.",
                         proposal_name)
                proposal_body = proposal.get("proposal")
                if not isinstance(proposal_body, dict):
                    raise ValueError("the v3 typed proposal payload is not an object")
                terms = proposal_body.get("terms", [])
                if not isinstance(terms, list):
                    raise ValueError("the v3 Terms inventory is not a list")
                canonical_terms = sorted(
                    (item for item in terms if isinstance(item, dict)),
                    key=lambda item: str(item.get("id", "")),
                )
                terms_hash = hashlib.sha256(json.dumps(
                    canonical_terms, sort_keys=True, ensure_ascii=False,
                    separators=(",", ":")
                ).encode("utf-8")).hexdigest()
                if terms_hash != lock.get("terms_hash"):
                    cerr("closure.terms_hash_mismatch",
                         "the inline Terms inventory does not match its v3 lock hash.",
                         proposal_name)
                contracts = proposal_body.get("contracts", [])
                if not isinstance(contracts, list):
                    raise ValueError("the v3 contract inventory is not a list")
                canonical_contracts = sorted(
                    ({
                        **{key: item.get(key) for key in ("id", "attachment", "model", "phase", "guarantee", "rule")},
                        "fields": sorted(item.get("fields", [])),
                    }
                     for item in contracts if isinstance(item, dict)),
                    key=lambda item: str(item.get("id", "")),
                )
                contract_hash = hashlib.sha256(json.dumps(
                    canonical_contracts, sort_keys=True, ensure_ascii=False,
                    separators=(",", ":")
                ).encode("utf-8")).hexdigest()
                if contract_hash != lock.get("contract_inventory_hash"):
                    cerr("closure.contract_inventory_hash_mismatch",
                         "the compiled contract inventory does not match its v3 lock hash.",
                         proposal_name)
                decisions = proposal_body.get("decisions", [])
                if not isinstance(decisions, list):
                    raise ValueError("the v3 decision inventory is not a list")
                canonical_decisions = sorted(
                    ({key: item[key] for key in ("id", "target", "ruling", "status")}
                     for item in decisions
                     if isinstance(item, dict)
                     and item.get("status") == "locked"
                     and all(key in item for key in ("id", "target", "ruling", "status"))),
                    key=lambda item: str(item.get("id", "")),
                )
                decisions_hash = hashlib.sha256(json.dumps(
                    canonical_decisions, sort_keys=True, ensure_ascii=False,
                    separators=(",", ":")
                ).encode("utf-8")).hexdigest()
                if decisions_hash != lock.get("locked_decisions_hash"):
                    cerr("closure.decision_inventory_mismatch",
                         "the settled locked-decision inventory does not match its v3 lock hash.",
                         proposal_name)
            except Exception as exc:
                cerr("closure.lock_unparseable",
                     f"the v3 typed proposal snapshot is invalid: "
                     f"{type(exc).__name__}: {exc}",
                     proposal_name)

# C4 — a snapshot of an unapproved spec is a build nobody signed off.
if lock is not None and lock.get("spec_status_at_copy") != "approved":
    cerr("closure.lock_status_not_approved",
         f"{lock_name} records spec_status_at_copy="
         f"{lock.get('spec_status_at_copy')!r} — the closure was generated from "
         f"a spec that was not approved. Approval is what gets copied and "
         f"hashed; without it nothing here was signed off.", lock_name,
         {"expected": "approved", "actual": lock.get("spec_status_at_copy")})

# C5 / C6 — the build record exists and names the SAME plan as the lock. It is
# generated (`dp_diagnostics.py record init`), never hand-authored, and it must
# exist before this phase runs because this phase is one of its writers.
record = None
recp = Path("build-record.json")
if not recp.exists():
    cerr("closure.build_record_missing",
         "build-record.json is missing from the closure root — outcomes "
         "(attempts, concessions, blockers, read-back) have nowhere to land, "
         "so a green run would be indistinguishable from a green run that "
         "conceded something. Run `dp_diagnostics.py record init` at "
         "generation, before self_check.py.", "build-record.json")
else:
    try:
        record = json.loads(recp.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise ValueError("not a JSON object")
        if record.get("schema") != "nxd-build-record-v2":
            raise ValueError(f"schema is {record.get('schema')!r}, expected "
                             f"'nxd-build-record-v2'")
        record_keys = {
            "schema", "workflow", "data_product", "closure_path", "compiled_from",
            "compiler_version", "generated_at_unix_ms", "generator_model", "stages",
            "attempts", "review_rounds", "concessions", "blockers", "readback",
            "evidence", "caps", "narrative",
        }
        if set(record) != record_keys:
            raise ValueError("build record keys do not match the complete v2 envelope")
        compiler = record.get("compiler_version")
        if not isinstance(compiler, dict) or set(compiler) != {
                "plugin", "generator_skill", "dp_spec_version", "canonicalization"}:
            raise ValueError("compiler_version is not the complete v2 build-record shape")
        record_version = compiler.get("dp_spec_version")
        if record_version not in {2, 3}:
            raise ValueError("compiler_version.dp_spec_version is not 2 or 3")
        expected_record_canon = "nxd-dp-spec-canon-v3" if record_version == 3 else "nxd-dp-spec-canon-v2"
        if compiler.get("canonicalization") != expected_record_canon:
            raise ValueError(f"compiler_version.canonicalization is not {expected_record_canon}")
        if any(not isinstance(compiler[key], str) or not compiler[key]
               for key in ("plugin", "generator_skill")):
            raise ValueError("compiler_version plugin/generator_skill values must be non-empty strings")
    except Exception as exc:
        record = None
        cerr("closure.build_record_invalid",
             f"build-record.json could not be read as a build record — "
             f"{type(exc).__name__}: {exc}", "build-record.json")
if lock is not None and record is not None and \
        record.get("compiled_from") != lock.get("spec_hash"):
    cerr("closure.build_record_hash_mismatch",
         f"build-record.json compiled_from does not equal {lock_name} "
         f"spec_hash — the record describes a build of a DIFFERENT plan than "
         f"the one snapshotted here. Regenerate rather than reconciling by "
         f"hand.", "build-record.json",
         {"expected": lock.get("spec_hash"),
          "actual": record.get("compiled_from")})

# C7 — the reopen recipe. It is the one thing a cold reader needs that is
# neither plan (the snapshot) nor outcome (the record).
if not Path("README.md").exists():
    cerr("closure.readme_missing",
         "README.md is missing from the closure root — it carries the reopen "
         "recipe and the credential key names, and nothing else does.",
         "README.md")

# C8 — escape scan. Any closure file that references a design/contract doc by a
# path escaping the closure (a ../-rooted markdown reference) is a dangling
# cross-boundary pointer. The snapshot IS scanned: a ../-rooted reference inside
# the approved plan is exactly the dangling pointer this design removes, and no
# carve-out is needed anywhere because the IR is COPIED rather than pointed at.
ESCAPE = re.compile(r"\.\.(?:/[^\s\)\"']*)+\.md", re.IGNORECASE)
scan = ["README.md", snap_name, "spec.py", "models.py",
        "transform/main.py"]
# rglob, not glob: contracts/ now holds expectations/ and promises/ subtrees as
# well as the flat contracts/<name>.md, and a verifier that points out of the
# closure escapes just as effectively from one level down.
scan += [str(p) for p in Path(".").rglob("contracts/*")]
scan += [str(p) for p in Path(".").rglob("contracts/*/*")]
for rel in scan:
    p = Path(rel)
    if not p.is_file():
        continue
    # errors="replace" rather than a try/except: this loop only regex-searches
    # for ../*.md, so a replacement character can neither create nor mask a
    # match, and a file this scan cannot decode is still a file whose escaping
    # references must be reported. Skipping it would make an undecodable
    # contract the one place an escaping reference hides.
    for m in ESCAPE.findall(p.read_text(encoding="utf-8", errors="replace")):
        cerr("closure.escaping_reference",
             f"{rel}: references '{m}' — a contract/design path that escapes "
             f"the closure. Materialize it inside the closure "
             f"(dp-blueprint.approved.md / contracts/<name>.md / inert derived "
             f"model), never a ../ pointer.", rel, {"found": m})

# C10 — sensitivity artifacts. The trigger is STRUCTURAL: a *-source service
# carrying a populated `attributes:` list holds a live credential in plaintext.
# A CSV or file source keeps `attributes: []` and is exempt, so this cannot
# false-positive on a healthy local closure. Names the missing FILES only —
# never reads or echoes an attribute value, because a check that prints the
# secret it found turns a contained file leak into a transcript leak.
profile = Path("infra-profile.yaml")
if profile.exists():
    text = profile.read_text(encoding="utf-8", errors="replace")
    # A populated attributes list = `attributes:` followed by a `- ` item before
    # the next key at the same or shallower indent. `attributes: []` never matches.
    # Match every YAML spelling of a populated list, because a gate that only
    # recognises the shipped templates is not the structural check it claims to
    # be — an improvised profile carries exactly the same live credential:
    #   block, indented      `attributes:` / `  - key: ...`   (what templates emit)
    #   block, zero-indent   `attributes:` / `- key: ...`     (also valid YAML)
    #   inline flow          `attributes: [{key: ...}]`
    # `attributes: []` and `attributes: [ ]` must NOT match in any form.
    has_secret = (
        re.search(r"^\s*attributes:\s*\n\s*-\s", text, re.MULTILINE) is not None
        or re.search(r"^\s*attributes:\s*\[\s*[^\s\]]", text, re.MULTILINE) is not None
    )
    if has_secret:
        for name, code, why in (
            (".gitignore", "closure.gitignore_missing",
             "git will happily commit infra-profile.yaml without it"),
            ("SENSITIVE", "closure.sensitive_missing",
             "a cold reader gets no warning before opening the closure"),
        ):
            if not Path(name).exists():
                cerr(code,
                     f"{name} is missing, but infra-profile.yaml carries a populated "
                     f"`attributes:` list (a live credential in plaintext) — {why}. "
                     f"See reference/database-source.md, 'Sensitivity artifacts'.",
                     name)
        gi = Path(".gitignore")
        if gi.exists() and "infra-profile.yaml" not in gi.read_text(
            encoding="utf-8", errors="replace"):
            cerr("closure.gitignore_not_naming_profile",
                 ".gitignore exists but does not ignore infra-profile.yaml — the one "
                 "file that must never be committed. Ignore it by name, never `*`.",
                 ".gitignore")

# C12 — the custom-contract gate. Explicit custom-contract wiring in spec.py
# compiles to an executable verifier under contracts/. This proves the wiring
# produced something that can actually fail: a contract that parses but can
# never return FAILED is decorative, and a decorative contract is worse than
# none — it reports a guarantee as enforced while enforcing nothing.
#
# The gate is STRUCTURAL and offline. A pass means every declared contract is
# wired once, names a verifier that exists under contracts/, and that verifier
# is shaped to run. It does NOT mean the verifier was executed against data.
CONTRACT_DIRS = {"expectations": "pre_transform", "promises": "post_transform"}
# Three arms, each bounded at an identifier boundary.
#   1. the credential word as a SUFFIX, with any prefix — `db_password`,
#      `openai_api_key`, `MY_SECRET`. A uniform leading \b missed all three.
#   2. the credential word as a PREFIX of `_key` — `SECRET_KEY`, `private_key`,
#      `aws_secret_access_key`. Arm 1 only sees suffixes, so these escaped it,
#      and `SECRET_KEY = "..."` is about as idiomatic as a Python secret gets.
#      Deliberately NOT bare `*_key`: `sort_key`, `primary_key` and `cache_key`
#      are not credentials.
#   3. `token`, narrowest of the three: bare, or behind a prefix that denotes a
#      credential. Each prefix is bounded — unbounded, `id` let `valid_token`,
#      `uuid_token` and `grid_token` in. The bare arm excludes `-` as well as
#      word characters, because \b treats a hyphen as a boundary and
#      `csrf-token` would otherwise escape the carve-out `csrf_token` gets.
# Non-assignments (`password_columns`, `token_fields`, `tokenizer`) match none.
SECRET_LITERAL = re.compile(
    r"(?i)(?:(?:^|[^A-Za-z0-9])[A-Za-z0-9_]*(?:api[_-]?key|password|passwd|secret)"
    r"|(?:^|[^A-Za-z0-9])(?:secret|private|signing|encryption"
    r"|aws[_-]?secret[_-]?access)[_-]key"
    r"|(?:^|[^A-Za-z0-9])(?:access|auth|oauth|refresh|bearer|session|api|jwt|id"
    r"|secret|private|github|gitlab|slack)[_-]token"
    r"|(?:^|[^A-Za-z0-9_-])token)\s*=\s*[\"']")


def _verify_scripts(tree):
    """-> (paths by name, unnamed count, duplicate names, {name: (desc, model)}).

    `custom("x").model(m).verify(script("p").compute(c))` is one call chain, so
    the verify() that belongs to a given custom() is the INNERMOST one whose
    subtree contains it. Matching on "any verify() in the file" would associate
    the wrong path as soon as a closure declares a second contract.
    """
    found, unnamed, dupes, meta = {}, 0, [], {}
    verifies = [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and call_name(n) == "verify"]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or call_name(node) != "custom":
            continue
        cname = literal_str(node.args[0] if node.args else None)
        if cname is None:
            unnamed += 1
            continue
        # Smallest enclosing verify() — by node count, so a nested chain picks
        # its own and never a sibling contract's.
        best, best_size = None, None
        for v in verifies:
            subtree = list(ast.walk(v))
            if not any(sub is node for sub in subtree):
                continue
            if best_size is None or len(subtree) < best_size:
                best, best_size = v, len(subtree)
        path = None
        if best is not None:
            for sub in ast.walk(best):
                if isinstance(sub, ast.Call) and call_name(sub) == "script":
                    path = literal_str(sub.args[0] if sub.args else None)
                    break
        # .description(...) and .model(...) hang off the same custom() chain.
        # The reference doc lists both as preflight failures, so check them here
        # rather than leaving the eval fixture as the only gate that does — a
        # closure passing Phase C and failing the scenario checker is the split
        # this file keeps closing.
        outer = None
        for cand in ast.walk(tree):
            if not isinstance(cand, ast.Call):
                continue
            if any(c is node for c in spine(cand)) and (
                    outer is None or len(list(ast.walk(cand))) > len(list(ast.walk(outer)))):
                outer = cand
        chain = spine(outer) if outer is not None else [node]
        has_desc = any(call_name(c) == "description" and c.args
                       and literal_str(c.args[0]) for c in chain)
        has_model = any(call_name(c) == "model" and c.args for c in chain)
        if cname in found:
            dupes.append(cname)
        found[cname] = path
        meta[cname] = (has_desc, has_model)
    return found, unnamed, dupes, meta


try:
    _spec_tree = ast.parse(spec_src, "spec.py")
except SyntaxError:
    _spec_tree = None          # Phase A already reported it; do not double-report

if _spec_tree is not None:
    contracts, unnamed, dupes, contract_meta = _verify_scripts(_spec_tree)
    approved_contracts = (
        v3_approved_contracts
        if is_v3_lock
        else set(lock.get("contract_names") or []) if isinstance(lock, dict) else set()
    )
    wired_contracts = set(contracts)
    # An Input's Expectations compile to pre_transform contracts, and the desktop
    # runtime executes a custom input expectation ONLY for a declared CSV
    # source-aligned input bound to the `csv-source` service. A db-source or
    # api-source closure declares no such input, so there is nowhere to attach
    # them and the inventory can never be satisfied.
    #
    # Without this check the author learns that from a bare
    # contract_inventory_mismatch, AFTER a whole closure exists, and the obvious
    # way to make it green is to wire them as output promises - which silently
    # moves a phase the user approved. Name it, and name the three ways out.
    if is_v3_lock and v3_pre_transform_contracts and "csv-source" not in declared_sources:
        cerr(
            "closure.contract_phase_unsupported",
            f"{len(v3_pre_transform_contracts)} approved contract(s) are "
            f"pre_transform input expectations - "
            f"{sorted(v3_pre_transform_contracts)} - but spec.py declares "
            f"{sorted(declared_sources) or 'no connector service'} and no "
            f"csv-source. This runtime runs a custom input expectation only for "
            f"a declared CSV source-aligned input, so these cannot execute at "
            f"that phase and the inventory cannot be satisfied. Resolve it in "
            f"dp-blueprint.md, not here: state the guarantee under the matching "
            f"Output's Promises (verified post-transform against the landed "
            f"relation), keep it as prose with no executable contract, or supply "
            f"a CSV export. Each is a spec edit needing re-approval - wiring an "
            f"input expectation as an output promise to clear this check moves a "
            f"phase the user approved.",
            "spec.py",
            {"pre_transform_contracts": sorted(v3_pre_transform_contracts),
             "declared_sources": sorted(declared_sources)},
        )
    if approved_contracts != wired_contracts:
        cerr(
            "closure.contract_inventory_mismatch",
            f"spec.py custom contracts {sorted(wired_contracts)} do not match "
            f"the approved contract inventory {sorted(approved_contracts)}.",
            "spec.py",
            {"approved": sorted(approved_contracts), "wired": sorted(wired_contracts)},
        )

    if unnamed:
        cerr("closure.contract_not_wired",
             f"spec.py: {unnamed} custom(...) contract(s) have a non-literal "
             f"name. The name selects the verifier file, so it must be a string "
             f"literal.", "spec.py", {"count": unnamed})
    for d in sorted(set(dupes)):
        cerr("closure.contract_duplicate_name",
             f"spec.py: two custom contracts are named {d!r} — the name selects "
             f"the generated verifier file, so one silently overwrites the "
             f"other. Names are unique across expectations AND promises.",
             "spec.py", {"found": d})

    for cname, (has_desc, has_model) in sorted(contract_meta.items()):
        if not has_desc:
            cerr("closure.contract_not_wired",
                 f"spec.py: custom({cname!r}) has no non-empty "
                 f".description(...). The description is what a later reader "
                 f"sees when the contract fails.", "spec.py",
                 {"contract": cname})
        if not has_model:
            cerr("closure.contract_not_wired",
                 f"spec.py: custom({cname!r}) has no .model(...) — a contract "
                 f"with no subject cannot be attached to an input or output.",
                 "spec.py", {"contract": cname})

    referenced = set()
    for cname, vpath in sorted(contracts.items()):
        if not vpath:
            cerr("closure.contract_not_wired",
                 f"spec.py: custom({cname!r}) has no "
                 f".verify(script(...).compute(_compute)) — a contract with no "
                 f"verifier declares a guarantee nothing checks.",
                 "spec.py", {"contract": cname})
            continue
        # Containment before touching the path: a verifier resolved outside the
        # closure is the cross-boundary pointer C9 exists to stop.
        if vpath.startswith("/") or ".." in Path(vpath).parts or \
                not vpath.startswith("contracts/"):
            cerr("closure.contract_not_wired",
                 f"spec.py: custom({cname!r}) verifier {vpath!r} must stay under "
                 f"contracts/ inside the closure.", "spec.py",
                 {"contract": cname, "found": vpath})
            continue
        referenced.add(vpath)
        vp = Path(vpath)
        if not vp.is_file():
            cerr("closure.contract_verifier_missing",
                 f"{vpath}: referenced by custom({cname!r}) but the file does "
                 f"not exist.", vpath, {"contract": cname})
            continue
        try:
            # Pinned to UTF-8, not the locale codec. Phase E reads this same
            # file as UTF-8 and defers an undecodable one to here, so reading it
            # under a different codec breaks the deferral in both directions: on
            # a cp1252 host Phase C decodes bytes Phase E rejected and reports
            # nothing, and under an ASCII locale it rejects a file that is valid
            # UTF-8 — failing the closure over an em dash in a comment, with a
            # message telling the author to write UTF-8 that they already wrote.
            vsrc = vp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            # Phase E defers an undecodable verifier to "Phase C's finding to
            # report" — so Phase C has to survive long enough to report it.
            # Unguarded, this read died before cerr could be called and the
            # deferral pointed at a phase that had already crashed.
            cerr("closure.contract_verifier_malformed",
                 f"{vpath}: cannot be read as UTF-8 text ({exc}). A verifier is "
                 f"executed Python; it must be readable and UTF-8-encoded.", vpath,
                 {"contract": cname})
            continue
        try:
            vtree = ast.parse(vsrc, vpath)
        except SyntaxError as exc:
            cerr("closure.contract_verifier_malformed",
                 f"{vpath}: verifier does not parse ({exc.msg}).", vpath,
                 {"contract": cname})
            continue

        verifiers = [n for n in ast.walk(vtree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and any(call_name(d) == "on_verify"
                             for d in n.decorator_list
                             if isinstance(d, ast.Call))]
        # Report async BEFORE bailing on the count. A file carrying one sync and
        # one async verifier is both duplicated and unrunnable, and an agent
        # told only "found 2" deletes the wrong one.
        if any(isinstance(v, ast.AsyncFunctionDef) for v in verifiers):
            cerr("closure.contract_verifier_malformed",
                 f"{vpath}: Desktop custom verifier must be synchronous; the "
                 f"runtime does not await async verifier functions, so an async "
                 f"verifier never runs and the contract silently passes.",
                 vpath, {"contract": cname})
        if len(verifiers) != 1:
            cerr("closure.contract_verifier_malformed",
                 f"{vpath}: needs exactly one @data_product.on_verify() "
                 f"function, found {len(verifiers)}. script(...) executes the "
                 f"whole file, so the contract name cannot select among "
                 f"several.", vpath, {"contract": cname, "found": len(verifiers)})
            continue
        if isinstance(verifiers[0], ast.AsyncFunctionDef):
            continue
        # vtree.body only — a guard nested inside a function never fires when
        # script(...) executes the file, so it is not a guard at all.
        if not any(isinstance(n, ast.If) and "__main__" in ast.dump(n.test)
                   and any(isinstance(s, ast.Expr)
                           and isinstance(s.value, ast.Call)
                           and call_name(s.value) == "verify"
                           for s in n.body)
                   for n in vtree.body):
            cerr("closure.contract_verifier_malformed",
                 f"{vpath}: needs a module-level main guard — "
                 f"`if __name__ == \"__main__\": data_product.verify()`. Without "
                 f"it at module level script(...) imports the file and checks "
                 f"nothing; a guard nested inside a function never fires.",
                 vpath, {"contract": cname})
            continue

        # Inert: a verifier that cannot fail. It must be able to return FAILED,
        # and that return must sit behind a real (non-constant) condition — an
        # `if True:` branch reads as a check and is dead code.
        # Scope both tests to the verifier body, not the whole file: a FAILED
        # mentioned only in an import or a comment elsewhere does not make this
        # function able to fail.
        body_src = ast.unparse(verifiers[0])
        can_fail = "FAILED" in body_src
        # ast.IfExp as well as ast.If — `FAILED if violations else PASS` is a
        # non-literal condition by any reading, and the message says
        # "non-literal condition", so rejecting the ternary contradicts it.
        live_branch = any(
            isinstance(n, (ast.If, ast.IfExp)) and not isinstance(n.test, ast.Constant)
            for n in ast.walk(verifiers[0]))
        # A stub BODY is inert; a `pass` deeper inside is not. `except X: pass`
        # is ordinary error handling, so testing every statement in the function
        # would reject a working verifier — and the eval checker, which says it
        # mirrors this predicate, must agree.
        stub_body = all(
            isinstance(n, ast.Pass)
            or (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                and n.value.value is Ellipsis)
            for n in verifiers[0].body)
        if not can_fail or not live_branch or stub_body or "PASS" not in body_src:
            cerr("closure.contract_verifier_inert",
                 f"{vpath}: verifier is inert — require FAILED behind a "
                 f"non-literal condition and a PASS result, never "
                 f"pass/ellipsis/dead branches. A contract that always passes "
                 f"reports the guarantee as enforced while enforcing nothing.",
                 vpath, {"contract": cname})
        if SECRET_LITERAL.search(vsrc):
            cerr("closure.contract_verifier_secret",
                 f"{vpath}: contains a literal secret-like assignment. "
                 f"Credentials reach a closure through the infra profile, "
                 f"never inline in a verifier.", vpath, {"contract": cname})

    # Every file under contracts/*/ must be reachable from spec.py. An
    # unreferenced verifier is the decorative case in its purest form: it looks
    # like an enforced guarantee to a reader and never executes.
    for sub in sorted(CONTRACT_DIRS):
        d = Path("contracts") / sub
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.py")):
            if p.name == "__init__.py":
                continue
            if str(p) not in referenced:
                cerr("closure.contract_verifier_unreferenced",
                     f"{p}: not referenced by any custom(...) in spec.py — a "
                     f"verifier nothing wires never runs.", str(p))

    # --- the local desktop runtime binding for source-aligned inputs -----------------
    # These apply to EVERY source_aligned_input(), contract or not: on this
    # runtime a desktop input must be the unlabeled csv-source, and the labeled
    # instances are transform secrets only. A labeled service bound through
    # .input(...).source(...) does not resolve, so the closure pins clean and
    # fails at s5/s6 — which is exactly the class of fault an offline gate
    # should catch first.
    CSV_SERVICE = "/infra-profile/desktop-local#/services/csv-source"
    inputs = [n for n in ast.walk(_spec_tree)
              if isinstance(n, ast.Call) and call_name(n) == "source_aligned_input"]
    sources = [n for n in ast.walk(_spec_tree)
               if isinstance(n, ast.Call) and call_name(n) == "source"]
    csv_literals = {t.id for st in ast.walk(_spec_tree)
                    if isinstance(st, ast.Assign)
                    and literal_str(st.value) == CSV_SERVICE
                    for t in st.targets if isinstance(t, ast.Name)}
    for s_call in sources:
        arg = s_call.args[0] if s_call.args else None
        ref = literal_str(arg)
        named = isinstance(arg, ast.Name) and arg.id in csv_literals
        if named or ref == CSV_SERVICE:
            continue
        shown = ref if ref is not None else (
            ast.unparse(arg) if arg is not None else "<none>")
        cerr("closure.input_service_mismatch",
             f"spec.py: Desktop source-aligned inputs currently require "
             f".source(_csv) bound exactly to {CSV_SERVICE}; labeled CSV "
             f"services are transform-only on this runtime. Got {shown!r}.",
             "spec.py", {"found": shown})

    # The DuckDB output port must carry the duckdb storage service, not the CSV
    # one. Swapping them parses and pins, then writes the output nowhere useful.
    for n in ast.walk(_spec_tree):
        if not isinstance(n, ast.Call) or call_name(n) != "port":
            continue
        if literal_str(n.args[0] if n.args else None) != "duckdb":
            continue
        st = n.args[1] if len(n.args) > 1 else None
        inner = st.args[0] if isinstance(st, ast.Call) and st.args else None
        ref = literal_str(inner)
        if isinstance(inner, ast.Name) and inner.id in csv_literals or \
                ref == CSV_SERVICE:
            cerr("closure.port_storage_mismatch",
                 f"spec.py: the DuckDB output declaration is bound to the CSV "
                 f"service. .port(\"duckdb\", storage(_duckdb)) must carry the "
                 f"duckdb service.", "spec.py")

    # csv-source-path is the export root every model_paths entry resolves under.
    # It must be relative and contained: an absolute or ../ root reaches outside
    # the closure, which is the same escape C9 forbids for contract references.
    csv_root = None
    csvp = Path("csv-source-path")
    if inputs and not csvp.is_file():
        cerr("closure.csv_root_invalid",
             "csv-source-path is missing, but spec.py declares a source-aligned "
             "input. It carries the export root every model_paths entry "
             "resolves under.", "csv-source-path")
    elif csvp.is_file():
        # errors="replace" like the neighbouring reads: this value is only
        # checked for a "/" prefix, ".." parts and emptiness, none of which a
        # replacement character can create or mask — so a latin-1 export root
        # ("données/") surfaces as closure.csv_root_invalid naming the mangled
        # path, which is a finding, instead of a bare traceback with no code.
        raw = csvp.read_text(encoding="utf-8", errors="replace").strip()
        parts = Path(raw).parts if raw else ()
        if (not raw or raw.startswith("/") or ".." in parts
                or any(p in ("", ".") for p in parts)):
            cerr("closure.csv_root_invalid",
                 f"csv-source-path: custom CSV input requires an existing "
                 f"contained relative export root, got {raw!r}.",
                 "csv-source-path", {"found": raw})
        else:
            csv_root = Path(raw)

    # Each declared model_paths entry must resolve to a real CSV under that root.
    for n in ast.walk(_spec_tree):
        if not isinstance(n, ast.Call) or call_name(n) != "config":
            continue
        cfg = n.args[0] if n.args else None
        if not isinstance(cfg, ast.Dict):
            continue
        for k, v in zip(cfg.keys, cfg.values):
            if literal_str(k) != "model_paths" or not isinstance(v, ast.Dict):
                continue
            for mk, mv in zip(v.keys, v.values):
                model, rel = literal_str(mk), literal_str(mv)
                if not model or not rel:
                    continue
                if rel.startswith("/") or ".." in Path(rel).parts:
                    cerr("closure.model_path_unresolved",
                         f"spec.py: model_paths[{model!r}] must be a safe "
                         f"relative path, got {rel!r}.", "spec.py",
                         {"found": rel})
                elif csv_root is not None and not (csv_root / rel).is_file():
                    cerr("closure.model_path_unresolved",
                         f"spec.py: model_paths[{model!r}] must resolve to an "
                         f"existing csv-source-path/*.csv file; "
                         f"{csv_root / rel} does not exist.", "spec.py",
                         {"found": str(csv_root / rel)})

    # The profile must name the services spec.py references, bound to the
    # drivers this runtime provides. Binding driver TO service name catches a
    # swap, which a per-driver presence check cannot.
    prof = Path("infra-profile.yaml")
    if prof.is_file():
        ptext = prof.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"^metadata:\n\s+name: desktop-local$", ptext, re.M):
            cerr("closure.profile_name_mismatch",
                 "infra-profile.yaml: metadata.name must be desktop-local to "
                 "match the spec.py infra_profile.", "infra-profile.yaml")
        required = {"duckdb": "nxd:local/duckdb/storage:0.1.0",
                    "python-compute": "nxd:local/python/compute:0.1.0"}
        if inputs:
            required["csv-source"] = "nxd:local/file/storage:0.1.0"
        for svc, driver in sorted(required.items()):
            # Stop at the next `- name:`. Without that guard the lazy `.*\n`
            # walks out of this service's block and adopts a LATER service's
            # driver, so a service with no `driver:` key at all is reported as
            # bound to someone else's — naming a line that does not exist and
            # never emitting profile_service_missing, the accurate code.
            block = re.search(
                r"- name: %s\n(?:(?!\s*-\s+name:)\s+.*\n)*?\s+driver: (\S+)"
                % re.escape(svc),
                ptext)
            if block is None:
                # Absence is a fault, not a skip. Phase A only checks that a
                # /infra-profile/…#/services/<name> ref is well FORMED, never
                # that the profile declares it, so a closure missing a service
                # entirely reached the supervisor unreported — which is exactly
                # the before-arm failure this PR's ledger entry quotes.
                cerr("closure.profile_service_missing",
                     f"infra-profile.yaml does not declare the {svc} service, "
                     f"which spec.py references. The desktop-local profile "
                     f"needs duckdb, python-compute and — for a source-aligned "
                     f"input — csv-source.", "infra-profile.yaml",
                     {"service": svc})
            elif block.group(1) != driver:
                cerr("closure.profile_driver_mismatch",
                     f"infra-profile.yaml: {svc} must use {driver}, got "
                     f"{block.group(1)}.", "infra-profile.yaml",
                     {"service": svc, "found": block.group(1)})

if cerrors:
    say("\nPHASE C FAILED — closure-record gate (approved spec snapshot, lock, "
        "build record):")
    for e in cerrors:
        say(f"  - {e}")
    close_stage("s3_closure", "failed", errors=len(cerrors))
    finish(1)
say("phase C ok — approved spec snapshot + lock present, no closure-escaping "
    "contract references")

# ---------------------------------------------------------------- Phase D ---
# Policy-boundary gate. A ruling is landed data the user can edit, never a
# literal in transform code. Both halves are checked, because complying with
# either alone leaves the defect intact:
#   (a) nxd_decisions, if promised, is a BASE model backed by data/ with a
#       status column AND a provenance column — not a derived model generated
#       from a Python literal, which produces a ledger that DESCRIBES code
#       rather than driving it. status and provenance are orthogonal axes
#       (settled-or-not vs authored-by), so both are required: a ledger with
#       only status cannot tell a weight the user supplied from a threshold the
#       agent invented to fill an underspecified rubric;
#   (b) no value in a landed policy CSV also appears as a literal in
#       transform/main.py — a duplicated threshold silently diverges from the
#       row that claims to be editable.
# Ground truth is the closure's own landed data, so this needs no fixture.
# Phase D shares stage s3_closure with Phase C: both are offline, both are
# agent-owned, both self-heal. The code and the path tell them apart.
# === PHASE-D-BEGIN ===
# Anchors, not decoration: evals/tests/test_policy_boundary_phase_d.py slices the
# block out by these two markers. It used to locate the block by scanning the
# script for the phase's error-list assignment and cutting from there to the
# branch that reports it — a content heuristic that holds only
# while exactly one phase has that shape. Phase E now has it too, so the
# heuristic is one edit away from selecting the wrong region, and a test that
# extracts the wrong region does not fail: it passes, having stopped testing
# Phase D. Move these anchors with the block.
derrors = []
dcodes = []
def derr(code, msg, at=""):
    """Buffer a Phase D finding WITH its code, in-block.

    Deliberately self-contained: evals/tests/test_policy_boundary_phase_d.py
    slices this phase out between the derrors initialiser and the reporting
    branch and runs it standalone, so anything Phase D calls must be defined
    between those two lines. Diagnostics are built in the reporting block below,
    which is outside the slice.
    """
    dcodes.append((code, at))
    derrors.append(msg)

# Use the values Phase B IMPORTED, not only the statically-parsed ones: the
# template's simple `PHYSICAL_MODELS = BASE_MODELS + DERIVED_MODELS` expression
# is resolved statically too, but runtime imports remain authoritative when a
# closure uses a more dynamic declaration.
if "nxd_decisions" in set(PHYSICAL_MODELS):
    if "nxd_decisions" not in set(BASE_MODELS):
        derr("policy.decisions_not_base_model",
            "nxd_decisions is promised but is not in BASE_MODELS — it is being "
            "generated in the transform. A ledger built from a Python literal "
            "describes the code instead of driving it: editing a row changes "
            "nothing. Write data/nxd_decisions/nxd_decisions.csv and land it "
            "like any other base model (reference/derivation-plan.md).",
            "transform/main.py")
    else:
        led = Path("data/nxd_decisions/nxd_decisions.csv")
        if not led.exists():
            derr("policy.decisions_csv_missing",
                 "nxd_decisions is in BASE_MODELS but "
                 "data/nxd_decisions/nxd_decisions.csv is missing.",
                 "data/nxd_decisions/nxd_decisions.csv")
        else:
            import csv as _csv
            with led.open(newline="", encoding="utf-8", errors="replace") as fh:
                lrows = list(_csv.DictReader(fh))
            # status and provenance are checked INDEPENDENTLY: they are separate
            # axes, so a missing provenance column must not skip status
            # validation (or the reverse). Each column: present, then in-vocab.
            LEDGER_VOCAB = {
                "status": ({"confirmed", "proposed", "blocked"},
                           "status is how a user tells a settled ruling from an "
                           "open one"),
                "provenance": ({"user_confirmed", "agent_authored",
                                "source_derived", "deferred"},
                               "provenance is how a reviewer tells a value the "
                               "USER supplied from one the AGENT invented to "
                               "fill an underspecified rubric — status does not "
                               "carry that, it is a different axis"),
            }
            for lcol, (okvals, why) in LEDGER_VOCAB.items():
                if lrows and lcol not in lrows[0]:
                    derr("policy.decisions_column_missing",
                        f"nxd_decisions.csv has no {lcol!r} column (found "
                        f"{sorted(lrows[0])}). {why}. Allowed values are "
                        f"{sorted(okvals)} (reference/derivation-plan.md).",
                        f"data/nxd_decisions/nxd_decisions.csv:{lcol}")
                    continue
                badv = {(r[lcol] or "").strip() for r in lrows} - okvals
                if badv:
                    derr("policy.decisions_value_out_of_vocab",
                        f"nxd_decisions.{lcol} has {sorted(badv)}; allowed "
                        f"values are {sorted(okvals)}. The vocabulary is fixed: "
                        f"a value outside it is not queryable as a class.",
                        f"data/nxd_decisions/nxd_decisions.csv:{lcol}")

# (b) A policy value that is landed AND hardcoded is a divergence waiting to
# happen. Only scan CSVs whose model name looks like landed policy, and only
# compare distinctive values: short/common tokens ("1", "US", "PASS") collide by
# coincidence, and flagging those would make this unreliable.
POLICY_HINT = ("rubric", "weight", "threshold", "band", "anchor", "verdict",
               "scale", "policy", "gate", "criteri")
tsrc = Path("transform/main.py").read_text(encoding="utf-8", errors="replace")
tlits = set()
for node in ast.walk(ast.parse(tsrc)):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            tlits.add(node.value.strip())
        elif isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            tlits.add(str(node.value))
# A KEY column is how the transform looks a row UP, so naming it in code is
# correct and expected ("c1_backend_depth" as an output column). What must not
# be duplicated is the PARAMETER the row carries — the weight, threshold or
# verdict the lookup returns. Skip the first column and any *_id/key/name/
# criterion column; check the rest.
KEYISH = ("id", "key", "name", "criterion", "code", "slug", "label")
for pcsv in sorted(Path("data").rglob("*.csv")):
    if not any(h in pcsv.parent.name.lower() for h in POLICY_HINT):
        continue
    import csv as _csv
    with pcsv.open(newline="", encoding="utf-8", errors="replace") as fh:
        prows = list(_csv.DictReader(fh))
    if not prows:
        continue
    cols = list(prows[0])
    for col in cols[1:]:                       # first column is the key
        if any(k in col.lower() for k in KEYISH):
            continue
        for row in prows:
            v = (row[col] or "").strip()
            # Distinctive = long enough to be a real policy value, or a
            # decimal/multi-digit number. Single digits and 1-3 char codes
            # collide with array indices and unrelated strings.
            distinctive = len(v) >= 4 or (
                v.replace(".", "", 1).isdigit() and len(v) >= 2)
            if distinctive and v in tlits:
                derr("policy.literal_duplicates_landed_value",
                    f"{pcsv}: value {v!r} (column '{col}') is landed AND "
                    f"appears as a literal in transform/main.py. The "
                    f"transform must READ it from the row; a copy diverges "
                    f"from the row the user edits.", f"{pcsv}:{col}")

# === PHASE-D-END ===

if derrors:
    say("\nPHASE D FAILED — policy boundary (rulings are data, not code):")
    seen = set()
    for (dcode, dat), e in zip(dcodes, derrors):
        if e in seen:
            continue
        seen.add(e)
        say(f"  - {e}")
        diag("s3_closure", dcode, e, path=cpath(dat))
    close_stage("s3_closure", "failed", errors=len(seen))
    finish(1)
close_stage("s3_closure", "passed", scanned=len(scan))
say("phase D ok — rulings land as editable data with status + provenance, "
    "not transform literals")
# FROZEN STRING — do NOT "fix" the stale label. Phase C's MEANING changed with
# this design (it now verifies the byte-copied spec snapshot, the lock and the
# build record, not the completeness of a hand-written context document) but its
# LABEL stays "C (context-completeness)" because run.py's deterministic-check wiring
# and every scenario checker key on this line byte-for-byte. Accuracy of the
# label loses to stability of the contract; this comment is what keeps the
# trade visible instead of inviting a helpful rename that breaks every checker.
#
# Phase G is DELIBERATELY absent from the enumeration below, for the same
# reason: this string is keyed on byte-for-byte by run.py and every scenario
# checker, so adding a phase to it is a breaking change to a contract that has
# nothing to do with consent. Phase G reports its own verdict on its own
# `phase G ok` line, which is where its truth lives. The omission is a decision,
# not an oversight — do not "complete" the list.
say("SELF-CHECK OK — Phases A (structural), E (reach, pre-execution), "
    "B (transform dry-run), C (context-completeness), D (policy boundary) all "
    "passed. Phase E is import-level over transform/main.py plus a "
    "model-SDK scan of contracts/**/*.py: it does not make the transform "
    "offline (see 'What Phase E cannot see').")

# Distribution read-back. NOT a gate — it never fails the run. It prints the
# value counts of every classification-shaped string column of every derived
# model, so a fabricated gate or verdict is visible instead of hiding behind a
# green exit. Unconditional by design: deciding which columns "matter" is the
# judgement that would make this unreliable. A column qualifies on shape alone —
# it must actually GROUP (few distinct values, and fewer than one per row), which
# excludes keys and free text without naming either. Relay these counts to the
# user before the build (see nxd-run-job-loop Step 3). It is also the designated
# stage-8 predictor: a green build that answers wrongly shows up here first, so
# it is recorded as DATA in build-record.readback, not only printed.
for m in (sorted(set(PHYSICAL_MODELS) - set(BASE_MODELS) - absent_optional_models)
          if dry_run_runnable else ()):
    n_rows = con.execute(f"SELECT COUNT(*) FROM main.{m}").fetchone()[0]
    cols = [r[0] for r in con.execute(
        f"SELECT name FROM pragma_table_info('{m}') WHERE type = 'VARCHAR'").fetchall()
        if not r[0].startswith("_dlt_")]  # dlt bookkeeping, not model data
    for c in cols:
        rows = con.execute(f'SELECT "{c}", COUNT(*) FROM main.{m} '
                           f'GROUP BY 1 ORDER BY 2 DESC').fetchall()
        if not (len(rows) <= 12 and len(rows) * 2 <= n_rows):
            continue  # a key or free text, not a classification
        counts = ", ".join(f"{v!r}={n}" for v, n in rows)
        uniform = len(rows) == 1
        flag = "  <- UNIFORM: this column does not discriminate" if uniform else ""
        say(f"distribution {m}.{c}: {counts}{flag}")
        values = [{"value": v, "count": n} for v, n in rows]
        READBACK["distribution"].append(
            {"model": m, "column": c, "values": values, "uniform": uniform,
             "origin": "tool_computed"})
        # Evidence keys are the ones dp_diagnostics._merge_readback reads back
        # (model / column / values / uniform). Renaming one here silently
        # empties the build record's readback block.
        diag("s2_transform", "semantic.distribution",
             f"distribution {m}.{c}: {counts}",
             path=cpath(f"transform/main.py:{m}.{c}"),
             evidence={"model": m, "column": c, "values": values,
                       "uniform": uniform})
        if uniform:
            diag("s2_transform", "semantic.uniform_column",
                 f"{m}.{c} has one value across {n_rows} rows — a column built "
                 f"to distinguish rows that does not discriminate. A gate that "
                 f"passes every row is not a gate.",
                 path=cpath(f"transform/main.py:{m}.{c}"),
                 evidence={"model": m, "column": c, "values": values,
                           "uniform": True},
                 fix="State it to the user before the build. Do not silently "
                     "repair it: a gate that cannot fail is a ruling you "
                     "authored, so it lands in nxd_decisions or goes back as a "
                     "question.")

# Declared-but-absent read-back. The dual of UNIFORM, and equally unconditional:
# a vocabulary the closure LANDS (verdict labels, statuses, buckets) whose value
# never appears in any derived column is a branch that did not fire. Deciding
# whether it CANNOT fire needs reasoning and stays with you — this only reports
# what is mechanically true: declared, never produced. Vocabularies come from
# landed data, never from a transform literal, so this is independent of the
# code it is checking.
produced = set()
for m in (sorted(set(PHYSICAL_MODELS) - set(BASE_MODELS) - absent_optional_models)
          if dry_run_runnable else ()):
    for (c,) in con.execute(
            f"SELECT name FROM pragma_table_info('{m}') "
            f"WHERE type = 'VARCHAR'").fetchall():
        if c.startswith("_dlt_"):
            continue
        produced |= {str(v[0]).strip() for v in con.execute(
            f'SELECT DISTINCT "{c}" FROM main.{m}').fetchall() if v[0] is not None}
VOCAB_HINT = ("verdict", "bucket", "outcome", "band", "tier", "category")
for vcsv in sorted(Path("data").rglob("*.csv")):
    # nxd_decisions is the ledger ABOUT the policy, not an output vocabulary:
    # its own status values are meant to describe rulings, not to appear in a
    # scored row. Reporting them absent would be noise on every closure.
    if vcsv.parent.name == "nxd_decisions":
        continue
    import csv as _csv
    with vcsv.open(newline="", encoding="utf-8", errors="replace") as fh:
        vrows = list(_csv.DictReader(fh))
    for col in (vrows[0] if vrows else {}):
        if not any(h in col.lower() for h in VOCAB_HINT):
            continue
        declared = {(r[col] or "").strip() for r in vrows} - {""}
        missing = sorted(d for d in declared if d not in produced)
        if missing and len(declared) <= 12:
            say(f"ABSENT {vcsv.parent.name}.{col}: declared {missing} — "
                f"never produced in any derived column")
            READBACK["absent"].append(
                {"source": vcsv.parent.name, "column": col,
                 "declared_missing": missing, "origin": "tool_computed"})
            diag("s2_transform", "semantic.absent_vocabulary",
                 f"ABSENT {vcsv.parent.name}.{col}: declared {missing} — never "
                 f"produced in any derived column",
                 path=cpath(f"{vcsv}:{col}"),
                 evidence={"source": vcsv.parent.name, "column": col,
                           "declared_missing": missing})

finish(0)
