# When questions require inference

How the loop handles a question that needs a **judgement produced by reading
each entity's evidence** — a per-entity score, verdict, or classification the
source does not state. The landed *shape* (row schema, evidence citation, batch
convention) is owned by nxd-generate-data-product's `reference/llm-judgments.md`; this file
owns *which lane judges, and when* inside the loop.

## Contents

- [The rule: land judgement as data](#the-rule-land-judgement-as-data)
- [Two lanes: exploring vs packaged](#two-lanes-exploring-vs-packaged)
- [Teach before you judge](#teach-before-you-judge)
- [Judge in a lane, never in a query answer](#judge-in-a-lane-never-in-a-query-answer)
- [Judge incrementally](#judge-incrementally)

## The rule: land judgement as data

Some questions need a judgement produced by reading each entity's evidence —
score candidates against a rubric, assign tickets a verdict, classify rows on a
dimension the data doesn't carry. That is real analytical value. Route it the
same way you route any ruling: **land it as data**, `status = proposed`, evidence
cited, against a rubric that is itself landed data.
nxd-generate-data-product's `reference/llm-judgments.md` has the row schema.

## Two lanes: exploring vs packaged

**Where the judging runs depends on whether the product is packaged**, and the
two cases pull in genuinely different directions.

While the user is **exploring** — trying criteria, reshaping the rubric, deciding
whether the product is worth having — judge **agent-side**, in your own session,
and write the rows as CSV into the closure's export before the build. It costs
nothing, needs no consent grant, no approval interaction, and no model spend, and
the whole thing can be thrown away when the criteria change an hour later.

Once the product is **packaged and built** — it ships, it is handed off, or it
will be rebuilt later — the judging moves **inside the transform**, through the
field-mapper seam (`nxd.experimental.field_mapper`, called via `make_call`, under
a consent grant). See nxd-generate-data-product's `reference/field-mapper.md`.

The reason is self-containment, correctly understood. **Self-containment is a
property of the logic, not of the values.** A closure whose scores were produced
in an earlier agent session is not self-contained however complete its file list
looks: the prompt, the reading of the evidence and the model identity all lived
outside it, and what ships is a frozen output nobody who receives the closure can
re-derive from the closure. Bundling the procedure and accepting that a rerun may
score a borderline entity differently is the better trade — the artifact carries
everything needed to run its own logic, and the movement in the values is
**disclosed rather than prevented** (`rubric_version`, `judged_by`,
`status = proposed` are what keep a moving score attributable).

Two things the seam settles that a hand-rolled model call does not:

- **The model credential never enters the data product.** It is resolved outside
  the artifact — an explicit secrets mapping, or the opt-in allowlisted
  `ANTHROPIC_API_KEY` fallback — never in `infra-profile.yaml`, never in
  `dp-blueprint.md`, never in chat. In a Desktop build the user authorizes the
  subject through the supervisor-owned loopback review surface plus native OS
  presence decision. The MCP peer does not receive the one-time capability, and
  no agent-authored field can forge the supervisor's subject or admission.
- **A direct provider SDK import stays denied.** `import anthropic` in
  `transform/main.py` fails the reach gate, and should: it routes around the
  grant check, the approval boundary, and the sanitized credential handling.

Do not silently switch lanes. Moving a product from exploring to packaged changes
what a build costs (real model spend, an approval interaction) and what a rerun
guarantees — say so when it happens.

## Teach before you judge

A judgement is only checkable against a rubric that already exists as data. A
score of "4" means nothing until "out of the landed 1–5" is landed. Elicit or
confirm the rubric — criteria, weights, scale, verdict enum, gates — and have
nxd-generate-data-product land it (and an `nxd_decisions` row, classified
`provenance = user_confirmed` where the user supplied the rubric and
`agent_authored` for any anchor or band you filled in yourself) FIRST,
before any entity is judged.

## Judge in a lane, never in a query answer

In the exploration lane, read the entity facts and write the judgement rows as
CSV — `status = proposed`, each row carrying a verbatim evidence citation
(`evidence_field` + `evidence_quote`, or `not stated`). In the packaged lane the
seam produces the same shaped rows during the run.

What is banned in **both** lanes: scoring in a `run_semantic_query` answer, in a
scratch table, or as a transform constant. A per-entity judgement literal in
transform code is hardcoded even when the arithmetic around it is computed — the
same fabrication the loop exists to prevent, and no less so for being weighted.

## Judge incrementally

For a re-run or a handoff, do not re-score the whole population. Score only
`source keys − already-judged keys` (for the current `rubric_version`), append
those rows as a **new** `batch-00N.csv` (never edit an existing batch — dlt
replace-loads every batch each build, so a new file is a legitimate input change,
not drift), and rebuild through MCP with the **same** `workflow` id.

A rubric change is a new `rubric_version`, landed explicitly — never an in-place
edit that silently invalidates existing judgements.

Incremental judging is an **exploration-lane** discipline: it exists because the
agent is the one doing the scoring and re-scoring a whole population by hand is
wasteful. A packaged closure judges what its run covers, so the incremental
question there is about spend and thresholds, not about which keys you skipped.
In the exploration lane, if no live agent is available to judge new entities on an
unattended rerun, the product degrades honestly: unjudged entities land in an
explicit unscored bucket and are reported, never faked.
