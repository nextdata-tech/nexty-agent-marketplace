# Mapper preflight and run-status reporting

## Contents

- [What this file is](#what-this-file-is)
- [Desktop preparation](#desktop-preparation)
- [Developer/CI reference only](#developerci-reference-only)
- [The preflight, in order](#the-preflight-in-order)
- [The preflight report](#the-preflight-report)
- [MAPPER RUN STATUS — the required format](#mapper-run-status--the-required-format)
- [Seven claims, never collapsed into one](#seven-claims-never-collapsed-into-one)
- [Redaction rules](#redaction-rules)

## What this file is

A mandatory non-spending setup check before a mapper build. Desktop uses the
supervisor tools below; the standalone checks and diagnostic formats later in
this file are developer/CI reference, not the Desktop conversation path.
Use [user-facing-language.md](../../nxd-run-job-loop/reference/user-facing-language.md)
for all narration. Keep findings, hashes and technical status blocks internal.
Agent-owned authoring prepares a proposal; source access is a separate
question; only the human's native spending confirmation grants consent.

## Desktop preparation

1. Call `get_workflow_capabilities` and check `mapper_preparation`, including
   its `tool`, `structural_mode`, `models` and `family_aliases`. If absent,
   say: "This setup step needs a Desktop update before I can continue."
   Never ask the user to run commands, compute hashes, inspect package source,
   or edit JSON. A missing document does not prove the runtime is unavailable.
2. Author the spec and a complete capped spending proposal through the normal
   authoring surface, using the agreed business scope. Use the `opus` family
   for an unspecified primary model; preserve an explicit model or family
   choice, including an existing grant's model. Call
   `prepare_mapper` with `definition` (the absolute host definition directory)
   and `workflow` (the workflow ID). It is read-only: `mapper_spec_id` is the
   installed canonical ID when available; `findings` use `code` and `detail`
   for static consistency/pricing corrections. Invalid input may omit the ID.
3. For `mapper/model_missing`, save the complete returned `proposed_spec` to
   `contracts/mapper_spec.json` through the normal authoring surface, then
   call `prepare_mapper` again. The tool is read-only and the proposal has no
   binding ID until this save and recheck. For `mapper/source_changed`,
   reprepare before saving. Do not ask the user to edit the model field.
   For `mapper/grant_id_missing`, write the returned `proposed_grant` to the
   existing proposal path (`contracts/mapper_grant.json`, or
   `contracts/mapper_spec_grant.json`; the spec must be
   `contracts/mapper_spec.json`) through the same authoring surface. The tool changes
   no files. If the grant is missing, author a complete proposal first.
   An incorrect supplied ID (`mapper/spec_mismatch`) is a finding, not license
   to restore an old consent. Resolve drift with the agreed plan and a fresh
   native approval. Never add approval claims or widen scope to clear a finding.
   `proposed_grant` is returned only for a complete, matching proposal missing
   its ID; a missing or invalid proposal is not synthesized.
4. Resolve all findings before capture or consent. In particular,
   `workflow/mapper_model_unpriced` covers unknown or unpriced exact models.
   Keep the user's explicit model choice when priced; an explicit concrete ID
   stays pinned only when priced. If an explicit primary or corroboration model
   is unknown or unpriced, offer options from the live catalog with both
   `input_micro_usd_per_mtok` and `output_micro_usd_per_mtok` present. Ask the
   user to choose before changing the model; never retry the unchanged unpriced
   ID or replace it automatically. If no priced options exist or the user
   declines them, stop with `workflow/mapper_model_unpriced`. Do not remove the
   mapper or fall back to an unpriced family. An unpriced family remains a
   finding, and corroboration still requires an exact installed ID.
   For an unspecified model, keep the `opus` family in the saved spec so the
   supervisor resolves its latest supported, priced concrete ID at native
   approval. A chosen `haiku`, `sonnet` or `fable` family follows the same
   resolution. Present the chosen model or family and a factual cost/scope
   tradeoff in the complete prepared-plan echo-back; its plan approval
   confirms that choice. Ask a separate model question only when material
   ambiguity blocks preparation. If preflight reveals a mismatch after plan
   approval, revise the complete spec and proposal and obtain the required
   fresh plan and spending approvals. Catalog rows carry `id`,
   `display_name`, `provider`, `input_micro_usd_per_mtok`,
   `output_micro_usd_per_mtok`, `price_unit` and `price_source`; name models
   to the user by `display_name`. Prices are micro-USD per million tokens, not
   proof of provider access, observed quality, or permission to spend. A primary
   family alias (`haiku`, `sonnet`, `opus`, `fable`) is reported as
   `model_resolution:"deferred_to_native_approval"` and resolves at native
   approval; corroboration models require exact installed IDs.
5. Call `prepare_mapper` again after each repair until `findings` is empty.
   Its `native_approval_required:true`, `run_admitted:false`,
   `live_validation:unverified` and `policy_status:not_examined` are boundaries,
   not approval evidence. Stop if a finding cannot be resolved safely.
   `answer_format_check.provider_check` is always `not_checked`; act on an
   `answer_format_check.advisory` (`mapper/answer_format_may_be_too_large`)
   before approval by splitting the target fields, per
   [field-mapper.md](field-mapper.md#authoring-the-spec-and-the-grant).
6. Call `check_data_product` with the same `definition`, `workflow`, and
   `mode:"structural"` before asking for spending approval. Require
   `structural_status:"pass"`; a skipped check is not a pass. This does not
   execute authored Python or scoring, compile the full definition, probe
   imports, or validate transform/runtime/contract/semantic behavior.
   `check_scope:"non_executing_structure"`, `full_compile_status:"not_checked"`
   and `build_eligible:false` remain true even on a pass. Say:
   "The setup checks passed; scoring has not run."
   For a setup without a mapper, say: "The setup checks passed; nothing has
   been run yet." `mode` defaults to `"full"` when omitted.
7. Before asking for spending approval, tell the user in plain words which
   source fields' content is sent to the model. Only the mapper input's text
   (`landed_text`) and the instruction reach the model. The fields sent are
   each input's `text_fields`: check that every `MapperInput` sets them and
   that the grant's non-empty `input_fields` lists each one, or the run is
   refused before any model call. Include long free-text fields such as job
   descriptions. See
   [disclosing what is sent](field-mapper.md#disclosing-what-is-sent-to-the-model).
8. Continue normal capture, retained review, native spending approval,
   validation and admission. None of these checks replaces a gate or changes
   budgets or credential handling. The provider key stays in the supervisor's
   secure store, never in a closure or child environment.

Preparation corrections include `workflow/invalid_request`,
`mapper/preparation_input_invalid`, `mapper/spec_invalid`, `mapper/model_missing`,
`mapper/grant_missing`, `mapper/grant_invalid`, `mapper/grant_expired`,
`mapper/model_mismatch`, `mapper/corroboration_mismatch`, and
`workflow/mapper_approval_claims_rejected` (approval or execution claims).
`mapper/runtime_unavailable` needs the Desktop installation restored;
`mapper/source_changed` needs a fresh check after files are saved. Preparation
reports `status:"ready_for_native_approval"` only when its findings are empty;
this does not establish policy eligibility or consent.

Structural mode reports `structural_status:"findings"` for failures and
`"skip"` when checks are unavailable without another failure. File corrections
use `structure/source_missing`, `structure/python_syntax_invalid`,
`structure/source_unsafe`, `structure/source_too_large`,
`structure/source_name_invalid`, and `structure/source_changed`.
`structure/checker_unavailable` has `status:"skip"`; restore the installed
checker before continuing. Mapper corrections use the preparation codes above.
The result includes `stages` and `findings` with `code`, `detail`, and `status`,
and always includes skipped `structure/full_compile_not_checked`,
`runtime/not_reached`, `contract/not_reached`, and `semantic/not_reached` checks.
`runtime_status`, `contract_status`, and `semantic_status` are `"not_reached"`.
When present, `source_provenance` hashes selected authored content and records
its stability during checking; it is not a build `definition_id`.

## Developer/CI reference only

The following standalone preflight and report formats are not instructions to
run local commands in the Desktop conversation or to show technical blocks to
the user. They never establish Desktop authorization. Record the seven evidence
claims separately internally and narrate only supported business outcomes.

## The preflight, in order

Run all of them and report every result — do not stop at the first failure. One
run that names three problems is worth more than three builds that each name
one.

| # | Check | How | Failure means |
|---|---|---|---|
| 1 | mapper import available | `python -c "import nxd.experimental.field_mapper"` | the `nxd` package is missing or too old |
| 2 | anthropic SDK importable | `python -c "import anthropic"` | runtime under-provisioned — `nxd-desktop-setup.sh --force` |
| 3 | API key present in the child environment | test the variable is set and non-empty — **never print it** | execution reachability for the standalone harness only, not Desktop authorization. Under Desktop supervision the key is never in the child environment: the supervisor keeps it in the Keychain and brokers every call. See [field-mapper.md](field-mapper.md#desktop-supervisor-approval-boundary) |
| 4 | mapper spec parses | `MapperSpec.load(path)` | malformed spec — fix before anything else |
| 5 | canonical spec id computed | `python -m nxd.experimental.field_mapper spec-id <spec>` | the id the grant must carry |
| 6 | grant exists | the grant file is present in `contracts/` | standalone consent must be user-authored; Desktop proposals are agent-owned |
| 7 | grant binds to that spec id | `python -m nxd.experimental.field_mapper grant-check <spec> <grant>` | spec drifted since consent — **ask again** |
| 8 | input fields + document classes covered | `grant-check` reports both; Desktop also needs a non-empty `input_fields` | the grant does not authorize this data |
| 9 | generated transform compiles | `compile()` / AST parse of the transform | syntax error — offline, deterministic |
| 10 | `MapperInput` construction compatible | construct one from a real row with `text_fields` set, and confirm `render_landed_text(fields, text_fields)` reproduces its `landed_text` | catches the `document_id=` class of defect, and the hand-joined text that fails at `map_inputs` with `landed_text_undeclared` |
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
author an authorized standalone grant, extend an expiry, or rewrite the spec so an existing grant
starts binding again. In Desktop they do not admit a build: grant/request files
remain untrusted scope proposals until the supervisor has obtained protected
human confirmation.

## The preflight report

For developer/CI diagnostics only, emit one structured block. Same key order every run, so two runs diff cleanly:

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

For developer/CI diagnostics only, record this **before** a build attempt (execution fields as `not reached`) and
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
- Spec ids and grant ids may appear in internal diagnostics, not default user narration.
- Do not echo source document text into a status report. The spec id, the input
  count, and the stage are enough to diagnose, and landed text may be customer
  data.
