# Exporting a local desktop data product for another person

## Contents

- [Export vs. reopen](#export-vs-reopen)
- [The call](#the-call)
- [Fail-closed redaction — why you don't hunt for secrets](#fail-closed-redaction--why-you-dont-hunt-for-secrets)
- [Writing `import_notes`](#writing-import_notes)
- [Read the result](#read-the-result)
- [What the recipient gets, and how they import it](#what-the-recipient-gets-and-how-they-import-it)
- [User-facing handoff](#user-facing-handoff)

## Export vs. reopen

Portability has two halves, and they use different tools:

- **Reopen** (`reference/context-and-resume.md`) is the *inbound* half: get a
  product you already own back in a later session, on the **same host, same
  user**. It is resume-first — `list_data_products` → `resume_data_product` —
  and reconstructs through workflow-v2 only when the published artifact is
  gone. New construction uses the v2 capability, prepare, consent, capture,
  retained-review, and admission actions.
- **Export** (this file) is the *outbound* half: hand a product to **another
  person or another machine**. It is a single call to
  `mcp__nxd-desktop__export_data_product` that produces a shareable zip.

`export_data_product` is **read-only**: it takes no lock, boots nothing, and
never modifies the definition directory. It is safe to run on a product that is
currently built and served — exporting does not disturb the running instance.

## The call

```
mcp__nxd-desktop__export_data_product(
    definition   = "<abs path to the closure — the SAME path you built from>",
    import_notes = "<required; see below>",
    redact       = { "<service>": ["<attr>", ...] },  # optional; rarely needed
    destination  = "<abs path for the .zip>"          # optional; tool-chosen if omitted
)
```

- `definition` is the closure's durable, host-visible absolute path — the one
  stored in the structured handoff and named by the workflow id
  (`…/nxd-jobs/<workflow>/closure/`). It is the path supplied to the v2 capture
  action; it is never a direct build input or a way around workflow admission.
- `import_notes` is **required**.
- `redact` is optional and rarely needed — see the next section.
- `destination` is optional: where to write the `.zip`. Omit it and the tool
  picks the path — an `exports/` directory beside the supervisor's state, named
  from the closure's content. Either way, the written path comes back as
  `archive_path` in the result (see "Read the result").

## Fail-closed redaction — why you don't hunt for secrets

The tool strips credentials **fail-closed**: **every** infra-profile attribute
that is not explicitly marked `public: true` has its value replaced with a
placeholder automatically, without being named. You do not enumerate secrets,
and you do not need to know which attributes are sensitive — anything not proven
public is redacted by default.

`nxd-generate-data-product` marks each connector attribute by sensitivity (see
`nxd-generate-data-product`'s `reference/database-source.md` and `reference/api-source.md`):
credentials and identity (`password`, `user`, tokens/keys) are `public: false`,
while non-secret topology/config (`host`, `port`, `database`, `schema`,
`base_url`, `auth_type`, `region`) is `public: true`. So an export of a
desktop-generated closure **keeps the topology and redacts only the secrets** —
the recipient's bundle already carries the connection shape and they refill just
the credentials the `IMPORT.md` header names.

Two consequences:

- **`redact` is rarely needed here.** The `redact` map only strips values that
  *are* marked `public: true`; it is keyed by infra-profile **service name**.
  The fail-closed default already redacts every secret, so never reach for
  `redact` to strip a credential. Its one legitimate use is to *also* strip a
  non-secret `public: true` value the user decides shouldn't ship (e.g. an
  internal host) — an export-time override of the automatic classification.
- **Never mark a credential `public: true`.** `public: true` means "safe to
  ship in an export." Marking a credential public would leak it into the bundle.

## Writing `import_notes`

`import_notes` is appended **below** a generated header. That header already
lists which credentials the recipient must refill and the exact rebuild
commands — so **do not repeat those**. Put only product-specific context the
recipient needs and the header cannot know:

- What the data is and what the product answers.
- Any ruling or derived-model caveat worth flagging. The bundle already carries
  the contract: `dp-blueprint.approved.md` is the approved plan, byte for byte, and
  `build-record.json` is what happened when it was built — `import_notes` can
  point at either rather than restating them.
- Who to ask, or where the live source lives, if the recipient must supply their
  own credential.

Keep it to the product's story. The mechanics of refilling and rebuilding are
the header's job.

## Read the result

The call returns:

- **`archive_path` — the verified user-deliverable bundle location.** Relay this
  absolute path in the user-facing handoff when the user needs to retrieve or
  share the bundle. Never expose temporary or supervisor-owned staging paths.
  When `destination` is omitted the path is tool-chosen and known *only* from
  this field (the result also carries `archive_sha256` and the archive's entry
  count if you want to confirm integrity).
- **`redacted` — everything it stripped**, each entry naming the service, key,
  and reason (`non_public` by the default rule, or `requested` via `redact`).
  Relay it so the user can confirm the bundle carries no live credential.
- **A separate report of any misspelled `redact` target** —
  `unmatched_redact_services` (service name the profile doesn't declare) and
  `unmatched_redact_keys` (service exists, no such key). Reported *apart* from
  the redaction list precisely so a typo is never mistaken for a clean run: a
  value you meant to strip was **not** stripped — fix the name and re-export
  before handing the bundle off.
- **`secret_ref_services` — services whose credentials come from an
  `attributesSecretRef`.** Nothing was redacted for them, but each still needs a
  refill after import (the reference points at a secret store the recipient does
  not have) — flag them in the handoff alongside what the `IMPORT.md` header lists.

## What the recipient gets, and how they import it

The zip contains the whole closure —
`spec.py`, `models.py`, `infra-profile.yaml`, `transform/main.py`,
`requirements.txt`, `dp-blueprint.approved.md`, `dp-blueprint.lock.json`,
`build-record.json`, `README.md`, the connector companion artifact where the type has one — and,
for a credentialed source, `SENSITIVE` and `.gitignore`
— with `infra-profile.yaml`'s credentials replaced by placeholders, plus the
generated `IMPORT.md` and a machine-readable `export.json`.

The recipient gets the plan and the outcome together and needs no separate
document: `dp-blueprint.approved.md` is the approved plan byte for byte,
`dp-blueprint.lock.json` binds it to this closure by hash, and `build-record.json`
records what the build actually did — including any concession taken to reach
green. Nothing in the bundle points outside itself.

**Importing is reopen-through-workflow-v2 with the recipient's own credential.**
The recipient unzips and refills the credentials the `IMPORT.md` header names (a
file/CSV closure needs none), places the approved blueprint beside the imported
closure, and follows the v2 construction sequence so capture, retained review,
and admission remain mandatory. If the recipient's supervisor cannot admit the
closure, report that blocker rather than substituting a direct build. Because
the closure embeds its own copy of the source data and the transform is
deterministic, a successful reconstruction carries identical rulings and rows.

This is the guided version of `context-and-resume.md`'s "file is absent —
closure obtained as a clone or copy" credential-recovery branch: instead of a
bare missing `infra-profile.yaml`, the
recipient gets placeholder values in place and a header naming exactly what to
refill.

## User-facing handoff

- **A live-source product will not connect until the recipient refills the
  credentials** the header lists. Say so in the handoff; the export deliberately
  ships no live secret.
- **The export does not transfer a running instance or a bearer token.** The
  recipient stands up their own instance by rebuilding; the token is minted per
  session and never travels in the bundle.
