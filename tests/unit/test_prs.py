from pathlib import Path

from prlab_eval.prs import push_eval_branch, remote_branch_exists


def test_remote_branch_exists_is_false_when_ls_remote_is_empty(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(args, cwd=None):
        calls.append(list(args))
        return ""

    monkeypatch.setattr("prlab_eval.prs.run", fake_run)
    assert remote_branch_exists(tmp_path, "eval/missing") is False
    assert calls[0][:3] == ["git", "fetch", "origin"]
    assert "--prune" in calls[0]
    assert calls[1] == ["git", "ls-remote", "--heads", "origin", "eval/missing"]


def test_push_creates_branch_when_missing(monkeypatch, tmp_path: Path) -> None:
    pushed: list[list[str]] = []

    def fake_run(args, cwd=None):
        if args[:2] == ["git", "push"]:
            pushed.append(list(args))
        return ""

    monkeypatch.setattr("prlab_eval.prs.run", fake_run)
    assert push_eval_branch(tmp_path, "eval/new") == "created"
    assert pushed == [["git", "push", "-u", "origin", "HEAD:eval/new"]]


def test_push_force_updates_existing_branch(monkeypatch, tmp_path: Path) -> None:
    pushed: list[list[str]] = []

    def fake_run(args, cwd=None):
        if args[:3] == ["git", "ls-remote", "--heads"]:
            return "abc123\trefs/heads/eval/old"
        if args[:2] == ["git", "push"]:
            pushed.append(list(args))
        return ""

    monkeypatch.setattr("prlab_eval.prs.run", fake_run)
    assert push_eval_branch(tmp_path, "eval/old") == "updated"
    assert pushed == [["git", "push", "-u", "origin", "HEAD:eval/old", "--force-with-lease"]]


def _git_remotes(monkeypatch, remotes: dict[str, str]) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(args, cwd=None):
        calls.append(list(args))
        if args == ["git", "remote"]:
            return "\n".join(remotes)
        if args[:3] == ["git", "remote", "get-url"]:
            return remotes[args[3]] + "\n"
        return ""

    monkeypatch.setattr("prlab_eval.prs.run", fake_run)
    return calls


def test_remote_for_default_owner_is_origin(monkeypatch, tmp_path: Path) -> None:
    from prlab_eval.prs import remote_for

    calls = _git_remotes(monkeypatch, {"origin": "https://github.com/srajat-leap/prlab-cricket-stats.git"})
    assert remote_for(tmp_path, "srajat-leap/prlab-cricket-stats") == "origin"
    assert not any(call[:3] == ["git", "remote", "add"] for call in calls)


def test_remote_for_other_owner_adds_named_remote(monkeypatch, tmp_path: Path) -> None:
    from prlab_eval.prs import remote_for

    calls = _git_remotes(monkeypatch, {"origin": "https://github.com/srajat-leap/prlab-cricket-stats"})
    assert remote_for(tmp_path, "org-qodo1/prlab-cricket-stats") == "org-qodo1"
    assert ["git", "remote", "add", "org-qodo1", "https://github.com/org-qodo1/prlab-cricket-stats.git"] in calls


def test_push_uses_given_remote(monkeypatch, tmp_path: Path) -> None:
    pushed: list[list[str]] = []

    def fake_run(args, cwd=None):
        if args[:2] == ["git", "push"]:
            pushed.append(list(args))
        return ""

    monkeypatch.setattr("prlab_eval.prs.run", fake_run)
    assert push_eval_branch(tmp_path, "eval/new", "org-coderabbit1") == "created"
    assert pushed == [["git", "push", "-u", "org-coderabbit1", "HEAD:eval/new"]]
