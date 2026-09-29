#!/usr/bin/env bash
# Delete the 11 prlab-cricket-* repos of an org, so 10-new-org-from-main.sh can
# recreate them with no history at all.
#
# Only a fresh repo is truly clean: GitHub keeps every PR's commits under
# refs/pull/N/head, closed PRs included, and nobody can delete those refs. An
# org whose repos ever held TRAPS.md or a trap/* PR keeps them reachable until
# the repo itself is gone.
#
# Irreversible: PRs, reviews and comments on these repos are lost (the harness
# keeps each tool's comments in reports/). Org secrets and app installs set to
# "All repositories" survive; a portal setting per repo (Greptile Repo Cluster)
# must be redone.
#
# Usage: CONFIRM=ORG 05-delete-estate.sh ORG
# Needs: gh auth refresh -s delete_repo
set -euo pipefail

ORG="${1:?usage: CONFIRM=<org> 05-delete-estate.sh <org>}"
[ "${CONFIRM:-}" = "$ORG" ] || { echo "refusing: set CONFIRM=$ORG to delete $ORG's 11 estate repos" >&2; exit 1; }
# Never the source estate: every clone's origin points at it, and rewriting or
# deleting it removes what each new org is copied from.
SOURCE_OWNER=$({ git -C "$(cd "$(dirname "$0")" && pwd)/../../../cricket-protocol" remote get-url origin 2>/dev/null || true; } \
  | sed -E 's#.*github.com[:/]([^/]+)/.*#\1#')
SOURCE_OWNER="${SOURCE_OWNER:-srajat-leap}"
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }
[ "$(lower "$ORG")" != "$(lower "$SOURCE_OWNER")" ] \
  || { echo "refusing: $ORG is the source estate (the clones' origin)" >&2; exit 1; }
REPOS="protocol scoring broadcast stats fantasy highlights social archive notifications live-gateway mobile"

for r in $REPOS; do
  repo="$ORG/prlab-cricket-$r"
  if gh api "repos/$repo" --jq .name >/dev/null 2>&1; then
    gh repo delete "$repo" --yes
    echo "  - $repo deleted"
  else
    echo "  = $repo not present"
  fi
done
echo "next: 10-new-org-from-main.sh $ORG, then 20-/25- for that tool"
