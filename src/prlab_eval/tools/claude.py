from __future__ import annotations

import re

from prlab_eval.cases import Case
from prlab_eval.github import GitHubError, run
from prlab_eval.scoring import Review
from prlab_eval.tools.base import PullRequest
from prlab_eval.tools.github_review import (
    build_review,
    fetch_rows,
    inline_text,
    login_of,
    logins_of,
)

# claude-code-action with track_progress keeps one sticky comment and edits it
# in place: "Claude is reviewing…" while running, then "Claude finished …".
# "Claude encountered an error" can still carry a full review: the action
# flags a run that finished past --max-turns as failed after the review was
# posted. Such a comment counts if it has more than the progress checklist.
FINISHED = re.compile(r"\*\*Claude (?:finished|encountered an error)\b[^\n]*\n", re.I)
STATUS_HEADING = re.compile(r"^#{1,6}\s*Claude (?:finished|is) reviewing[^\n]*\n|^\*\*Tasks\*\*\s*\n", re.I | re.M)
HEADER_RULE = re.compile(r"^\s*---\s*\n", re.M)
CHECKLIST = re.compile(r"^\s*- \[[ xX]\] .*\n?", re.M)


def review_body(body: str) -> str:
    """The review from a finished sticky comment, or "" while Claude is still working."""
    match = FINISHED.search(body or "")
    if not match:
        return ""
    text = body[match.end():]
    text = HEADER_RULE.sub("", text, count=1)
    text = CHECKLIST.sub("", text)
    text = STATUS_HEADING.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return "" if not re.sub(r"[\s\-#*]", "", text) else text


class ClaudeActionTool:
    """Claude Code GitHub Action (anthropics/claude-code-action), one variant per org.

    The workflow runs on pull_request open, so there is no mention to post.
    ``trigger`` re-runs the PR's workflow instead. ``trigger_body`` is the
    finished-review marker, so ``prlab-eval trigger`` skips PRs Claude has
    already reviewed.
    """

    bot_logins = frozenset({"claude[bot]"})
    trigger_body = "**Claude finished"
    workflow = "claude-review.yml"

    def __init__(self, name: str) -> None:
        self.name = name

    def context_files(self, case: Case) -> dict[str, str]:
        # The workflow (and, for the estate-aware variant, the sibling clones)
        # lives on each org's main branch, not on the trap PR.
        return {}

    def collect(self, pr: PullRequest) -> Review:
        rows = fetch_rows(pr)
        texts: list[str] = []
        for row in rows.issue:
            if login_of(row) in self.bot_logins:
                texts.append(review_body(row.get("body") or ""))
        for row in rows.inline:
            if login_of(row) in self.bot_logins:
                texts.append(inline_text(row))
        for row in rows.reviews:
            if login_of(row) in self.bot_logins and (row.get("body") or "").strip():
                texts.append(row["body"])
        return build_review(texts, logins_of(rows), pr)

    def trigger(self, pr: PullRequest) -> None:
        run_id = run(
            [
                "gh", "run", "list",
                "--repo", pr.repo,
                "--branch", pr.branch,
                "--workflow", self.workflow,
                "--limit", "1",
                "--json", "databaseId",
                "--jq", ".[0].databaseId // empty",
            ]
        ).strip()
        if not run_id:
            raise GitHubError(f"no {self.workflow} run for {pr.url}; is the workflow on main?")
        run(["gh", "run", "rerun", run_id, "--repo", pr.repo])
