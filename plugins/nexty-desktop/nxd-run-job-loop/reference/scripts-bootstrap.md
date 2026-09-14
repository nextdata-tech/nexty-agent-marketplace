# Making the desktop helper scripts reachable

## Contents

- [Resolve `JOB_HELPER_DIR` once](#resolve-job_helper_dir-once)
- [Run the helpers](#run-the-helpers)

The helper scripts ship with **nxd-run-job-loop** at `scripts/`; most must stay in
that installed helper tree. `self_check.py` is the deliberate exception: it
imports no sibling helper, has a stdlib-only bootstrap, and is copied into the
generated closure root before it is run there with the closure's runtime
dependencies. A bare `scripts/...` path is invalid after installation, so resolve
the installed directory before calling the shared helpers or copying
`self_check.py`.

## Resolve `JOB_HELPER_DIR` once

Run this stdlib-only resolver before the first helper call. It emits one absolute
directory or fails. When the official scenario runner sets
`NXD_JOB_HELPER_DIR`, that exact staged directory is checked and no host cache
fallback is attempted. Without the variable, interactive sessions may discover
an installed copy as below. The resolver only discovers installed copies; it
does not create or edit Claude app state.

```bash
JOB_HELPER_DIR="$(python3 - "$HOME" "$PWD" "${NXD_JOB_HELPER_DIR:-}" <<'PY'
from pathlib import Path
import json
import re
import sys

home, cwd = map(Path, sys.argv[1:3])
injected = sys.argv[3].strip()
required = (
    "SKILL.md",
    "scripts/dp_diagnostics.py",
    "scripts/dp_spec_authoring.py",
    "scripts/dp_spec_v2.py",
    "scripts/requirements.txt",
    "scripts/self_check.py",
    "scripts/validate_dp_spec.py",
)

if injected:
    root = Path(injected).expanduser().resolve()
    plugin = root.parent.parent
    try:
        root.relative_to(plugin)
    except ValueError as exc:
        raise SystemExit("NXD_JOB_HELPER_DIR is outside its plugin root") from exc
    manifest_path = plugin / ".claude-plugin" / "plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit("NXD_JOB_HELPER_DIR has no readable plugin manifest") from exc
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if not isinstance(version, str) or not version.strip():
        raise SystemExit("NXD_JOB_HELPER_DIR plugin manifest has no version")
    missing = [relative for relative in required if not (root / relative).is_file()]
    if missing:
        raise SystemExit("NXD_JOB_HELPER_DIR is incomplete: " + ", ".join(missing))
    skill_text = (root / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r"(?ms)^metadata:\s*$.*?^\s+version:\s*([^\s#]+)\s*$", skill_text)
    if match is None or match.group(1).strip("\"'") != version:
        raise SystemExit("NXD_JOB_HELPER_DIR skill version does not match its plugin manifest")
    print(root)
    raise SystemExit(0)

roots = [home / ".claude" / "skills" / "nxd-run-job-loop"]
roots += [parent / ".claude" / "skills" / "nxd-run-job-loop" for parent in (cwd, *cwd.parents)]
plugins = home / ".claude" / "plugins"
searched = list(roots)
plugin_src = plugins / "**/src/nxd-run-job-loop"
plugin_skills = plugins / "**/skills/nxd-run-job-loop"
searched += [plugin_src, plugin_skills]
def _version_key(root):
    parts = root.parents[1].name.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return (-1, -1, -1)
    return tuple(int(part) for part in parts)


plugin_candidates = [
    *plugins.glob("**/src/nxd-run-job-loop"),
    *plugins.glob("**/skills/nxd-run-job-loop"),
]
roots += sorted(plugin_candidates, key=lambda root: (_version_key(root), str(root)), reverse=True)


for mount in (home, home / "mnt"):
    local_plugins = mount / ".local-plugins"
    cached_candidates = []
    for plugin_name in ("nexty-desktop", "nexty-datamesh", "nexty-agent-skills"):
        cached_pattern = local_plugins / f"cache/nexty/{plugin_name}/*/skills/nxd-run-job-loop"
        searched.append(cached_pattern)
        cached = list(local_plugins.glob(str(cached_pattern.relative_to(local_plugins))))
        cached_candidates += cached
    roots += sorted(cached_candidates, key=lambda root: (_version_key(root), str(root)), reverse=True)
claude = home / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions"
plugin_patterns = [
    claude / f"*/*/cowork_plugins/cache/nexty/{plugin_name}/*/skills/nxd-run-job-loop"
    for plugin_name in ("nexty-desktop", "nexty-datamesh", "nexty-agent-skills")
]
skills_plugin_pattern = claude / "skills-plugin/*/*/*/skills/nxd-run-job-loop"
searched += [*plugin_patterns, skills_plugin_pattern]
desktop_candidates = []
for pattern in plugin_patterns:
    desktop_candidates += list(claude.glob(str(pattern.relative_to(claude))))
desktop_candidates += list(claude.glob("skills-plugin/*/*/*/skills/nxd-run-job-loop"))
roots += sorted(desktop_candidates, key=lambda root: (_version_key(root), str(root)), reverse=True)
for root in roots:
    skill = root / "SKILL.md"
    if ((root / "scripts/dp_diagnostics.py").is_file()
            and (root / "scripts/validate_dp_spec.py").is_file()
            and (root / "scripts/dp_spec_authoring.py").is_file()
            and (root / "scripts/dp_spec_v2.py").is_file()
            and (root / "scripts/self_check.py").is_file()
            and skill.is_file()):
        print(root.resolve())
        break
else:
    attempted = "\n".join(f"  - {root}" for root in searched)
    raise SystemExit(
        "nxd-run-job-loop helpers not found; searched these roots/patterns:\n"
        f"{attempted}\n"
        "Install or upload the skill, then retry."
    )
PY
)"
test -n "$JOB_HELPER_DIR"
```

Pass this absolute value as `job_helper_dir` whenever Step 3 invokes
`nxd-generate-data-product`. The generator must use that exact path; it must not
resolve a second copy.

## Run the helpers

The helpers require PyYAML, declared in
`"$JOB_HELPER_DIR/scripts/requirements.txt"`. Run them from a Python
environment that has installed that file's requirements. For example:

```bash
python3 -m pip install -r "$JOB_HELPER_DIR/scripts/requirements.txt"
python3 "$JOB_HELPER_DIR/scripts/validate_dp_spec.py" <workflow>/dp-blueprint.md --json
python3 "$JOB_HELPER_DIR/scripts/dp_diagnostics.py" lock verify <closure> \
    --spec <workflow>/dp-blueprint.md

# The v3 parser owns user-facing authoring. v2 remains only for verifying old
# closure evidence; v1 is rejected by both paths.
python3 "$JOB_HELPER_DIR/scripts/dp_spec_authoring.py" validate <workflow>/dp-blueprint.md --json

# Only when inspecting an existing v2 closure artifact:
python3 "$JOB_HELPER_DIR/scripts/dp_spec_v2.py" validate <legacy-v2-spec.md>
python3 "$JOB_HELPER_DIR/scripts/dp_spec_v2.py" schema
```

Only `self_check.py` is intended to be copied out of this tree:

```bash
cp "$JOB_HELPER_DIR/scripts/self_check.py" <closure>/self_check.py
cd <closure> && python3 self_check.py --json --record build-record.json
```

Keep `dp_diagnostics.py`, `validate_dp_spec.py`, `dp_spec_authoring.py`, and
`dp_spec_v2.py` in the resolved helper tree. The shared diagnostic and validation
path imports companion modules from that directory; copying one of those files
alone does not make it runnable from an isolated closure or execution shell.

The adversarial-review dispatch is deliberately different: it receives only the
closure path and verbatim request, never `job_helper_dir`; it verifies the
generator's recorded evidence without executing helpers.
