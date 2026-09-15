# DLT Data Product Authoring

Data Load Tool (dlt) is used to author job loop transformation functions. The source and documentation is
available at https://github.com/dlt-hub/dlt/archive/refs/tags/VERSION.tar.gz (substitute the actual dlt release
tag for `VERSION`).

**nxd-generate-data-product owns the closure shape end to end — do not re-derive or re-explain it here.** That includes the
`infra-profile.yaml` services, the `duckdb`-named output port and transform parameter, the
`ingest(duckdb: DuckDbOutput, secrets: dict[str, Any])` signature, the connector-type-specific `secrets` keys /
service name (`csv_source`/`csv-source` using `nxd:local/file/storage:0.1.0`, `file_source`/`file-source`; for the
`generic-secrets` connectors `db-source` and `api-source` the keys are that service's own flat `attributes` —
`secrets["host"]`, `secrets["base_url"]` — never `secrets["db_source"]`/`secrets["api_source"]`), and their
labeled multi-source variants, where each instance's attribute keys carry a `<label>_` prefix. See nxd-generate-data-product's `SKILL.md` and its
`reference/` directory (`multi-source.md` for the labeled-instance shape) for the exact, current wiring — never
construct an ad hoc infra profile or transform signature from memory of this file.
