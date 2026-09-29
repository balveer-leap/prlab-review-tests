"""Shared GitHub comment plumbing for review-tool plugins.

Bots post findings in three places: issue comments, inline review comments,
and review bodies. Each plugin decides which of those hold findings; this
module fetches them, tracks every login for isolation, and dedupes text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from prlab_eval.github import gh_api_list
from prlab_eval.scoring import Review
from prlab_eval.tools.base import PullRequest

_OPEN = re.compile(r"<details\b[^>]*>", re.I)
_CLOSE = re.compile(r"</details\s*>", re.I)
_SUMMARY = re.compile(r"^[\s>]*<summary\b[^>]*>([\s\S]*?)</summary>", re.I)


@dataclass(frozen=True)
class PrRows:
    issue: list[dict]
    inline: list[dict]
    reviews: list[dict]

    def all(self) -> list[dict]:
        return self.issue + self.inline + self.reviews


@dataclass(frozen=True)
class Details:
    summary: str
    inner: str


def fetch_rows(pr: PullRequest) -> PrRows:
    return PrRows(
        issue=list(gh_api_list(f"repos/{pr.repo}/issues/{pr.number}/comments?per_page=100")),
        inline=list(gh_api_list(f"repos/{pr.repo}/pulls/{pr.number}/comments?per_page=100")),
        reviews=list(gh_api_list(f"repos/{pr.repo}/pulls/{pr.number}/reviews?per_page=100")),
    )


def login_of(row: dict) -> str:
    return (row.get("user") or {}).get("login") or ""


def logins_of(rows: PrRows) -> set[str]:
    return {login for login in (login_of(row) for row in rows.all()) if login}


def located(body: str, path: str, line: object = None) -> str:
    body = (body or "").strip()
    if not body:
        return ""
    if not path:
        return body
    loc = f"{path}:{line}" if line else path
    return f"{loc}\n{body}"


def inline_text(row: dict) -> str:
    return located(row.get("body") or "", row.get("path") or "", row.get("line") or row.get("original_line"))


def strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def unquote(text: str) -> str:
    """Drop markdown blockquote markers that bots put in front of nested blocks."""
    return "\n".join(re.sub(r"^\s*>\s?", "", line) for line in (text or "").splitlines())


def top_level_details(text: str) -> list[Details]:
    """Split HTML <details> blocks at depth 0, honouring nesting."""
    tokens = sorted(
        [(m.start(), m.end(), 1) for m in _OPEN.finditer(text or "")]
        + [(m.start(), m.end(), -1) for m in _CLOSE.finditer(text or "")]
    )
    blocks: list[Details] = []
    depth = 0
    inner_start = 0
    for start, end, kind in tokens:
        if kind == 1:
            if depth == 0:
                inner_start = end
            depth += 1
            continue
        if depth == 0:
            continue
        depth -= 1
        if depth == 0:
            body = text[inner_start:start]
            match = _SUMMARY.match(body)
            if match:
                blocks.append(Details(summary=strip_tags(match.group(1)), inner=body[match.end():]))
            else:
                blocks.append(Details(summary="", inner=body))
    return blocks


def find_details(text: str, wanted) -> list[Details]:
    """Every <details> block at any depth whose summary satisfies ``wanted(summary)``."""
    found: list[Details] = []
    for block in top_level_details(text):
        if wanted(block.summary):
            found.append(block)
        found.extend(find_details(block.inner, wanted))
    return found


def build_review(texts: list[str], logins: set[str], pr: PullRequest) -> Review:
    kept: list[str] = []
    seen: set[str] = set()
    for body in texts:
        key = " ".join((body or "").split())
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(body.strip())
    return Review(text="\n\n".join(kept), comments=tuple(kept), logins=logins, pr_url=pr.url)
