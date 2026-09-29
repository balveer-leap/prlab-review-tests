#!/usr/bin/env bash
# Turn on everything CodeAnt can be given through repository config: commit
# .codeant/configuration.json (every analysis enabled, SAST and secrets
# included) and .codeant/instructions.json (domain-neutral review guidance) to
# main in every repo of ORG. See https://docs.codeant.ai/repositories/analysis_configuration
# and https://docs.codeant.ai/pull_request/customize/instructions.
#
# The instructions are the same kind of guidance the estate-aware Claude prompt
# carries: follow changed values to their consumers, review changed endpoints
# against the OWASP Top 10. They name no service, field, or case.
#
# Dashboard-only settings this cannot set (app.codeant.ai/x/settings/ai-rules):
# the Pentesting Agent persona and each agent's reporting threshold.
#
# Builds on the org's own main and never force-pushes; a run that changes
# nothing pushes nothing.
#
# Usage: 27-codeant-config.sh [ORG] [CLONES_DIR]
set -euo pipefail

ORG="${1:-org-codeant}"
HERE="$(cd "$(dirname "$0")" && pwd)"
CLONES="${2:-$HERE/../../..}"
PREFIX="${PREFIX:-prlab-}"
DRY_RUN="${DRY_RUN:-0}"
AUTHOR_NAME="${AUTHOR_NAME:-prlab estate}"
AUTHOR_EMAIL="${AUTHOR_EMAIL:-estate@prlab.invalid}"

gh api "orgs/$ORG" --jq .login >/dev/null 2>&1 || { echo "org $ORG not found or not visible to this token" >&2; exit 1; }

CONFIGURATION='{
  "code_analysis": {
    "enabled": true,
    "features": {
      "sast_analysis": "enabled",
      "secrets_analysis": "enabled",
      "sca_analysis": "enabled",
      "iac_analysis": "enabled",
      "deadcode_analysis": "enabled",
      "duplicatecode_analysis": "enabled",
      "antipatterns_analysis": "enabled",
      "docstring_analysis": "enabled",
      "complex_function_analysis": "enabled"
    }
  }
}'

INSTRUCTIONS='{
  "instructions": [
    {
      "id": "downstream-impact",
      "description": "This repository is one service among several that exchange data over HTTP and shared payloads. For every field, status code, value or message this change writes, returns, renames or stops returning, consider who consumes it and what breaks for them, even when no import links the two. Say which consumer is affected and how.",
      "files": ["**/*"],
      "scope": ["pr"]
    },
    {
      "id": "security-review",
      "description": "Review new or changed endpoints and input handling against the OWASP Top 10, and report exploitable issues as security findings with the attack they enable.",
      "files": ["**/*"],
      "scope": ["pr"]
    }
  ]
}'

CLONE_DIRS=()
while IFS= read -r dir; do CLONE_DIRS+=("$dir"); done \
  < <(find "$CLONES" -maxdepth 1 -mindepth 1 -type d -name 'cricket-*' | sort)
[ "${#CLONE_DIRS[@]}" -gt 0 ] || { echo "no cricket-* clones under $CLONES" >&2; exit 1; }

for dir in "${CLONE_DIRS[@]}"; do
  repo="${PREFIX}$(basename "$dir")"
  url="https://github.com/$ORG/$repo.git"
  git -C "$dir" fetch -q "$url" main
  work="_codeant_main_$$"
  git -C "$dir" checkout -q -B "$work" FETCH_HEAD
  mkdir -p "$dir/.codeant"
  printf '%s\n' "$CONFIGURATION" > "$dir/.codeant/configuration.json"
  printf '%s\n' "$INSTRUCTIONS" > "$dir/.codeant/instructions.json"
  git -C "$dir" add .codeant/configuration.json .codeant/instructions.json
  if git -C "$dir" diff --cached --quiet; then
    echo "  = $ORG/$repo  already current"
  else
    git -C "$dir" -c user.name="$AUTHOR_NAME" -c user.email="$AUTHOR_EMAIL" \
      commit -q -m "Enable CodeAnt analyses and review instructions"
    if [ "$DRY_RUN" = 1 ]; then
      echo "DRY  $ORG/$repo"
    else
      git -C "$dir" push -q "$url" "$work:refs/heads/main"
      echo "OK   $ORG/$repo  main=$(git -C "$dir" rev-parse --short HEAD)"
    fi
  fi
  git -C "$dir" checkout -q main
  git -C "$dir" branch -q -D "$work"
done
