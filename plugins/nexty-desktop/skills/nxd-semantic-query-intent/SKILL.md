---
name: nxd-semantic-query-intent
description: Use when a query adapter has a verbatim semantic question, a complete agreed catalog, and a concept-name selection that must be checked for correct intent before governed execution. This narrow foundation skill validates intent; it is not an entry point for discovery, runtime lifecycle, routing, or query syntax.
allowed-tools:
  - Read
  - AskUserQuestion
metadata:
  author: nextdata
  version: 0.52.8
---

# Semantic query intent validation

Apply this foundation after the calling query adapter has collected the
verbatim question, the complete catalog for its agreed scope, and a candidate
selection. Consult
[reference/semantic-intent-validation.md](reference/semantic-intent-validation.md)
for the canonical gate.

The gate validates intent-to-selection mapping before governed execution:

1. Check that catalog coverage is complete for the agreed scope before relying
   on relevance or grain decisions.
2. Have the catalog-aware critic compare the question, selection, and model
   descriptions, including compatibility and reachability metadata.
3. Show a plain-language round-trip echo assembled from the selected concepts'
   descriptions, including applicable PII or governance notes.
4. Clarify or abstain when the mapping is ambiguous, likely wrong, unavailable,
   or unreachable.

Execute only after the critic returns `ok` or the user explicitly confirms a
surfaced ambiguity. Do not treat confirmation as making an unavailable concept
or impossible relationship valid; remap or ask for a supported concept first.

Keep discovery, catalog scoping, query grammar, routing, authentication,
storage, runtime lifecycle, and result presentation in the calling adapter.
Do not add surface-specific operator lists or transport instructions to this
foundation. Do not use it as a standalone entry point when the caller has not
supplied the question and catalog.

## Additional resources

For the detailed gate, consult
`reference/semantic-intent-validation.md` after the adapter has established
the agreed catalog and before it executes the selection.
