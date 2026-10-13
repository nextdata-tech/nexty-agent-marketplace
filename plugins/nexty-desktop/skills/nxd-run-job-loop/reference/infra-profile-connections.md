# Native infra-profile connections

## Contents

- [Capability and preparation](#capability-and-preparation)
- [Connect and reuse](#connect-and-reuse)
- [Lifecycle and verification](#lifecycle-and-verification)
- [Recovery and handoff](#recovery-and-handoff)

## Capability and preparation

Read the live `get_workflow_capabilities` result and MCP tool catalog. Use this
route only when `infra_profile_connections.native_entry` is true and the four
connection tools are available. An unavailable or older runtime does not gain
native entry from a skill instruction. On unsupported hosts, report that the
native connection is unavailable and give the supported Desktop update or
connection action shown by the runtime. Do not suggest a chat key, shell
environment, YAML credential, or connector substitute.

Author the `infra-profile.yaml` service with its real, nonsecret destination,
driver, and scope. Every inline `attributes` entry on a service intended for a
Desktop-local reference must be explicitly `public: true`. Do not add an empty
or placeholder secret entry. In the final executable closure, keep the
`spec.py` service reference; the manifest, deployment, and profile must resolve
that service together.

When the source and all nonsecret service configuration are settled, this
service may first be declared in a dedicated source-preparation sibling of the
executable closure, never its parent or ancestor, before approval of the full
build plan. For example, use `…/nxd-jobs/<workflow>/source-preparation/` beside
`…/nxd-jobs/<workflow>/closure/`; do not use their shared parent as the
preparation root. This sibling contains only public topology and the opaque
reference, never executable closure artifacts. Native entry reads
that profile service; it needs no compiled spec, manifest, prepared workflow,
or plan consent. Do not create executable closure artifacts for this access
step. If the public destination or scope is still unsettled, ask for the
missing nonsecret detail and defer the prompt.

The agent supplies one to eight distinct scalar field descriptors, each
`{key, entry}`. Entry is `masked_nonempty`, `masked_allow_empty`, or
`literal_empty`. `literal_empty` supplies an intentionally present empty
string without opening a field prompt. Use it only when the source contract
requires that exact value. The other two modes collect an exact value through
the native masked UI. Keep spaces and quotes unchanged. Field keys are
machine-facing connector names, never the primary explanation to the user.
Keep authentication field names and wire format in agent-owned connector work;
give the user the secure prompt as the next action without narrating those
details unless they ask for a technical explanation.
For a Linear personal API key, set public topology `auth_type: api_key`,
`auth_key_name: Authorization`, and `auth_key_location: header`; collect the
private `auth_api_key` with `masked_nonempty`. This sends
`Authorization: <key>` without a `Bearer` prefix. Linear OAuth access tokens
use bearer auth instead, with `auth_token: masked_nonempty`. Ashby Basic auth
uses `auth_username: masked_nonempty` and `auth_password: literal_empty`.
These are examples of the general scalar profile field mechanism, including
database and other infra services.

## Connect and reuse

Call `inspect_infra_profile_connection` with
`{"selector":"service_at_root","definition_root":"...","service_name":"..."}` before creating a
connection. It discovers compatible local metadata without reading the source
credential and may return multiple candidates. Reuse a uniquely compatible
active reference. Resolve ambiguity with a specific connection choice or a
fresh connection; never guess. Begin has the same duplicate guard.

After generation, inspect again using the final closure root and declared
service. Reuse the early reference only when the returned metadata uniquely
matches. Zero compatible records means native entry is needed for that final
service; multiple candidates need an explicit choice, never a guess. The
generated profile must preserve the early service's canonical profile name and
domain, mesh, service name, driver, and every public attribute, then attach
its opaque reference in the same active Desktop data folder. A change to any
of that bound identity requires a fresh connection. Neither matching metadata
nor a stored reference proves remote access.

If needed, call `begin_infra_profile_connection` with `definition_root`,
`service_name`, `fields`, and a short purpose. No credential value belongs in
the MCP arguments. Inspect the returned `request_id` until the native prompt
and store operation settles. A successful connection supplies an opaque
`nxd-desktop-keychain:v1:` reference; attach it as the service's single
`attributesSecretRef`. No value, Keychain account, local secret path, or
environment name belongs in the profile, transform source, chat, logs, or
blueprint. Never invent a reference or copy one from another data directory.

## Lifecycle and verification

The request can report `queued`, `awaiting_input`, `storing`,
`available_stored_unverified`, `cancelled`, `failed`, `uncertain`,
`disconnected`, or `cleanup_pending`. Inspect an uncertain request with
`{"selector":"request_id","request_id":"..."}` before any
retry; a timeout can occur after a store write. Do not start a second request
to work around uncertainty. If inspection still reports `uncertain` and the
user chooses to retry the native entry, call `begin_infra_profile_connection`
with the same `definition_root`, `service_name`, `fields`, and `purpose`, plus
`retry_uncertain: true`; the supervisor reconciles or retries the same
request. Never use that option for an ordinary new connection. A connection's public state is
`stored_unverified`; storage says nothing about remote permission or validity.
Submitting the native prompt authorizes local credential storage only; it does
not approve the plan, source execution, a build, or paid scoring.
The first release has no direct probe for an infra-profile source credential.
Only a successful admitted source execution shows that a particular fetch
worked at that time. Keep
native connection creation separate from source use, workflow consent, mapper
approval, and model spending authority.

A stored reference can still fail when macOS is asked to read it. The
`source_connection/protected_access_timeout` code remains the bounded
protected-access timeout and may include an optional `reason`;
`source_connection/state_mac_key_unavailable` identifies access to protected
saved workflow state and may also include an optional `reason`. Apply the
shared plain-language mapping in
[failure-handling.md](failure-handling.md#keychain-credential-and-saved-state-access-outcomes).
Credential-read permission, protected-state access, and spending approval are
separate. A timeout does not establish denial, and a running SecurityAgent
process does not prove a prompt is visible. A user's confirmation that a macOS
request was completed or dismissed triggers one re-check of the existing
supervisor action; without confirmation, one re-check after a short wait is
allowed. If it is still `pending`, report that and stop. Retry only when
`retryable: true`, with the same request and revision. Inspect an uncertain
native-entry request before retrying it; never open a duplicate entry window.

Use `rotate_infra_profile_connection` when replacing fields on the same
destination. A changed endpoint, database, or scope needs a fresh native
binding. Use `disconnect_infra_profile_connection` only on an explicit user
request to revoke that local connection. Inspect the resulting state if a
rotation or deletion is uncertain. A scratch validation may be invalidated by
rotation or disconnection, so let the supervisor revalidate before admission.

Static Python checks can confirm only syntax, explicit public topology, and
recognizable field references; they cannot read authenticated Desktop metadata
or establish that a secret is present. Interpret a `deferred` result as
requiring supervisor validation. The supervisor's structural check can inspect
metadata binding without reading the source secret; admitted validation checks
the selected secret payload and performs the actual source use.

## Recovery and handoff

In a later chat, inspect by the current definition root and service name to
find a compatible local connection. A copied profile, changed destination, or
different data directory may need a new native connection. For an exported
product, preserve the opaque reference and the non-authoritative field labels
but report `reconnect_on_destination`; the recipient connects their own
credential through native entry before an admitted rebuild. An export does not
carry Keychain values or prove the recipient can use the old reference.
