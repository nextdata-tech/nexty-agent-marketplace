# Local desktop inference handoff

The local end-to-end flow uses this skill only to profile sources and infer
public semantic roles. Write `schema.json` and `semantic-model-plan.json` beside
the owning job loop's `dp-blueprint.md`, never under `closure/`. The plan is
data, not Python: record models, physical columns, grains, dimensions, metrics,
joins, descriptions, PII flags, source labels, and any `gap_found`.

Stop after returning those two inference artifacts. Do not create or edit
`models.py`, `spec.py`, `transform/`, `requirements.txt`, or another closure
file, and do not invoke `nxd-generate-data-product`. A description omitted from
the plan cannot be recovered by the generator, so include one for every model,
dimension, and metric; use the public role grammar without emitting private
metadata.

The owning job loop must prepare the exact blueprint and relay a successful
supervisor `session_decision` before invoking the generator. The generator then
translates this handoff to the public DSL and owns every executable closure
artifact.
