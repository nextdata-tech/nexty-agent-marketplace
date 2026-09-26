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
   then name the slots and ask the user to fill them in directly. Give them a
   runnable command that opens the file rather than only its path, per
   [user-facing-language.md](user-facing-language.md) § Asking the user to open a
   file; a non-technical user handed a path alone is stuck before they start.
   That file is already `0600`, gitignored and marked `SENSITIVE` — it is the
   surface designed to hold the value. Wait for their confirmation, then run the
   connectivity check. This is the human-boundary form of the subagent's
   placeholder + `credential_slots` hand-back.
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
exposed by the file-writing tool. For a new construction, pass that path only
through the supervisor's returned `capture` action after consent and generation
self-check. Never use a direct command or local substitute to bypass that
sequence. Never derive a definition path from an opaque attachment ID, a
tool-internal ID, or a Linux workspace path. If no host-visible path is
available, stop and explain that the local build cannot reach the materialized
definition yet.
