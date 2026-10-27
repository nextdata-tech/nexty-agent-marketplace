# `dp-blueprint.md` — the user-owned plan between intent and a local data product

## Contents

- [Authoring contract](#authoring-contract)
- [Frontmatter](#frontmatter)
- [Sections](#sections)
- [Conventions every plan states](#conventions-every-plan-states)
- [Where an Input expectation can actually run](#where-an-input-expectation-can-actually-run)
- [Procedures are landed, not described](#procedures-are-landed-not-described)
- [AI steps](#ai-steps)
- [Terms](#terms)
- [A Term relative to the clock must name its anchor](#a-term-relative-to-the-clock-must-name-its-anchor)
- [Interpretation and approval](#interpretation-and-approval)
- [Sharing the document](#sharing-the-document)
- [Porting a blueprint to a new workflow](#porting-a-blueprint-to-a-new-workflow)
- [Claude Desktop form contract](#claude-desktop-form-contract)
- [Worked example](#worked-example)

`dp-blueprint.md` is the document the user writes and edits. It is ordinary
Markdown with fixed top-level navigation and free prose inside each section.
The user is never expected to author or inspect the terse typed proposal used
by the compiler.

## Authoring contract

- Claude Desktop pre-fills the document from the request and source material;
  the user reviews and edits that prose rather than completing an empty schema.
- The only fixed syntax is the frontmatter and the ordered `##` headings below.
  Prose, lists, tables, examples, and code blocks are allowed inside sections.
- The authoring parser records headings, section text, and source spans. It does
  not interpret business meaning and it does not reject natural-language
  colons or list-shaped prose as pseudo-YAML.
- An external AI extraction step produces a typed proposal for deterministic
  validation. That proposal is an internal artifact, not a user-facing file.
- Missing information becomes an **Open Question**. The AI must not invent a
  behavior merely to satisfy a typed field.
- `status: approved` means the user approved the natural-language
  interpretation shown in the echo-back. Approval binds both the source hash
  and the exact typed proposal hash.
- Version 1 is unsupported. Version 3 is the prose-first authoring contract;
  the older v2 parser is retained only for read-only verification of existing
  v2 closure evidence while it remains present.

## Frontmatter

```yaml
---
dp_spec_version: 3
name: monthly_revenue
workflow: monthly-revenue
status: proposed
---
```

The required lifecycle fields are `dp_spec_version`, `name`, `workflow`, and
`status`. An approved document also carries `approved_content_hash` and
`approved_proposal_hash`. When an approved document is edited, the resulting
`status: proposed` document retains `prior_approved_proposal_hash`; the next
approval must supply that prior typed snapshot so locked Decisions cannot be
silently replaced.

`status`, `approved_content_hash`, `approved_proposal_hash` and
`prior_approved_proposal_hash` are excluded from the source semantic hash.
`dp_spec_version`, `name` and `workflow` **are** hashed: identity is part of the
plan, not metadata about it. Retargeting a blueprint to a new `workflow` id
therefore changes the content hash by design and requires a fresh approval —
that is the expected porting path, not a defect. See
[Porting a blueprint to a new workflow](#porting-a-blueprint-to-a-new-workflow).

## Sections

The document has exactly these top-level headings, in this order:

1. **Intent** — the outcome, audience, and desired use.
2. **Questions** — the natural-language questions the product should answer.
3. **Scope** — inclusions, exclusions, grain boundaries, and assumptions.
4. **Terms** — definitions used by this document and the product.
5. **Inputs** — source material and expectations about accepted data.
6. **Models** — named relations or concepts needed by the result.
7. **Transform** — business logic, joins, aggregation, procedures, and model
   orchestration. Decisions belong here when they affect computation.
8. **Outputs** — user-visible products and promises about their behavior.
9. **Decisions** — rationale and provenance for explicit decisions that can be
   locked after approval.
10. **Open Questions** — unresolved questions, their target, and whether they
    block approval or materialization.

### Conventions every plan states

Every plan states these conventions as explicit defaults, the first plan
included, not only a revised or successor one. Put them in Transform (or in
Scope when they bound the question), and ask the user when a choice changes the
reported numbers and the source does not settle it:

- whether rows marked deleted, inactive, or cancelled are excluded or kept, and
  whether an exclusion drops the row from every count and total or only from some;
- whether categorical values match exactly or after normalising case and whitespace;
- the timezone assumed for naive timestamps;
- the tie-break when a version or recency field is missing or tied between rows;
- how an unparseable or null measure is treated in counts and in totals (skipped,
  counted as zero, or kept as null);
- whether totals and other aggregates span every categorical state, including
  closed or terminal ones, or only some of them;
- which source fields the connector fetches (state it under Inputs), versus which
  the output keeps (its Output projection);
- how identifiers are normalised (trimmed, coerced from numbers) and what happens
  to a record with no usable identifier;
- how a non-text value in a categorical or status field is treated;
- the rounding or precision of each landed measure and timestamp.
- for a product that refreshes: what a refresh with no new source rows does (it
  must keep every table's row count, so no new version or snapshot row), and,
  when a per-run cap or time limit can leave rows pending, when those rows are
  processed. Settle both per [incremental-transforms.md § Refresh replay](../../nxd-generate-data-product/reference/incremental-transforms.md#refresh-replay-settle-backlog-and-history-in-the-plan)
  before the first prepare; validation refuses a design that changes row
  counts on an unchanged re-run.

Review blocks on unwritten conventions, and each block costs a reset and a fresh
approval. A review that names conventions one round at a time can use up every
re-approval before validation ever runs. So, before the first capture of every
plan:

1. **Sweep the transform you wrote.** Read it top to bottom and list every
   choice the source does not settle: each normalisation, coercion, rounding,
   truncation, default, fallback, tie-break and exclusion, beyond the list above.
2. **Record each one as a Decision** marked as authored by you, not by the user,
   so it lands as a row in the decisions table. Plan prose alone does not count:
   review grades a convention as disclosed only when it is a landed row.
3. **When a review names a convention you missed,** sweep the whole transform
   again and record every missed one in the same revision, not only the one
   named.
4. **Answer each review finding with the smallest disclosing change:** a Decision
   row, or a promise reworded so it claims no more than its check does. Never add
   a model, relation or promise the user did not ask for to settle a finding.
   Each addition is new surface for the next review.
5. **Check every claim the plan makes against the code you wrote,** in the same
   pass and before the first capture, because each overclaim the review finds costs
   a fresh approval. Three kinds recur:
   - **What is fetched:** say "received, then dropped before anything is stored"
     unless the request itself asks the source for fewer fields.
   - **Who decided it:** mark each Decision as the user's only when the user said
     it in their own words, and as yours (adopted by approval) otherwise.
   - **What a promise checks:** word each promise exactly as its check behaves,
     for example "at least one current row has an amount", not "every amount is
     present".

### Where an Input expectation can actually run

An Input's `#### Expectations` compile to **`pre_transform`** executable
contracts, and this runtime executes a custom input expectation **only** for a
declared CSV source-aligned input bound to the `csv-source` service. A product
whose source is a REST API or a database declares no such input — there is
nowhere to attach one — so those expectations cannot execute at that phase.

Stop the current approved inventory and preserve the live source. A phase change
requires an explicit recorded scope decision, amended proposal, fresh prepare and
fresh consent; do not silently move the expectation to an Output or offer a CSV
export as a workaround. A different source scope is allowed only after an
independent user request.

If the user approves post-transform verification, the same predicate and
columns may become a corresponding **Output's `#### Promises`** only when the
landed relation includes those columns and the actual output storage and
contract compute context support that verifier. Record the phase shift in a
Decision: a violation is then caught after rows land, as a failed promise rather
than a refused ingest. Until that runtime support is verified, do not claim the
promise will execute or fail the build; explain the gap and keep the guarantee
as prose with no executable contract if the user chooses that scope.

Authoring an API- or database-backed Input with executable expectations is not
caught by the prose validators; it surfaces as
`closure.contract_phase_unsupported` once a closure exists. Getting the phase
right here is much cheaper than discovering it there.

There is deliberately no user-authored `Delivery` section: local DPs currently
produce a DuckDB-backed semantic-query result. There is no user-authored
`Contracts` section: executable contracts are compiled internally from Input
expectations and Output promises. A request for unsupported delivery is an
Open Question, not a user-selectable transport setting.

### Procedures are landed, not described

Prose states the intent of a procedure; **rows decide the result**. When
`Transform` supplies a procedure — a rubric, a scoring scale, gates,
thresholds, a verdict vocabulary, a selection rule — its executable values are
a **landed model declared in `Models`**, not prose in the procedure's
description.

The test is whether a reader of this document can predict the result for a case
you have not written out. A criterion whose scale declares more levels than it
states rules for is underspecified: a 1–5 scale with anchors written for 5 and 1
says nothing about 2, 3 or 4. The missing rules are either an Open Question, or
rows in the model that holds them — never a value the closure supplies later.

That model needs a column per thing the rule reads and returns: the criterion it
belongs to, an addressable band id, the bound or predicate that selects it, the
score or verdict it yields, and the version it belongs to. A generated closure
resolves each score by reading those rows, so the logic is queryable in the
built product and travels with this document to anyone who receives it.

Prose anchors remain useful and are not replaced — they explain *why* a band is
where it is, which rows cannot. They simply do not decide anything.

### AI steps

When the product calls an AI model, `Transform` carries a paragraph that
starts with the bold label **AI steps**. It is not a new top-level heading: the
ten headings above are fixed. List each step in run order, in plain words:

- what it does ("writes a two-sentence summary of each posting");
- what it reads, by the user's own field names, including an earlier step's
  output ("the posting's description and the summary written in step 1");
- what it produces, and the model it uses;
- for a checking step, what happens to a row it rejects (publication blocked,
  row excluded, or row published with its flag). Record that choice as a
  Decision, and word the matching `Outputs` promise exactly as it checks.

Then state the shared spending limit once for all steps (calls, a dollar cap
and an expiry) and any per-step call limit. State how long one run may take (rows per run × calls per row × seconds per
call) and the transform time limit you will request at prepare, which cannot
change later without removing the workflow
([workflow-v2.md](workflow-v2.md#size-the-transform-time-budget-at-prepare)). Say that one approval window will
cover every step. Describe a checking step as a second opinion that can miss
errors, never as proof. A single-instruction product lists one step. When
`prepare_mapper` shows this Desktop runs only one AI step, plan only one and
tell the user that checking its output with a second AI step is not available
in this version ([field mapper](../../nxd-generate-data-product/reference/field-mapper.md#what-the-desktop-flow-supports-today)).

## Terms

Terms are inline. A term may use a simple `###` heading and natural prose:

```text
## Terms

### Customer

A customer is the person or organization responsible for an order.
Also known as an account holder. Examples include a company buying a plan.
```

The extraction layer derives a stable local id and NXD-compatible metadata:
`name`, `definition`, `synonyms`, `related_terms`, `examples`, `term_values`,
`priority`, and an NXD glossary string-to-string `tags` map. `related_terms` must resolve to another
term in this document. External glossary DPs, URLs, and implicit external
definitions are not supported. Omitted priority uses NXD's P3 default and is
disclosed in the echo-back.
This is glossary-term importance for filtering or sorting definitions, not a
business issue score or rank by default. An omitted platform-fixed value is not
a separate user decision: group its required disclosure briefly by affected
term names, retaining `P3`, `default` and each term's coverage. Preserve explicit
priorities and any approved business or governance use of glossary importance;
do not treat those consequences as incidental metadata. The current checker
still requires the raw default disclosure in the human echo.

### A Term relative to the clock must name its anchor

A derived model cannot read the clock: `now()` makes every rebuild
irreproducible and no assert can pin it. So a Term defined relative to *now*,
*today*, *the fetch time* or *the current period* — "stale after 30 days",
"overdue", "this quarter" — has to say **which instant** it counts from, or it
cannot be computed as written.

Name it here, and record the choice in `Decisions` so it lands with the product's
other rulings. The instant is itself a ruling: a landed fetch timestamp, the
latest event time in the source, or an explicit as-of date all give different
answers for the same rows, and a reader comparing two builds needs to know which
one produced the number.

## Interpretation and approval

The lifecycle is:

```text
Markdown structure and spans
  → AI typed proposal with provenance
  → deterministic validation
  → natural-language echo-back and Open Questions
  → user approval
  → self-contained closure snapshots
```

Every approval-relevant typed value records `explicit`, `inferred`, or
`platform_fixed` provenance. Inferred values must be covered by the echo-back.
Blocking Open Questions prevent approval.

Disclosure or suppression choices that change which rows or values are exposed,
choices between a baseline and the reporting period, and any other choice that
changes the reported numbers and that neither the request nor the source
settles, are **blocking Open Questions**. Examples of the last kind: which date
places an event in a period (when it took effect or when it was received), or
whether a customer's movements within one period are netted or counted per
event. Ask and get the user's answer before you prepare or ask for approval; a
proposed default does not resolve any of these choices, and you never list one
as a non-blocking default. Ask each blocking question on its own, with
its options, so the reply can name the choice. Never offer a shortcut such as
"reply yes to use my recommendations" for any blocking question: a bare "yes",
"sure" or "ok" leaves every blocking question open, and you re-ask, naming the
first open one. A reply that says which field or rule carries the meaning the request needs does
settle the choice, even if it does not name one of your options: a reply that
describes one date column as the moment a change took effect settles that a
per-period report counts each change in the period that column falls in.
Record that reading as an `inferred` Decision whose basis quotes the user's
words, state the reading itself in the echo-back (for example, "From your
answer that this column is when a change took effect, I'll count each change in
the month it falls in"), and do not ask again; approval of that echo-back is
consent to the inference.

Only a choice between real alternatives is blocking. Your own definitions of
the requested measures, the reporting range, and other readings that follow
from the request and the source go into the plan as `inferred` values; plan
approval confirms them. Do not hold them as a separate question or wait for a
"yes" to them before you prepare. If a definition depends on one of the
blocking choices above (for example, which date places an event in a period,
or whether movements within a period net out), that choice is still a blocking
question: ask it, and infer only the rest of the definition.

Once a suppression rule is approved, the
Models section lands nothing below the suppressed grain (for example a
person-level roster): those rows are read in memory by the transform, so
nothing landed can recount a suppressed value.

Constraints attached to approval — for example, "approved, but keep this field
as a declared enum" — belong in the prepared blueprint and an executable
**Output Promise**, enforced by a typed model or a definition-bound verifier,
before capture. Re-prepare and show the revised echo-back so the user's approval
covers those constraints. A check that runs only in the transform does not
satisfy the promised contract.

**Enforce requested rules from the first plan approval.** When the prepared
plan is first shown for approval, include each rule the user requested as an
automatically enforced guarantee, so first approval and first capture already
cover it. Do not ask the user whether to enforce a rule they asked for. Ask only
when a rule cannot be enforced, and say what that means for them. Adding a
guarantee later changes the saved plan and needs fresh consent; including it up
front avoids that second round. This never weakens the rule that a change to the
plan needs fresh consent. If re-approval is genuinely required later, follow the
narration rules in [Current Desktop generation](../../nxd-generate-data-product/reference/desktop-generation.md#enforce-requested-rules-from-the-first-plan-approval).

Each approved Decision is locked by id and hash. Extraction cannot overwrite a
locked Decision. A conflicting edit becomes an explicit proposed change and
revokes the approval; it is never silently merged.

Formatting-only edits may retain approval only after re-extraction proves the
typed proposal is unchanged. Behavioral edits, Terms edits, contract-inventory
edits, or delivery-profile changes require a new approval. The closure stores
the byte-identical `dp-blueprint.approved.md` and the approved typed proposal JSON;
it never points back to the live document.

### Sharing the document

What approval binds is normative in `docs/architecture/dp-spec-authoritative.md`
§ "Approval and locks" and is not restated here. One consequence of it governs
sharing, and is easy to miss.

`dp-blueprint.md` alone carries the plan's **content** — every Section, Term,
Model and Decision re-parses from the Markdown with no other file present. It
does not carry **approval**, because `approved_proposal_hash` binds a typed
snapshot the document does not contain. A regenerated proposal is a new reading
of the same prose and will not match; `lock write` reports the mismatch as
`v3:frontmatter.approved_proposal_hash`.

So the unit of sharing is never the lone file:

| handed over | plan | approval |
|---|---|---|
| `dp-blueprint.md` alone | yes | no — the recipient approves it themselves |
| the job directory, document plus its proposal | yes | yes |
| an exported closure (`export_data_product`) | yes | yes, hash-verified |

This is deliberate rather than a gap to close. Approval binds an
interpretation, not just prose; an approval that travelled with the words alone
would let a different reading of the same document inherit it.

### Porting a blueprint to a new workflow

Reproducing a plan as a **new** data product — a second user rebuilding it, or
the same user standing up a parallel copy — is a supported path, and it starts a
fresh approval chain rather than inheriting one:

1. Copy `dp-blueprint.md` to the new job directory.
2. Change `name` and `workflow` to the new identity. Both are hashed, so this
   invalidates the old `approved_content_hash` — expected.
3. Set `status: proposed` and **delete** `approved_content_hash`,
   `approved_proposal_hash` **and** `prior_approved_proposal_hash`.
4. Re-extract the typed proposal, show the echo-back, and take a fresh approval.

Step 3's last deletion is the one that is easy to get wrong.
`prior_approved_proposal_hash` exists only for an **in-place** re-approval, where
the prior typed snapshot is actually on hand to supply. Carrying it into a port
names evidence the new holder does not have, and the next approval fails with
`cannot re-approve an approved v3 document without prior locked proposal
evidence`.

What must survive the copy is the **logic**: every Term, Model, Transform step,
threshold, band, precedence rule and Decision. What is expected NOT to survive is
**identity and approval** — the workflow id, the hashes, and any credential,
which never lived in this document anyway. A port that has to re-derive a
threshold or re-ask a settled ruling is an encapsulation defect in the blueprint;
a port that has to be re-approved under a new id is the system working.

## Claude Desktop form contract

The form layer should:

- show the prefilled Markdown/read-back before asking for field completion;
- group controls by the same section order as this document;
- use stable paths such as `v3:inputs[orders].text` and source spans for inline
  diagnostics and focused edits;
- keep inferred values and Open Questions visible;
- offer **Propose**, **Approve**, and **Reject** actions separately;
- disable approval while validation errors or blocking Open Questions remain;
- apply stale-hash-checked targeted patches or present a reviewed proposal;
- never replace the whole user document with a canonical typed emit.

## Worked example

```markdown
---
dp_spec_version: 3
name: monthly_revenue
workflow: monthly-revenue
status: proposed
---

## Intent

Provide an auditable monthly revenue relation for finance review.

## Questions

What was monthly revenue for each customer and month?

## Scope

Include completed paid orders. Exclude refunds until their timing rule is
decided.

## Terms

### Customer

A person or organization responsible for an order. Also known as an account
holder.

## Inputs

### Orders

Load completed orders from the monthly export.

#### Expectations

Rows have an order identifier. Amounts are expressed in EUR.

## Models

### Monthly revenue

Revenue grouped by customer and month.

## Transform

Group accepted orders by customer and month and sum their amounts. Apply the
refund decision here when its timing rule is answered.

## Outputs

### Monthly revenue by customer

The user-visible monthly revenue relation.

#### Promises

Refunds are excluded and accepted rows reconcile to the result.

## Decisions

### Refund treatment

Exclude refunds until a timing rule is supplied.

## Open Questions

When should refunds be applied? This blocks approval of the output.
```

The AI should echo the interpretation and ask the refund question before
approval. It should not ask the user to inspect the typed proposal.
