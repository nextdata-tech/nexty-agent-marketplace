# Database connector: a live database connection

## Contents

- Scope
- The `sql_database` source shape
- Credential handling — read this before shipping
- Naming
- Sensitivity artifacts
- `transform/main.py` diff from the CSV template
- `requirements.txt` additions
- `spec.py` / `infra-profile.yaml` diffs
- Self-check (connectivity smoke test)

This is a sibling of the proven CSV connector documented inline in
`SKILL.md` — same closure shape (`duckdb` port, `PHYSICAL_MODELS`,
read-back assert, `.transform-complete`), with the database read at transform
time instead of a CSV export. A pure `db-source` closure may omit both
`csv-source-path` and `data/`; neither is a database input. S1 also validated a
database capture with an empty/README-only `data/` scaffold and the CSV marker,
so do not claim that the directory itself causes a validation failure. In S1,
`validation/scratch_transform_failed` carried SQLAlchemy/transform errors, and
the same layout passed after a fresh capture.

## Scope

Postgres and MySQL for this first cut — the two vendors dlt's
`sql_database` source has SQLAlchemy-dialect drivers proven against in this
pack. Other SQLAlchemy-dialect databases are architecturally plausible but
unverified here; do not claim support for a vendor that hasn't been proven.

## The `sql_database` source shape

```python
from dlt.sources.sql_database import sql_database

source = sql_database(
    credentials=<connection string or ConnectionStringCredentials>,
    schema=<db schema>,
    table_names=[<source table for each promised model>],
)
```

This returns one dlt resource per requested table, named after the *source*
table. Because the naming invariant requires the resource's final name to
equal the promised physical model name, **rename each resource** via
`.with_name(table_name)` using a model→source-table map — this is exactly
what the `db-source-tables` companion file below carries, so the source
table name and the model name never need to match.

## Credential handling — read this before shipping

For CSV, the committed "connector config" is just a non-secret path. For a
database connector it necessarily includes a real credential (host, user,
password). That value is delivered by writing it into the `db-source`
service's `attributes` in `infra-profile.yaml` — the desktop supervisor's
`generic-secrets` driver reads it from there and merges it into the
transform's `secrets` dict.
**`secrets` is FLAT.** The supervisor merges every service named in
`.secrets([...])` into one map. For a `generic-secrets` service the keys are
exactly the `attributes` you wrote — the service name is not among them — so a `db-source` attribute `host` arrives as
`secrets["host"]`, never `secrets["db_source"]["host"]`. A nested read raises
`KeyError: 'db_source'` at transform time, after the credential has already
been resolved.

- **When this closure names more than one service in `.secrets([...])`, check
  for key collisions before writing the profile.** The merge is flat, so a key
  declared by two services resolves to one value and the loser vanishes with no
  error — two databases both declaring `host` is the common case. Prefix the
  attribute `key` (`orders_host`) to separate them. Full rule:
  `reference/multi-source.md`.
- The companion file `db-source-tables` holds **only non-secret topology** —
  one line per model, `<model>=<schema-qualified source table name>`.
- The `db-source` service's `attributes` list carries the live payload as
  **one entry per connection field**, each shaped `{"key": <field>, "value":
  <live value>, "public": <bool>}` — `host`, `port`, `database`, `schema`,
  `user`, `password` (see the sensitivity classification under Credential
  handling for the `public:` value per field) — never one attribute holding a
  nested object. Each becomes a top-level
  key on `secrets` — `secrets["host"]`, `secrets["password"]`, and so on. See
  the worked example below.
- **`value` is always a plain string** on the transform side — `port` arrives
  as `"5432"`, not `5432`; cast in `_build_connection_string` if the
  dialect's connection-string/DSN builder needs an int.
- **Mark each attribute by sensitivity.** The `public:` flag controls **only**
  `export_data_product` redaction — the transform reads every attribute via
  `secrets` regardless. Secrets and identity — `password`, `user` —
  are `public: false` (redacted fail-closed on export). Non-secret topology —
  `host`, `port`, `database`, `schema` — is `public: true` so it survives an
  export and the recipient only refills the credentials. **Never mark a
  credential `public: true`.** If the user explicitly designates an attribute's
  sensitivity, honor their choice over this default.
- **Never fabricate a credential the user hasn't supplied**, and never
  narrate a raw password in chat — enter it into `infra-profile.yaml` exactly
  as the user gave it, nowhere else.
- **The closure directory itself now holds a live credential in plaintext.**
  The `public:` flag only controls `export_data_product` redaction; it does not
  make `infra-profile.yaml` safe to commit, hand-zip, or hand off. Treat the
  whole closure directory as sensitive once this file carries a real password:
  don't commit it to a shared repo, don't hand-attach it to a ticket or chat,
  and don't reuse it as a template for a different database without clearing the
  old credential first. To share the product, use the supervisor's
  `export_data_product` tool — it strips every attribute not marked
  `public: true` fail-closed, so **never mark a credential attribute
  `public: true`** (`public: true` means "safe to ship in an export").
- **Emit the sensitivity artifacts in the same step that writes the
  credential** — see [Sensitivity artifacts](#sensitivity-artifacts) below.
  Writing the credential and not the artifacts is an incomplete step, not a
  later cleanup.
- This shape (`key`/`value`/`public`) is verified against the supervisor's
  `yaml_schemas::infra_profile::KeyValuePairWithPublic` type and
  `SecretsHandler` construction — not inferred from a single example.

## Naming

- Infra-profile service: `db-source`, driver `nxd:generic-secrets:1.0.0`.
- Transform secrets keys: the attribute keys themselves, flat on `secrets` —
  `secrets["host"]`, `secrets["port"]`, `secrets["database"]`,
  `secrets["schema"]`, `secrets["user"]`, `secrets["password"]`. There is no
  `secrets["db_source"]` level.
- Companion file: `db-source-tables` — one line per model,
  `<model>=<source table>` (non-secret topology only; identity mapping if
  the source table already matches the model name).

These names are for exactly **one** database source. When this closure
needs two or more database sources (or mixes a database with another
connector type), label each instance instead — see
`reference/multi-source.md` for the full `db-source-<label>` /
label-prefixed attribute keys (`orders_host`) /
`db-source-<label>-tables` pattern.

## Sensitivity artifacts

The trigger is **structural**, not a judgement call: any `*-source` service in
`infra-profile.yaml` with a populated `attributes` list. A file or CSV source
keeps `attributes: []` and needs none of this. Emit all three in the same step
that writes the credential — Phase C fails the closure if the first two are
missing while a populated `attributes` list is present.

1. **`.gitignore`** at the closure root, ignoring the credential file only:

   ```gitignore
   # Holds live credentials in plaintext. Never commit.
   infra-profile.yaml
   ```

   Never `*`. The rest of the closure — `spec.py`, `models.py`,
   `transform/main.py`, and connector-owned exports when present — is exactly
   what a colleague needs to rebuild; a blanket ignore silently destroys that.
   `data/` is not universal for database sources. A clone missing
   `infra-profile.yaml` is the intended outcome: the recipient supplies their
   own credential and rebuilds.

2. **`SENSITIVE`** at the closure root — a marker a cold reader hits before
   opening anything, naming keys but **never values**:

   ```
   This closure holds a live credential in plaintext.

   File:  infra-profile.yaml
   Keys:  <service>.attributes -> user, password
   Rotate: replace the `value:` entries and rebuild the data product.

   Do not commit or hand-zip this directory with the credential intact, or
   reuse it as a template for a different source without clearing the credential
   first. To share the product, use the supervisor's export_data_product tool,
   which strips credentials fail-closed.
   ```

3. **`chmod 0600 infra-profile.yaml`** where a shell can reach the closure.
   Best-effort risk reduction, never a gate: it is unavailable on surfaces with
   no host shell (Claude Cowork's Bash is an isolated Linux environment, not the
   user's host). Skip it silently there — the two files above are mandatory on
   every surface.

**Never write a credential value into `SENSITIVE`, `README.md`,
`dp-blueprint.approved.md`, `dp-blueprint.lock.json`, `build-record.json`, or chat
narration.** Keys and file paths only. These artifacts exist so the credential's
location is discoverable without the credential being copied. The generated
record files are covered by the same rule and by a mandatory redaction pass —
every diagnostic's `message` and `evidence` is run through the validator's own
credential-value pattern before it is written, because a check that prints the
secret it found turns a contained file leak into a transcript leak.

## `transform/main.py` diff from the CSV template

Same `duckdb` param/typing, `PHYSICAL_MODELS` discipline, read-back assert,
`write_disposition="replace"`, and `.transform-complete` touch as the CSV
template. Only the ingestion body changes:

```python
from dlt.sources.sql_database import sql_database

# `secrets` is the FLAT merge of every service in `.secrets([...])` — read the
# attribute keys directly. There is no per-service level to index first.
connection_string = _build_connection_string(secrets)  # never hardcoded
table_map = _load_db_source_tables()  # parses the db-source-tables companion file

source = sql_database(
    credentials=connection_string,
    schema=secrets["schema"],
    table_names=list(table_map.values()),
)
readers = []
# The models this connector actually serves are the ones the companion maps —
# NOT PHYSICAL_MODELS. That tuple also holds derived models (Step 3a) and any
# landed reference data (`nxd_decisions`, FX rates), neither of which has a
# `db-source-tables` entry, so indexing `table_map[model]` over it raises
# `KeyError` on a closure that is otherwise correct — and the message points at
# the companion file as though an entry were missing there. Both reach the same
# `pipeline.run` as their own `@dlt.resource` appended to this list.
# The labeled multi-DB body in `multi-source.md` already iterates this way.
for model in table_map:
    table_name = duckdb.model_tables[model]
    resource = source.resources[table_map[model]]
    readers.append(resource.with_name(table_name))
pipeline.run(readers, write_disposition="replace")
```

Build the connection string from `secrets` at runtime — never
hard-code host/user/password in the transform source. `_build_connection_string`
and `_load_db_source_tables` are **not** dlt or stdlib functions — the author
must write both: the former assembles a dialect-correct connection string
(Postgres and MySQL differ) from the flat `secrets` fields, the latter
parses the `db-source-tables` companion file's `<model>=<table>` lines into a
dict. Neither is optional boilerplate; a transform that calls them without
defining them raises `NameError` at runtime.

## `requirements.txt` additions

The Desktop runtime does not install the closure's `requirements.txt`.
Database drivers and dlt extras must be provided by the installed Desktop
runtime; adding a package here does not make it available to a Desktop
transform. An NXD change adding `dlt[sql_database]` and a PostgreSQL driver to
the Desktop runtime is in flight. Use only database drivers actually present
in the connected runtime; do not claim pending runtime support as installed.

For other runtimes that do install the closure's `requirements.txt`, keep the
base pins unchanged and add `dlt[sql_database]==1.28.2` plus **exactly one**
vendor driver matched to the user's database — `psycopg2-binary` for Postgres,
`pymysql` for MySQL. Never install both speculatively.

## `spec.py` / `infra-profile.yaml` diffs

- `spec.py`: `_db = "/infra-profile/desktop-local#/services/db-source"`,
  `.secrets([_db])`. Everything else (`.promise`, `.model`,
  `.port("duckdb", ...)`, no `.semantic_tools()`) is identical to the CSV
  template.
- `infra-profile.yaml`: third service named `db-source` (same driver
  `nxd:generic-secrets:1.0.0`), with its `attributes` populated with the
  live connection credentials:

  ```yaml
    - name: db-source
      driver: nxd:generic-secrets:1.0.0
      attributes:
        - key: host
          value: <live host>
          public: true
        - key: port
          value: <live port>
          public: true
        - key: database
          value: <live database>
          public: true
        - key: schema
          value: <live schema>
          public: true
        - key: user
          value: <live user>
          public: false
        - key: password
          value: <the live password the user supplied>
          public: false
  ```

  **No CSV export is required.** `db-source-tables` is the database table map
  and stays non-secret topology only. The transform reads database rows through
  `db-source`; it does not read `csv-source-path` or use `data/` as a database
  source. A pure database closure may omit both, and an empty or README-only
  `data/` scaffold is not itself a failure. Use any landed reference files only
  when the approved blueprint names them and the transform explicitly loads
  them through their own resource. For 2+ database
  sources, add one labeled service per instance instead (`db-source-<label>`
  / label-prefixed attribute keys such as `secrets["orders_host"]`) — see
  `reference/multi-source.md`.

## Self-check (connectivity smoke test)

A database connector needs live credentials to dry-run at all — unlike CSV
or another local file connector, there's no offline structural check that
proves the data actually loads. When credentials are available in the
authoring session, do **not** reuse the full `pipeline.run(...)` ingestion
path unmodified — pulling entire production tables just to prove
connectivity is wasteful and, for large or sensitive tables, unnecessary
risk. Instead call `.add_limit(1)` on each resource before running the
pipeline (`source.resources[table_map[model]].add_limit(1)`) so the
connectivity check pulls at most one row per model, then assert
`row_count > 0` per model — **not** an exact fixture count, since live data
isn't static. When credentials are not available in-session, report the
connectivity self-check as **not run** — do not claim it passed. Structural
checks (naming invariant, no `.semantic_tools()`, import correctness) still
run regardless.

**Never let a probe's traceback reach the transcript unredacted.** A failing
connection raises through SQLAlchemy, which masks the password in its own
`repr` — but a malformed URL raises `ArgumentError` carrying the string you
passed it, and an improvised probe that builds its own DSN or prints the
connection string leaks the live value into chat, where the user cannot
remediate it. Wrap the probe so the failure is substituted from the known
secret values, never pattern-matched:

```python
# The profile's `public:` flag does NOT survive the flat merge — the transform
# sees only key -> value — so mirror it by key name here. Suffix-matched, because
# with two instances every key carries a label prefix (`orders_host`).
_PUBLIC_SUFFIXES = ("host", "port", "database", "schema",
                    "base_url", "auth_type", "region")

def _redact(exc: BaseException, secrets: dict) -> str:
    text = str(exc)
    # Redact by DEFAULT and exempt the known-public topology, rather than
    # matching a fixed list of secret keys: an equality test against
    # ("password", "user") matches nothing once the keys are prefixed, and a
    # redactor that matches nothing is indistinguishable from no redactor — it
    # leaks the live password into chat on the first failed probe. Defaulting to
    # redact also fails closed on a field nobody thought of.
    #
    # But do not blank the whole map either: host/port/database/schema ARE the
    # diagnostic, and "could not connect to <redacted>:<redacted>" tells the user
    # nothing they can act on. Short public values would also corrupt unrelated
    # text by substring — schema `public`, port `5432`.
    for key, value in secrets.items():
        if not value:
            continue
        if any(key == p or key.endswith(f"_{p}") for p in _PUBLIC_SUFFIXES):
            continue
        text = text.replace(str(value), "<redacted>")
    return text

try:
    ...  # the bounded probe
except Exception as exc:
    raise SystemExit(f"connectivity check failed: {_redact(exc, secrets)}") from None
```

`from None` is mandatory: without it Python chains the original exception as
`__context__` and re-prints it in full, defeating the redaction. The same rule
governs anything you improvise — never `print()` a connection string, and
never paste a raw traceback from a failed connection into the answer.
