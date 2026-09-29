#!/usr/bin/env bash
# Push the Claude review workflow onto main of every repo in a Claude org.
# Usage: ./20-install-claude-workflow.sh org-claudplain  normal
#        ./20-install-claude-workflow.sh org-claudeskill best
set -euo pipefail
ORG="${1:?usage: 20-install-claude-workflow.sh <org> <normal|best>}"
KIND="${2:?usage: 20-install-claude-workflow.sh <org> <normal|best>}"
SRC="$(dirname "$0")/../workflows/claude-$KIND.yml"
[ -f "$SRC" ] || { echo "ERROR: no workflow at $SRC"; exit 1; }
REPOS="protocol scoring broadcast stats fantasy highlights social archive notifications live-gateway mobile"
# tr, not `base64 -w0`: the flag is GNU-only and missing on older macOS.
B64=$(base64 < "$SRC" | tr -d '\n')
PATH_IN_REPO=".github/workflows/claude-review.yml"
for r in $REPOS; do
  repo="$ORG/prlab-cricket-$r"
  sha=$(gh api "repos/$repo/contents/$PATH_IN_REPO" --jq '.sha' 2>/dev/null || true)
  if [ -n "$sha" ]; then
    gh api "repos/$repo/contents/$PATH_IN_REPO" --method PUT \
      -f message="Update Claude review workflow ($KIND)" -f content="$B64" -f sha="$sha" \
      --jq '"  ~ \(.content.path) in '"$repo"'"'
  else
    gh api "repos/$repo/contents/$PATH_IN_REPO" --method PUT \
      -f message="Add Claude review workflow ($KIND)" -f content="$B64" \
      --jq '"  + \(.content.path) in '"$repo"'"'
  fi
done
