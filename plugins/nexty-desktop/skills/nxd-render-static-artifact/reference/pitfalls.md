# Static artifact pitfalls

## Contents

- [Partial catalog fallback](#partial-catalog-fallback)
- [Transport fallback used as error recovery](#transport-fallback-used-as-error-recovery)
- [Dropped fields](#dropped-fields)
- [Inferred relationships](#inferred-relationships)
- [Labelled blanks](#labelled-blanks)
- [Unsafe HTML](#unsafe-html)
- [Historical releases](#historical-releases)

## Partial catalog fallback

`list_data_products`, `info`, `models`, and `describe_models` are not substitute
inputs. They can make a page look plausible while omitting the complete pinned
schema or release provenance. Fail the artifact when the required bundle cannot
be read.

## Transport fallback used as error recovery

`read_data_product_resource` exists because some clients never expose resource
primitives — not because a resource read failed. The two paths share one
reader, so a uri that returned `resource_not_found`, `artifact_unavailable`, or
a supersession redirect returns exactly that again through the bridge. Retrying
across transports only converts a clear diagnosis into an unexplained one, and
tempts a partial page built on a release nobody actually read.

The same reasoning bars mixing: a bundle whose documents arrived by different
transports is unauditable even when each document is valid. Pick the transport
from the client's capabilities before the first read, and keep it.

## Dropped fields

The query-oriented `models` registry is not `data_model`. It may omit
unannotated attributes. Complex types are objects, and one attribute can carry
multiple roles. Ports own their `model_names`/`models`; top-level output arrays
are not their union.

## Inferred relationships

The observed failure: a registry with `joins: []` rendered a header reading
`joins: none declared`, a legend reading `no join declared in the registry`, and
prose reading `no declared joins` — while the diagram drew dashed edges between
models sharing `application_id` and `candidate_name`, and the prose then called
the result "a small star".

Every text layer was honest and the artifact was still false, because a diagram
edge is an assertion and readers trust it over the caption denying it. Matching
column names across models are the normal appearance of a registry that declares
nothing; treating them as evidence of joins manufactures the withheld fact. With
no declared joins, draw nothing and name no shape.

## Labelled blanks

A `Description` header over blank cells reads as "these have no description",
which is a claim the payload usually did not make. Distinguish absent key,
`null`, `""`, and `[]`, and gloss each. When a column is absent for every row,
drop the column instead of shipping a header over empty cells; a blank slot is
the placeholder the contract already forbids, wearing a table header.

## Unsafe HTML

Payload text can contain markup-like strings. Never concatenate it into HTML or
an attribute. Do not load web fonts or scripts: the artifact must remain useful
offline. A raw JSON tab is both unsafe and outside this artifact's scope.

## Historical releases

An artifact is immutable evidence of its release. On same-workflow rebuild,
discard cached URIs and render the new sequence before any describe/query step.
Do not overwrite the old page or call it current.
