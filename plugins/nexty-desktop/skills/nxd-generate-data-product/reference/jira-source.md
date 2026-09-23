# Jira REST source

This recipe is listed in the [source types index](source-types.md). It is a
service-specific profile of the generic `api-source`; it does not introduce a
new desktop driver or a second credential boundary.

## Shape

Use the settled Jira Cloud-style contract and a read-only issue-search
resource:

- `GET /rest/api/3/search` with flat `jql`, `startAt`, and `maxResults`
  attributes;
- select the top-level `issues` array with `data_selector`;
- paginate using the response `total` together with `startAt` and
  `maxResults` (or the equivalent pinned dlt offset configuration);
- flatten the nested `fields` object into the scalar columns promised by
  `models.py` before the resource is renamed.

Use `http_basic` auth assembled from flat `secrets` fields: the account name
and API token are separate attributes, and the token is private. The search
endpoint is read-only; do not add issue creation, update, transition,
attachment, or permission operations. Do not replace dlt's REST connector with
a hand-written HTTP loop.

The closure still requires the generic API probe and the standard local DuckDB
landing path. The probe is bounded and read-only; it does not prove that a
later full load is safe or complete.
