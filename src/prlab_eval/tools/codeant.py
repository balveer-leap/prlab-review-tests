"""CodeAnt AI (app.codeant.ai) — automatic PR review on GitHub.

Verified against real reviews in org-codeant (2026-09-28):
- Bot login ``codeant-ai[bot]``. Reviews every PR on open; ``@codeant-ai: review``
  re-triggers.
- Findings are inline review comments: "**Suggestion:** ...", an "Assessment"
  line (severity, occurrence, category), "Fix in Cursor/VSCode" badge links and
  a collapsed "Prompt for AI Agent" block.
- Review bodies are empty. One issue comment per PR is a "Review Status" table
  carrying a machine-readable ``<!-- codeant-review-status:[...] -->`` marker
  (``"done": true`` when finished). That comment is status, not a finding.
- It may rewrite the PR description; the harness reads case text from
  cases.json, so that does not affect scoring.

Override the login with ``--tool-option bot_logins=<login>[,<login>...]``.
"""

from __future__ import annotations

import re

from prlab_eval.cases import Case
from prlab_eval.github import run
from prlab_eval.scoring import Review
from prlab_eval.tools.base import PullRequest
from prlab_eval.tools.github_review import build_review, fetch_rows, inline_text, login_of, logins_of

# The per-PR "Review Status" table; identified by its marker, not its wording.
STATUS_MARKER = re.compile(r"<!--\s*codeant-review-status:", re.I)
# "[![Fix in Cursor](badge.svg)](link)" action badges carry no review content.
BADGE_LINK = re.compile(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)\s*")


def is_status(body: str) -> bool:
    return bool(STATUS_MARKER.search(body or ""))


def strip_badges(body: str) -> str:
    return BADGE_LINK.sub("", body or "").strip()


class CodeAntTool:
    name = "codeant"
    trigger_body = "@codeant-ai: review"
    DEFAULT_LOGINS = ("codeant-ai[bot]",)

    def __init__(self) -> None:
        self.bot_logins = frozenset(self.DEFAULT_LOGINS)

    def configure(self, options: dict[str, str]) -> None:
        for key, raw in options.items():
            if key != "bot_logins":
                raise ValueError(f"codeant has no option {key!r} (known: bot_logins)")
            logins = frozenset(item.strip() for item in raw.split(",") if item.strip())
            if not logins:
                raise ValueError("codeant option bot_logins needs at least one login")
            self.bot_logins = logins

    def context_files(self, case: Case) -> dict[str, str]:
        return {}

    def collect(self, pr: PullRequest) -> Review:
        rows = fetch_rows(pr)
        texts: list[str] = []
        for row in rows.inline:
            if login_of(row) in self.bot_logins:
                texts.append(inline_text({**row, "body": strip_badges(row.get("body") or "")}))
        # Review bodies and other issue comments are empty today; kept so a
        # finding posted there later is not silently dropped.
        for row in rows.reviews + rows.issue:
            body = strip_badges(row.get("body") or "")
            if login_of(row) in self.bot_logins and body and not is_status(body):
                texts.append(body)
        return build_review(texts, logins_of(rows), pr)

    def trigger(self, pr: PullRequest) -> None:
        run(["gh", "pr", "comment", pr.url, "--body", self.trigger_body])
