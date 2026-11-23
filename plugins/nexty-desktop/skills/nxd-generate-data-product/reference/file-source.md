# File connector (non-CSV): JSON / JSONL / Parquet

## Contents

- Scope and layout
- The JSON-vs-JSONL distinction
- Excel/xlsx — not supported
- Naming (service, secrets key, companion file)
- `transform/main.py` diff from the CSV template
- One level of nested arrays (JSON / JSONL)
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

- nesting deeper than the **one** level of nested arrays of objects that
  [One level of nested arrays](#one-level-of-nested-arrays-json--jsonl) handles,
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

## One level of nested arrays (JSON / JSONL)

When each record of a `.json` / `.jsonl` file holds an array of objects (for
example 38 products, each with an array of 6 stage objects), land the file as an
**exact byte copy, as always**, and unnest it **inside the transform**. Never
write a flattened CSV/JSON copy beside the original, by hand or by shell: the
landed file stays the user's file, and the unnesting is code the user can read.

The shape is two models from one file:

- **Parent model** — a normal base model: `data/<parent>/` holds the file, and
  the parent resource yields each record's **scalar fields only** (the array
  fields are dropped from it).
- **Child model** — one per nested array of objects, one row per element. It is
  a **derived model** (it has no `data/` directory of its own, so it never trips
  the base-model-vs-data-directory check): its resource re-reads the parent's
  file itself (see
  [derived-models.md](derived-models.md#reading-the-sources-yourself)) and
  yields the parent's key column(s) plus the element's scalar fields. Add a
  `<array>_position` column (1-based) so the child's key is
  `(parent key, position)` unless the elements carry their own id.

```python
def _json_records(model_dir: Path) -> list[dict[str, Any]]:
    # The generic loop's one-format rule, kept for the parent it skips.
    files = sorted(p for p in model_dir.iterdir() if p.is_file() and not p.name.startswith("."))
    suffixes = {p.suffix for p in files}
    assert suffixes in ({".json"}, {".jsonl"}), f"{model_dir} must hold one format: {suffixes}"
    records: list[dict[str, Any]] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            records.extend(json.loads(line) for line in text.splitlines() if line.strip())
        else:
            loaded = json.loads(text)
            records.extend(loaded if isinstance(loaded, list) else [loaded])
    assert records, f"no records read from {model_dir}"
    return records

CHILD_ARRAYS = {"stages"}  # the arrays the plan turns into child models

def _flat(row: dict[str, Any], skip: Collection[str] = ()) -> dict[str, Any]:
    # Fail closed: any nested value the plan did not name stops the build.
    for k, v in row.items():
        if isinstance(v, (list, dict)) and k not in skip:
            raise ValueError(f"unplanned nested field {k!r}")
    return {k: v for k, v in row.items() if k not in skip}

records = _json_records(source_root / "products")
keys = [r["product_id"] for r in records]
assert len(set(keys)) == len(keys), "parent key is not unique"

@dlt.resource(name=duckdb.model_tables["products"])
def products_resource():
    for r in records:
        yield _flat(r, skip=CHILD_ARRAYS)  # the child arrays are dropped here

@dlt.resource(name=duckdb.model_tables["product_stages"])
def product_stages_resource():
    for r in records:
        # A missing, null or empty array means zero child rows for that record.
        for i, stage in enumerate(r.get("stages") or [], start=1):
            yield {"product_id": r["product_id"], "stage_position": i, **_flat(stage)}

BASE_MODELS = ("products",)
DERIVED_MODELS = ("product_stages",)  # PHYSICAL_MODELS lists both
UNNESTED_PARENTS = ("products",)  # read by their own resource above

for model in BASE_MODELS:
    if model in UNNESTED_PARENTS:
        continue  # the generic per-directory reader would yield the arrays too
    ...  # the generic reader from the template diff below
resources += [products_resource(), product_stages_resource()]
```

Rules:

- **Parent key required.** The parent needs a stable, unique key (an id or
  code). Assert it is unique before yielding; the child carries it so the two
  join. If there is none, stop and ask (below).
- **The generic base loop skips the parent.** An unnested parent is read only
  by its own resource. List it in `UNNESTED_PARENTS` and skip it in the
  per-directory `for model in BASE_MODELS:` loop; otherwise the generic reader
  yields whole records, arrays included, and a second resource writes the same
  table. Its reader keeps the one-format rule the
  loop would have applied: the directory must hold only `.json` files or only
  `.jsonl` files (hidden files aside); a mix, another suffix or no files stops
  the build.
- **Flat dicts only, failing closed.** Yield no list or dict value (a nested
  value makes dlt spawn an extra `parent__field` table that fails the table
  assert). Drop only the arrays a child model carries (`CHILD_ARRAYS`), and
  raise on any other list or dict, so a shape the plan did not cover stops the
  build instead of losing fields silently. Flatten a nested object the plan
  names into `<field>_<key>` columns before `_flat` sees it, and name every
  flattened or dropped field in the plan. The snippet above has no nested
  objects.
- **Missing arrays.** A record whose array is missing, `null` or empty has zero
  child rows (`r.get("stages") or []`), in the resource and in the read-back
  expectation alike. If the plan says otherwise, follow the plan.
- **Both models** are in `models.py`, in `spec.py` (`.promise(...)`), and in
  `PHYSICAL_MODELS`; the child has a `join(...)` on the parent key and no
  `data/<child>/` directory. The file keeps its single `data/<parent>/` copy.
- **Read-back expectation.** Parent rows == number of records; child rows ==
  the sum of the array lengths, computed independently from the file (for
  example `sum(len(r.get("stages") or []) for r in records)`), and child keys unique. This
  is the derived-model row-count check in
  [derived-models.md](derived-models.md#tier-1--mandatory-for-every-derived-model-no-exceptions),
  with the array lengths as the arithmetic expectation. Report both counts to
  the user (for example "38 products, 228 product-stage rows").
- **Several arrays** in one record: one child model per array of objects, each
  with the same parent key.
- **Plan wording.** The plan states the unnesting in plain words, such as "one
  row per product, and one row per product and stage", so the user approves the
  grain before anything is built.

**Still stop and ask** (in plain words, before building) when:

- arrays nest inside the array elements (two or more levels deep) — ask which
  level the user wants as rows;
- a field is an array of plain values (tags, ids) — ask whether to keep it as
  one joined text column, one row per value, or leave it out;
- there is no stable key to tie child rows to their parent — ask which field
  identifies a record, or whether row position may be used;
- elements of one array mix shapes — ask which shape the rows should follow.

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
UNNESTED_PARENTS: tuple[str, ...] = ()  # set only when a parent is unnested
...
for model in BASE_MODELS:  # derived models have no data/ directory
    if model in UNNESTED_PARENTS:
        continue  # see "One level of nested arrays"
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
