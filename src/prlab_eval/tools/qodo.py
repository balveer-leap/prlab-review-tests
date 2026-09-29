from __future__ import annotations

import re

from prlab_eval.cases import Case
from prlab_eval.github import run
from prlab_eval.scoring import Review
from prlab_eval.tools.base import PullRequest
from prlab_eval.tools.github_review import (
    build_review,
    fetch_rows,
    inline_text,
    login_of,
    logins_of,
    located,
    strip_tags,
    top_level_details,
    unquote,
)

# "PR Summary by Qodo" describes the change; it is not a finding.
PR_SUMMARY = re.compile(r"PR Summary by Qodo", re.I)
CODE_REVIEW = re.compile(r"Code Review by Qodo", re.I)
NUMBERED = re.compile(r"^\s*\d+\\?\.\s+(?P<title>.+?)\s*(?:<code>|$)", re.M)
CODE_LINK = re.compile(r"\[(?P<path>[^\[\]]+?)\[(?P<lines>[^\]]+)\]\]")


def finding_key(title: str) -> str:
    return " ".join(strip_tags(title).lower().split())


def inline_title(body: str) -> str:
    match = NUMBERED.search(body or "")
    return finding_key(match.group("title")) if match else ""


def summary_findings(body: str) -> list[tuple[str, str]]:
    """(title key, text) for each numbered finding in a "Code Review by Qodo" comment."""
    findings: list[tuple[str, str]] = []
    for block in top_level_details(body):
        match = re.match(r"\s*\d+\.\s+(?P<title>.+)", block.summary)
        if not match:
            continue
        title = re.sub(r"\s*[🐞📘📜].*$", "", match.group("title")).strip()
        parts = {part.summary.lower(): unquote(part.inner) for part in top_level_details(unquote(block.inner))}
        description = parts.get("description", "").strip()
        code = CODE_LINK.search(parts.get("code", ""))
        text = f"{title}\n{description}".strip()
        text = located(text, code.group("path"), code.group("lines")) if code else text
        findings.append((finding_key(title), text))
    return findings


QODO_DONE = re.compile(r"Code Review by Qodo|reviews are paused", re.I)
QODO_BLOCKED = re.compile(r"reviews are paused", re.I)


class QodoTool:
    name = "qodo"
    bot_logins = frozenset({"qodo-code-review[bot]", "qodo-merge-pro[bot]", "qodo-merge[bot]"})
    trigger_body = "/review"

    def context_files(self, case: Case) -> dict[str, str]:
        # Qodo indexes the installed org on its own; nothing goes on the PR.
        return {}

    def finished(self, pr: PullRequest) -> bool:
        """True once Qodo posted its review, or said reviews are paused (none will come)."""
        return any(
            login_of(row) in self.bot_logins and QODO_DONE.search(row.get("body") or "")
            for row in fetch_rows(pr).issue
        )

    def blocked(self, pr: PullRequest) -> str:
        """Qodo's "reviews are paused" notice (a plan limit), or ""."""
        for row in fetch_rows(pr).issue:
            body = row.get("body") or ""
            if login_of(row) in self.bot_logins and QODO_BLOCKED.search(body) and "Code Review by Qodo" not in body:
                return body.strip().splitlines()[0][:160]
        return ""

    def collect(self, pr: PullRequest) -> Review:
        rows = fetch_rows(pr)
        texts: list[str] = []
        titles: set[str] = set()
        for row in rows.inline:
            if login_of(row) in self.bot_logins:
                texts.append(inline_text(row))
                titles.add(inline_title(row.get("body") or ""))
        for row in rows.issue:
            body = row.get("body") or ""
            if login_of(row) not in self.bot_logins or not body.strip() or PR_SUMMARY.search(body):
                continue
            if CODE_REVIEW.search(body):
                # The summary repeats inline findings; keep only ones not posted inline.
                texts.extend(text for key, text in summary_findings(body) if key not in titles)
            else:
                texts.append(body)
        for row in rows.reviews:
            if login_of(row) in self.bot_logins and (row.get("body") or "").strip():
                texts.append(row["body"])
        return build_review(texts, logins_of(rows), pr)

    def trigger(self, pr: PullRequest) -> None:
        run(["gh", "pr", "comment", pr.url, "--body", self.trigger_body])
