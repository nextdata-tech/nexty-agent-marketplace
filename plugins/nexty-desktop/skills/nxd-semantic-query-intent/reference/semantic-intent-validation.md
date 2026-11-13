# Semantic query: intent validation

## Contents

- [Purpose and boundary](#purpose-and-boundary)
- [The four techniques](#the-four-techniques)
- [Names and synonyms](#names-and-synonyms)
- [Derived metrics and native grains in the catalog](#derived-metrics-and-native-grains-in-the-catalog)
- [OR groups, metric thresholds, labels and snapshots](#or-groups-metric-thresholds-labels-and-snapshots)
- [Why the gate is client-side](#why-the-gate-is-client-side)
- [End-to-end flow](#end-to-end-flow)
- [Execution rule](#execution-rule)

## Purpose and boundary

A natural-language semantic question passes through two independent mappings:

```
question ──(intent→selection)──► selection ──(selection→result)──► result
            "did we understand?"   "did the governed surface execute
                                     that selection faithfully?"
```

The lower mapping can be deterministic while the upper mapping is still wrong.
A valid selection can choose the wrong metric, omit a constraint, use the wrong
time boundary, or apply a dimension that the selected metric cannot reach. The
selection is therefore a first-class artifact that can be inspected before it
is executed.

This reference defines only the intent-to-selection gate. The caller supplies:

- the user's question verbatim;
- the complete catalog for the agreed scope, including per-model descriptions;
- the candidate concept-name selection and its constraints; and
- the caller's clarification and governed-execution interfaces.

Keep source discovery, scope selection, query grammar, routing, authentication,
storage, lifecycle, and result rendering outside this foundation.

## The four techniques

Run all four checks before execution. Coverage is a precondition on discovery;
the other checks inspect the selection against the catalog metadata.

| Technique | Required behavior |
|---|---|
| **Coverage (no skipping)** | Read the authoritative description for every model in the agreed scope before deciding relevance, grain, or selection. Do not wave a model off because its name or apparent grain sounds irrelevant; those decisions are made from its description. If a later description reveals a better-fitting concept, revisit the selection. When the agreed scope was narrowed by the caller, state that scope in the echo. When no user is reachable, use the full caller-defined scope and state its size in the echo. |
| **Catalog-aware critic** | Compare the verbatim question and candidate selection with all available descriptions and, where the catalog lists them, each concept's `synonyms` (see [names and synonyms](#names-and-synonyms)). Return `ok`, `ambiguous`, or `likely-wrong`, plus suspect concepts or missing constraints. **Constraint coverage:** honor every SCOPE constraint (a named value, entity, segment, or time window that narrows the population). Use a filter when a catalog dimension's description expresses it. If a metric definition already encodes it (for example, a partner-sourced metric), choosing that metric satisfies the constraint without a filter. A value found in exactly one candidate dimension resolves which dimension holds it; descriptions still decide whether that dimension means what the question asks. A requested BREAKDOWN (such as "by product" or "per month") maps to a grouped dimension. A comparison of named values (such as "Psychiatry vs Neurology") needs a filter restricting to those values and grouping by that dimension; grouping never substitutes for filtering on a named value. Add a missing filter only when the question asks for it and a dimension description expresses the constraint, then re-run the critic. Never add an unrequested filter or exclusion. Return `likely-wrong` and abstain or clarify only when no metric or dimension can express the constraint. Check that each chosen metric's description matches the question and that each chosen dimension is compatible with the metric or reachable through documented catalog relationships such as `compatible_dimensions` and `reaches_dimensions`. |
| **Round-trip echo** | Restate the resolved selection in plain language before execution, using the selected metric and dimension descriptions rather than raw names alone. Include filters, ordering, limits, and any PII or governance note that changes how the result should be understood. Make the restatement concrete enough for the user to spot a mis-mapping. |
| **Clarification on ambiguity** | Before asking which dimension a value belongs to, the adapter probes each non-PII candidate dimension; if the value exists in exactly one, that resolves the dimension unless that dimension's description contradicts the question. Clarify only if it exists in several with different meanings, or in none and no stored value plausibly matches. For PII dimensions, do not enumerate values; present the real candidate concepts and ask the user to clarify when necessary. For other ambiguous, unavailable, incompatible, or unreachable concepts, ask about the real candidate concepts and abstain while unresolved. |

### Coverage is part of the gate

Do not build a selection from a partial catalog and then claim that the critic
considered every candidate. A different grain is not a valid reason to skip a
model before its description has been read: the description may contain the
metric or relationship that answers the question. Re-run the critic after any
late-read model changes the candidate set.

### The critic and echo have different jobs

The echo is a deterministic read-back of the selected concepts and catalog
descriptions. The critic is a judgment over whether that selection answers the
verbatim question. Passing one does not substitute for the other: a fluent echo
can faithfully describe the wrong metric, and a critic without an echo hides the
mapping from the user who must catch a business-meaning error.

## Names and synonyms

A metric or dimension row in the catalog may list `synonyms`: other names the
data product author declared for that concept, such as "PUMs", "patients under
management" and "active patients" for the metric `pums_sp`. When choosing a
concept, match the question's terms against each concept's name AND its
synonyms, not only its description. A term that is a declared synonym of exactly
one concept is strong evidence for that concept; the critic still checks the
description, filters and grain as for any other candidate. When a term is a
synonym of one concept and only loosely fits another's description, prefer the
synonym match and say in the echo which term it matched ("patients under
management" → `pums_sp`).

- **Send the canonical name.** Put the concept's own `name` in the selection,
  never the synonym or a different letter case. The service may resolve a
  synonym or case variant itself, but that is a backstop, not the plan: the
  echo and the result columns use the canonical name.
- **No `synonyms` key** means the author declared none, or the runtime predates
  synonyms. Fall back to names and descriptions as before; never infer that a
  concept lacks a meaning because it lists no synonyms.
- **Ambiguous names (SQ-108).** A name that matches more than one concept (two
  concepts whose names differ only in case, or, across data products, a synonym
  that two products use for different concepts) is refused with SQ-108, and
  the message lists the candidates. Pick among the listed candidates only when
  the question's context and their descriptions decide it, then resend that
  name exactly; otherwise ask the user which one they mean. Never pick one at
  random or retry with another spelling. The cross-data-product query service
  returns a generic preflight error instead, with no candidates: re-read the
  catalog and send canonical names.
- **`resolved` in the response.** When present, it lists each name the service
  read differently, as `{requested, resolved, kind, via}` with `via` `case` or
  `synonym`. Check that every `resolved` concept is the one the gate approved.
  When a substitution changes how the answer reads (the user said "active
  patients" and the figure is `pums_sp`), name it in the answer. An absent
  `resolved` means every name was used as written.

## Derived metrics and native grains in the catalog

A derived metric (`aggregation: "derived"` under a semantic composition in
`describe_model`) is a metric like any other for the critic: match its
description and expression to the question. Prefer it over selecting its inputs
separately and combining them by hand when the question asks for the combined
figure; a selection that does arithmetic across separate queries is
`likely-wrong`. A threshold on its value ("where the total exceeds 10") is a
metric-value filter on the derived metric, not a dimension filter (that is
refused with SQ-307). A derived metric
the service cannot plan is refused with SQ-306 ("invalid derived metric");
report it as a data product defect rather than rewriting the question. A `NULL`
in a derived total is a missing sum input, not a zero: say so, and never report
it as 0.

A `time_grain` on a model or metric limits the periods the echo may promise.
If the question asks for a period finer than the native grain ("daily" on a
weekly metric) or a window that cuts a period, clarify or remap; the service
refuses it with SQ-403. Echo a week as the ISO Monday week.

## OR groups, metric thresholds, labels and snapshots

Filters can now express more of a question, so the critic checks that each
form says what the question says:

- **OR vs AND.** "EU or web" across two dimensions is one `any` group; two
  top-level filters would mean "EU and web" and is `likely-wrong`. "EU or US"
  on one dimension is an `IN` list. When the question's scope is genuinely
  unclear about AND vs OR ("customers in Spain and online"), clarify rather
  than pick. In the echo, read a group back with its connective and
  brackets: "orders that are (EU **or** web) **and** paid".
- **Row filter vs metric threshold.** A condition on a stored attribute
  ("orders over 100 euros" when `amount` is a dimension) is a dimension
  filter on rows. A condition on an aggregated value ("customers whose revenue
  is over 1M", "regions with more than 50 orders") is a metric-value filter
  on a metric that is also in `measures`, applied after grouping. Swapping one
  for the other answers a different question: `likely-wrong`. Echo the
  threshold with the grouping it applies to: "customers, keeping those whose
  total revenue is over 1,000,000". Do not substitute "the top N" for a
  threshold the question states, or a threshold for a top N.
- **Labelled dimensions.** On a dimension with a display label, `=` / `IN`
  match the stored id or the label, so a value taken from a result or the
  user's own wording usually works as given; there is no need to translate a
  label to its code first. A negative filter (`!=`, `NOT IN`) excludes rows
  whose id or label matches, and rows with no id. If the value could be both
  one row's label and another row's id, the filter keeps both: say so, or
  clarify which was meant.
  A labelled dimension cannot sit inside an `any` group of two or more
  entries (or an `all` nested in one); the service refuses it with SQ-505.
  When the question's OR needs such a dimension ("Enterprise customers or web
  orders"), the faithful answer is separate queries reported as possibly
  overlapping, or a clarification; an echo that silently drops the branch,
  turns the OR into AND, or adds the two results is `likely-wrong`.
- **Snapshot metrics.** A metric whose catalog row lists `non_additive_over`
  is a point-in-time amount. A question about "total stock this month" or
  "the balance over Q1" cannot be a sum over the period: map it to one pinned
  date (the period's last day, when the question means "at the end of") or to
  a grouping by the date at its stored grain, and say which in the echo. A
  selection that would sum the snapshots is `likely-wrong` even before the
  service refuses it with SQ-308.
- **Private fields.** A concept the user names that is absent from the
  catalog may be undeclared or private; both are refused with SQ-103 and look
  the same. On a runtime that resolves synonyms, SQ-103 also means the name
  matched no concept ignoring case or through a listed synonym. Treat it as
  unavailable and do not speculate that it exists.
- **Presentation.** When a metric row carries `display_name` or `format`,
  use them in the echo and the answer (a `percent` value `0.25` is 25%); they
  never change which concept answers the question.

## Why the gate is client-side

The gate operates on the question, selection, and metadata already supplied by
the caller. It does not change the governed execution mechanism or its
determinism. Keeping the critic, echo, and clarification decision at the
conversation boundary makes the selection inspectable while leaving execution
to the caller's governed surface.

## End-to-end flow

```
complete agreed catalog + per-model descriptions
        │
        ├─ coverage: every model in scope was described
        │
verbatim question + concept-name selection
        │
        ├─ critic: {question, selection, catalog} → verdict
        ├─ echo: plain-language restatement (+ PII/governance notes)
        └─ clarify: ambiguity or invalid reachability → ask and abstain
        │
        └─ verdict ok / explicit user confirmation
                │
                └─ caller performs governed execution
```

## Execution rule

Execute only when the verdict is `ok` or the user explicitly confirms an
ambiguity that has been shown in the echo with its real candidates. A user
confirmation resolves uncertainty; it does not authorize an unavailable
concept, an incompatible dimension, or a relationship the catalog does not
document. Those cases require a supported remapping or a new catalog.
