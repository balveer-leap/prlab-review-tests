"""Check that a review org hands its reviewer nothing but the PR.

`prlab_eval all` runs this for every tool's org before it opens or scores a PR,
and stops if any org fails. Run it alone with
`python3 -m prlab_eval.audit ORG [ORG ...]` or `--all`. Orgs listed in
cases/audit_exempt.json are skipped, with their reason printed.

Checks, per org:
  harness      this harness (the answer key) is not a repo in the org
  history      no branch can reach a commit that touched TRAPS.md
  pull refs    no PR, open or closed, can reach one either (refs/pull outlive branches)
  branches     no trap/* branches
  old PRs      no PR, open or closed, whose title or body cites the answer key
  PR files     an open eval PR changes only the files its patch changes
  workflow     in a Claude org every repo has the workflow, checking out with
               fetch-depth: 1 and giving Claude no unrestricted Bash
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from prlab_eval.cases import ROOT
from prlab_eval.github import TRANSIENT, parse_json_pages

REPOS = ("protocol", "scoring", "broadcast", "stats", "fantasy", "highlights",
         "social", "archive", "notifications", "live-gateway", "mobile")
HINT = re.compile(r"TRAPS\.md|\btrap/|prlab-review-tests|review-eval", re.I)
WORKFLOW = ".github/workflows/claude-review.yml"
FETCH_DEPTH_1 = re.compile(r"fetch-depth:\s*1\b")
# "Bash" in --allowedTools without a (command:*) restriction: any shell command,
# network included, so the reviewer could fetch the public harness repo.
OPEN_SHELL = re.compile(
    r"allowed[-_]?tools\W+[^\n]*?\bBash\b(?:(?!\()|\(\s*\*\s*\))", re.I
)

# GET a GitHub API path; None on 404 or no access.
Getter = Callable[[str], object | None]


class AuditError(RuntimeError):
    """GitHub could not be read, so the org cannot be declared clean."""


def gh_get(path: str) -> object | None:
    """Every page of a list (gh --paginate). None only for 404.

    Any other failure (rate limit, network, auth) raises: an unreadable org
    must fail the audit, not pass it.
    """
    for attempt in range(1, 5):
        try:
            proc = subprocess.run(["gh", "api", "--paginate", path], capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            if attempt == 4:
                raise AuditError(f"gh api {path}: timed out") from None
            time.sleep(5 * attempt)
            continue
        if proc.returncode == 0:
            return parse_json_pages(proc.stdout)
        if "HTTP 404" in proc.stderr:
            return None
        if attempt == 4 or not TRANSIENT.search(proc.stderr):
            raise AuditError(f"gh api {path}: {proc.stderr.strip()[-200:]}")
        time.sleep(5 * attempt)
    raise AssertionError("unreachable")


@dataclass
class OrgAudit:
    org: str
    fails: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.fails

    def summary(self) -> list[str]:
        """One line per check: the first finding in full, then how many more."""
        by_check: dict[str, list[str]] = {}
        for line in self.fails:
            by_check.setdefault(line.split(":", 1)[0], []).append(line)
        lines = [f"{'PASS' if self.ok else 'FAIL'}  {self.org}"]
        for found in by_check.values():
            more = f"  (+{len(found) - 1} more like this)" if len(found) > 1 else ""
            lines.append(f"      {found[0]}{more}")
        return lines


def exempt_orgs() -> dict[str, str]:
    """Orgs deliberately left unaudited (cases/audit_exempt.json), with the reason."""
    path = ROOT / "cases/audit_exempt.json"
    return json.loads(path.read_text()) if path.exists() else {}


def patch_files(monorepo: bool = False) -> dict[str, set[str]]:
    """Eval branch -> files its patch touches (under the service folder in a monorepo)."""
    files: dict[str, set[str]] = {}
    for case in json.loads((ROOT / "cases/cases.json").read_text()):
        text = (ROOT / case["patch"]).read_text()
        paths = set(re.findall(r"^diff --git a/(\S+) b/", text, re.M))
        files[case["branch"]] = {f"{case['local']}/{path}" for path in paths} if monorepo else paths
    return files


def audit_repo(
    repo: str, expected: dict[str, set[str]], get: Getter = gh_get, *, needs_workflow: bool = False
) -> list[str]:
    fails: list[str] = []
    branches = get(f"repos/{repo}/branches?per_page=100")
    if branches is None:
        return [f"missing: {repo} is not readable (missing, or no access)"]
    for branch in branches:
        name = branch["name"]
        if name.startswith("trap/"):
            fails.append(f"branches: {repo} has {name}. Delete it.")
        if get(f"repos/{repo}/commits?sha={name}&path=TRAPS.md&per_page=1"):
            fails.append(f"history: {repo} {name} reaches a commit that touched TRAPS.md. "
                         "Run estate/scripts/15-squash-main.sh (main) or delete the branch.")
    for pr in get(f"repos/{repo}/pulls?state=all&per_page=100") or []:
        if get(f"repos/{repo}/commits?sha={pr['head']['sha']}&path=TRAPS.md&per_page=1"):
            fails.append(f"pull refs: {repo}#{pr['number']} ({pr['state']}) still reaches a commit that "
                         "touched TRAPS.md via refs/pull. Only a fresh repo removes it: "
                         "estate/scripts/05-delete-estate.sh, then 10-new-org-from-main.sh.")
        if HINT.search(f"{pr['title']}\n{pr.get('body') or ''}"):
            fails.append(f"old PRs: {repo}#{pr['number']} ({pr['state']}) cites the answer key in its "
                         "title or body. Closed PRs cannot be deleted: recreate the repo "
                         "(05-delete-estate.sh, then 10-new-org-from-main.sh).")
        if pr["state"] == "open" and pr["head"]["ref"] in expected:
            changed = {f["filename"] for f in get(f"repos/{repo}/pulls/{pr['number']}/files?per_page=100") or []}
            extra = changed - expected[pr["head"]["ref"]]
            if extra:
                fails.append(f"PR files: {repo}#{pr['number']} also changes {sorted(extra)}. "
                             "Run `prlab_eval cleanup` and `setup` again for this tool.")
    workflow = get(f"repos/{repo}/contents/{WORKFLOW}")
    if needs_workflow and not (isinstance(workflow, dict) and workflow.get("content")):
        fails.append(f"workflow: {repo} has no {WORKFLOW}, so its Claude review cannot run as audited. "
                     "Run estate/scripts/20-install-claude-workflow.sh.")
    if isinstance(workflow, dict) and workflow.get("content"):
        text = base64.b64decode(workflow["content"]).decode()
        if not FETCH_DEPTH_1.search(text):
            fails.append(f"workflow: {repo} checks out without fetch-depth: 1, so history is readable. "
                         "Re-run estate/scripts/20-install-claude-workflow.sh.")
        if OPEN_SHELL.search(text):
            fails.append(f"workflow: {repo} lets Claude run any shell command, network included, so it can "
                         "fetch the public harness repo. Restrict Bash to named commands, e.g. Bash(git diff:*).")
    return fails


def claude_orgs() -> set[str]:
    """Orgs that run a Claude tool: every repo there must carry the workflow."""
    owners = json.loads((ROOT / "cases/owners.json").read_text())
    return {org for tool, org in owners.items() if tool.startswith("claude-")}


def audit_org(
    org: str, expected: dict[str, set[str]] | None = None, get: Getter = gh_get, *, needs_workflow: bool = False
) -> OrgAudit:
    if "/" in org:  # a monorepo owner: one repo, not the eleven
        owner = org.split("/", 1)[0]
        result = OrgAudit(org)
        if get(f"repos/{owner}/prlab-review-tests") is not None:
            result.fails.append(f"harness: {owner}/prlab-review-tests exists; the reviewer can read every trap.")
        result.fails += audit_repo(org, patch_files(monorepo=True), get)
        return result
    expected = patch_files() if expected is None else expected
    result = OrgAudit(org)
    if get(f"repos/{org}/prlab-review-tests") is not None:
        result.fails.append(f"harness: {org}/prlab-review-tests exists; the reviewer can read every trap. "
                            "Keep the harness in an org no reviewer is installed on.")
    with ThreadPoolExecutor(max_workers=8) as pool:
        repos = [f"{org}/prlab-cricket-{short}" for short in REPOS]
        for fails in pool.map(lambda repo: audit_repo(repo, expected, get, needs_workflow=needs_workflow), repos):
            result.fails += fails
    return result


def audit_orgs(orgs: list[str], get: Getter = gh_get) -> list[OrgAudit]:
    expected = patch_files()
    claude = claude_orgs()
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(lambda org: audit_org(org, expected, get, needs_workflow=org in claude), orgs))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    if argv == ["--all"]:
        argv = sorted(set(json.loads((ROOT / "cases/owners.json").read_text()).values()))
    exempt = exempt_orgs()
    for org in [org for org in argv if org in exempt]:
        print(f"SKIP  {org}\n      exempt (cases/audit_exempt.json): {exempt[org]}")
    argv = [org for org in argv if org not in exempt]
    try:
        results = audit_orgs(argv)
    except AuditError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for result in results:
        print("\n".join(result.summary()))
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
