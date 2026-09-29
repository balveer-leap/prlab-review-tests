#!/usr/bin/env bash
# Replace main in every repo of an EXISTING org with one root commit holding the
# same files, so no history (and no deleted TRAPS.md) is reachable from it.
#
# For orgs created before 10-new-org-from-main.sh squashed on its own. The files
# do not change, workflow and .coderabbit.yaml included, so 20-/25- need no
# re-run. Open eval PRs lose their common history with main: run
# `prlab_eval cleanup --tool X` first and `prlab_eval setup --tool X` after.
#
# Usage: 15-squash-main.sh ORG
# Env:   DRY_RUN=1   print what would happen
# Writes squash-backup-<ORG>-<stamp>.tsv (old main SHAs) in the current directory.
set -euo pipefail

ORG="${1:?usage: 15-squash-main.sh <org>}"
# Never the source estate: every clone's origin points at it, and rewriting or
# deleting it removes what each new org is copied from.
SOURCE_OWNER=$({ git -C "$(cd "$(dirname "$0")" && pwd)/../../../cricket-protocol" remote get-url origin 2>/dev/null || true; } \
  | sed -E 's#.*github.com[:/]([^/]+)/.*#\1#')
SOURCE_OWNER="${SOURCE_OWNER:-srajat-leap}"
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }
[ "$(lower "$ORG")" != "$(lower "$SOURCE_OWNER")" ] \
  || { echo "refusing: $ORG is the source estate (the clones' origin)" >&2; exit 1; }
DRY_RUN="${DRY_RUN:-0}"
REPOS="protocol scoring broadcast stats fantasy highlights social archive notifications live-gateway mobile"
BACKUP="squash-backup-$ORG-$(date -u +%Y%m%dT%H%M%SZ).tsv"

for r in $REPOS; do
  repo="$ORG/prlab-cricket-$r"
  old=$(gh api "repos/$repo/git/ref/heads/main" --jq .object.sha)
  tree=$(gh api "repos/$repo/git/commits/$old" --jq .tree.sha)
  parents=$(gh api "repos/$repo/git/commits/$old" --jq '.parents|length')
  if [ "$parents" = 0 ]; then echo "  = $repo already one commit"; continue; fi
  if [ "$DRY_RUN" = 1 ]; then echo "DRY  $repo main=$old -> one commit with tree $tree"; continue; fi
  printf '%s\t%s\n' "$repo" "$old" >> "$BACKUP"
  new=$(gh api "repos/$repo/git/commits" --method POST -f message="Initial commit" -f tree="$tree" --jq .sha)
  gh api "repos/$repo/git/refs/heads/main" --method PATCH -f sha="$new" -F force=true >/dev/null
  got=$(gh api "repos/$repo/git/commits/$new" --jq '"\(.tree.sha) \(.parents|length)"')
  [ "$got" = "$tree 0" ] && echo "OK   $repo main=one commit" \
    || { echo "MISMATCH $repo got $got" >&2; exit 1; }
done
if [ -f "$BACKUP" ]; then echo "old main SHAs: $BACKUP"; fi
