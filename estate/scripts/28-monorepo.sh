#!/usr/bin/env bash
# Build a monorepo variant of the estate: ONE repo holding every service as a
# folder (cricket-protocol/, cricket-scoring/, ...), for a reviewer that only
# sees the repo a PR is in (CodeAnt has no cross-repo feature). Consumers' code
# is then in the same repo as the change.
#
# main is one root commit of the source files (no history, so no deleted
# TRAPS.md). With CONFIG_FROM=<repo in ORG>, that repo's .codeant/ and
# greptile.json are copied to the root. The repo is cloned next to this harness
# so `prlab_eval setup` can apply each patch under its service folder.
#
# Point a tool at it in cases/owners.json as "ORG/REPO".
#
# Usage: 28-monorepo.sh ORG [REPO] [CLONES_DIR]
#   REPO        default prlab-cricket-estate
# Env:
#   VISIBILITY  private (default) or public
#   CONFIG_FROM repo in ORG whose root config to copy, e.g. prlab-cricket-scoring
#   REPLACE_MAIN=1  replace main if the repo already has one
set -euo pipefail

ORG="${1:?usage: 28-monorepo.sh <org> [repo] [clones-dir]}"
REPO="${2:-prlab-cricket-estate}"
HERE="$(cd "$(dirname "$0")" && pwd)"
CLONES="${3:-$HERE/../../..}"
VISIBILITY="${VISIBILITY:-private}"
SRC_REMOTE="${SRC_REMOTE:-origin}"
CONFIG_FROM="${CONFIG_FROM:-}"

SOURCE_OWNER=$({ git -C "$CLONES/cricket-protocol" remote get-url origin 2>/dev/null || true; } \
  | sed -E 's#.*github.com[:/]([^/]+)/.*#\1#')
SOURCE_OWNER="${SOURCE_OWNER:-srajat-leap}"
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }
[ "$(lower "$ORG")" != "$(lower "$SOURCE_OWNER")" ] \
  || { echo "refusing: $ORG is the source estate (the clones' origin)" >&2; exit 1; }
case "$VISIBILITY" in private) PRIVATE=true ;; public) PRIVATE=false ;; *) echo "VISIBILITY must be private or public" >&2; exit 1 ;; esac
gh api "orgs/$ORG" --jq .login >/dev/null 2>&1 || { echo "org $ORG not found or not visible to this token" >&2; exit 1; }

if gh api "repos/$ORG/$REPO/branches/main" --jq .name >/dev/null 2>&1 && [ "${REPLACE_MAIN:-0}" != 1 ]; then
  echo "  = $ORG/$REPO exists with a main; left as it is (REPLACE_MAIN=1 replaces it)"
else
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  n=0
  while IFS= read -r dir; do
    name=$(basename "$dir")
    git -C "$dir" fetch -q "$SRC_REMOTE"
    mkdir -p "$work/$name"
    git -C "$dir" archive "$SRC_REMOTE/main" | tar -x -C "$work/$name"
    n=$((n + 1))
  done < <(find "$CLONES" -maxdepth 1 -mindepth 1 -type d -name 'cricket-*' | sort)
  [ "$n" -gt 1 ] || { echo "no cricket-* clones under $CLONES" >&2; exit 1; }
  if [ -n "$CONFIG_FROM" ]; then
    src=$(mktemp -d)
    git clone -q --depth 1 "https://github.com/$ORG/$CONFIG_FROM.git" "$src/c"
    [ -d "$src/c/.codeant" ] && cp -R "$src/c/.codeant" "$work/.codeant"
    [ -f "$src/c/greptile.json" ] && cp "$src/c/greptile.json" "$work/greptile.json"
    rm -rf "$src"
  fi
  git -C "$work" init -q -b main
  git -C "$work" add -A
  git -C "$work" -c user.name="prlab estate" -c user.email="estate@prlab.invalid" commit -q -m "Initial commit"
  if ! gh api "repos/$ORG/$REPO" --jq .name >/dev/null 2>&1; then
    gh api -X POST "orgs/$ORG/repos" -f name="$REPO" -F private="$PRIVATE" \
      -f description="PR-review eval copy of the estate as one repo" >/dev/null
    echo "  + $ORG/$REPO created ($VISIBILITY)"
  fi
  for try in 1 2 3 4 5 6; do
    git -C "$work" push -q --force "https://github.com/$ORG/$REPO.git" main && break
    [ "$try" = 6 ] && { echo "push to $ORG/$REPO failed" >&2; exit 1; }
    sleep 5
  done
  echo "OK   $ORG/$REPO main=one commit, $n services"
fi

if [ ! -d "$CLONES/$REPO/.git" ]; then
  git clone -q "https://github.com/$ORG/$REPO.git" "$CLONES/$REPO"
  echo "  cloned to $CLONES/$REPO"
fi
