# Pre-capture audit

Run this deterministic audit after authoring the executable closure and before
calling the supervisor's `capture` action. Resolve every item from the approved
typed proposal and the closure; do not infer approval from a review finding.

On workflow-v2, verify every typed-v3 Decision is covered by the approval
echo-back. A typed `proposed` Decision is a valid consent candidate; after the
subject-bound `session_decision`, the trusted materializer projects it to
`locked` in the approved snapshot. Do not change the proposal after consent.

## Model, output, and physical-surface checks

- Classify every blueprint `Model` separately from every explicit blueprint
  `Output`. Only an explicit `Output` is a user-facing projection, question,
  or delivery channel.
- Distinguish a blueprint Output/custom promise from the ordinary DSL
  `.promise(model)`. The DSL method declares required physical materialization;
  it does not declare that a model is a blueprint Output or make it public.
- Treat `.promise(...)`, `.model(...)`, and semantic roles as non-privacy
  boundaries on desktop. Landed relations share the DuckDB output, so changing
  one of those declarations cannot hide a physical table from direct access.
- When a sensitive field is forbidden from every physical surface, remove it
  before yielding any resource or avoid landing the relation. Do not try to
  enforce that requirement by toggling `.promise()` / `.model()` or removing a
  semantic role.
- Treat every generated description as approved content, not free-form
  documentation: model, field, metric, and custom-contract descriptions must
  be copied from the relevant approved blueprint section. A useful paraphrase
  is still an unapproved closure value and can fail the supervisor's
  `struct.description_unreachable` check; amend and re-approve the blueprint if
  the wording itself needs to change.
- Compare the closure README with the actual mechanism: describe explicit
  Outputs, physical support relations, the DuckDB surface, and privacy limits
  as they are implemented. Do not preserve a claim that the code cannot enforce.

## Source-derived semantic checks

For every derived model that reads two or more sources, or whose approved
Output names coverage or a ratio metric:

- Confirm that the transform validates each source schema before matching. For
  CSV/file sources, the approved field names must appear in the source contract
  and `expected_columns` must be used when the header set is exact; for
  API/database sources, apply the equivalent check to the complete captured
  response rows. A source column guessed from a similar name is not a match.
  Missing, duplicate, or exact-contract mismatches must raise before any
  derived resource is built.
- Confirm that every requested source-side coverage value is a separate landed
  row with the complete source row count, matched count, basis-point rate, and
  unmatched identity list. An aggregate match count is not evidence for both
  source sides.
- Confirm that aggregate ratios use separate additive numerator and denominator metrics and are computed from those query results at the approved grain. A row-level ratio (such as per-campaign CPA) must never be exposed as a metric with `Agg.AVG` or any other reduction, including `SUM`, `MIN`, or `MAX`, and a ratio column must never be summed to produce an aggregate ratio. The allowed row-grain alternative is a derived ratio column validated with `assert_row_ratios`; it must remain at that row grain and must not be reduced. Use the fail-closed recipe in `reference/derived-models.md` and inspect the actual assertions, not only the model column names.
- Run the transform assertions against the complete supplied sources before
  capture. A retained review may find a semantic defect, but it does not make a
  missing source contract or a failed assertion safe to publish.

## Decision-ledger projection

Build `nxd_decisions.csv` with one ledger row per approved typed Decision.
“Row for row” means this one-to-one projection, not a byte-for-byte copy of
typed-v3 fields. Apply this mechanical status projection:

For the workflow-v2 path, the approved snapshot contains locked Decisions after
the supervisor's post-consent projection, so its projected ledger status is
`confirmed`. The `proposed` row below remains the valid projection for an
exploratory or pre-approval closure; it is not the approved workflow snapshot.

| Typed-v3 decision status | Ledger `status` |
|---|---|
| `locked` | `confirmed` |
| `proposed` | `proposed` |

Use ledger `blocked` only for a deliberately deferred ruling that materializes
nothing, and pair it with `provenance = deferred`. Do not turn a typed
`proposed` decision into `blocked` merely because a review found a problem.

Set `provenance` from authorship, not lifecycle or review vocabulary:

- `user_confirmed`: the value was explicitly supplied by the user.
- `agent_authored`: the agent supplied an otherwise unspecified value or
  anchor; user approval changes `status`, never this provenance.
- `source_derived`: the value was read from source data.
- `deferred`: the step was deliberately not taken and nothing was authored.

Use only `confirmed`, `proposed`, and `blocked` for ledger `status`, and only
`user_confirmed`, `agent_authored`, `source_derived`, and `deferred` for ledger
`provenance`. Never write `locked`, `approved`, `settled`,
`operator_accepted`, or `user_approved` to `nxd_decisions`.

Keep review findings and adjudications in the external
`review-record.json`. Do not add session-only decisions or convert review
findings into new ledger rows. A behavior-changing finding requires a
blueprint amendment and fresh user reapproval before adding or changing a
Decision. Preserve the approved Decision identity and binding while applying
the mechanical status projection above.
