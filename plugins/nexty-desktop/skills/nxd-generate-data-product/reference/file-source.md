# File connector (non-CSV): JSON / JSONL / Parquet

## Contents

- Scope and layout
- The JSON-vs-JSONL distinction
- Excel/xlsx — not supported
- Naming (service, secrets key, companion file)
- `transform/main.py` diff from the CSV template
- `requirements.txt` additions
- `spec.py` / `infra-profile.yaml` diffs
- Self-check

This is a sibling of the proven CSV connector documented inline in
`SKILL.md` — same closure shape, same naming invariant, same `duckdb` port
contract. Only the reader and a few names differ.

## Scope and layout

Supported formats: JSON (a single array or object per file), JSONL/NDJSON,
and Parquet. Same per-model layout as CSV: one subdirectory per model under
the file-source root, `data/<model>/*.<ext>`. **One format per model
subdirectory** — if a directory mixes formats or has none, stop and surface
it; do not guess which format the model intends.

### The two normal export shapes (all file connectors, CSV included)

A supplied export arrives in one of two shapes, and both are NORMAL:

- **One subdirectory per model** — `<root>/<model>/*.<ext>` lands at
  `data/<model>/`, the model named by the directory.
- **Flat files at the export root** — one base model per file, named from the
  **snake_cased filename** (`Card Txns 2024.csv` → `card_txns_2024`), landed at
  `data/<model>/<file>.<ext>` as an **EXACT BYTE COPY by default**. A declared,
  verified projection may omit only personal-data columns the approved
  blueprint does not need; follow
  [the source-materialization rule](../../nxd-run-job-loop/reference/source-materialization.md#privacy-projection-for-unused-personal-data-columns).

Read the headers either way, and never merge files, rename a retained header,
add a column, or reshape a row while landing. The projection exception keeps
all rows and retained field values in order and records its explicit allow-list,
dropped-column reasons, and source/landed digests.

**Stop and surface it** — do not guess — only for a genuinely ambiguous shape:

- nesting more than one level deep,
- a directory mixing formats,
- two files whose names snake_case to the **same** model name (including a
  collision with the reserved `nxd_decisions`).

## The JSON-vs-JSONL distinction

These are different shapes and need different handling:

- **JSONL/NDJSON** (one JSON object per line): dlt has a built-in reader,
  `read_jsonl` — a direct drop-in swap for `read_csv` in the existing
  `filesystem(...) | read_X()` pattern.
- **Plain `.json`** (a single array of records, or a single object): dlt has
  **no built-in reader** for this shape. Write a small custom `@dlt.resource`
  named `_json_array_resource(model_dir)` (not a dlt/stdlib function — the
  author implements it) that opens the file, `json.load()`s it, and yields
  each record (or the whole object if it isn't a list) — still
  `.with_name(table_name)`-tagged the same way as every other reader.

Do not conflate the two — treating a plain `.json` array as if `read_jsonl`
could parse it will fail silently or read nothing.

## Excel/xlsx — not supported

No built-in dlt filesystem reader exists for `.xlsx`/`.xls` (unlike
CSV/JSONL/Parquet). Supporting it would require a hand-written
`@dlt.resource` wrapping `pandas.read_excel`/`openpyxl`, which is untested in
this pack. **Do not build this now.** Ask the user to export the sheet to
CSV or Parquet first, and proceed with the CSV or file-source connector.

## Naming

- Infra-profile service: `file-source`, driver `nxd:generic-secrets:1.0.0`.
  This deliberately differs from CSV: `csv-source` uses
  `nxd:local/file/storage:0.1.0` on the local desktop runtime.
- Transform secrets key: `secrets["file_source"]` — a string, the file-source
  export root (same shape as `csv_source`, just a different key).
- Companion file: `file-source-path` — one line, the **relative** path from
  the closure root to the file-source export root. Same rule as
  `csv-source-path`: relative only, resolved inside the pinned snapshot; an
  absolute path escapes the snapshot and fails.

These names are for exactly **one** file source. When this closure needs two
or more file sources (or mixes one with another connector type), label each
instance instead — see `reference/multi-source.md` for the full
`file-source-<label>` / `file_source_<label>` / `file-source-<label>-path`
pattern (each labeled instance also gets its own root, `data-<label>/`).

## `transform/main.py` diff from the CSV template

Same skeleton as `SKILL.md` Step 3's CSV template — same `duckdb` param
typed `DuckDbOutput`, same `PHYSICAL_MODELS` discipline, same read-back
assert, same `write_disposition="replace"`, same `.transform-complete`
touch. Only the source-root secrets key and the reader construction change:

```python
import dlt
from dlt.sources.filesystem import filesystem, read_jsonl, read_parquet

source_root = Path(secrets["file_source"])
...
for model in PHYSICAL_MODELS:
    table_name = duckdb.model_tables[model]
    model_dir = source_root / model
    ext = _detect_format(model_dir)  # "jsonl" | "parquet" | "json"
    if ext == "jsonl":
        reader = filesystem(bucket_url=str(model_dir), file_glob="*.jsonl") | read_jsonl()
    elif ext == "parquet":
        reader = filesystem(bucket_url=str(model_dir), file_glob="*.parquet") | read_parquet()
    else:  # plain .json — no built-in reader
        reader = _json_array_resource(model_dir)
    readers.append(reader.with_name(table_name))
```

`_detect_format` and `_json_array_resource` are **not** dlt or stdlib
functions — the author must write both. `_detect_format` inspects the
directory's file extensions and stops if it finds a mix or none (do not
guess); `_json_array_resource` is the custom `@dlt.resource` described in the
JSON-vs-JSONL section above. A transform that calls either without defining
it raises `NameError` at runtime.

## `requirements.txt` additions

Base pins unchanged (`nxd.data_product[spec]`, `dlt[duckdb]==1.28.2`,
`duckdb==1.5.4`, `pandas==2.3.3`). Add `pyarrow` **only if any promised model
is Parquet** — confirm the exact extra name (`dlt[parquet]` vs. bare
`pyarrow`) against the pinned `dlt==1.28.2` before relying on it; do not
float the version.

## `spec.py` / `infra-profile.yaml` diffs

- `spec.py`: `_file = "/infra-profile/desktop-local#/services/file-source"`,
  `.secrets([_file])` instead of `.secrets([_csv])`. Everything else
  (`.promise`, `.model`, `.port("duckdb", ...)`, no `.semantic_tools()`) is
  identical to the CSV template.
- `infra-profile.yaml`: the third service is named `file-source` with the
  generic driver `nxd:generic-secrets:1.0.0` (unlike local-file
  `csv-source`). Like `csv-source`, it has nothing
  secret to deliver — the export root is non-secret topology, covered by
  `file-source-path` — so `attributes` stays `[]`, unlike `db-source` /
  `api-source` which populate it with a live credential (see
  `reference/database-source.md` / `reference/api-source.md`). For 2+ file
  sources, add one labeled service per instance instead — see
  `reference/multi-source.md`.

## Self-check

Identical pattern to the CSV `self_check.py` shipped by `nxd-run-job-loop` and
invoked in `SKILL.md` Step 7 — this
connector type needs no live credentials, so the full offline dry-run
applies unchanged: build a scratch `DuckDbOutput`, call `ingest` with
`secrets={"file_source": <local path>}`, assert the read-back and the
`.transform-complete` touch.
