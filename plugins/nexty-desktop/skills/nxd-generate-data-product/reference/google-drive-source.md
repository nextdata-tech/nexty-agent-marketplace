# Google Drive file source

This recipe is listed in the [source types index](source-types.md).

## Scope

Use this profile when a user wants tabular files stored in Google Drive or a
shared drive to land as local DuckDB rows. It is a read-only source recipe for
CSV and JSON files. It is not a document-search or OCR connector, and it does
not turn arbitrary Google Docs into a relational model.

The desktop closure still declares an `api-source` service. The
`source_kind: google_drive_files` attribute selects this source-specific
recipe; it is not a new supervisor driver. The transform uses Google's
official Drive client to enumerate files and a dlt resource to land rows.
Do not replace that client with a hand-written `requests` or `urllib` loop.

## Profile attributes

The service has flat attributes, merged into the transform's `secrets` map:

| Attribute | Public | Meaning |
|---|---:|---|
| `source_kind` | true | Must be `google_drive_files` |
| `base_url` | true | Drive API base URL used by the client configuration |
| `drive_folder_id` | true | Folder or shared-drive root to enumerate |
| `drive_query` | true | Read-only Drive query for the selected files |
| `drive_file_mime_type` | true | Accepted input MIME type, such as `text/csv` |
| `auth_type` | true | `bearer` for the supplied OAuth access token |
| `auth_token` | false | Runtime OAuth access token |

The profile may carry `drive_id` and `include_items_from_all_drives` when the
source is a shared drive. Never put a token in a query string, a file ID in a
credential field, or a credential in a `header_*` attribute.

## Source recipe

The generated transform must:

1. Build a read-only Drive client from the flat bearer credential.
2. Call `files.list` with an explicit `fields` projection and follow every
   `nextPageToken`.
3. Filter by the configured folder, MIME type, and non-trashed state.
4. Download each selected binary file with `files.get_media`.
5. Parse the file in memory and yield flat scalar dictionaries through a dlt
   resource. Add `_drive_file_id`, `_drive_file_name`, and
   `_drive_modified_time` so lineage and incremental filtering remain
   queryable.
6. Run one dlt pipeline through the `DuckDbOutput` destination and assert that
   only the promised physical models were written.

Google Workspace files are a separate mode: use the [source types index](source-types.md)
to select the Sheets recipe rather than treating a Sheet's export URL as a
normal binary CSV file.

## Credential and failure behavior

The source is local-only in the sense that the runner uses a synthetic token
and a local mock client; it must not call Google during an atomic test. A real
closure must report an unavailable or expired token instead of silently
falling back to anonymous access.

The atomic source contract distinguishes:

- missing or expired token → authentication failure;
- valid token without file access → authorization failure;
- valid token with access → file listing and download;
- a file whose MIME type is not accepted → skipped with a bounded diagnostic;
- a malformed accepted file → source failure naming the file ID, not its
  contents or credential.

The transform must not log file bodies, access tokens, authorization headers,
or full Drive API responses.

## Deterministic eval shape

The codegen eval supplies a pinned contract and synthetic responses for:

- two pages from `files.list`;
- one accepted CSV file;
- one ignored non-CSV file;
- one file download response;
- a permission-denied variant.

The checker verifies the generated client calls, token boundary, page-token
handling, MIME filtering, metadata columns, dlt resource shape, and absence of
live Google network calls. It does not launch the desktop supervisor.
