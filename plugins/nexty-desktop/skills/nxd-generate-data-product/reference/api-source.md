# API connector: an off-mesh REST API

## Contents

- Scope
- The `RESTAPIConfig` / `rest_api_resources` shape
  - REST response envelopes: select the row array
  - Paginator `type` values
  - A POST body is scanned for dlt expressions — escape every literal brace
  - Paginating a GraphQL connection
  - Flatten fetched rows before they reach the port
  - Deriving from a fetched source
- Payload inspection gate — before authoring
- Credential handling, including refresh-on-401 for expiring tokens — read this before shipping
- Custom request headers
- Naming
- Why the endpoints live in the profile
- `transform/main.py` diff from the CSV template
- `requirements.txt`
- `spec.py` / `infra-profile.yaml` diffs
- Self-check (connectivity smoke test)
  - Two ways a probe lies

This is a sibling of the proven CSV connector documented inline in
`SKILL.md` — same closure shape (`duckdb` port, `PHYSICAL_MODELS`,
read-back assert, `.transform-complete`), no `data/` export, no local
file export. "No `data/`" is about the *connector*: this type brings no
export of its own. A closure may still carry `data/` for landed reference
data it authored — see § "Landed reference data in an API closure".

## Closure root

Before authoring, resolve exactly one absolute `<closure-root>`: the directory
passed to the supervisor as the closure. Normalize existing artifacts into it
before creating or checking any other artifact. Keep `infra-profile.yaml`,
`connectivity_check.py`, `spec.py`, `models.py`, `transform/`, and
`requirements.txt` inside that root: root-level artifacts are direct children,
and nested artifacts are descendants. Credentialed closures also keep
`.gitignore` and `SENSITIVE` inside that root. Do not place credentials,
those sensitivity artifacts, or the profile beside or outside the root. Use the
same root for the self-check, lock, and build.

## Scope

An **off-mesh** REST API the user names directly — not an upstream
Nextdata data product, and not the k8s/mesh `nxd-add-inputs` topology.
One base URL, one or more resources/endpoints, each mapped to a promised
physical model.

## The `RESTAPIConfig` / `rest_api_resources` shape

```python
from dlt.sources.rest_api import rest_api_source, rest_api_resources, RESTAPIConfig

config: RESTAPIConfig = {
    "client": {
        "base_url": <base_url>,
        "auth": <bearer | http_basic | api_key | oauth2_client_credentials>,
        "headers": {...},  # non-secret request headers — see Custom request headers
        # paginator: omit and let dlt auto-detect unless the user specifies one
    },
    "resources": [
        {"name": <model>, "endpoint": {"path": <endpoint path>, "params": {...}, "data_selector": <optional>}},
        ...
    ],
}
resources = {r.name: r for r in rest_api_resources(config)}  # returns a LIST
```

### REST response envelopes: select the row array

If the API returns a top-level object that wraps rows in a key such as `data` — for example
`{"page": 1, "per_page": 10, "total": 12, "pages": 2, "data": [...]}` — set
the resource endpoint's `data_selector` to that row-array path:

```python
resources_config = [
    {
        "name": "monitors",
        "endpoint": {
            "path": secrets["endpoint_monitors"],
            "data_selector": "data",
        },
    },
    {
        "name": "checks",
        "endpoint": {
            "path": secrets["endpoint_checks"],
            "data_selector": "data",
        },
    },
]
```

`data_selector` is configuration-optional only when the response itself is
already the row list. It is response-shape-required for an envelope: use it, or
an equivalent mapping that extracts the array before handing rows to dlt, and
verify that `page`, `per_page`, `total`, and `pages` do not land as row
columns. Repeat the selector for every resource whose response has the same
envelope. Do not replace the declared REST connector with a hand-written HTTP
loop.

### Paginator `type` values — copy these exactly

Omitting the paginator and letting dlt auto-detect is the default advice above,
and it is usually right. When the API needs an explicit one, the `type` value is
validated against a fixed table, and **the separator is an underscore**. Guessing
the hyphenated spelling — `page-number` — is the natural mistake and it fails
with an error that names neither the field nor the fix:

```
For `DltResource`: Path `.`: field `resources[0]` expects `callable`
(function or class instance) but got {...}
```

That message points at `resources[0]` and says "expects callable", so it reads
as a problem with how the resource list was built rather than one bad string
three levels down. Enumerated from `dlt==1.28.2`'s own `PAGINATOR_MAP`:

`auto`, `cursor`, `header_cursor`, `header_link`, `json_link`, `json_response`,
`offset`, `page_number`, `single_page`

```python
"paginator": {"type": "page_number", "base_page": 1,
              "page_param": "page", "total_path": "pages"},
```

`total_path` is the path to the page COUNT in the response envelope — with
`{"page": 1, "pages": 4, "data": [...]}` that is `"pages"`. Omit it and dlt
paginates until a page comes back empty, which is correct but costs one extra
request per resource.

Re-confirm this shape against the pinned `dlt==1.28.2` changelog before
relying on it in code — it was verified against current dlt docs, not
version-pinned documentation.

### A POST body is scanned for dlt expressions — escape every literal brace

`endpoint` accepts `method: "POST"` and a `json` body (verified by introspection
against the pinned `dlt==1.28.2`: `Endpoint.method` is
`Optional[Literal["GET", "POST"]]` and `Endpoint.json` is
`Optional[Dict[str, Any]]`), which is what an API with no GET surface needs — a
GraphQL endpoint being the common case.

**dlt scans every string in `json` for its OWN placeholder expressions** —
`{resources.other_resource.field}`, `{incremental.start_value}` — using
`string.Formatter`. A GraphQL query is nothing but braces, so the resource list
is rejected before a single request is sent, and the message names neither
GraphQL nor the query:

```
ValueError: Expression `
  issues(
    first` defined in `json` is not valid. Valid expressions must start with
one of: `{'resources'}`. If you need to use literal curly braces in your
expression, escape them by doubling them: {{ and }}
```

Read it as "the body was treated as a template", not as a malformed query. The
fix is the one the message names — double every brace when building the config,
never in the query constant itself:

```python
_ISSUES_QUERY = """
query PocketIssues($first: Int!, $after: String, $project: String!) {
  issues(first: $first, after: $after,
         filter: { project: { name: { eqIgnoreCase: $project } } }) {
    nodes { id identifier title state { name type } assignee { name }
            labels { nodes { name } } }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# Keep the constant readable; escape at the boundary.
escaped_query = _ISSUES_QUERY.replace("{", "{{").replace("}", "}}")
```

**The doubled braces are not sent to the API.** dlt's `expand_placeholders()`
collapses `{{` back to `{` when it builds the request — verified against the
pinned version to round-trip to the byte-identical query, so the upstream sees
exactly what you wrote. Do not "fix" a working escape by removing it because the
query looks wrong in the source: the escape and the expansion are a pair.

### Paginating a GraphQL connection

The cursor paginator writes the next cursor into the request **body** rather
than a query parameter, which is what a Relay-style connection needs.
`JSONResponseCursorPaginator` takes `cursor_body_path` and `has_more_path`
(both present in the pinned `dlt==1.28.2`; `cursor_param` and `cursor_body_path`
are mutually exclusive and passing both raises):

```python
"data_selector": "data.issues.nodes",
"paginator": {
    "type": "cursor",
    "cursor_path": "data.issues.pageInfo.endCursor",
    "cursor_body_path": "variables.after",     # into the POSTed json
    "has_more_path": "data.issues.pageInfo.hasNextPage",
},
```

### Flatten fetched rows before they reach the port

The query above selects `state { name type }` and `labels { nodes { name } }`,
because a real GraphQL selection almost always does. dlt restructures both, in
**two different ways, only one of which is caught for you**. Verified against the
pinned `dlt==1.28.2`:

```
TABLES:  ['linear_issues_landed', 'linear_issues_landed__labels__nodes']
COLUMNS: ['assignee__name', 'id', 'identifier', 'state__name', 'state__type', 'title']
```

**A nested LIST becomes a child table.** `labels.nodes` lands as
`linear_issues_landed__labels__nodes`, which appears in
`pipeline.default_schema.data_table_names()`, so the mandatory read-back assert
fires and the build stops. Loud, and correct.

**A nested DICT becomes `__`-joined columns.** `state` lands as `state__name` and
`state__type`, `assignee` as `assignee__name`. No extra table is produced, so
**the read-back assert cannot see this one**. A `models.py` declaring the obvious
`state_name` binds to a column that does not exist: the product builds,
publishes and serves, and that dimension is simply empty. It is the same silent
class as a `primary_key()` with no `dimension()` — no error, no failed assert, no
missing table, only questions that quietly have no answer.

So flatten each fetched row into flat scalars before it reaches the port, with
`.add_map()` on the resource, ahead of `.with_name(...)`:

```python
def _flatten_issue(node: dict[str, Any]) -> dict[str, Any]:
    """Flat scalars only. Names here are what models.py must declare."""
    state = node.get("state") or {}
    assignee = node.get("assignee") or {}
    labels = (node.get("labels") or {}).get("nodes") or []
    return {
        "id": node.get("id"),
        "identifier": node.get("identifier"),
        "title": node.get("title") or "",
        "state_name": state.get("name") or "",
        "state_type": state.get("type") or "",
        "assignee_name": assignee.get("name") or "",
        # A list must collapse to a scalar, or it lands as a child table.
        "label_names": ",".join(sorted(l.get("name", "") for l in labels)),
    }

FLATTENERS = {"linear_issues_landed": _flatten_issue}
```

Then, in the reader loop:

```python
resource = resources[model]
flatten = FLATTENERS.get(model)
if flatten is not None:
    resource = resource.add_map(flatten)
readers.append(resource.with_name(table_name))
```

The read-back assert already covers the list case. The dict case needs a check
of its own, so once the rows land confirm every column `models.py` declares
actually exists on the landed table — a `__` anywhere in a landed column name
means something nested got through.

**A GraphQL error is an HTTP 200.** The body carries `{"errors": [...]}` with
`data: null`, so dlt raises nothing on the transport and the resource simply
yields no rows — which surfaces much later as an empty promised model. Do not
diagnose that as a credential problem before checking the response body; the
standalone probe below is what distinguishes them.

## Payload inspection gate — before authoring

After the plan is settled and explicit operator consent has been relayed,
resolve exactly one absolute closure root. For an `api-source`, write the
closure-local `connectivity_check.py` as the first closure artifact after
consent. Its non-secret resource list, endpoint paths, expected row-array
selectors, and pagination bounds come from the settled plan; it must not rely
on a later generated transform or on an untrusted caller working directory.
Keep it dependency-light and independent of NXD, dlt, DuckDB, and the
generated transform — Python's standard library is sufficient for the bounded
HTTP request and response parsing. Supply credentials only through the
authoring session's runtime secret input; never embed or print them.

When credentials are available, execute that probe before writing any other
closure artifact, using the actual resolved base URL and endpoint path plus the
configured authentication and headers. It must make one bounded, read-only
request per configured resource, assert a parseable response matching the
expected shape, and print a bounded, sanitized summary for **each** resource
before it exits successfully. Each summary includes the endpoint label, HTTP status,
top-level shape/keys, row-array path, pagination fields (`total`/`pages` when
present), and one or two representative row keys and values. Values may be
shown only when they are clearly non-sensitive and non-personal scalars (for
example, a status enum or a count). Secret-like response fields (`token`,
`secret`, `password`, `api_key`, `authorization`, `cookie`, or similar) and
personal or account data are represented only by their field name and type or
a `<redacted>` placeholder. Redact or omit authorization headers, bearer/API-key
values, cookies, secret query parameters, and full response bodies. A summary
from only one resource, a status code without the body shape, API documentation,
or an inferred schema is not payload inspection. A failed or uninspectable
resource must produce a bounded diagnostic and stop authoring; do not guess a
schema. Repeat the gate whenever the endpoint set or source binding changes.

This ordering is strict: after consent, the only file written before the probe
passes is `connectivity_check.py`. Do not create `infra-profile.yaml`,
`.gitignore`, `SENSITIVE`, `spec.py`, `models.py`, `transform/`,
`requirements.txt`, or `README.md` first. The probe receives the runtime
credential from the authoring session; it does not need the profile file to
exist yet. If the probe cannot inspect every configured resource, stop with a
bounded diagnostic rather than authoring from a guessed schema.

If credentials are unavailable in-session, author the probe and then continue
authoring the remaining closure from the settled plan. Mark **both** payload
inspection and the connectivity self-check as **not run** and **unverified**;
the plan is the source of the intended schema, not evidence that the source
was reached. Structural checks may still run, but this is not source
validation, is not a complete happy path, and must not be reported as a
materialized success; leave the closure incomplete until a later credentialed
probe succeeds. Do not manufacture a response summary from API docs,
fixtures, or assumptions.

## Credential handling — read this before shipping

Same mechanism as the database connector: for CSV the committed connector
config is a non-secret path; for a REST API it necessarily includes a real
credential (bearer token, API key, or OAuth client secret). That value is
delivered by writing it into the `api-source` service's `attributes` in
`infra-profile.yaml` — the desktop supervisor's `generic-secrets` driver
reads it from there and merges it into the transform's `secrets` dict.
**`secrets` is FLAT.** The supervisor merges every service named in
`.secrets([...])` into one map. For a `generic-secrets` service the keys are
exactly the `attributes` you wrote — the service name is not among them — so an `api-source` attribute `base_url` arrives as
`secrets["base_url"]`, never `secrets["api_source"]["base_url"]`. A nested read
raises `KeyError: 'api_source'` at transform time, after the credential has
already been resolved.

- **When this closure names more than one service in `.secrets([...])`, check
  for key collisions before writing the profile.** The merge is flat, so a key
  declared by two services resolves to one value and the loser vanishes with no
  error — prefix the attribute `key` (`orders_base_url`) to separate them. Full
  rule: `reference/multi-source.md`.
- **Endpoint paths are attributes too, one per model**: `endpoint_<model>`,
  marked `public: true`. They are non-secret topology and belong beside
  `base_url` in the same service — **not** in a companion file. See "Why the
  endpoints live in the profile" below.
- The `api-source` service's `attributes` list carries the live payload as
  **one entry per property**, each shaped `{"key": <property>, "value":
  <live value>, "public": <bool>}` — never one attribute holding a nested
  object (see the sensitivity classification under Credential handling for the
  `public:` value per property). `base_url` is always present. When the API requires
  authentication, add `auth_type` (one of `bearer` / `http_basic` /
  `api_key` / `oauth2_client_credentials`) plus that type's own flat
  fields — never a single `auth` attribute holding the whole credential,
  since dlt's `client.auth` needs a *structured* value (e.g. `{"type":
  "bearer", "token": ...}`), not one opaque string, and some types
  (`api_key`, `oauth2_client_credentials`) carry more than one secret
  field to begin with:

  | `auth_type` | additional attributes |
  |---|---|
  | `bearer` | `auth_token`; optional `auth_refresh_path` (`public: true`) |
  | `http_basic` | `auth_username`, `auth_password` |
  | `api_key` | `auth_api_key`, `auth_key_name`, `auth_key_location` (optional, defaults to `header`) |
  | `oauth2_client_credentials` | `auth_client_id`, `auth_client_secret`, `auth_token_url` |

  Implement only the modes named by the approved source contract. A bearer-only
  contract still dispatches on `auth_type`, but its unsupported-mode branch
  must reject API-key, basic, or OAuth values instead of adding unused auth
  mechanisms or credentials.

  Omitting `auth_type` (and its fields) entirely means
  `secrets` has no `"auth_type"` key, not an empty one. See
  the worked example below and the `auth_type` dispatch in the transform
  diff, which assembles these flat fields into the structured dict dlt
  expects.
- **Non-secret request headers use the `header_` prefix** — one attribute per
  header, `header_<name>` with `-` written as `_` (`header_user_agent` →
  `User-Agent`), `public: true`. This is the *only* supported way to add a
  required client header; see **Custom request headers** below for why the
  prefix exists and how the transform reassembles it. A **secret-valued**
  header (an API key sent as `X-API-Key`) does NOT go here — it belongs to
  `auth_type: api_key`, which already sends a header and keeps the value
  `public: false`.
- **`value` is always a plain string** on the transform side — dlt/Python
  types (ints, bools) are not preserved; cast in the transform if needed.
- **Mark each attribute by sensitivity.** The `public:` flag controls **only**
  `export_data_product` redaction — the transform reads every attribute via
  `secrets` regardless. Secrets and identity —
  `auth_token`, `auth_username`, `auth_password`, `auth_api_key`,
  `auth_client_id`, `auth_client_secret` — are `public: false` (redacted
  fail-closed on export). Non-secret topology/config — `base_url`, `auth_type`,
  `auth_refresh_path`, `auth_key_name`, `auth_key_location`, `region`, and every `header_*` — is `public: true` so it
  survives an export and the recipient only refills the credentials. **Never
  mark a credential `public: true`.** If the user explicitly designates an
  attribute's sensitivity, honor their choice over this default.
- **Never fabricate a credential the user hasn't supplied**, and never
  narrate a live token/key in chat — enter it into `infra-profile.yaml`
  exactly as the user gave it, nowhere else.
- **The closure directory itself now holds a live credential in plaintext.**
  The `public:` flag only controls `export_data_product` redaction; it does not
  make `infra-profile.yaml` safe to commit, hand-zip, or hand off. Treat the
  whole closure directory as sensitive once this file carries a real token/key:
  don't commit it to a shared repo, don't hand-attach it to a ticket or chat,
  and don't reuse it as a template for a different API without clearing the old
  credential first. To share the product, use the supervisor's
  `export_data_product` tool — it strips every attribute not marked
  `public: true` fail-closed, so **never mark a credential attribute
  `public: true`** (`public: true` means "safe to ship in an export").
- **Emit `.gitignore` and `SENSITIVE` in the same step that writes the
  credential**, and `chmod 0600 infra-profile.yaml` where a shell can reach
  the closure. The trigger is structural — any `*-source` service with a
  populated `attributes` list — and Phase C fails the closure when the two
  files are missing. `database-source.md`'s **Sensitivity artifacts** section
  is the canonical definition, including the exact file bodies; it applies
  unchanged here with `Keys: <service>.attributes -> auth_token` (or whichever
  auth keys this API uses). Writing the credential and not the artifacts is an
  incomplete step, not a later cleanup.
- This shape (`key`/`value`/`public`) is verified against the supervisor's
  `yaml_schemas::infra_profile::KeyValuePairWithPublic` type and
  `SecretsHandler` construction — not inferred from a single example.

### Expiring credentials: refresh on the session, never on a custom auth class

An API whose token expires mid-extraction needs a retry that re-authenticates
and replays the request. Two shapes look obvious and both fail; live runs have
lost several build cycles to each.

**A custom auth class is rejected before a request is sent.** `RESTAPIConfig`'s
`auth` dispatch accepts dlt's own configured auth types, not an arbitrary
object, so passing a hand-written class raises at configuration time:

```
dlt.common.configuration.exceptions.ConfigurationWrongTypeException:
  Invalid configuration instance type `<class '_RefreshingBearerAuth'>`.
```

**Overriding `Session.request` never fires.** dlt's `RESTClient` calls
`session.send()` directly, so a `requests.Session` subclass that overrides
`request()` is simply bypassed — every call goes out with the stale token and
the extraction dies inside the resource generator, which reads as a bug in your
pagination rather than in your auth:

```
dlt.extract.exceptions.ResourceExtractionError: In processing pipe `<resource>`
requests.exceptions.HTTPError: 401 Client Error: Unauthorized for url: ...
```

**What works** is the complete refresh-aware session in [`../scripts/api_source_refresh_session.py`](../scripts/api_source_refresh_session.py). It overrides `send()`, the single chokepoint used by dlt `RESTClient`, and supports the flat `base_url`, `auth_refresh_path`, and `auth_token` profile attributes described above.

One dlt detail matters here: dlt may install a response hook that calls
`raise_for_status()` before `requests.Session.send()` returns. The shipped
recipe handles both forms — a returned 401/429 response and an
`requests.exceptions.HTTPError` carrying a 401/429 response — with the same
bounded refresh or retry policy. It re-raises errors without a response and
HTTP errors for other statuses; do not replace this with a broad exception
catch or an unbounded retry loop.

**Preserve exact JSON measures before dlt consumes the response.**
`Response.json()` normally creates Python floats for JSON fractional numbers;
converting that float later with `Decimal(str(value))` cannot recover digits
that were already lost. When source precision is load-bearing, add a
closure-local `response_actions` hook that parses `response.content` with
`json.loads(..., parse_float=Decimal)` and rewrites the response body so those
values remain exact decimal strings for the resource; convert those strings to
`Decimal` in the transform and keep the `Decimal` through the yielded row.
Declare `decimal(precision, scale)` from the approved source contract rather
than `number()` or a scale-zero decimal. Verify the exact decimal value before
the write; do not compare against a cast into the already-rounded destination
type. The hook must preserve pagination metadata and all non-measure fields.

The script is shipped as a source recipe, not as a runtime dependency of a generated closure. Copy its source into the generated self-contained transform, or copy and adapt its `_headers_from`, `RefreshingSession`, and `make_rest_api_config` definitions there. Do not import it from the installed skill tree: the closure must still work after handoff to the supervisor. Keep the generated transform resource list and auth/profile wiring around the copied implementation.

The session is attached at `config["client"]["session"]`, which is the
`RESTAPIConfig` client field consumed by dlt `RESTClient`; do not put it on a
custom auth object or as a top-level config field. Use
`make_rest_api_config(secrets, resources_config)` and pass the result to
`rest_api_resources`.

The state machine allows at most one refresh after a 401 and at most one
capped delay after a 429 for each original request. It is deliberately not
recursive: a second 401 raises immediately, and a second 429 is returned to
dlt. The prior response is closed before every replay, and a non-rewindable
POST/GraphQL body is rejected before the first send. The timeout is always
explicit; `Retry-After` accepts finite numeric seconds only, with malformed or
negative values becoming an immediate retry and excessive values capped by
`max_retry_after_s`.

The refresh request is a same-origin `POST` resolved from the
`auth_refresh_path` attribute; both `auth/refresh` and the profile
root-relative `/auth/refresh` shape are valid. Query strings, fragments,
credentials in URLs, absolute URLs, network paths, and redirects are rejected
or returned without following them. A successful refresh may return any one of `token`,
`access_token`, `bearer_token`, `new_token`, `accessToken`, or `bearerToken`;
if none of those keys is present, the current bearer is retained. If a
recognized key is present, its value must be a non-empty string; recognized
empty, non-string, or conflicting values fail without including the value in
the exception. Non-2xx refresh responses and a second 401 fail immediately.

The refresh path itself is an attribute on the `api-source` service, like every
other endpoint — see Credential handling above. Add `auth_refresh_path` beside
`auth_token` only when the bearer flow supports refresh, mark it `public: true`,
and never inline a token or put a credential in a URL, error, or log.

## Custom request headers

Some APIs reject a request that carries valid credentials, because of a header
that has nothing to do with authentication. The common case is `User-Agent`:
**dlt sends `User-Agent: dlt/1.28.2` by default**, and an upstream that
filters unrecognized clients answers `403 Forbidden` — with an error body about
permissions, not about the header. The same credentials succeed under `curl`,
which sends `curl/x.y.z`. That contrast reads as "Python traffic is blocked" or
"the token is wrong", and the tempting fix — abandon dlt, hand-roll `urllib`
with a browser-ish `User-Agent` — throws away the connector for a one-line
config change and fails the acceptance check. **Diagnose a 403 by comparing the
headers the two clients send before touching the credential.**

`RESTAPIConfig`'s `client` accepts a `headers` mapping (verified by
introspection against the pinned `dlt==1.28.2`: `ClientConfig.headers` is typed
`Optional[Dict[str, str]]`). It composes with `auth` rather than replacing it —
the `Authorization` header the `auth` dispatch builds is still sent on every
request — and a per-resource `endpoint.headers` **merges with** these rather
than replacing them, so a resource adding its own header keeps the client's.

### The `header_` prefix

`secrets` is one flat string→string map, so a header mapping cannot be stored
as a nested object under one attribute. Encode each header as its own flat
attribute instead:

| Header | Attribute key | `public:` |
|---|---|---|
| `User-Agent` | `header_user_agent` | `true` |
| `Accept` | `header_accept` | `true` |
| `X-Trace-Id` | `header_x_trace_id` | `true` |

Lowercase the header name and write `-` as `_`. The transform reverses it by
title-casing each `_`-separated part and rejoining with `-`, so
`header_x_trace_id` → `X-Trace-Id`. Reconstruction can differ from the upstream
docs' capitalization (`header_x_api_key` → `X-Api-Key`); that is fine, because
HTTP header field names are case-insensitive per RFC 7230 §3.2. Do not try to
preserve exact casing by inventing a second attribute to hold it.

**`header_` is for non-secret values only.** Every `header_*` attribute is
`public: true` and therefore survives `export_data_product` verbatim. A
credential sent as a header belongs to `auth_type: api_key`
(`auth_api_key` + `auth_key_name`, `public: false`), which puts the same header
on the wire with the value redacted on export. Putting a token in
`header_authorization` marks a live credential exportable and is the one
mistake this convention must not invite.

### Transform assembly

The [shipped refresh script](../scripts/api_source_refresh_session.py) defines
`_headers_from`; copy that helper with the session into the generated transform
when using refresh. It reconstructs the flat `header_*`
attributes once. Pass the same mapping to dlt's `client["headers"]` and to
`RefreshingSession(headers=...)`: dlt carries it on ordinary requests, while
the session carries it onto the refresh request and writes its own
`Authorization` afterward. Any `Authorization` spelling supplied by a caller
or filed under `header_*` is rejected case-insensitively before transport.

For a non-refresh transform, the same one-time assembly is in the
`client_config` build (after the `auth_type` dispatch, so a
malformed profile fails on the credential first):

```python
headers = _headers_from(secrets)
if headers:
    client_config["headers"] = headers
```

Assign it **only when non-empty** — `"headers": {}` is accepted but says the
closure configures headers when it does not, and an empty dict reads in review
as "the author checked and there are none" rather than "no `header_*` attribute
was written". Build it from `secrets` like everything else: a `User-Agent`
hard-coded in `transform/main.py` is the same defect as a hard-coded base URL,
and it is not fixed by the value being non-secret — the profile is where a
deployment-varying value belongs.

## Naming

- Infra-profile service: `api-source`, driver `nxd:generic-secrets:1.0.0`.
- Transform secrets keys: the attribute keys themselves, flat on `secrets` —
  `secrets["base_url"]` (always present); only when the API requires
  authentication, `secrets["auth_type"]` plus that type's own fields (see
  Credential handling); and only when the API requires a non-secret header,
  one `secrets["header_<name>"]` per header (see Custom request headers).
  There is no `secrets["api_source"]` level.
- Endpoint paths: `secrets["endpoint_<model>"]`, one attribute per API-backed
  model (non-secret topology, `public: true`). **No companion file.**

These names are for exactly **one** API source. When this closure needs two
or more APIs (or mixes an API with another connector type), label each
instance instead — see `reference/multi-source.md` for the full
`api-source-<label>` / label-prefixed attribute keys (`orders_base_url`,
`orders_endpoint_<model>`) pattern.

## Why the endpoints live in the profile

An earlier revision of this reference had the author write an
`api-source-endpoints` file beside the transform, one `<model>=<endpoint path>`
line each. Do not do that, and do not reintroduce it under another name.

The endpoint map is **configuration**, and this closure already has exactly one
configuration channel: the `api-source` service's `attributes`, which the
supervisor merges flat into `secrets`. `base_url` travels that way already, and
an endpoint path is the same kind of value — non-secret topology the recipient
of an export needs to see and may need to change. Splitting it into a sidecar
file bought nothing and cost two things: the file had to survive closure
materialization to be readable at transform time (it did not, for a while), and
an export had to decide separately whether it should travel.

On the platform (k8s) path the model→source-object binding lives in the
manifest — `target-tables` / `target-files` — or in a source-aligned input's
`.config(attributes={...})` bag. **Neither is available here.** The desktop
runtime ships exactly one input storage driver, `nxd:local/file/storage:0.1.0`,
which reads CSVs out of the pinned definition's `data/` directory; there is no
local driver that can back a source-aligned input pointed at a REST API, and an
api-source closure declares no input at all — the transform reaches the API
itself through dlt. That leaves the profile attributes, which is where a flat,
string-valued, per-model key belongs on this runtime.

## `transform/main.py` diff from the CSV template

Same `duckdb` param/typing, `PHYSICAL_MODELS` discipline, read-back assert,
`write_disposition="replace"`, and `.transform-complete` touch as the CSV
template. Only the ingestion body changes:

```python
from dlt.sources.rest_api import rest_api_resources, RESTAPIConfig

# `secrets` is the FLAT merge of every service in `.secrets([...])` — read the
# attribute keys directly. There is no per-service level to index first.
#
# The fetched models are exactly the ones the profile gives an endpoint for,
# filtered out of API_MODELS — the module-level tuple of models this closure
# fetches over HTTP.
#
# Do NOT filter PHYSICAL_MODELS, and do NOT filter BASE_MODELS. A derived model
# (Step 3a) is computed in Python and has no endpoint. BASE_MODELS is the landed
# reference data — `nxd_decisions`, agent judgement rulings, anything from
# `derivation-plan.md` / `llm-judgments.md` — which reaches the port as its own
# `@dlt.resource` rather than over HTTP, and which must equal the `data/`
# listing exactly (see "Landed reference data in an API closure" below).
# Filtering either one asks the API for a model it does not serve, or yields an
# empty fetch list because nothing in it has an `endpoint_<model>` attribute.
#
# A model that SHOULD be fetched but whose attribute you forgot drops out here
# rather than raising. That is caught: the mandatory read-back assert at the end
# of the transform compares what landed against PHYSICAL_MODELS and names the
# missing table. Do not delete that assert — here it is the only thing standing
# between a typo'd attribute key and a silently empty model.
#
# The filtered subset gets its OWN name. Assigning it back over API_MODELS makes
# the module-level tuple and the runtime subset the same identifier, so a later
# reader cannot tell which one a line means.
fetched_models = tuple(m for m in API_MODELS if f"endpoint_{m}" in secrets)

client_config = {"base_url": secrets["base_url"]}
auth_type = secrets.get("auth_type")
if auth_type == "bearer":
    client_config["auth"] = {"type": "bearer", "token": secrets["auth_token"]}
elif auth_type == "http_basic":
    client_config["auth"] = {
        "type": "http_basic",
        "username": secrets["auth_username"],
        "password": secrets["auth_password"],
    }
elif auth_type == "api_key":
    client_config["auth"] = {
        "type": "api_key",
        "name": secrets["auth_key_name"],
        "api_key": secrets["auth_api_key"],
        "location": secrets.get("auth_key_location", "header"),
    }
elif auth_type == "oauth2_client_credentials":
    client_config["auth"] = {
        "type": "oauth2_client_credentials",
        "access_token_url": secrets["auth_token_url"],
        "client_id": secrets["auth_client_id"],
        "client_secret": secrets["auth_client_secret"],
    }
elif auth_type is not None:
    # Do NOT drop this branch, and do not collapse the dispatch to whichever
    # single scheme today's profile uses. An unhandled auth_type means the
    # closure cannot authenticate; raising here says so at transform time
    # instead of sending a wrong-scheme request and reading the 401 as a
    # credential problem.
    raise ValueError(
        f"unsupported auth_type {auth_type!r} in secrets — "
        f"add a branch above, or fix the infra-profile attribute"
    )

# Non-secret request headers, rebuilt from the flat `header_*` attributes.
# See "Custom request headers" — this is what keeps a `User-Agent`-gated API on
# the dlt path instead of a hand-rolled urllib loop.
headers = _headers_from(secrets)
if headers:
    client_config["headers"] = headers

config: RESTAPIConfig = {
    "client": client_config,
    "resources": [
        {"name": model, "endpoint": {"path": secrets[f"endpoint_{model}"]}}
        for model in fetched_models
    ],
}
# rest_api_resources returns a LIST of DltResource, not a DltSource. Verified
# against the pinned dlt==1.28.2:
#     rest_api_resources(config: RESTAPIConfig) -> List[DltResource]
# It has no `.resources` mapping, so `source.resources[model]` raises
# `AttributeError: 'list' object has no attribute 'resources'` — and it raises at
# RUN time, after the config is assembled and the credential has already been
# used, so the closure looks correct right up until it lands nothing. Index the
# list by resource name instead.
#
# `rest_api_source` DOES return a DltSource whose `.resources` mapping is real.
# Pick one and stay with it; the two names differ by one word and not by shape.
resources = {r.name: r for r in rest_api_resources(config)}
# fetched_models again, matching the resource list above. Everything else promised —
# derived models (Step 3a) and landed reference data — reaches the same
# `pipeline.run` as `@dlt.resource` generators appended to this same list. They
# are landed in the one run, just not fetched over HTTP.
readers = []
for model in fetched_models:
    table_name = duckdb.model_tables[model]
    # Flatten nested selections first - see "Flatten fetched rows before they
    # reach the port". A nested dict silently becomes `state__name`-style
    # columns the read-back assert cannot catch.
    resource = resources[model]
    flatten = FLATTENERS.get(model)
    if flatten is not None:
        resource = resource.add_map(flatten)
    readers.append(resource.with_name(table_name))
pipeline.run(readers, write_disposition="replace")
```

### Deriving from a fetched source

`derived-models.md` § "Reading the sources yourself" tells you to re-read the
base export with stdlib `csv` because dlt's reader "streams straight to the
destination and cannot hand rows back to Python". That reasoning is correct and
it applies here too — but the remedy does not, because **a fetched model has no
`data/` directory to re-read**. There is no second copy of what the API
returned. Land it and it is in DuckDB; don't and it is nowhere.

So a derived model computed from fetched rows needs the rows captured on their
way past. Tee them in the flattener you already have:

```python
# Every fetched row is captured here on its way to the port. dlt streams a
# resource straight to the destination and cannot hand rows back to Python, and
# a fetched model has no data/ directory to re-read - so the flattener tees each
# row into these lists as it flattens it.
_FETCHED: dict[str, list[dict[str, Any]]] = {model: [] for model in API_MODELS}


def _flatten_issue(node: dict[str, Any]) -> dict[str, Any]:
    row = {...}                      # flat scalars, as above
    _FETCHED["issues_landed"].append(row)
    return row
```

Then land the derived models in a **second** `pipeline.run(...)`, after the
fetch run has returned:

```python
pipeline.run(readers, write_disposition="replace")     # fetch + reference data

rows = _FETCHED["issues_landed"]                       # now fully populated
derived = _derive(rows)
_assert_derived(derived, rows)                         # Step 3b, complete set

pipeline.run(                                          # second replace run
    [_rows_resource(derived, duckdb.model_tables["open_tickets"])],
    write_disposition="replace",
)
```

**This is a deliberate exception to "same list, same run"**, and the reason that
rule exists is preserved rather than waived: `replace` applies per table, so the
first run's tables are untouched, a rerun is still idempotent, and the Step-3b
asserts still run over the complete derived set before a single row is yielded.
Record it as a `concession.other` naming the two alternatives below, so a
reviewer sees the choice rather than inferring it.

Both obvious alternatives are wrong here:

- **Fetch by hand** (`requests`/`urllib` in `transform/main.py`) so the rows are
  already in Python. This is ingestion by hand — § "Two ways a probe lies" and
  the reach gate both forbid it, and it puts an HTTP client on the ingestion
  path where only the dlt connector belongs.
- **Append the derived resources to the same `readers` list.** dlt gives no
  guarantee that the fetched resources are exhausted before a later resource in
  the same list is pulled, so `_FETCHED` may be partial when the derived
  generator runs. That produces a derived model computed from some of the rows,
  with asserts that pass because they only see the same partial set — the
  failure mode Step 3b exists to prevent.

`db-source` has the same shape and the same remedy.

### Landed reference data in an API closure

`derivation-plan.md` and `llm-judgments.md` tell you to write
`data/<name>/<name>.csv`, add the model to `BASE_MODELS`, and let it flow
through the reader loop with "no special casing anywhere". **Step 1 still
applies; the reader-loop half does not.** The loop above iterates
`fetched_models` — the endpoint-filtered subset of `API_MODELS`, not
`BASE_MODELS` — so a reference model added to `BASE_MODELS` and nothing else is
neither fetched nor read, and drops out silently until the read-back assert
reports a table that never landed.

**Still write the CSV, at `data/<name>/<name>.csv`.** "No connector artifact"
below, under `spec.py` / `infra-profile.yaml` diffs, means this connector brings
no *export* of its own — it does not mean the closure may not carry one. The supervisor materializes `transform/` and `data/` into the
pinned snapshot for every closure, by path and not by connector type, so a
`data/` tree an api closure authors itself travels with it. Do **not** inline
the rows as a literal in the transform instead: `SKILL.md`'s "Reference data is
landed, never hardcoded" invariant forbids exactly that, and it does not relax
by connector.

The rows reach the port differently, **and so do their types** — see the cast
rule below; this is not a pure change of route. Read the CSV yourself with
stdlib `csv` and yield them as your own resource, appended to the same `readers`
list before the one `pipeline.run(...)` — the form is in `derived-models.md`
§ "The resource template":

```python
import csv, os
from pathlib import Path

# Anchor on the execution root, not the working directory. The local Python
# compute driver exports NXD_TRANSFORM_ROOT on every desktop transform run,
# unconditionally, set to the materialized closure root; it also chdir's there,
# so a relative open happens to work — but the working directory is an
# implementation detail of how the child is spawned, and the env var is the
# stated contract. Never an authoring-checkout absolute path: it escapes the
# pinned snapshot and fails.
#
# Desktop only. The k8s compute driver does not export it, which is fine here —
# this skill emits desktop closures — but do not carry this line into a k8s
# data product.
root = Path(os.environ["NXD_TRANSFORM_ROOT"])

# Read it yourself with stdlib csv: dlt's filesystem reader streams straight to
# the destination and cannot hand rows back to Python (same rule as Step 3a).
# sorted() because glob order is filesystem-dependent.
decision_rows: list[dict[str, str]] = []
for path in sorted((root / "data" / "nxd_decisions").glob("*.csv")):
    with path.open(newline="", encoding="utf-8") as handle:
        decision_rows.extend(csv.DictReader(handle))

@dlt.resource(name=duckdb.model_tables["nxd_decisions"])
def nxd_decisions_resource() -> Iterator[dict[str, Any]]:
    yield from decision_rows          # flat scalar dicts only

readers.append(nxd_decisions_resource())
```

`secrets["csv_source"]` is **not** available here — that key is supplied by the
`csv-source` service, which an api-source closure does not name in
`.secrets([...])`. `NXD_TRANSFORM_ROOT` is the anchor that does not depend on a
connector service being present.

**Cast the measures — this read does not type them for you.** A file
connector's `read_csv()` infers column types; `csv.DictReader` yields strings
for every column, so a reference model landed this way reaches DuckDB as
VARCHAR throughout. That is invisible for an all-`string()` model like
`nxd_decisions`, and wrong the moment the model promises a number — the
`fx_rates(currency, month, rate)` case `derivation-plan.md` routes down this
same path, or a judgement model whose `score` is `field(number(), ...)` under an
`Agg.AVG`. `derived-models.md` § "Reading the sources yourself" is the rule:
convert measures, not identifiers; `Decimal` for money, cast to `float` only in
the final dict.

It is still a base model everywhere else — promised in `spec.py`, declared in
`models.py`, listed in `BASE_MODELS` and `PHYSICAL_MODELS`. What changes is how
the rows reach the port, because on this connector there is no reader loop to
carry them — and, because you are now reading the file yourself, their types.

**What moves is the FETCHED models, not this one.** The self-check enforces
`BASE_MODELS == the data/ directory listing` whenever `data/` exists. The landed
reference model has a `data/` directory and belongs in `BASE_MODELS`; the models
fetched over HTTP have none, so leaving them in `BASE_MODELS` breaks that
equality:

```
struct.base_models_vs_data_dirs: BASE_MODELS ['linear_comments_landed',
'linear_issues_landed', 'nxd_decisions', 'scoring_rubric',
'verdict_thresholds'] != data/ directories ['nxd_decisions', 'scoring_rubric',
'verdict_thresholds']
```

Use three tuples, with `PHYSICAL_MODELS` written as a literal (the structural
check cannot evaluate a computed one and reports it `unverified`):

```python
# Fetched over HTTP; no data/ directory of their own.
API_MODELS = ("linear_issues_landed", "linear_comments_landed")
# Landed from data/<name>/. This tuple must equal the data/ listing exactly —
# all three directories from the finding above, none missing.
BASE_MODELS = ("nxd_decisions", "scoring_rubric", "verdict_thresholds")
# Computed in Python from the rows above.
DERIVED_MODELS = ("open_tickets",)
# A literal: the structural check cannot evaluate a computed tuple and reports
# it `unverified`. The assert keeps the literal honest as the others change.
PHYSICAL_MODELS = (
    "linear_issues_landed",
    "linear_comments_landed",
    "nxd_decisions",
    "scoring_rubric",
    "verdict_thresholds",
    "open_tickets",
)
assert set(PHYSICAL_MODELS) == set(API_MODELS + BASE_MODELS + DERIVED_MODELS)
```

That is the same closure the finding above came from, with the fetched models
moved out of `BASE_MODELS` and nothing dropped: `BASE_MODELS` is now byte-equal
to the `data/` listing the finding named.

At transform time, filter `API_MODELS` — never `BASE_MODELS` — into a subset
under its own name, as the template above does:

```python
fetched_models = tuple(m for m in API_MODELS if f"endpoint_{m}" in secrets)
```

Build the `RESTAPIConfig` from `secrets` at runtime — never
hard-code a base URL or credential in the transform source. The `auth_type`
dispatch assembles dlt's structured `auth` dict from the flat secret
fields, the same way `_build_connection_string` in `database-source.md`
assembles a connection string from the flat `host` / `port` / `user` /
`password` entries in `secrets` — never pass a flat secret value straight
through as `auth`.

**Keep the dispatch, and end it with an explicit `elif auth_type is not None:
raise`.** Writing only
the branch this closure happens to need — `client_config["auth"] = {"type":
"bearer", ...}` with no `auth_type` read at all — is the natural shortcut, and
it is wrong for a reason that is invisible on the day it is written: the profile
still carries `auth_type` as an attribute, so the closure claims to be
configured by it while ignoring it. Change the profile to `http_basic` and the
transform keeps sending a bearer header built from a field that is now absent —
a `KeyError` if you are lucky, and a silent 401 loop against the wrong scheme if
you are not. The credential is the one input a closure cannot re-derive, so the
branch that reads it must fail loudly on a value it does not handle:

```python
elif auth_type is not None:
    raise ValueError(
        f"unsupported auth_type {auth_type!r} in secrets — "
        f"add a branch above, or fix the infra-profile attribute"
    )
```

**`auth_type is None` is the one value that must NOT raise.** It means the
profile configures no authentication, which is why the template reads the field
with `secrets.get("auth_type")` and why `secrets` carries
`auth_type` *only when the API requires authentication* (see the attributes list
above). A bare `else: raise` fails every unauthenticated api-source closure at
transform time, with a message pointing the author at a profile attribute that is
legitimately absent — so the guard is `elif auth_type is not None`, matching the
template. Appending a bare `else:` after that `elif` is the same bug wearing a
different shape: it raises on exactly the case the `elif` exists to let through.

An `auth_type` the transform does not handle is a closure that cannot
authenticate. Discovering that as a raise at transform time beats discovering it
as an HTTP 401 whose body is someone else's error page.
**Never open a file to find an endpoint path.** Every value the ingestion body
needs — base URL, auth fields, endpoint paths — arrives in `secrets`. A helper
that reads a sidecar file beside the transform (`_load_api_source_endpoints` and
friends) is not a dlt or stdlib function, has to be hand-written, and is
reaching for a channel this closure does not use; § "Why the endpoints live in
the profile" above has the reasoning.

## `requirements.txt`

Base pins unchanged. `rest_api_resources` needs no additional pin beyond the
base `dlt[duckdb]==1.28.2` — confirmed by import against the pinned version in
the desktop runtime, not inferred from the dlt docs. If the chosen `auth_type`
(e.g. `oauth2_client_credentials`) turns out to need an extra dependency,
add it explicitly rather than assuming it's already covered.

## `spec.py` / `infra-profile.yaml` diffs

- `spec.py`: `_api = "/infra-profile/desktop-local#/services/api-source"`,
  `.secrets([_api])`. Everything else (`.promise`, `.model`,
  `.port("duckdb", ...)`, no `.semantic_tools()`) is identical to the CSV
  template.
- `infra-profile.yaml`: third service named `api-source` (same driver
  `nxd:generic-secrets:1.0.0`), with its `attributes` populated with the
  live payload. No-auth example (a public API needing only a base URL):

  ```yaml
    - name: api-source
      driver: nxd:generic-secrets:1.0.0
      attributes:
        - key: base_url
          value: https://aidevboard.com/api/v1
          public: true
        - key: endpoint_checks
          value: /v1/checks
          public: true
        - key: endpoint_monitors
          value: /v1/monitors
          public: true
  ```

  One `endpoint_<model>` per API-backed model, `<model>` byte-identical to the
  name in `PHYSICAL_MODELS` (the naming invariant reaches this attribute key,
  not a companion file). Always `public: true` — an endpoint path is topology,
  and redacting it would hand the recipient of an export a closure they cannot
  run without asking what the paths were.

  When the API needs authentication, add `auth_type` plus that type's own
  flat fields alongside `base_url` — never nest a whole credential under
  one attribute's value. Bearer-token example (topology `public: true`, the
  secret `public: false`):

  ```yaml
    - name: api-source
      driver: nxd:generic-secrets:1.0.0
      attributes:
        - key: base_url
          value: https://aidevboard.com/api/v1
          public: true
        - key: endpoint_checks
          value: /v1/checks
          public: true
        - key: endpoint_monitors
          value: /v1/monitors
          public: true
        - key: auth_type
          value: bearer
          public: true
        - key: auth_token
          value: <the live bearer token the user supplied>
          public: false
        # Optional: include only when this bearer flow supports refresh.
        - key: auth_refresh_path
          value: /auth/refresh
          public: true
  ```

  When the API also requires a non-secret header, add one `header_*` attribute
  per header alongside these (`public: true` — see **Custom request headers**):

  ```yaml
        - key: header_user_agent
          value: acme-analytics/1.0
          public: true
  ```

  For the other three types, add that type's fields from the table in
  Credential handling instead of `auth_token` (e.g. `auth_username` +
  `auth_password` for `http_basic`) — one attribute per field, each marked
  `public:` per the sensitivity classification (secrets `false`, non-secret
  config like `auth_key_name`/`auth_key_location` `true`).

  **Required probe; no endpoint-map companion** — every api-source closure
  includes the closure-local `connectivity_check.py` probe. Its endpoint map
  lives in these attributes, not in a companion file; everything the transform
  reads at run time is an attribute on the `api-source` service.

  **Every api-source closure using the desktop-supervisor workflow-v2 path still needs
  `csv-source-path` and a non-empty `data/` tree before the desktop supervisor
  will stage an api-only closure.** This is a staging preflight requirement, not an
  api-source contract: the API connector does not read the placeholder as
  source data. When this workflow-v2 preflight is exercised, it pins the
  directory before the connector runtime runs, so the closure must satisfy it
  even though it has no landed reference data.

  In two consecutive live `crm-pipeline` runs — one hand-authored and one
  through this skill — the supervisor spent a check cycle on this staging
  preflight before any Python ran. If the kernel stops pinning a CSV directory
  for api-only closures, remove this block.

  Ship the `csv-source-path` file holding the relative export root, exactly as a
  CSV closure does, and put at least one `.csv` under that root. Three findings
  arrive in sequence if you supply less, none of them mentioning that a *file*
  is required, and all of them before any Python runs — so they read as spec or
  manifest problems:

  ```
  structure/definition_files_missing:
    stage the kernel definition files: snapshot missing csv-source-path
  structure/pin_failed:
    definition runtime directory missing: <closure>/data
  publish/csv_source_empty:
    the pinned CSV source directory contains no .csv file
  ```

  An empty `csv-source-path` file is its own finding (`structure/csv_source_invalid`),
  so blanking it is not a way out. For a closure with no landed reference data,
  use a **flat** staging placeholder, not a model-shaped directory:

  ```sh
  printf 'data\n' > csv-source-path
  mkdir -p data
  printf 'placeholder\n' > data/api_source_placeholder.csv
  ```

  A flat file keeps `data/` from declaring `_unused` (or any other name) as a
  base model during Phase A; `data/<model>/` would be interpreted as a real
  source model and fail the base-model/data-directory check. None of this
  declares a CSV connector: the closure still names only `api-source` in
  `.secrets([...])`, and there is still no `csv-source` service in the profile.

  > This documents a supervisor staging requirement that is separate from the
  > api-source contract, not a design intent. If the kernel stops pinning a CSV
  > directory for api-only closures, delete this block rather than the
  > placeholder advice.

  For 2+ API sources, add one labeled service per
  instance instead (`api-source-<label>` / label-prefixed attribute keys such
  as `secrets["orders_base_url"]` and `secrets["orders_endpoint_<model>"]`) —
  see `reference/multi-source.md`.

## Self-check (connectivity smoke test)

A REST API connector has two separate checks; keep their results separate. The
payload-inspection gate above owns the ordering and the no-credentials
reporting; this section describes the probe's relationship to the shipped
offline self-check.

1. The shipped `self_check.py` provides offline closure checks. Its Phase B
   cannot authenticate an API source, because the harness invokes `ingest()`
   with an empty secrets map.
2. The authenticated connectivity smoke test is a real authoring-time probe.
   It is the closure-local `connectivity_check.py` written first under the
   gate above, not a manual `curl`, a fixture, or the `self_check.py` dry run.
   With credentials it must run successfully before the first build, making
   one bounded request per configured resource and asserting a parseable
   response matching the expected shape — not an exact fixture count, since
   remote data is not static. Missing NXD, dlt, DuckDB, or generated-transform
   packages do not excuse it: the probe uses only the standard library.

The closure is complete only after that authenticated probe succeeds. When
credentials are unavailable, retain the probe but report payload inspection
and connectivity as **not run** and **unverified**; structural checks still
run, while source validation and the complete happy path remain unverified.
A manually issued `curl` or other exploratory fetch is useful for diagnosis
but does **not** substitute for executing the closure-local probe. Apply the
redaction rules below to every failure message.

**Phase B of `self_check.py` cannot pass for this connector, and that is not a
defect to code around.** Do not confuse that offline limitation with the
authenticated `connectivity_check.py` above. Do not add a profile-reading
fallback to `self_check.py` to make Phase B green — that reintroduces the
sidecar channel this file spends a section rejecting. Report Phase B as **not runnable**.
In a workflow-v2-capable enrolled runtime, the supervisor's returned
`start_requirement` action performs the authoritative validation before
`start_run`; do not use a local check as a construction fallback.
`check_data_product` remains a read-only verification path: it pins and compiles
the real closure under the supervisor's own interpreter, which is stronger
evidence than the dry run it complements. It does not replace workflow-v2
capture, review, or admission.

### Two ways a probe lies

**Anchor the probe on its own directory, never the caller's cwd.** The
probe's initial non-secret request configuration comes from the settled plan,
so it does not need a later closure artifact in order to run. If it reads the
profile after that profile has been authored, resolve it from the probe's own
directory; never open `"infra-profile.yaml"` relative to the caller's working
directory. Running the probe by absolute path must not produce
`[Errno 2] No such file or directory: 'infra-profile.yaml'` merely because the
author's cwd is elsewhere:

```python
_CLOSURE = Path(__file__).resolve().parent
profile_path = _CLOSURE / "infra-profile.yaml"
```

**Never lead with OK when the response was empty.** A reachable endpoint that
accepts the credential and returns zero rows is a FAILED check — the filter is
wrong — and it is the single most likely thing to be wrong at this step. A
probe that prints `OK — 0 node(s)` and appends the warning below it will be read
as a pass, the build will be launched, and the failure resurfaces minutes later
as an empty promised model. Print the verdict first:

```python
if not nodes:
    print(f"{model}: NO ROWS — the endpoint answered and the credential is "
          f"accepted, but the filter matched nothing.")
    ok = False
    continue
print(f"{model}: OK — {len(nodes)} node(s) on the first page, ...")
```

The filter itself is the usual culprit, and equality comparators are stricter
than they read: Linear's `eqIgnoreCase` is exact-match-ignoring-case, so a
project displayed as `Nexty Pocket` is not matched by `pocket`. When a probe
returns nothing, ask the API what values it does hold — list the projects, the
teams, the accounts — before touching the credential.

**The probe is a standalone script beside the closure — never inside
`transform/`.** It runs once, at authoring time, from the author's shell. A
probe living in `transform/main.py` (or any `transform/*.py`) instead runs on
every materialization the supervisor performs, doubling the request count
against an upstream you were careful to rate-limit, and it puts an HTTP client
on the ingestion path where the only thing that should reach the wire is the
dlt connector. Keep `transform/` free of `requests` / `urllib.request` /
`httpx` entirely: if something under `transform/` is fetching, that is
ingestion by hand, whatever it is named. (`urllib.parse` is fine — it is string
manipulation and touches no socket.)

**Never let a probe's traceback reach the transcript unredacted.** This is a
sharper risk than the database case: `requests` puts the full URL in
`HTTPError`/`ConnectionError` messages, so an API keyed by query string
(`?api_key=…`) or basic auth leaks the live credential into chat the moment a
probe fails — and chat is the one place the user cannot remediate. Redact by
substituting every value that is not known-public topology, never by
pattern-matching:

```python
# The same exemption list as database-source.md's _redact — one pattern, two
# call sites. It matters here too: `secrets` is the whole flat map, so a closure
# naming both an api-source and a db-source hands this probe the db's host, port
# and schema as well.
_PUBLIC_SUFFIXES = ("host", "port", "database", "schema",
                    "base_url", "auth_type", "region")
# `header_*` is deliberately NOT exempt. Those values are non-secret by
# convention, but the exemption list is what stands between a mis-filed
# credential and the transcript — and `header_authorization` holding a token is
# exactly the mis-filing the convention warns about. A redacted User-Agent in an
# error message costs nothing; the reverse mistake cannot be taken back.
#
# Endpoint keys are `endpoint_<model>` (or `<label>_endpoint_<model>`), so the
# suffix rule below cannot reach them — the model name is the tail, and it is
# author-chosen and therefore unbounded. Match the segment instead. Leaving them
# out is not fail-safe here, it is just unhelpful: `.replace()` would rewrite the
# path out of the middle of the failing URL, so a 404 on the wrong endpoint —
# the single most likely thing to go wrong at this step — would print as
# `https://api.example.com<redacted>` and name neither the endpoint nor the model.
_PUBLIC_SEGMENTS = ("endpoint_",)

def _redact(exc: BaseException, secrets: dict) -> str:
    text = str(exc)
    # Redact by DEFAULT and exempt public topology, rather than listing secret
    # names: the credential key varies per API (`api_key`, `token`, `bearer`,
    # whatever this one calls it), so any fixed list misses the one that matters.
    # Exempting base_url is what keeps the failing URL readable while the
    # credential inside its query string still goes.
    for key, value in secrets.items():
        if not value:
            continue
        if any(key == p or key.endswith(f"_{p}") for p in _PUBLIC_SUFFIXES):
            continue
        if any(seg in key for seg in _PUBLIC_SEGMENTS):
            continue
        text = text.replace(str(value), "<redacted>")
    return text

try:
    ...  # the bounded GET
except Exception as exc:
    raise SystemExit(f"connectivity check failed: {_redact(exc, secrets)}") from None
```

`from None` is mandatory: without it Python chains the original exception as
`__context__` and re-prints it in full, defeating the redaction. The same rule
governs anything you improvise — never `print()` a request URL or a response
header dump, and never paste a raw traceback from a failed call into the
answer.
