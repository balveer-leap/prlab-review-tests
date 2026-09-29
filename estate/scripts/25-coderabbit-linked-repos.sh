#!/usr/bin/env bash
# Give CodeRabbit cross-repo context: commit a .coderabbit.yaml to main in every
# repo of ORG that links the other repos of the estate (CodeRabbit Multi-Repo
# Analysis, knowledge_base.linked_repositories).
#
# Adds one commit with only .coderabbit.yaml on top of the org's own main, so eval
# PRs branched from it carry the same config. It builds on the org's main, never
# on SRC_REMOTE/main: the source history still holds the deleted TRAPS.md, and
# 10-new-org-from-main.sh gives the org a history-free main. Re-run after every
# re-sync; a run that changes nothing pushes nothing.
#
# Usage: 25-coderabbit-linked-repos.sh [ORG] [CLONES_DIR]
#   ORG         default org-coderabbit1
#   CLONES_DIR  directory holding the cricket-* product clones (default: the
#               directory holding this repo)
#
# Plan limits: Free 0, Essentials 1, Team 5, Advanced (and the default trial) 10,
# Enterprise 20 linked repos. The estate has 11 repos, so each links 10 siblings.
set -euo pipefail

ORG="${1:-org-coderabbit1}"
HERE="$(cd "$(dirname "$0")" && pwd)"
CLONES="${2:-$HERE/../../..}"
SRC_REMOTE="${SRC_REMOTE:-origin}"
PREFIX="${PREFIX:-prlab-}"
# Neutral on purpose: no trap, field, or domain hint.
INSTRUCTIONS="${INSTRUCTIONS:-Sibling service in the same estate.}"
AUTHOR_NAME="${AUTHOR_NAME:-$(git config user.name)}"
AUTHOR_EMAIL="${AUTHOR_EMAIL:-$(git config user.email)}"
DRY_RUN="${DRY_RUN:-0}"

# Read with a loop, not mapfile: macOS still ships bash 3.2.
CLONE_DIRS=()
while IFS= read -r dir; do CLONE_DIRS+=("$dir"); done \
  < <(find "$CLONES" -maxdepth 1 -mindepth 1 -type d -name 'cricket-*' | sort)
[ "${#CLONE_DIRS[@]}" -gt 1 ] || { echo "no cricket-* clones under $CLONES" >&2; exit 1; }
NAMES=()
for dir in "${CLONE_DIRS[@]}"; do NAMES+=("${PREFIX}$(basename "$dir")"); done

yaml_for() {
  local self="$1"
  printf 'reviews:\n  review_details: true\nknowledge_base:\n  automatic_linking_mode: disabled\n  linked_repositories:\n'
  for other in "${NAMES[@]}"; do
    [ "$other" = "$self" ] && continue
    printf '    - repository: "%s/%s"\n      instructions: "%s"\n' "$ORG" "$other" "$INSTRUCTIONS"
  done
}

for dir in "${CLONE_DIRS[@]}"; do
  repo="${PREFIX}$(basename "$dir")"
  url="https://github.com/$ORG/$repo.git"
  git -C "$dir" fetch -q "$url" main
  base=$(git -C "$dir" rev-parse FETCH_HEAD)
  work="_coderabbit_main_$$"
  git -C "$dir" checkout -q -B "$work" "$base"
  yaml_for "$repo" > "$dir/.coderabbit.yaml"
  links=$(grep -c 'repository:' "$dir/.coderabbit.yaml")
  git -C "$dir" add .coderabbit.yaml
  if git -C "$dir" diff --cached --quiet; then
    echo "  = $ORG/$repo  links=$links  already current"
  else
    git -C "$dir" -c user.name="$AUTHOR_NAME" -c user.email="$AUTHOR_EMAIL" \
      commit -q -m "Add CodeRabbit linked repositories"
    if [ "$DRY_RUN" = 1 ]; then
      echo "DRY  $ORG/$repo  links=$links"
    else
      git -C "$dir" push -q "$url" "$work:refs/heads/main"
      echo "OK   $ORG/$repo  links=$links  main=$(git -C "$dir" rev-parse --short HEAD)"
    fi
  fi
  git -C "$dir" checkout -q main
  git -C "$dir" branch -q -D "$work"
done
