from __future__ import annotations

from pathlib import Path

from prlab_eval.cases import ROOT, Case
from prlab_eval.github import GitHubError, gh_json, run
from prlab_eval.tools import TOOLS
from prlab_eval.tools.base import PullRequest, ReviewTool

WORKSPACE = ROOT.parent


def find_open_pr(repo: str, branch: str) -> PullRequest | None:
    rows = gh_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--head",
            branch,
            "--json",
            "url,number,headRefName",
            "--state",
            "open",
        ]
    ) or []
    if not rows:
        return None
    row = rows[0]
    return PullRequest(repo=repo, number=row["number"], url=row["url"], branch=branch)


def remote_branch_exists(local: Path, branch: str, remote: str = "origin") -> bool:
    run(["git", "fetch", remote, "--prune"], cwd=str(local))
    return bool(run(["git", "ls-remote", "--heads", remote, branch], cwd=str(local)).strip())


def push_eval_branch(local: Path, branch: str, remote: str = "origin") -> str:
    """Create the remote eval branch if it is missing. Force-update only when it already exists."""
    exists = remote_branch_exists(local, branch, remote)
    cmd = ["git", "push", "-u", remote, f"HEAD:{branch}"]
    if exists:
        cmd.append("--force-with-lease")
    run(cmd, cwd=str(local))
    return "updated" if exists else "created"


def repo_url(repo: str) -> str:
    return f"https://github.com/{repo}.git"


def remote_for(local: Path, repo: str) -> str:
    """Git remote in ``local`` that points at ``repo``.

    ``origin`` for the default owner. A retargeted case (``--owner``) gets a
    remote named after its owner, added on first use.
    """
    url = repo_url(repo)
    names = run(["git", "remote"], cwd=str(local)).split()
    for name in names:
        current = run(["git", "remote", "get-url", name], cwd=str(local)).strip()
        if current.removesuffix(".git").lower() == url.removesuffix(".git").lower():
            return name
    name = repo.split("/", 1)[0]
    if name in names:
        run(["git", "remote", "set-url", name, url], cwd=str(local))
    else:
        run(["git", "remote", "add", name, url], cwd=str(local))
    return name


def context_files(case: Case, tool: ReviewTool | None, all_tools: bool) -> dict[str, str]:
    if all_tools:
        files: dict[str, str] = {}
        for registered in TOOLS.values():
            files.update(registered.context_files(case))
        return files
    if tool is None:
        return {}
    return tool.context_files(case)


def ensure_pr(
    case: Case,
    *,
    tool: ReviewTool | None = None,
    all_tools: bool = True,
    recreate: bool = False,
) -> PullRequest:
    existing = find_open_pr(case.github_repo, case.branch)
    if existing and not recreate:
        return existing

    local = WORKSPACE / case.local
    if not (local / ".git").exists():
        raise GitHubError(f"missing product clone: {local}")

    remote = remote_for(local, case.github_repo)
    run(["git", "fetch", remote, "--prune"], cwd=str(local))
    run(["git", "checkout", "-B", case.branch, f"{remote}/main"], cwd=str(local))
    run(["git", "reset", "--hard", f"{remote}/main"], cwd=str(local))
    # Stage only what the patch touches: untracked files in the clone (Finder's
    # .DS_Store) would otherwise land in one tool's PR and not another's.
    directory = ["--directory", case.subdir] if case.subdir else []
    run(["git", "apply", "--index", *directory, str(ROOT / case.patch)], cwd=str(local))

    for dest, content in context_files(case, tool, all_tools).items():
        path = local / dest
        path.write_text(content)
        run(["git", "add", dest], cwd=str(local))

    status = run(["git", "diff", "--cached", "--name-only"], cwd=str(local))
    if not status.strip():
        raise GitHubError(f"{case.id}: patch produced no changes")

    run(["git", "commit", "-m", f"{case.title}\n\n{case.body}"], cwd=str(local))
    push_eval_branch(local, case.branch, remote)

    existing = find_open_pr(case.github_repo, case.branch)
    if existing:
        return existing

    url = run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            case.github_repo,
            "--base",
            "main",
            "--head",
            case.branch,
            "--title",
            case.title,
            "--body",
            case.body,
        ]
    ).strip()
    number = int(url.rsplit("/", 1)[-1])
    return PullRequest(repo=case.github_repo, number=number, url=url, branch=case.branch)


def close_pr(pr: PullRequest) -> None:
    try:
        run(["gh", "pr", "close", str(pr.number), "--repo", pr.repo, "--delete-branch"])
    except GitHubError as exc:
        text = str(exc).lower()
        if "already closed" not in text and "not found" not in text:
            raise
        try:
            run(["gh", "api", "-X", "DELETE", f"repos/{pr.repo}/git/refs/heads/{pr.branch}"])
        except GitHubError as delete_exc:
            delete_text = str(delete_exc).lower()
            if "404" not in delete_text and "not found" not in delete_text and "does not exist" not in delete_text:
                raise
