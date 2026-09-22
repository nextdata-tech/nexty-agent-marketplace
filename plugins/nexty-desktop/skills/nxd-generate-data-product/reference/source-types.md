# Source types

This index maps the connector types supported by `nxd-generate-data-product` to
their service shape, companion artifacts, and detailed source recipe.

| Source type | Service | Companion artifact | Detailed recipe |
|---|---|---|---|
| CSV | `csv-source` | `csv-source-path` + `data/` | The inlined CSV workflow in [the generator skill](../SKILL.md) |
| Other local file | `file-source` | `file-source-path` + `data/` | [File source](file-source.md) |
| Database | `db-source` | `db-source-tables` | [Database source](database-source.md) |
| REST API | `api-source` | `connectivity_check.py`; endpoint attributes | [API source](api-source.md) |
| Google Drive files | `api-source` with `source_kind: google_drive_files` | `connectivity_check.py` | [Google Drive file source](google-drive-source.md) |
| Google Sheets | `api-source` with `source_kind: google_sheets` | `connectivity_check.py` | [Google Sheets source](google-sheets-source.md) |

## Choosing a source type

- Use `file-source` for a local JSON, JSONL, or Parquet file. Use `csv-source`
  for the proven CSV closure shape.
- Use `db-source` when the source is a database table or query. Do not export a
  database table into `data/`.
- Use `api-source` for an HTTP API whose response is already a row-oriented
  resource. Start with [the API source recipe](api-source.md) for auth,
  pagination, envelopes, and redaction.
- Use the Google Drive recipe for tabular files stored in Drive. Use the Google
  Sheets recipe for spreadsheet ranges; a Sheet is not a binary CSV file.

All source instances use flat connector attributes and the credential boundary
defined by the API or database recipe. For multiple instances, apply the
[multi-source naming rules](multi-source.md).
