# Host-local direct CLI lifecycle

This is the fallback procedure for a confirmed host-local Darwin shell when
the `nxd-desktop` MCP tools are not connected. It is deliberately narrower than
the MCP path: the supervisor process is the source of truth, and the shell must
not become a second lifecycle controller.

## Start and observe one serve

Run one foreground command:

```sh
nxd-desktop-supervisor serve \
  --definition <closure> \
  --workflow <workflow> \
  --data-dir <state>
```

If the shell tool backgrounds a long-running command, capture the task id and the output path
returned by that tool; read that output file at bounded intervals. Do not redirect the command
to a second log and poll the log with a shell loop: the supervisor owns the run lifecycle, and
an empty output does not distinguish a running process from a failed one.

The only successful receipt is the supervisor's publication metadata, including
`published=yes`, `run_id`, `artifact_id`, `definition_id`, and
`semantic_endpoint`. After that receipt, run `status` and `describe` before any
query. Keep the bearer in the protected environment or bearer file and pass it
only to the command that needs it.

The documented host-local `check` invocation intentionally omits `--data-dir`:
the check uses the supervisor's configured/default data store, like the MCP
surface, and its input contract here is the definition plus workflow. If a host
explicitly overrides the supervisor data directory, pass that same directory
consistently to both `check` and `serve`; never check one store and publish into
another.

## Bounded failure handling

Use a bounded observation deadline that matches the configured transform budget.
Read the task output once more at the deadline. If no publication receipt
exists, report that the run did not publish, preserve the closure and the
supervisor output, and stop the process cooperatively. Never infer a traceback
from an empty output file, read or edit supervisor SQLite state, use `tail -f`,
or leave a background poller running.

When MCP is available, call `inspect_run` exactly once with the failed `run_id`
and classify the returned diagnostic. Without MCP, the supervisor's terminal
output is the available evidence; do not replace it with a guessed cause.

The direct CLI fallback does not change the semantic-layer contract: describe
the catalog returned by the supervisor and issue governed selections only after
publication.
