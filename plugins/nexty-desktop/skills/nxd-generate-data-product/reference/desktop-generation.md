# Current Desktop generation

Use this route when workflow-v2 execution is available and the exact prepared
blueprint/proposal has received complete-plan consent. It selects authoring
recipes; it does not bypass native spending approval, cumulative budgets,
capture, retained review, validation, admission or the returned run actions.
Typed v3 is the current authoring schema; it is not a legacy control-plane route.

## Keep the settled input settled

Reuse the exact unchanged prepared and consented blueprint/proposal and inferred
model. The v3-capable `validate_dp_spec.py` remains supported; only this exact
unchanged input permits skipping duplicate standalone validation. A new or
changed plan, semantic drift or plan-invalidating error requires current
validation, fresh prepare and fresh scoped consent. A mechanical closure error
uses the existing bounded correction/check route without inventing a new policy
or changing scope. Never re-derive approved business logic during generation.

Resolve one host-visible closure root and the handed-off `job_helper_dir`.
Select references once from the source type, mapper use and exact approved
contract inventory. Keep the resolved recipes in context; re-read only for new
evidence, a concrete error, changed scope or relevant version drift. The pinned
DSL reference's compatibility caveat still applies: check only the affected
signature when needed, never treat a different installed version as verified.

## Enforce requested rules from the first plan approval

The first-approval rule lives in the job loop's echo-back guidance
([constraints attached to approval](../../nxd-run-job-loop/reference/dp-blueprint.md#interpretation-and-approval)),
because this route applies only after plan consent. Do not add a requested rule
as an enforced check here without fresh consent; a change to the plan always
needs it.

When re-approval is genuinely required, say plainly what changed. If the only
change is turning a rule the user already requested into an automatically
enforced check, you may say:

> This is the same rule you asked for, now checked automatically. Because the
> saved plan changed, I need you to confirm the updated plan again.

Add "Desktop will also ask again before anything is spent" only when the plan
includes a field mapper; a plan without one has no spending step.

If anything else changed (what is sent to the model, the model, the limits or
the instruction), do not use that wording. Name each change in plain words
before asking.

## Author in dependency order

| When applicable | Recipe and action |
|---|---|
| Every closure | Use the inferred model and [DSL surface](nxd-spec-api.md); preserve names, approved descriptions and physical/semantic identities. |
| REST API source | Use [API source](api-source.md) for probe, response shape, pagination, profile and ingest; CSV file/profile instructions are conditional examples. No dummy CSV export, source path or endpoint sidecar. |
| Database or file source | Use its selected recipe from [source types](source-types.md); do not read unrelated connector recipes. |
| Approved mapper | Use [Desktop preparation](mapper-preflight.md#desktop-preparation), [authoring](field-mapper.md#authoring-the-spec-and-the-grant) and [two dlt runs](field-mapper.md#two-dlt-runs-in-this-order). Developer/CI hash and paid dry-run recipes are not Desktop setup steps. |
| Approved custom contracts | Use [custom contracts](custom-contracts.md) for exactly the declared verifier/wiring inventory; an empty inventory has no placeholder. |
| Approved Decisions | Apply [decision-ledger projection](pre-capture-audit.md#decision-ledger-projection) once per Decision from the current prepared and consented blueprint, including explicit user-approved review rulings. Do not invent Decisions from session facts or unapproved review claims. |

For an API source with a Desktop-local connection, after plan consent author
the public topology service and request the native masked entry. The direct
`connectivity_check.py` probe defers credentialed execution for that reference;
admitted validation performs the first authoritative fetch. Keep the approved
response shape provisional until the admitted fetch supplies evidence. For a
legacy literal API source, use the existing probe-first order: inspect every
configured resource successfully before dependent authoring, and stop on a
failed probe.

The existing missing-credential exception permits structural authoring only:
payload inspection and connectivity are **not run** and **unverified**; the
closure stays incomplete. It is not source-validation proof, an intake
workaround or a completed happy path. Never invent access or source rows.

Unsupported API input-expectation phases stop the current approved inventory.
Never omit a contract, silently move its phase/guarantee or offer a CSV source
substitute. A phase/guarantee change requires an explicit recorded scope decision
and fresh prepare/consent; an output promise is not mechanically equivalent to
a pre-transform expectation. Preserve the requested live-source behavior.

## Check once per correction round, then hand off

Run the [pre-capture audit](pre-capture-audit.md) for the actual approved plan.
Keep source/derived assertions, privacy rules and the mapper's load, read, map,
gate and land ordering. Retain requested saved queries and fresh admitted fetch/map
behavior; reopening or querying published data is not a refresh.
Call `prepare_mapper` where applicable and resolve findings without spending,
then `check_data_product` (`mode:"structural"` for a mapper). Batch same-round
mechanical corrections and repeat checks only for changes or unresolved findings.

Return executable inputs through the current workflow capture action. Capture
owns snapshots, lock, build record and trusted checker; do not author those
reserved surfaces or modify retained state. Developer self-check/lock recipes
are outside this route and cannot execute a paid mapper outside admission.
Follow retained review and all native/budget/validation/admission gates before
`start_run`; local checks never substitute for them.

The supervisor compiler converts already-authored Python into definition YAML;
the proposal materializer creates capture-owned evidence. Neither generates
the complete executable API/mapper closure. The semantic scaffold targets
Snowflake/Kubernetes and is not a Desktop replacement. This route reduces
duplicate guidance work by design; no elapsed-time improvement is established.
