from __future__ import annotations

import re

from prlab_eval.cases import Case
from prlab_eval.github import gh_api_list, run
from prlab_eval.scoring import Review
from prlab_eval.tools.base import PullRequest


def comment_text(row: dict) -> str:
    body = (row.get("body") or "").strip()
    if not body:
        return ""
    path = row.get("path") or ""
    line = row.get("line") or row.get("original_line")
    if not path:
        return body
    loc = f"{path}:{line}" if line else path
    return f"{loc}\n{body}"


# Account notices posted in place of a review: no review is coming.
BLOCKED = re.compile(r"trial has ended|Reactivate Greptile|subscription (has )?(expired|ended)", re.I)


class GreptileTool:
    name = "greptile"
    bot_logins = frozenset({"greptile-apps[bot]"})
    trigger_body = "@greptileai"

    def context_files(self, case: Case) -> dict[str, str]:
        return {}

    def collect(self, pr: PullRequest) -> Review:
        issue = gh_api_list(f"repos/{pr.repo}/issues/{pr.number}/comments?per_page=100")
        inline = gh_api_list(f"repos/{pr.repo}/pulls/{pr.number}/comments?per_page=100")
        reviews = gh_api_list(f"repos/{pr.repo}/pulls/{pr.number}/reviews?per_page=100")
        texts: list[str] = []
        seen: set[str] = set()
        logins: set[str] = set()
        for row in list(issue) + list(inline) + list(reviews):
            login = (row.get("user") or {}).get("login") or ""
            body = comment_text(row)
            if login:
                logins.add(login)
            if login not in self.bot_logins or not body or BLOCKED.search(body):
                continue
            key = " ".join(body.split())
            if key in seen:
                continue
            seen.add(key)
            texts.append(body)
        return Review(
            text="\n\n".join(texts),
            comments=tuple(texts),
            logins=logins,
            pr_url=pr.url,
        )

    def blocked(self, pr: PullRequest) -> str:
        """The account notice Greptile posted instead of reviewing, or ""."""
        rows = gh_api_list(f"repos/{pr.repo}/pulls/{pr.number}/reviews?per_page=100") + gh_api_list(
            f"repos/{pr.repo}/issues/{pr.number}/comments?per_page=100"
        )
        for row in rows:
            body = row.get("body") or ""
            if (row.get("user") or {}).get("login") in self.bot_logins and BLOCKED.search(body):
                return body.strip().splitlines()[0][:160]
        return ""

    def trigger(self, pr: PullRequest) -> None:
        run(["gh", "pr", "comment", pr.url, "--body", self.trigger_body])
