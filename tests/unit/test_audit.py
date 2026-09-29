import base64

import pytest

from prlab_eval import runall
from prlab_eval.audit import OrgAudit, audit_org, audit_repo, patch_files
from prlab_eval.runall import RunAllError, run_all

REPO = "org-x/prlab-cricket-protocol"
BRANCH = "eval/protocol-default-confirm-cold"


def fake_github(responses: dict[str, object]):
    """GitHub GET stub: exact path -> JSON; anything unlisted is an empty list."""

    def get(path: str) -> object | None:
        return responses.get(path, [])

    return get


def clean_repo(**overrides: object) -> dict[str, object]:
    pr = {"number": 8, "state": "open", "title": "Default umpire_confirmed for older mobile clients",
          "body": "Older mobile clients sometimes omit umpire_confirmed.", "head": {"ref": BRANCH, "sha": "abc"}}
    responses: dict[str, object] = {
        f"repos/{REPO}/branches?per_page=100": [{"name": "main"}, {"name": BRANCH}],
        f"repos/{REPO}/pulls?state=all&per_page=100": [pr],
        f"repos/{REPO}/pulls/8/files?per_page=100": [{"filename": name} for name in patch_files()[BRANCH]],
    }
    responses.update(overrides)
    return responses


def test_clean_repo_passes() -> None:
    assert audit_repo(REPO, patch_files(), fake_github(clean_repo())) == []


def test_branch_history_reaching_traps_md_fails() -> None:
    get = fake_github(clean_repo(**{f"repos/{REPO}/commits?sha=main&path=TRAPS.md&per_page=1": [{"sha": "old"}]}))
    [fail] = audit_repo(REPO, patch_files(), get)
    assert fail.startswith("history:") and "main" in fail


def test_closed_pr_ref_reaching_traps_md_fails() -> None:
    get = fake_github(clean_repo(**{f"repos/{REPO}/commits?sha=abc&path=TRAPS.md&per_page=1": [{"sha": "old"}]}))
    [fail] = audit_repo(REPO, patch_files(), get)
    assert fail.startswith("pull refs:")


def test_pr_body_citing_the_answer_key_fails() -> None:
    old = {"number": 1, "state": "closed", "title": "Default umpire_confirmed", "body": "See `TRAPS.md`.",
           "head": {"ref": "trap/default-confirm-wickets", "sha": "def"}}
    get = fake_github(clean_repo(**{f"repos/{REPO}/pulls?state=all&per_page=100": [old]}))
    assert [f.split(":")[0] for f in audit_repo(REPO, patch_files(), get)] == ["old PRs"]


def test_trap_branch_fails() -> None:
    get = fake_github(clean_repo(**{f"repos/{REPO}/branches?per_page=100": [{"name": "trap/leak-raw-ball"}]}))
    assert any(f.startswith("branches:") for f in audit_repo(REPO, patch_files(), get))


def test_open_pr_with_a_file_outside_its_patch_fails() -> None:
    files = [{"filename": name} for name in patch_files()[BRANCH]] + [{"filename": ".DS_Store"}]
    get = fake_github(clean_repo(**{f"repos/{REPO}/pulls/8/files?per_page=100": files}))
    [fail] = audit_repo(REPO, patch_files(), get)
    assert fail.startswith("PR files:") and ".DS_Store" in fail


@pytest.mark.parametrize("depth, fails", [("1", False), ("0", True)])
def test_claude_workflow_needs_a_shallow_checkout(depth: str, fails: bool) -> None:
    workflow = f"steps:\n  - uses: actions/checkout@v6\n    with:\n      fetch-depth: {depth}\n"
    content = base64.b64encode(workflow.encode()).decode()
    get = fake_github(clean_repo(**{
        f"repos/{REPO}/contents/.github/workflows/claude-review.yml": {"content": content},
    }))
    assert bool(audit_repo(REPO, patch_files(), get)) is fails


def test_missing_repo_is_reported() -> None:
    def get(path: str) -> object | None:
        return None

    assert audit_repo(REPO, patch_files(), get)[0].startswith("missing:")


def test_harness_repo_inside_the_org_fails() -> None:
    def get(path: str) -> object | None:
        if path == "repos/org-x/prlab-review-tests":
            return {"name": "prlab-review-tests"}
        if path.endswith("/branches?per_page=100"):
            return [{"name": "main"}]
        return []

    result = audit_org("org-x", patch_files(), get)
    assert [f.split(":")[0] for f in result.fails] == ["harness"]


def test_summary_groups_repeats() -> None:
    result = OrgAudit("org-x", ["history: a", "history: b", "history: c", "harness: x"])
    assert result.summary() == [
        "FAIL  org-x",
        "      history: a  (+2 more like this)",
        "      harness: x",
    ]


def _owners(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runall, "resolve_owner", lambda name, owner: f"org-{name}")
    monkeypatch.setattr(runall, "owner_mismatch", lambda name, owner: None)


def test_run_all_stops_before_setup_when_an_org_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _owners(monkeypatch)
    ran: list[str] = []
    monkeypatch.setattr("prlab_eval.cli._run_setup", lambda *a: ran.append("setup"))
    with pytest.raises(RunAllError) as exc:
        run_all(tools="qodo", setup=True, audit=lambda orgs: [OrgAudit(orgs[0], ["history: x"])])
    assert "org-qodo" in str(exc.value) and "--skip-audit" in str(exc.value)
    assert ran == []


def test_run_all_skip_audit_never_calls_the_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    _owners(monkeypatch)
    monkeypatch.setattr("prlab_eval.cli._run_setup", lambda *a: None)
    monkeypatch.setattr(runall.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 4})())

    def boom(orgs: list[str]) -> list[OrgAudit]:
        raise AssertionError("audit ran")

    assert run_all(tools="qodo", setup=True, skip_audit=True, audit=boom) is not None


def test_dry_run_does_not_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    _owners(monkeypatch)

    def boom(orgs: list[str]) -> list[OrgAudit]:
        raise AssertionError("audit ran")

    assert run_all(tools="qodo", dry_run=True, audit=boom) == 0


def test_open_shell_in_a_claude_workflow_fails() -> None:
    workflow = ('steps:\n  - uses: actions/checkout@v6\n    with:\n      fetch-depth: 1\n'
                '  - with:\n      claude_args: "--max-turns 60 --allowedTools Read,Grep,Glob,Bash"\n')
    content = base64.b64encode(workflow.encode()).decode()
    get = fake_github(clean_repo(**{
        f"repos/{REPO}/contents/.github/workflows/claude-review.yml": {"content": content},
    }))
    [fail] = audit_repo(REPO, patch_files(), get)
    assert fail.startswith("workflow:") and "shell" in fail


def test_the_shipped_workflows_pass_the_workflow_checks() -> None:
    from prlab_eval.audit import FETCH_DEPTH_1, OPEN_SHELL
    from prlab_eval.cases import ROOT

    for name in ("claude-normal.yml", "claude-best.yml"):
        text = (ROOT / "estate/workflows" / name).read_text()
        assert FETCH_DEPTH_1.search(text) and not OPEN_SHELL.search(text), name


def test_an_unreadable_org_fails_instead_of_passing(monkeypatch: pytest.MonkeyPatch) -> None:
    from prlab_eval import audit

    def rate_limited(args, **kwargs):
        return type("P", (), {"returncode": 1, "stdout": "", "stderr": "gh: API rate limit exceeded (HTTP 403)"})()

    monkeypatch.setattr(audit.subprocess, "run", rate_limited)
    with pytest.raises(audit.AuditError):
        audit.gh_get("repos/org-x/prlab-cricket-protocol/branches")


def test_a_missing_repo_is_none_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from prlab_eval import audit

    monkeypatch.setattr(audit.subprocess, "run", lambda args, **kw: type(
        "P", (), {"returncode": 1, "stdout": "", "stderr": "gh: Not Found (HTTP 404)"})())
    assert audit.gh_get("repos/org-x/prlab-review-tests") is None


def test_errored_case_is_recorded_as_a_failed_row() -> None:
    from prlab_eval.cases import load_cases
    from prlab_eval.harness import ReviewHarness
    from prlab_eval.tools import TOOLS

    case = load_cases()[0]
    harness = ReviewHarness.__new__(ReviewHarness)
    harness.tool, harness.judge_mode = TOOLS["qodo"], "llm"
    row = harness.errored(case, RuntimeError("judge HTTP 429 after 6 retries"), "https://example.test/1")
    assert row.case_id == case.id and not row.finding_passed
    assert all(not claim.passed and "429" in claim.reason for claim in row.claims)
    assert row.metrics.recall == 0.0


@pytest.mark.parametrize("line, open_shell", [
    ('--allowedTools Read,Grep,Glob,Bash', True),
    ('--allowedTools "Read,Bash"', True),
    ('--allowed-tools Bash', True),
    ('--allowedTools Bash(*)', True),
    ('allowed_tools: "Bash"', True),
    ('--allowedTools Read,Grep,Glob,LS', False),
    ('--allowedTools Read,Bash(git diff:*)', False),
    ('--max-turns 20', False),
])
def test_open_shell_forms(line: str, open_shell: bool) -> None:
    from prlab_eval.audit import OPEN_SHELL

    assert bool(OPEN_SHELL.search(line)) is open_shell


def test_an_exempt_org_is_not_audited_and_is_named(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _owners(monkeypatch)
    monkeypatch.setattr(runall, "exempt_orgs", lambda: {"org-greptile": "kept as it is"})
    monkeypatch.setattr("prlab_eval.cli._run_setup", lambda *a: None)
    monkeypatch.setattr(runall.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 4})())
    audited: list[list[str]] = []

    def audit(orgs: list[str]) -> list[OrgAudit]:
        audited.append(orgs)
        return [OrgAudit(org) for org in orgs]

    run_all(tools="greptile,qodo", setup=True, audit=audit)
    assert audited == [["org-qodo"]]
    out = capsys.readouterr()
    assert "greptile (org-greptile) is not leak-audited: kept as it is" in out.err
    assert "not leak-audited: greptile" in out.out


def test_audit_all_skips_exempt_orgs(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from prlab_eval import audit

    monkeypatch.setattr(audit, "exempt_orgs", lambda: {"srajat-leap": "kept as it is"})
    monkeypatch.setattr(audit, "audit_orgs", lambda orgs: [OrgAudit(org) for org in orgs])
    assert audit.main(["srajat-leap", "org-qodo1"]) == 0
    out = capsys.readouterr().out
    assert "SKIP  srajat-leap" in out and "PASS  org-qodo1" in out


def test_an_exempt_org_is_never_set_up_or_triggered(monkeypatch: pytest.MonkeyPatch) -> None:
    _owners(monkeypatch)
    monkeypatch.setattr(runall, "exempt_orgs", lambda: {"org-greptile": "kept as it is"})
    touched: list[str] = []
    monkeypatch.setattr("prlab_eval.cli._run_setup", lambda only, name, owner: touched.append(f"setup {name}"))
    monkeypatch.setattr("prlab_eval.cli._run_trigger", lambda name, only, owner: touched.append(f"trigger {name}"))
    monkeypatch.setattr(runall.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 4})())
    for skip_audit in (False, True):
        touched.clear()
        run_all(tools="greptile,qodo", setup=True, trigger=True, skip_audit=skip_audit,
                audit=lambda orgs: [OrgAudit(org) for org in orgs])
        assert touched == ["setup qodo", "trigger qodo"]


def test_a_claude_org_repo_without_the_workflow_fails() -> None:
    get = fake_github(clean_repo())
    assert audit_repo(REPO, patch_files(), get) == []
    [fail] = audit_repo(REPO, patch_files(), get, needs_workflow=True)
    assert fail.startswith("workflow:") and "20-install-claude-workflow" in fail


def test_claude_orgs_come_from_owners() -> None:
    from prlab_eval.audit import claude_orgs

    assert claude_orgs() == {"org-claudplain", "org-claudeskill"}


def test_gh_get_reads_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    from prlab_eval import audit

    seen: list[list[str]] = []

    def two_pages(args, **kwargs):
        seen.append(args)
        return type("P", (), {"returncode": 0, "stdout": '[{"n": 1}]\n[{"n": 2}]', "stderr": ""})()

    monkeypatch.setattr(audit.subprocess, "run", two_pages)
    assert audit.gh_get("repos/org-x/prlab-cricket-protocol/pulls?state=all&per_page=100") == [{"n": 1}, {"n": 2}]
    assert "--paginate" in seen[0]


def test_estate_token_never_goes_in_a_clone_url() -> None:
    from prlab_eval.cases import ROOT

    text = (ROOT / "estate/workflows/claude-best.yml").read_text()
    assert "x-access-token:${ESTATE_TOKEN}@" not in text
    assert "extraheader" in text and "ESTATE_TOKEN found in a clone's .git/config" in text


def test_monorepo_patch_files_carry_the_service_folder() -> None:
    files = patch_files(monorepo=True)["eval/scoring-leak-raw-ball"]
    assert files and all(path.startswith("cricket-scoring/") for path in files)


def test_a_monorepo_owner_is_audited_as_one_repo() -> None:
    seen: list[str] = []

    def get(path: str):
        seen.append(path)
        if path == "repos/org-x/prlab-review-tests":
            return None
        return [{"name": "main"}] if path.endswith("/branches?per_page=100") else []

    result = audit_org("org-x/estate", get=get)
    assert result.ok
    assert any(path.startswith("repos/org-x/estate/") for path in seen)
    assert not any("prlab-cricket-protocol" in path for path in seen)
