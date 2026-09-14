# `dp-blueprint.md` — the user-owned plan between intent and a local data product

## Contents

- [Authoring contract](#authoring-contract)
- [Frontmatter](#frontmatter)
- [Sections](#sections)
- [Where an Input expectation can actually run](#where-an-input-expectation-can-actually-run)
- [Terms](#terms)
- [Interpretation and approval](#interpretation-and-approval)
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

### Where an Input expectation can actually run

An Input's `#### Expectations` compile to **`pre_transform`** executable
contracts, and this runtime executes a custom input expectation **only** for a
declared CSV source-aligned input bound to the `csv-source` service. A product
whose source is a REST API or a database declares no such input — there is
nowhere to attach one — so those expectations cannot execute at that phase.

State the guarantee under the corresponding **Output's `#### Promises`**
instead, against the landed relation. Same rule, same columns, verified after
the transform rather than before it. On this runtime the landed relations *are*
DuckDB tables, so that is where they are checkable at all.

The cost is worth naming in a Decision, because it is real and one-directional:
a violation is caught after the rows have landed rather than before, so a bad
fetch produces a failed promise rather than a refused ingest. What it does not
cost is the guarantee — the same predicate over the same columns still runs, and
still fails the build.

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
