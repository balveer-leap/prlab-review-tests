from __future__ import annotations

from pathlib import Path

from prlab_eval.cases import Case, load_cases
from prlab_eval.github import GitHubError, gh_json, run
from prlab_eval.prs import WORKSPACE, close_pr, find_open_pr
from prlab_eval.tools.base import PullRequest

EVAL_PREFIX = "eval/"


def _ignored(exc: GitHubError) -> bool:
    text = str(exc).lower()
    return any(
        needle in text
        for needle in (
            "already closed",
            "not found",
            "404",
            "reference does not exist",
            "does not exist",
        )
    )


def try_run(args: list[str], cwd: str | None = None) -> bool:
    try:
        run(args, cwd=cwd)
        return True
    except GitHubError as exc:
        if _ignored(exc):
            return False
        raise


def list_open_eval_prs(repo: str) -> list[PullRequest]:
    rows = (
        gh_json(
            [
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--limit",
                "100",
                "--json",
                "url,number,headRefName",
            ]
        )
        or []
    )
    found: list[PullRequest] = []
    for row in rows:
        branch = row.get("headRefName") or ""
        if not branch.startswith(EVAL_PREFIX):
            continue
        found.append(
            PullRequest(repo=repo, number=row["number"], url=row["url"], branch=branch)
        )
    return found


def delete_remote_branch(repo: str, branch: str) -> bool:
    return try_run(["gh", "api", "-X", "DELETE", f"repos/{repo}/git/refs/heads/{branch}"])


def reset_local_clone(local: Path, branches: set[str]) -> None:
    if not (local / ".git").exists():
        return
    try_run(["git", "fetch", "origin", "--prune"], cwd=str(local))
    current = ""
    try:
        current = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(local)).strip()
    except GitHubError:
        current = ""
    if current.startswith(EVAL_PREFIX) or current in branches:
        try_run(["git", "checkout", "-B", "main", "origin/main"], cwd=str(local))
    listed = ""
    try:
        listed = run(["git", "branch", "--list", f"{EVAL_PREFIX}*"], cwd=str(local))
    except GitHubError:
        listed = ""
    names = {line.strip().lstrip("* ").strip() for line in listed.splitlines()} | set(branches)
    for name in sorted(names):
        if name.startswith(EVAL_PREFIX):
            try_run(["git", "branch", "-D", name], cwd=str(local))


def leftover_eval_prs(cases: list[Case]) -> list[PullRequest]:
    known = {(case.github_repo, case.branch) for case in cases}
    extras: list[PullRequest] = []
    seen: set[tuple[str, int]] = set()
    for repo in sorted({case.github_repo for case in cases}):
        for pr in list_open_eval_prs(repo):
            key = (pr.repo, pr.number)
            if key in seen or (pr.repo, pr.branch) in known:
                continue
            seen.add(key)
            extras.append(pr)
    return extras


def cleanup_eval(only: set[str] | None = None, *, owner: str) -> list[str]:
    """Close eval PRs, delete eval branches, reset local clones in ``owner``. Reports stay."""
    if not owner:
        raise ValueError("cleanup_eval needs an explicit owner")
    everything = load_cases(owner=owner)
    cases = [case for case in everything if only is None or case.id in only]
    lines: list[str] = []
    seen_prs: set[tuple[str, int]] = set()
    locals_by_path: dict[Path, set[str]] = {}
    local_for_repo = {case.github_repo: WORKSPACE / case.local for case in everything}

    for case in cases:
        pr = find_open_pr(case.github_repo, case.branch)
        if pr and (pr.repo, pr.number) not in seen_prs:
            seen_prs.add((pr.repo, pr.number))
            close_pr(pr)
            lines.append(f"closed\t{case.id}\t{pr.url}")
        elif pr is None:
            lines.append(f"no_pr\t{case.id}\t{case.github_repo}\t{case.branch}")
        if delete_remote_branch(case.github_repo, case.branch):
            lines.append(f"deleted_branch\t{case.id}\t{case.branch}")
        locals_by_path.setdefault(WORKSPACE / case.local, set()).add(case.branch)

    if only is None:
        for pr in leftover_eval_prs(cases):
            if (pr.repo, pr.number) in seen_prs:
                continue
            seen_prs.add((pr.repo, pr.number))
            close_pr(pr)
            if delete_remote_branch(pr.repo, pr.branch):
                lines.append(f"deleted_branch\t{pr.branch}\t{pr.url}")
            lines.append(f"closed_leftover\t{pr.repo}\t{pr.url}")
            local = local_for_repo.get(pr.repo)
            if local is not None:
                locals_by_path.setdefault(local, set()).add(pr.branch)

    for local, branches in locals_by_path.items():
        reset_local_clone(local, branches)
        if local.exists():
            lines.append(f"reset_local\t{local.name}")
    return lines
