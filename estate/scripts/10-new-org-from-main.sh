#!/usr/bin/env bash
# Create the 11-repo estate in a NEW org from the product repos' current main.
#
# Pushes main and nothing else: no eval branches, no workflow, no review config.
# This is the out-of-the-box starting point every vendor org gets. Eval PRs are
# opened later by `prlab_eval setup`, which is the only supported way to create
# them — it uses neutral eval/* branch names and PR bodies that never reference
# this harness.
#
# main is pushed as ONE root commit holding the source main's files, not the
# source history. That history still contains the deleted TRAPS.md answer key,
# and a vendor that indexes the whole repo would read it. Re-running it on an
# existing org fills in missing repos and leaves every existing main alone,
# unless REPLACE_MAIN=1.
#
# Usage: 10-new-org-from-main.sh ORG [CLONES_DIR]
#   ORG         e.g. org-codeant (create it in GitHub first; the API cannot)
#   CLONES_DIR  directory with the cricket-* product clones (default: the
#               directory holding this repo)
# Env:
#   VISIBILITY  private (default) or public
#   DRY_RUN=1   print what would happen
#   REPLACE_MAIN=1  replace main in repos that already have one (re-run 20-/25-
#                   after, and re-open that tool's eval PRs)
set -euo pipefail

ORG="${1:?usage: 10-new-org-from-main.sh <org> [clones-dir]}"
HERE="$(cd "$(dirname "$0")" && pwd)"
CLONES="${2:-$HERE/../../..}"
# Never the source estate: every clone's origin points at it, and rewriting or
# deleting it removes what each new org is copied from.
SOURCE_OWNER=$({ git -C "$CLONES/cricket-protocol" remote get-url origin 2>/dev/null || true; } \
  | sed -E 's#.*github.com[:/]([^/]+)/.*#\1#')
SOURCE_OWNER="${SOURCE_OWNER:-srajat-leap}"
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }
[ "$(lower "$ORG")" != "$(lower "$SOURCE_OWNER")" ] \
  || { echo "refusing: $ORG is the source estate (the clones' origin)" >&2; exit 1; }
VISIBILITY="${VISIBILITY:-private}"
SRC_REMOTE="${SRC_REMOTE:-origin}"
PREFIX="${PREFIX:-prlab-}"
DRY_RUN="${DRY_RUN:-0}"

case "$VISIBILITY" in private) PRIVATE=true ;; public) PRIVATE=false ;; *) echo "VISIBILITY must be private or public" >&2; exit 1 ;; esac
gh api "orgs/$ORG" --jq .login >/dev/null 2>&1 || { echo "org $ORG not found or not visible to this token" >&2; exit 1; }

# Read with a loop, not mapfile: macOS still ships bash 3.2.
DIRS=()
while IFS= read -r dir; do DIRS+=("$dir"); done \
  < <(find "$CLONES" -maxdepth 1 -mindepth 1 -type d -name 'cricket-*' | sort)
[ "${#DIRS[@]}" -gt 0 ] || { echo "no cricket-* clones under $CLONES" >&2; exit 1; }

for dir in "${DIRS[@]}"; do
  repo="${PREFIX}$(basename "$dir")"
  git -C "$dir" fetch -q "$SRC_REMOTE"
  src=$(git -C "$dir" rev-parse --short "$SRC_REMOTE/main")
  if [ "$DRY_RUN" = 1 ]; then echo "DRY  $ORG/$repo  ($VISIBILITY)  main=$src"; continue; fi
  if gh api "repos/$ORG/$repo" --jq .name >/dev/null 2>&1; then
    # An existing main carries the workflow or .coderabbit.yaml commit, and open
    # eval PRs branch from it. Replacing it breaks both, so it takes a flag.
    if gh api "repos/$ORG/$repo/branches/main" --jq .name >/dev/null 2>&1 && [ "${REPLACE_MAIN:-0}" != 1 ]; then
      echo "  = $ORG/$repo exists; main left as it is (REPLACE_MAIN=1 replaces it)"
      continue
    fi
    echo "  = $ORG/$repo exists"
  else
    gh api -X POST "orgs/$ORG/repos" -f name="$repo" -F private="$PRIVATE" \
      -f description="PR-review eval copy of $repo" >/dev/null
    echo "  + $ORG/$repo created ($VISIBILITY)"
  fi
  tree=$(git -C "$dir" rev-parse "$SRC_REMOTE/main^{tree}")
  root=$(git -C "$dir" -c user.name="prlab estate" -c user.email="estate@prlab.invalid" \
    commit-tree "$tree" -m "Initial commit")
  # A repo created a moment ago can still answer "not found" to git.
  for try in 1 2 3 4 5 6; do
    git -C "$dir" push -q --force "https://github.com/$ORG/$repo.git" "$root:refs/heads/main" && break
    [ "$try" = 6 ] && { echo "push to $ORG/$repo failed" >&2; exit 1; }
    sleep 5
  done
  # A just-pushed main can 404 on the API for a few seconds.
  dst=""
  for _ in 1 2 3 4 5 6; do
    sha=$(gh api "repos/$ORG/$repo/commits/main" --jq .sha 2>/dev/null) \
      && dst=$(gh api "repos/$ORG/$repo/git/commits/$sha" --jq '"\(.tree.sha) \(.parents|length)"' 2>/dev/null) \
      && break
    sleep 5
  done
  [ "$dst" = "$tree 0" ] && echo "OK   $ORG/$repo main=one commit, files of $src" \
    || { echo "MISMATCH $ORG/$repo want tree $tree with no parents, got $dst" >&2; exit 1; }
done
