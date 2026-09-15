#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/nexty-marketplace-sync.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

SOURCE_ROOT="$TEST_ROOT/source"
DEST_ROOT="$TEST_ROOT/destination"
mkdir -p "$SOURCE_ROOT/.claude-plugin" "$SOURCE_ROOT/src"

cat >"$SOURCE_ROOT/.claude-plugin/marketplace.json" <<'JSON'
{
  "plugins": [
    {
      "name": "nexty-desktop",
      "source": "./src",
      "skills": ["./nxd-alpha", "./nxd-beta"]
    }
  ]
}
JSON
printf '{"version":"1.2.3"}\n' >"$SOURCE_ROOT/.claude-plugin/plugin.json"

for skill in nxd-alpha nxd-beta; do
  mkdir -p "$SOURCE_ROOT/src/$skill/reference"
  printf '%s\n' "---" "name: $skill" "---" >"$SOURCE_ROOT/src/$skill/SKILL.md"
  printf 'reference for %s\n' "$skill" >"$SOURCE_ROOT/src/$skill/reference/overview.md"
done
mkdir -p "$SOURCE_ROOT/src/nxd-alpha/scripts/__pycache__"
printf 'generated cache\n' >"$SOURCE_ROOT/src/nxd-alpha/scripts/__pycache__/ignored.pyc"

DEST_PLUGIN="$DEST_ROOT/plugins/nexty-desktop"
mkdir -p "$DEST_ROOT/.claude-plugin" "$DEST_PLUGIN/.claude-plugin" "$DEST_PLUGIN/skills/nxd-stale"
mkdir -p "$DEST_PLUGIN/nxd-stale"
printf 'stale\n' >"$DEST_PLUGIN/nxd-stale/SKILL.md"
printf 'stale\n' >"$DEST_PLUGIN/skills/nxd-stale/SKILL.md"
printf '{"plugins":[{"name":"nexty-desktop","version":"0.0.0"}]}\n' \
  >"$DEST_ROOT/.claude-plugin/marketplace.json"
printf '{"name":"nexty-desktop","skills":"./old"}\n' \
  >"$DEST_PLUGIN/.claude-plugin/plugin.json"

run_sync() {
  bash "$SCRIPT_DIR/sync-from-source.sh" "$SOURCE_ROOT" "$DEST_ROOT" >/dev/null
}

assert_file() {
  [[ -f "$1" ]] || { echo "missing expected file: $1" >&2; exit 1; }
}

assert_absent() {
  [[ ! -e "$1" ]] || { echo "unexpected path: $1" >&2; exit 1; }
}

run_sync
for skill in nxd-alpha nxd-beta; do
  assert_file "$DEST_PLUGIN/skills/$skill/SKILL.md"
  assert_file "$DEST_PLUGIN/skills/$skill/reference/overview.md"
  assert_absent "$DEST_PLUGIN/$skill"
done
assert_absent "$DEST_PLUGIN/skills/nxd-stale"
assert_absent "$DEST_PLUGIN/skills/nxd-alpha/scripts/__pycache__"
if grep -q '"skills"' "$DEST_PLUGIN/.claude-plugin/plugin.json"; then
  echo "generated plugin manifest contains a skills override" >&2
  exit 1
fi
grep -q '"version": "1.2.3"' "$DEST_PLUGIN/.claude-plugin/plugin.json"

FIRST_RESULT="$TEST_ROOT/first-result"
cp -R "$DEST_ROOT" "$FIRST_RESULT"
run_sync
diff -ru "$FIRST_RESULT" "$DEST_ROOT"

VALID_MARKETPLACE="$TEST_ROOT/valid-marketplace.json"
cp "$SOURCE_ROOT/.claude-plugin/marketplace.json" "$VALID_MARKETPLACE"
for invalid_path in './skills/nxd-alpha' './..' './.'; do
  printf '{"plugins":[{"name":"nexty-desktop","source":"./src","skills":["%s"]}]}\n' \
    "$invalid_path" >"$SOURCE_ROOT/.claude-plugin/marketplace.json"
  if run_sync 2>/dev/null; then
    echo "invalid skill path was accepted: $invalid_path" >&2
    exit 1
  fi
  diff -ru "$FIRST_RESULT" "$DEST_ROOT"
done
cp "$VALID_MARKETPLACE" "$SOURCE_ROOT/.claude-plugin/marketplace.json"

printf '%s\n' '{"plugins":[{"name":"nexty-desktop","source":"./src","skills":["./nxd-missing"]}]}' \
  >"$SOURCE_ROOT/.claude-plugin/marketplace.json"
if run_sync 2>/dev/null; then
  echo "missing skill was accepted" >&2
  exit 1
fi
diff -ru "$FIRST_RESULT" "$DEST_ROOT"

echo "sync fixture passed"
