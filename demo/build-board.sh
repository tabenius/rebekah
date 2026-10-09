#!/usr/bin/env bash
# Build a kanban projection from real WeftMark records, over real Git history.
#
# Nothing here is hand-written state. Every card comes from a Change Set created
# through the CLI, evidence is a command that actually ran, and the attention
# flags are what the projection itself concludes from those records. The point of
# a demo is that the awkward cards are awkward for a reason.
set -euo pipefail

REPO="${1:-/tmp/km-board}"
# Locate the WeftMark source tree. WMSRC wins; otherwise walk up from this
# script looking for a sibling checkout, so the demo does not hardcode a path
# that is only true on one box.
if [[ -z "${WMSRC:-}" ]]; then
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  while [[ "$here" != "/" ]]; do
    if [[ -d "$here/weftmark/src/weftmark" ]]; then WMSRC="$here/weftmark/src"; break; fi
    here="$(dirname "$here")"
  done
fi
: "${WMSRC:?set WMSRC to the src/ directory of the weftmark checkout}"
export PYTHONPATH="$WMSRC"
WM="timeout 120 python3 -m weftmark.cli.main"

mkdir -p "$REPO"
cd "$REPO"

git init -q .
git config user.email demo@ragbaz.cc
git config user.name "Rebekah demo"
mkdir -p src docs
[ -f README.md ] || echo "# Board demo" > README.md
[ -f src/a.js ] || echo "export const x = 1;" > src/a.js
[ -f docs/n.md ] || echo "notes" > docs/n.md
git add -A
git commit -qm "base commit" 2>/dev/null || true
BASE=$(git rev-parse HEAD)

$WM changeset create cs-evidence --goal "Bind a passing check to the revision it inspected" --base "$BASE" --scope "file:src/**" >/dev/null
$WM changeset create cs-collide --goal "Widen the API contract" --base "$BASE" --scope "contract:api-v1" >/dev/null
$WM changeset create cs-merge --goal "Ship the collector" --base "$BASE" --scope "file:docs/**" >/dev/null
$WM changeset create cs-orphan --goal "Never started, no evidence" --base "$BASE" --scope "file:src/**" >/dev/null

# 1. Evidence that passed, bound to this head: a card that can reach "ready".
#    The tool takes an argument vector, not a shell string, so no quoting games.
$WM evidence run cs-evidence --kind test --id unit --command git rev-parse HEAD

# 2. Evidence that failed, recorded honestly: a failed check is information.
$WM evidence run cs-collide --kind test --id suite --command false || true

# 3. Advance one head without re-running its check. This is the whole point of
#    binding evidence to a revision: the receipt stays on file and goes stale,
#    and the board says so rather than reporting last week's pass as current.
echo "// advanced without re-running evidence" >> src/a.js
git add -A && git commit -qm "advance the head, leave the receipt behind"
$WM changeset refresh cs-evidence

# 4. Two change sets declaring the same semantic scope: a collision is a fact
#    about two cards, so it cannot be resolved by looking at either alone.
$WM changeset create cs-collide-2 --goal "A second, conflicting claim on api-v1" --base "$BASE" --scope "contract:api-v1" >/dev/null

# 5. A recorded review, then the transitions that review makes legal. The tool
#    refuses to move a card to review without a decision behind it, which is the
#    behaviour worth showing: the lifecycle is derived, not asserted.
$WM review create cs-merge --id rev-1 --author reviewer --require test || true
$WM changeset transition cs-merge review || true
$WM changeset transition cs-merge merged || true

# 6. A dirty worktree, refreshed so it is recorded rather than hidden.
echo "// uncommitted" >> src/a.js
$WM changeset refresh cs-collide || true
$WM changeset refresh cs-orphan || true

echo "built in $REPO"