from __future__ import annotations

import re

from prlab_eval.cases import Case
from prlab_eval.github import run
from prlab_eval.scoring import Review
from prlab_eval.tools.base import PullRequest
from prlab_eval.tools.github_review import (
    build_review,
    fetch_rows,
    find_details,
    inline_text,
    located,
    login_of,
    logins_of,
    top_level_details,
)

# Issue comments CodeRabbit posts that are not findings: the walkthrough,
# replies to "@coderabbitai review", and rate-limit or paused notices.
NOISE = re.compile(
    r"summarize by coderabbit\.ai|Actions performed|Review triggered|rate limit|"
    r"review limit|Reviews paused|skip review by coderabbit",
    re.I,
)

# Review-body sections that hold findings. "Additional comments" are
# verified-OK notes and "Duplicate comments" repeat earlier inline ones.
FINDING_SECTIONS = re.compile(r"Nitpick comments|Outside diff range comments", re.I)
FILE_SUMMARY = re.compile(r"^(?P<path>.+?)\s*\(\d+\)$")
LINE_PREFIX = re.compile(r"^\s*`(?P<lines>[\d\s,-]+)`:\s*")


CITATION = re.compile(r"\s*\[::[^\]]*::[^\]]*\]")
REPO_HEADING = re.compile(r"^#{2,4}\s+`?(?P<repo>[^`\n]+?)`?\s*$", re.M)


def linked_findings(body: str) -> list[str]:
    """Multi-Repo Analysis findings from the walkthrough, one text per linked repo.

    CodeRabbit puts them in a nested "Multi-repo context" <details> block under
    Review details > Additional context used, as ``### <repo>`` sections of bullets.
    """
    findings: list[str] = []
    for block in find_details(body, lambda summary: "multi-repo context" in summary.lower()):
        heads = list(REPO_HEADING.finditer(block.inner))
        for i, head in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(block.inner)
            text = CITATION.sub("", block.inner[head.end():end]).strip()
            if text and not text.lower().startswith("linked repositories findings"):
                findings.append(located(text, f"linked:{head.group('repo').strip()}"))
    return findings


def review_body_findings(body: str) -> list[str]:
    """Split nitpick and outside-diff sections into one text per finding."""
    findings: list[str] = []
    for section in top_level_details(body):
        if not FINDING_SECTIONS.search(section.summary):
            continue
        for file_block in top_level_details(section.inner):
            match = FILE_SUMMARY.match(file_block.summary)
            path = match.group("path") if match else file_block.summary
            for item in re.split(r"\n\s*---\s*\n", file_block.inner):
                item = re.sub(r"</?blockquote>", "", item).strip()
                if not item:
                    continue
                lines = LINE_PREFIX.match(item)
                if lines:
                    item = item[lines.end():]
                findings.append(located(item, path, lines.group("lines").strip() if lines else None))
    return findings


NOTHING_TO_FLAG = re.compile(r"No actionable comments were generated", re.I)


class CodeRabbitTool:
    name = "coderabbit"
    bot_logins = frozenset({"coderabbitai[bot]"})
    trigger_body = "@coderabbitai review"
    # linked_findings: also count Multi-Repo Analysis findings from the walkthrough.
    # Off by default: those are research notes CodeRabbit did not raise as review
    # comments, and the rest of the walkthrough only restates the change.
    DEFAULT_OPTIONS = {"linked_findings": False}

    def __init__(self) -> None:
        self.options = dict(self.DEFAULT_OPTIONS)

    def configure(self, options: dict[str, str]) -> None:
        for key, raw in options.items():
            if key not in self.DEFAULT_OPTIONS:
                known = ", ".join(sorted(self.DEFAULT_OPTIONS))
                raise ValueError(f"coderabbit has no option {key!r} (known: {known})")
            value = raw.strip().lower()
            if value not in {"true", "false", "1", "0", "yes", "no"}:
                raise ValueError(f"coderabbit option {key}={raw!r}: use true or false")
            self.options[key] = value in {"true", "1", "yes"}

    def context_files(self, case: Case) -> dict[str, str]:
        # Linked repositories live in the CodeRabbit portal, not on the PR.
        return {}

    def finished(self, pr: PullRequest) -> bool:
        """True once CodeRabbit posted a review, or said it had nothing to flag."""
        rows = fetch_rows(pr)
        if any(login_of(row) in self.bot_logins for row in rows.reviews):
            return True
        return any(
            login_of(row) in self.bot_logins and NOTHING_TO_FLAG.search(row.get("body") or "")
            for row in rows.issue
        )

    def collect(self, pr: PullRequest) -> Review:
        rows = fetch_rows(pr)
        texts: list[str] = []
        for row in rows.issue:
            body = row.get("body") or ""
            if login_of(row) not in self.bot_logins or not body.strip():
                continue
            if self.options["linked_findings"]:
                texts.extend(linked_findings(body))
            if not NOISE.search(body):
                texts.append(body)
        for row in rows.inline:
            if login_of(row) in self.bot_logins:
                texts.append(inline_text(row))
        for row in rows.reviews:
            if login_of(row) in self.bot_logins:
                texts.extend(review_body_findings(row.get("body") or ""))
        return build_review(texts, logins_of(rows), pr)

    def trigger(self, pr: PullRequest) -> None:
        run(["gh", "pr", "comment", pr.url, "--body", self.trigger_body])
