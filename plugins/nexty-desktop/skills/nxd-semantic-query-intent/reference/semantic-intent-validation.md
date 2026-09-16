# Semantic query: intent validation

## Contents

- [Purpose and boundary](#purpose-and-boundary)
- [The four techniques](#the-four-techniques)
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
| **Catalog-aware critic** | Compare the verbatim question and candidate selection with all available descriptions. Return `ok`, `ambiguous`, or `likely-wrong`, plus suspect concepts or missing constraints. Check that each chosen metric's description matches the question and that each chosen dimension is compatible with the metric or reachable through documented catalog relationships such as `compatible_dimensions` and `reaches_dimensions`. |
| **Round-trip echo** | Restate the resolved selection in plain language before execution, using the selected metric and dimension descriptions rather than raw names alone. Include filters, ordering, limits, and any PII or governance note that changes how the result should be understood. Make the restatement concrete enough for the user to spot a mis-mapping. |
| **Clarification on ambiguity** | If the critic returns `ambiguous` or `likely-wrong`, or a concept is unavailable, incompatible, or unreachable, present the real candidate concepts and ask the user to clarify. Abstain from execution while unresolved; do not replace uncertainty with a confident guess. |

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
