# Salesforce REST source

This recipe is listed in the [source types index](source-types.md). It is a
service-specific profile of the generic `api-source`; it does not introduce a
new desktop driver or a second credential boundary.

## Shape

Use the pinned Salesforce instance/base URL from the settled source contract
and a read-only SOQL query resource:

- `GET /services/data/<version>/query` with the SOQL query supplied through a
  flat profile attribute;
- select the top-level `records` array with `data_selector`;
- follow `nextRecordsUrl` with dlt's `json_link` paginator when the response
  supplies one;
- flatten any nested fields before the resource is renamed to its promised
  model.

The continuation URL is response data, not a hard-coded endpoint. Keep the
instance URL, API version, query, and `source_kind: salesforce` in the
`api-source` attributes. Use bearer auth assembled from flat `secrets` fields;
the access token is private. Do not add Salesforce DML resources, mutation
endpoints, or a hand-written HTTP loop beside dlt's REST connector.

The closure still requires the generic API probe and the standard local DuckDB
landing path. The probe is bounded and read-only; it does not prove that a
later full load is safe or complete.
