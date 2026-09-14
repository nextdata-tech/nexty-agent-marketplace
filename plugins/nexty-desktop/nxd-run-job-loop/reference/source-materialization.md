# Materializing a source faithfully

How each kind of in-scope source is landed at Step 1, before inference. These
rules govern **source materialization only** and they are absolute — everything
downstream depends on the landed rows being a byte-exact record of what the user
supplied.

## Contents

- [Fidelity here; derivation downstream](#fidelity-here-derivation-downstream)
- [Attached or workspace file](#attached-or-workspace-file)
- [Pasted table](#pasted-table)
- [Database connection](#database-connection)
- [REST API](#rest-api)
- [Host path handoff](#host-path-handoff)

## Fidelity here; derivation downstream

The CSVs you land are a byte-exact record of what the user supplied, so any
later number traces back to it. Cleaning, dedup, amortization, currency
normalization, reclassification and regrain are legitimate — often necessary —
but exist **only as derived models computed downstream of the pristine source**,
never as an edit to the source export. Preserve the row on the way in, then
derive the corrected model beside it; `nxd-generate-data-product` owns how.

Materialization is also **gated**: if the request supplied a procedure with a
result-changing gap, copying a source into a closure waits for the user's reply
to the `dp-blueprint.md` read-back. Reading a source is always allowed.

With 2+ sources, repeat the matching section once per source and tag every
artifact with that source's label.

## Attached or workspace file

Make an exact byte-for-byte copy into the generated connector export. Do not
rewrite delimiter, encoding, headers, or rows.

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
asking the user to type it into chat is the last of them.

## Where credentials land

A database or API credential lands in exactly one place: the generated
`infra-profile.yaml` connector service's `attributes`. Never in chat, never in
`dp-blueprint.md` (which names key names only), never in a subagent's prompt or
return. Per-type shape is in `nxd-generate-data-product`'s `reference/database-source.md`
and `reference/api-source.md`; labeled instances in its
`reference/multi-source.md`.

**How the value gets there, in order of preference.** A pasted credential is in
conversation history for good, and history is the one surface `SENSITIVE`,
`.gitignore` and `chmod 0600` cannot reach — the same reasoning that keeps a
credential out of a subagent's prompt (`reference/scheduling.md`) applies to the
main thread, which is also a transcript. So prefer a route where you never see
the value:

1. **The user writes it.** Generate the closure with a placeholder in each slot,
   then name the slots and the `infra-profile.yaml` path and ask the user to fill
   them in directly. That file is already `0600`, gitignored and marked
   `SENSITIVE` — it is the surface designed to hold the value. Wait for their
   confirmation, then run the connectivity check. This is the human-boundary form
   of the subagent's placeholder + `credential_slots` hand-back.
2. **An environment variable already visible to your own tooling.** This route is
   narrower than it sounds. A variable the user exports in their interactive
   shell *after* the session starts is not visible to your tool calls, which run
   in a separate process: reading it raises `KeyError`, or yields an empty string
   you would then write into the profile as though it were the credential. It
   works only when the variable is already in the environment your tools inherit
   — set in the user's shell profile, or exported before the session began.
   Given that, substitute it when writing the profile by reading `os.environ`
   **inside** the script — never as a shell-expanded `$TOKEN` on a command line,
   which lands the value in the transcript exactly as a paste would. Have that
   script fail loudly on a missing or empty key rather than write a profile with
   a hollow credential in it, and when the variable turns out not to be visible,
   fall back to route 1 rather than asking the user to re-export and retry.
   `generic-secrets` stores a literal string and does not interpolate, so the
   substitution happens at write time, not at run time.
3. **Pasted into chat — last resort.** If the user supplies it this way anyway,
   do not echo it back, and say plainly that it now lives in conversation history
   where no closure guard reaches it, so the credential should be rotated after
   the build.

Never invite route 3 when route 1 is available: "paste your token" and "fill in
`auth_token` in `infra-profile.yaml`" cost the user the same keystrokes and
differ only in where the secret comes to rest.

## Host path handoff

Materialize the source at the host-visible, absolute path explicitly returned or
exposed by the file-writing tool. For a new construction on an enrolled
workflow-v2 runtime, pass that path only through the supervisor's returned
`capture` action after consent and generation self-check; never invoke
`build_data_product` as a shortcut around that sequence. Never derive a
definition path from an opaque attachment ID, a tool-internal ID, or a Linux
workspace path. If no host-visible path is available, stop and explain that the
local build cannot reach the materialized definition yet.

The legacy `build_data_product` path is retained only for an explicitly
feature-off or non-enrolled compatibility runtime. It still requires the same
host-visible absolute path, but it does not make that runtime evidence of the
workflow-v2 consent, capture, review, or admission contract.
