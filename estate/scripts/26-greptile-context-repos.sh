#!/usr/bin/env bash
# Give Greptile cross-repo context: commit a greptile.json to main in every repo
# of ORG that lists the other 10 repos of the estate under context.repos
# (https://www.greptile.com/docs/code-review/greptile-json-reference).
#
# Greptile reads greptile.json from the PR's source branch. Eval branches are cut
# from main, so they carry the file without it ever appearing in a PR diff. Every
# repo lists all of its siblings, not the ones a trap touches: a per-case list
# names the consumers the reviewer is supposed to find.
#
# Builds on the org's own main (history-free, from 10-new-org-from-main.sh),
# never on the source history, and never force-pushes. A run that changes
# nothing pushes nothing. Re-run after 10- with REPLACE_MAIN=1.
#
# Usage: 26-greptile-context-repos.sh [ORG] [CLONES_DIR]
#   ORG         default org-greptile
#   CLONES_DIR  directory holding the cricket-* product clones (default: the
#               directory holding this repo)
# Env:
#   DRY_RUN=1   print what would happen
set -euo pipefail

ORG="${1:-org-greptile}"
HERE="$(cd "$(dirname "$0")" && pwd)"
CLONES="${2:-$HERE/../../..}"
PREFIX="${PREFIX:-prlab-}"
DRY_RUN="${DRY_RUN:-0}"
AUTHOR_NAME="${AUTHOR_NAME:-prlab estate}"
AUTHOR_EMAIL="${AUTHOR_EMAIL:-estate@prlab.invalid}"

gh api "orgs/$ORG" --jq .login >/dev/null 2>&1 || { echo "org $ORG not found or not visible to this token" >&2; exit 1; }

# Read with a loop, not mapfile: macOS still ships bash 3.2.
CLONE_DIRS=()
while IFS= read -r dir; do CLONE_DIRS+=("$dir"); done \
  < <(find "$CLONES" -maxdepth 1 -mindepth 1 -type d -name 'cricket-*' | sort)
[ "${#CLONE_DIRS[@]}" -gt 1 ] || { echo "no cricket-* clones under $CLONES" >&2; exit 1; }
NAMES=()
for dir in "${CLONE_DIRS[@]}"; do NAMES+=("${PREFIX}$(basename "$dir")"); done

json_for() {
  local self="$1" first=1
  printf '{\n  "context": {\n    "repos": [\n'
  for other in "${NAMES[@]}"; do
    [ "$other" = "$self" ] && continue
    [ "$first" = 1 ] && first=0 || printf ',\n'
    printf '      "%s/%s"' "$ORG" "$other"
  done
  printf '\n    ]\n  }\n}\n'
}

for dir in "${CLONE_DIRS[@]}"; do
  repo="${PREFIX}$(basename "$dir")"
  url="https://github.com/$ORG/$repo.git"
  git -C "$dir" fetch -q "$url" main
  work="_greptile_main_$$"
  git -C "$dir" checkout -q -B "$work" FETCH_HEAD
  json_for "$repo" > "$dir/greptile.json"
  links=$(grep -c "\"$ORG/" "$dir/greptile.json")
  git -C "$dir" add greptile.json
  if git -C "$dir" diff --cached --quiet; then
    echo "  = $ORG/$repo  context repos=$links  already current"
  else
    git -C "$dir" -c user.name="$AUTHOR_NAME" -c user.email="$AUTHOR_EMAIL" \
      commit -q -m "Add Greptile cross-repo context"
    if [ "$DRY_RUN" = 1 ]; then
      echo "DRY  $ORG/$repo  context repos=$links"
    else
      git -C "$dir" push -q "$url" "$work:refs/heads/main"
      echo "OK   $ORG/$repo  context repos=$links  main=$(git -C "$dir" rev-parse --short HEAD)"
    fi
  fi
  git -C "$dir" checkout -q main
  git -C "$dir" branch -q -D "$work"
done
