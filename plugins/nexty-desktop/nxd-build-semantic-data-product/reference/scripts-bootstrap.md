# Making the profiler reachable from Bash

The semantic-builder skill is script-first for local source inference. The
profiler may be absent from the shell's mounted skill tree even when the skill
documentation is available, so resolve its directory once before profiling.

## Probe and bootstrap

1. Set `$SKILL_DIR` to the absolute directory containing this skill's
   `SKILL.md`.
2. Probe the profiler:

   ```bash
   if [ -f "$SKILL_DIR/scripts/profile_tabular.py" ]; then
     PROFILE_SKILL_DIR="$SKILL_DIR"
   else
     PROFILE_SKILL_DIR=""
   fi
   ```

3. If `PROFILE_SKILL_DIR` is empty, materialize this skill's `scripts/`
   directory into a writable scratch directory with the file-copy tool. Keep
   `profile_tabular.py` at `<scratch>/scripts/profile_tabular.py`; it is a
   standalone entrypoint and does not need the analyzer's `drivers/` or
   `meshlib/` packages.
4. Confirm the copy before continuing:

   ```bash
   python3 -B - "$PROFILE_SKILL_DIR/scripts/profile_tabular.py" <<'PY'
   import ast
   from pathlib import Path
   import sys

   ast.parse(Path(sys.argv[1]).read_text(encoding="utf-8"))
   PY
   ```

Cache `PROFILE_SKILL_DIR` for the session. Run the profiler from that resolved
directory for every source so the `schema.json` handoff is produced by one
consistent script copy.

## Dependencies

CSV, JSON, and JSONL inputs use the Python standard library. DuckDB mode needs
the `duckdb` package, and Parquet mode needs `pandas` plus a Parquet engine such
as `pyarrow`. Prefer an ephemeral environment:

```bash
python3 -m venv .nxd-semantic-profiler-venv
.nxd-semantic-profiler-venv/bin/pip install -r "$PROFILE_SKILL_DIR/scripts/requirements.txt"
```

For a one-off DuckDB profile, `uv run --with duckdb python` is sufficient.
