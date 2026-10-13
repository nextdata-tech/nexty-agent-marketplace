# Materializing a source faithfully

How each kind of in-scope source is landed at Step 1, before inference. The
default is a byte-exact copy of the supplied file. The sole exception is the
declared projection below for personal-data columns the approved blueprint does
not need.

## Contents

- [Fidelity here; derivation downstream](#fidelity-here-derivation-downstream)
- [Attached or workspace file](#attached-or-workspace-file)
- [Privacy projection for unused personal-data columns](#privacy-projection-for-unused-personal-data-columns)
- [Pasted table](#pasted-table)
- [Database connection](#database-connection)
- [REST API](#rest-api)
- [Host path handoff](#host-path-handoff)

## Fidelity here; derivation downstream

An in-scope file is an exact byte-for-byte copy by default. Cleaning, dedup,
amortization, currency normalization, reclassification and regrain belong only
in derived models downstream of the landed source; never edit the user's
original. The privacy projection below is the only exception to exact-copy
landing, and it preserves row order and every retained field value unchanged.

Materialization is also **gated**: if the request supplied a procedure with a
result-changing gap, copying a source into a closure waits for the user's reply
to the `dp-blueprint.md` read-back. Reading a source is always allowed.

With 2+ sources, repeat the matching section once per source and tag every
artifact with that source's label.

## Attached or workspace file

Before asking the user for a path or claiming that no source exists, inspect all
supplied attachments, declared workspace artifacts, source profiles, and
reference files. Resolve the in-scope source from those declared inputs when it
is present; ask only about a real missing or ambiguous source. Make an exact
byte-for-byte copy into the generated connector export unless the narrow
personal-data projection below applies. Do not modify the user's original.

## Privacy projection for unused personal-data columns

Byte-exact landing remains mandatory by default. For an in-scope file only, use
a column projection when the source contains personal-data columns and the
approved blueprint does not need them. Personal data includes direct
identifiers (names, email addresses, phone numbers, street addresses, national
IDs, dates of birth) and compensation fields such as salary. Keep every other
source column and every row, in its original order. Retained CSV cell values
must remain identical UTF-8 strings: do not trim, coerce, normalize, or derive
from them during projection.

Before projecting, list the needed columns from the approved blueprint. Declare
that exact output `allow_columns` list in the blueprint's `Inputs` section, and
list each omitted source column with its reason. Only personal-data columns
absent from the approved needed-column list may be dropped; retain all
non-personal source columns. For CSV, use `project_csv_columns` from
`nxd-generate-data-product/scripts/source_contract.py`, then run
`verify_csv_landing` while the original is still available. The verifier
checks the declared allow-list, the exact source-header partition, row count
and order, retained cell values, and both recorded SHA-256 digests. Save its
returned evidence as sorted-key UTF-8 JSON in
`source-projection-evidence.json` beside the closure; do not copy the original
source into the closure just to make later verification possible. A format
without an equivalent deterministic projector and verifier must not silently
fall back to copying unneeded personal data.

Never inspect, profile, print, log, quote, or query values in a column marked
for dropping, except to compute an explicitly approved derived key in memory.
The projection step may parse rows internally, but must emit only the declared
columns, and its diagnostics must not contain cell values. Tell the user which
columns were omitted and why, without quoting their contents. If the approved
blueprint needs a personal-data column, pause and raise that need for an
explicit user decision; do not retain it by default. If a needed result depends
on person continuity, declare a derived output, its input columns, method, and
non-secret key reference and domain in the blueprint. For CSV, declare the
`hmac-sha256:v1` derivation and pass the referenced key through
`derivation_keys` to `project_csv_columns`; the helper reads only the declared
input columns to compute the key. Use `hmac_identifier` for the canonical
domain-separated HMAC, keep its stable key in the approved secret slot, and
pass the same `derivation_keys` to `verify_csv_landing` so it recomputes the key
from the original in memory. The evidence records the derivation method,
inputs, key reference, domain, and both digests, never the raw identifier or
key. The raw identifier must be listed as dropped. Never land, echo, or query
the raw identifier.

CSV evidence has `mode: column_projection`, `format: csv`, `allow_columns`,
`required_columns`, `personal_data_columns`, `drop_reasons`, `derived_columns`,
`personal_data_decision`, `reason`, `row_count`, `source_sha256`, and
`landed_sha256`. `derived_columns` is empty when there is no derivation;
`personal_data_decision` is null unless a personal-data source column is
explicitly retained after a user decision. The exact-copy default does not
authorize removing columns: an undeclared difference from the source is a
failure.

## Pasted table

Materialize every supplied header and value faithfully in a separate CSV export.
Do not add columns, coerce values, deduplicate rows, or invent an identifier;
treat prose before or after the table as narrative, not a data row.

A header followed by consistently shaped rows is sufficient evidence to proceed
— don't claim a value was spliced, truncated, or missing merely because the chat
renderer visually wraps the prompt. Ask only on a real structural ambiguity: a
row with a different field count, or an unparseable value.

## Database connection

Record host/port/database/schema and the table(s)/view(s) the user explicitly
named — plus the credential **slot names** the connection needs (`user`,
`password`), not their values. Treat access as **read-only** and never invent a
table name.

## REST API

Record the base URL, auth scheme, the endpoint(s)/resource(s) in scope, a sample
response shape if available, and any known pagination. When auth is required,
record the credential **slot names** for that scheme — per
`nxd-generate-data-product`'s `reference/api-source.md`, e.g. `auth_token` for
`bearer`, or `auth_username` + `auth_password` for `http_basic` — not their
values. Treat access as **read-only** and never fabricate an endpoint.

For both kinds: you gather the *shape* of the credential here. The value itself
reaches the closure by one of the routes in **Where credentials land**, and
must never be solicited in chat. Connector credential slot names come from the
declared auth scheme; they are not a request for the user's env-variable name.

A requested live in-product source must use its generated connector/fetch in
the admitted product. A chat-connector fetch staged as a file is not that
behavior and does not provide fresh in-product fetching. Never offer or solicit
a chat-connector fetch or file substitute as an access workaround, including a
new publication. Use it only after an independent user request to change source
scope, and disclose its snapshot boundary; an agent-proposed choice is not that request.

## Where credentials land

A database, API, storage, compute, or governance profile can need one or more
scalar secret fields. Author its service with only explicitly public topology
in `attributes`; keep credential values out of chat, `dp-blueprint.md`, agent
prompts, and authored files. Read
[`infra-profile-connections.md`](infra-profile-connections.md) for the native
connection sequence and the distinction between stored and usable access.
Per-type field shape is in `nxd-generate-data-product`'s connector references.

Before asking for source access, check `get_workflow_capabilities` and the live
MCP catalog. When `infra_profile_connections.native_entry` is true, author the
nonsecret service in a dedicated source-preparation sibling of the executable
closure, never its parent or ancestor, once its public destination and scope
are settled. For example, `…/nxd-jobs/<workflow>/source-preparation/` is beside
`…/nxd-jobs/<workflow>/closure/`, not their shared parent. Only public topology
and the opaque reference belong in this sibling; no executable artifacts. This separate source step
can happen before the complete build plan is approved, including when the user
asks where to enter a key. Inspect reusable connections for that service, then
use `begin_infra_profile_connection` only if none is uniquely compatible. The
user enters each field in a masked native prompt. Attach only the returned
opaque `attributesSecretRef` to the service. Do not generate an executable
closure or fetch source data to collect the credential. Preserve the service's
bound public identity when the approved plan later generates the closure, and
inspect compatibility at that final root before reusing the reference. A
stored connection is `stored_unverified`: admitted source execution must still
fetch successfully. A changed destination or scope needs a new native binding.
Rotation and disconnection are explicit connection actions; native storage
approves neither a workflow nor spending.

For native source-connection entry, never ask for an env-variable name or give
commands, OS setup, or restart instructions. Keychain access recovery follows
the canonical reason-to-action mapping in
[failure-handling.md](failure-handling.md#keychain-credential-and-saved-state-access-outcomes).
If the capability is unavailable,
report the source-access blocker and an evidenced action to connect an updated
Desktop runtime. For `state_unavailable`, report that connection state could
not be read or stored and keep this source step blocked. Do not infer a login
requirement, tell the user to open an app, or claim waiting cannot help. Resume
the existing request after actual recovery only when `retryable: true`; do not
ask for broader approval just to repeat this access step. Typed
`source_connection/protected_access_timeout` remains a bounded access timeout
and may carry a `reason`; `source_connection/state_mac_key_unavailable` is a
separate protected-workflow-state outcome and may also carry a `reason`. Use
the reason mapping in [failure-handling.md](failure-handling.md#keychain-credential-and-saved-state-access-outcomes).
When the user confirms they completed or dismissed a macOS request, re-check
the same supervisor action once; without confirmation, one re-check after a
short wait is allowed. If it is still `pending`, report and stop. Retry only
when `retryable: true`, with the same request and revision. Inspect an uncertain
native-entry request before any retry; do not start another request or open a
duplicate entry window. Never
request a YAML credential edit, a file
containing a key, or a value in chat. A chat connector does not supply the
Desktop runtime credential. If the user volunteers a value anyway, do not echo
or copy it; explain that it is in conversation history and should be rotated.
Do not substitute a file export for a requested live source unless the user
independently changes source scope.

A names-only shell environment probe establishes only which variable names
are visible to the checking process. Presence or absence does not establish
the supervisor's startup environment, selected profile or credential allowlist,
nonempty credential validity, or usable source permissions. Runtime source
access remains unverified; do not claim the supervisor has or lacks access
from the shell probe alone. This diagnostic check must not read or print
secret values, and must not be run during first-plan discovery. Native
connection and admitted source-execution evidence remain authoritative.
This names-only shell probe is not normal recovery for a Keychain access
outcome; do not direct users to terminal commands or environment-variable edits.

Mapper provider keys are a separate matter: they stay in the supervisor's
secure store and never reach the profile. The provider-only
`provider_credential_status` tool cannot establish that a source reference is
readable or that the source is reachable.

## Host path handoff

Materialize the source at the host-visible, absolute path explicitly returned or
exposed by the file-writing tool. For a new construction, pass that path only
through the supervisor's returned `capture` action after consent and generation
self-check. Never use a direct command or local substitute to bypass that
sequence. Never derive a definition path from an opaque attachment ID, a
tool-internal ID, or a Linux workspace path. If no host-visible path is
available, stop and explain that the local build cannot reach the materialized
definition yet.
