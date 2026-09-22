# Google Sheets source

This recipe is listed in the [source types index](source-types.md).

## Scope

Use this profile when a user wants one or more ranges from a Google
spreadsheet to land as local DuckDB rows. A spreadsheet is not a Drive binary
file: it has tabs, ranges, headers, formulas, and cell values. Keep this mode
separate from `google_drive_files` so the generated code cannot accidentally
download a Sheet export when the user asked for a particular range.

The desktop closure declares an `api-source` service with
`source_kind: google_sheets`. The transform uses Google's official Sheets
client and a dlt resource; it does not hand-roll HTTP transport.

## Profile attributes

| Attribute | Public | Meaning |
|---|---:|---|
| `source_kind` | true | Must be `google_sheets` |
| `base_url` | true | Sheets API base URL used by the client configuration |
| `spreadsheet_id` | true | Spreadsheet to read |
| `sheet_range` | true | A1 range, including the tab name when needed |
| `value_render_option` | true | Usually `FORMATTED_VALUE` or `UNFORMATTED_VALUE` |
| `access` | true | Declared source intent, normally `read-only`; it does not grant provider access |
| `auth_type` | true | `bearer` for the supplied OAuth access token |
| `auth_token` | false | Runtime OAuth access token |

For multiple spreadsheets, label every source service and prefix every flat
attribute according to `reference/multi-source.md`.

## Source recipe

The generated transform must:

1. Build a read-only Sheets client from the flat bearer credential.
2. Read the configured range and treat the first returned row as headers only
   when the approved source contract says it has headers.
3. Preserve duplicate, blank, and reordered values deterministically rather
   than inventing column names from a sample row.
4. Normalize each subsequent row to a flat dictionary, padding missing cells
   with `None` and retaining extra cells under a deterministic error or
   explicitly approved overflow policy.
5. Yield rows through dlt and use the standard DuckDB output/read-back
   assertion.

`access: read-only` is an explicit source contract, not an authorization
mechanism. The provider still decides whether the token may read the selected
spreadsheet and range; a valid token without that permission is an
authorization failure, not anonymous fallback.

Formula handling is a user decision. `FORMATTED_VALUE` reads the displayed
result; `FORMULA` preserves formula expressions; do not silently choose a
different interpretation because it is convenient for the transform.

## Deterministic eval shape

The atomic eval should cover:

- a header row and two data rows;
- a blank cell and a missing trailing cell;
- a second tab that must not be read;
- a valid token without spreadsheet access;
- a malformed range configuration;
- formula/value rendering as an explicit source decision.

The checker verifies range selection, header handling, token isolation, flat
row output, and the dlt boundary without launching the desktop supervisor or
contacting Google.
