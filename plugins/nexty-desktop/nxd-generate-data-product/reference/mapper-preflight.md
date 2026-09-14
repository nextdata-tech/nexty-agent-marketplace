# Mapper preflight and run-status reporting

## Contents

- [What this file is](#what-this-file-is)
- [The preflight, in order](#the-preflight-in-order)
- [The preflight report](#the-preflight-report)
- [MAPPER RUN STATUS — the required format](#mapper-run-status--the-required-format)
- [Seven claims, never collapsed into one](#seven-claims-never-collapsed-into-one)
- [Redaction rules](#redaction-rules)

## What this file is

A **mandatory** check before any mapper build, and a **fixed report format** for
before and after it. Both exist because of one incident: a mapper build was
reported as progressing when it had in fact stopped before any model was
contacted, and the two things that stopped it — a missing SDK and a missing API
key — were each discoverable in under a second, before spending anything.

The preflight is entirely offline. **It must not call the model.** Its whole
value is that it fails free: every check below is cheaper than the build it
guards, and each one maps to a failure actually observed in the field.

## The preflight, in order

Run all of them and report every result — do not stop at the first failure. One
run that names three problems is worth more than three builds that each name
one.

| # | Check | How | Failure means |
|---|---|---|---|
| 1 | mapper import available | `python -c "import nxd.experimental.field_mapper"` | the `nxd` package is missing or too old |
| 2 | anthropic SDK importable | `python -c "import anthropic"` | runtime under-provisioned — `nxd-desktop-setup.sh --force` |
| 3 | API key present in the child environment | test the variable is set and non-empty — **never print it** | execution reachability only, not Desktop authorization or credential isolation; see [field-mapper.md](field-mapper.md#desktop-supervisor-approval-boundary) |
| 4 | mapper spec parses | `MapperSpec.load(path)` | malformed spec — fix before anything else |
| 5 | canonical spec id computed | `python -m nxd.experimental.field_mapper spec-id <spec>` | the id the grant must carry |
| 6 | grant exists | the grant file is present in `contracts/` | the user must author one; you cannot |
| 7 | grant binds to that spec id | `python -m nxd.experimental.field_mapper grant-check <spec> <grant>` | spec drifted since consent — **ask again** |
| 8 | input fields + document classes covered | `grant-check` reports both | the grant does not authorize this data |
| 9 | generated transform compiles | `compile()` / AST parse of the transform | syntax error — offline, deterministic |
| 10 | `MapperInput` construction compatible | construct one from a real row | catches the `document_id=` class of defect |
| 11 | `map_inputs` invocation compatible | check the call site names `spec`, `grant`, `run_dir`, `call` | catches a call written against a stale contract |
| 12 | ceilings available | `estimate(...)` vs the grant's `max_calls` / `max_tokens` / `max_usd` | an unbounded run is refused at the boundary |

Checks 10 and 11 are the ones the incident needed and nothing had. Verify them
by **construction and signature**, exactly as
[mapper/CONTRACT.md](../mapper/CONTRACT.md) specifies them —
never by introspecting the installed signature and adapting the generated code
to match. Adapting turns a loud `TypeError` into a silent behavioural difference
between two runtimes, which is strictly worse than failing.

Checks 5–8 are the standalone harness's consent checks. A failure there is
**always** a blocker for the user, never something to route around: you cannot
author a grant, extend an expiry, or rewrite the spec so an existing grant
starts binding again. In Desktop they do not admit a build: grant/request files
remain untrusted scope proposals until the supervisor has obtained protected
human confirmation.

## The preflight report

Emit one structured block. Same key order every run, so two runs diff cleanly:

```json
{
  "mapper_preflight": {
    "mapper_import": "PASS",
    "anthropic_sdk": "PASS",
    "anthropic_sdk_version": "0.117.1",
    "api_key_present": true,
    "spec_parses": "PASS",
    "mapper_spec_id": "3d4adc20449936d74fd57d18306743dd",
    "grant_present": "PASS",
    "grant_binds_spec": "PASS",
    "input_fields_covered": "PASS",
    "document_classes_covered": "PASS",
    "transform_compiles": "PASS",
    "mapper_input_construction": "PASS",
    "map_inputs_signature": "PASS",
    "estimated_calls": 2,
    "estimated_tokens": 4100,
    "estimated_usd": 0.01,
    "within_grant_ceilings": true,
    "model_called": false
  }
}
```

`api_key_present` is a **boolean**, and `model_called` is always `false` in a
preflight — if it is ever `true`, this was not a preflight. `api_key_present`
never proves Desktop authorization, human consent, or credential isolation.

## MAPPER RUN STATUS — the required format

Report this **before** a build attempt (execution fields as `not reached`) and
again **after** it. Verbatim structure, every time:

```
MAPPER RUN STATUS

Prerequisites:
- SDK: PASS/FAIL
- API key visible to MCP child: PASS/FAIL (execution reachability only)
- spec: PASS/FAIL
- grant: PASS/FAIL (standalone harness binding only)
- Desktop supervisor admission: `unknown` unless the supervisor returned a structured outcome; otherwise `PASS`, `FAIL`, or `unsupported` verbatim (only the supervisor may report `PASS`)
- generated transform: PASS/FAIL

Execution:
- stage reached: one of s0_spec, s1_structure, s2_transform, s3_closure,
  s4_pin, s5_serve, s6_run, s7_publish, s8_answer
- model dispatch: not reached/reached
- model calls: exact count if known
- spend/tokens: exact redacted accounting if available
- publication: yes/no
- query verification: yes/no/not run

Conclusion:
- confirmed root cause
- user action required, if any
- agent action required
- next safe action
- whether retrying unchanged would help
```

The stage names are the ladder in
[nxd-run-job-loop/reference/failure-handling.md](../../nxd-run-job-loop/reference/failure-handling.md).
Use them literally in this block. State an exact count or say "unknown" —
never estimate a call count or a spend figure that was not measured, since the
whole point of the line is that the user can trust it.

**`whether retrying unchanged would help` is answered `no` for every
deterministic failure** — a wrong call shape, a missing dependency, a
non-binding grant, or anything that died in `s0_spec` through `s3_closure`.

## Seven claims, never collapsed into one

These are seven separate facts. Report each, and never let an earlier one imply
a later one:

1. **self-check** — the offline checks passed.
2. **build dispatch** — the build was accepted and started.
3. **transform execution** — the transform actually ran.
4. **model invocation** — the model was contacted (with a call count).
5. **publication** — derived rows were landed.
6. **catalog/model verification** — the published models are visible.
7. **semantic query result** — a real query returned a real answer.

A green consent gate is claim 1. A dispatched build is claim 2. **Neither is a
successful mapper run**, and describing either as one is the reporting failure
this file exists to prevent. Desktop supervisor admission is a separate,
earlier boundary: a green Phase G or present API key is never a human
authorization. In the incident, claims 1–3 held and claim 4 never happened: the
transform started and died constructing its first `MapperInput`. The honest
report is "failed at s2_transform, model never contacted, nothing published" —
not "the mapper run failed", which invites the reader to assume spend occurred.

## Redaction rules

- **Never print the API key**, in any form — not a prefix, not a suffix, not a
  length, not a hash. Report presence as a boolean and nothing more.
- Report the **SDK version**, which is diagnostic and not sensitive.
- Spec ids and grant ids are hashes of declared policy, and are safe to print.
- Do not echo source document text into a status report. The spec id, the input
  count, and the stage are enough to diagnose, and landed text may be customer
  data.
