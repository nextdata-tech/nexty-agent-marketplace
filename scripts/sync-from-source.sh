#!/usr/bin/env bash

set -euo pipefail

SOURCE_ROOT="${1:?usage: $0 /path/to/nexty-agent-skills [destination-root]}"
DEST_ROOT="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SOURCE_MANIFEST="$SOURCE_ROOT/.claude-plugin/marketplace.json"
DEST_MANIFEST="$DEST_ROOT/.claude-plugin/marketplace.json"

[[ -f "$SOURCE_MANIFEST" ]] || { echo "missing source marketplace: $SOURCE_MANIFEST" >&2; exit 1; }
[[ -d "$SOURCE_ROOT/src" ]] || { echo "missing source skill tree: $SOURCE_ROOT/src" >&2; exit 1; }

SKILLS_TEXT="$(python3 - "$SOURCE_MANIFEST" <<'PY'
import json
import re
import sys

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
for plugin in manifest.get("plugins", []):
    if plugin.get("name") == "nexty-desktop":
        skills = plugin.get("skills")
        if plugin.get("source") != "./src" or not isinstance(skills, list) or not skills:
            raise SystemExit("source nexty-desktop entry must use a non-empty ./src skills list")
        for skill in skills:
            if (
                not isinstance(skill, str)
                or not skill.startswith("./")
                or not NAME_RE.fullmatch(skill[2:])
            ):
                raise SystemExit(f"invalid Desktop skill path: {skill!r}")
            print(skill[2:])
        break
else:
    raise SystemExit("source marketplace has no nexty-desktop entry")
PY
)"
[[ -n "$SKILLS_TEXT" ]] || { echo "source nexty-desktop entry produced no skills" >&2; exit 1; }
mapfile -t SKILLS <<<"$SKILLS_TEXT"

for skill in "${SKILLS[@]}"; do
  source_skill="$SOURCE_ROOT/src/$skill"
  [[ -f "$source_skill/SKILL.md" ]] || { echo "missing source skill: $skill" >&2; exit 1; }
done

SOURCE_VERSION="$(python3 - "$SOURCE_ROOT/.claude-plugin/plugin.json" <<'PY'
import json
import sys
version = json.load(open(sys.argv[1], encoding="utf-8")).get("version")
if not isinstance(version, str) or not version:
    raise SystemExit("source plugin manifest has no version")
print(version)
PY
)"

DEST_PLUGIN="$DEST_ROOT/plugins/nexty-desktop"
DEST_SKILLS="$DEST_PLUGIN/skills"
mkdir -p "$DEST_PLUGIN/.claude-plugin" "$DEST_SKILLS"

# The plugin root contains only its manifest and the conventional skills tree.
# Remove generated content from both the old flat layout and the new layout so
# deleted or renamed skills cannot remain discoverable after a sync.
find "$DEST_PLUGIN" -mindepth 1 -maxdepth 1 \
  ! -name '.claude-plugin' ! -name 'skills' -exec rm -rf {} +
find "$DEST_SKILLS" -mindepth 1 -maxdepth 1 -exec rm -rf {} +

for skill in "${SKILLS[@]}"; do
  source_skill="$SOURCE_ROOT/src/$skill"
  mkdir -p "$DEST_SKILLS/$skill"
  rsync -a --delete \
    --exclude '.git' --exclude '.git/**' \
    --exclude '.DS_Store' \
    --exclude '__pycache__' --exclude '__pycache__/**' \
    --exclude '*.pyc' --exclude '*.pyo' \
    --exclude '.pytest_cache' --exclude '.pytest_cache/**' \
    "$source_skill/" "$DEST_SKILLS/$skill/"
done

python3 - "$DEST_MANIFEST" "$SOURCE_VERSION" <<'PY'
import json
import sys
from pathlib import Path

path, version = sys.argv[1:]
manifest = json.loads(Path(path).read_text(encoding="utf-8"))
manifest["plugins"][0]["version"] = version
Path(path).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
PY

python3 - "$DEST_PLUGIN/.claude-plugin/plugin.json" "$SOURCE_VERSION" <<'PY'
import json
import sys
from pathlib import Path

path, version = sys.argv[1:]
path_obj = Path(path)
if path_obj.exists():
    manifest = json.loads(path_obj.read_text(encoding="utf-8"))
else:
    manifest = {
        "name": "nexty-desktop",
        "displayName": "Nexty Desktop",
        "description": "Build, serve, query, refine, and export local data products through the Nexty Desktop supervisor.",
        "author": {"name": "Nextdata"},
        "homepage": "https://github.com/nextdata-tech/nexty-agent-marketplace",
        "repository": "https://github.com/nextdata-tech/nexty-agent-marketplace",
    }
manifest["version"] = version
manifest.pop("skills", None)
path_obj.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
PY

echo "synchronized ${#SKILLS[@]} Desktop skills at ${SOURCE_VERSION}"
