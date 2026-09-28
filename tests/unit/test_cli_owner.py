"""Live commands must never target an owner by accident."""

import pytest

from prlab_eval import cli


def test_cleanup_requires_a_target(monkeypatch) -> None:
    monkeypatch.delenv("PRLAB_OWNER", raising=False)
    monkeypatch.setattr(cli, "cleanup_eval", lambda *a, **k: pytest.fail("cleanup ran"))
    with pytest.raises(SystemExit, match="--tool or --owner"):
        cli.main(["cleanup"])


def test_cleanup_uses_the_tools_owner(monkeypatch) -> None:
    monkeypatch.delenv("PRLAB_OWNER", raising=False)
    seen = {}

    def fake_cleanup(only, *, owner):
        seen["owner"] = owner
        return []

    monkeypatch.setattr(cli, "cleanup_eval", fake_cleanup)
    assert cli.main(["cleanup", "--tool", "qodo"]) == 0
    assert seen["owner"] == "org-qodo1"


def test_setup_without_tool_or_owner_stops(monkeypatch) -> None:
    monkeypatch.delenv("PRLAB_OWNER", raising=False)
    monkeypatch.setattr(cli, "ensure_pr", lambda *a, **k: pytest.fail("setup ran"))
    with pytest.raises(SystemExit, match="no GitHub owner"):
        cli.main(["setup"])


def test_trigger_targets_the_tools_owner(monkeypatch) -> None:
    monkeypatch.delenv("PRLAB_OWNER", raising=False)
    repos = []

    def fake_trigger(case, tool):
        repos.append(case.github_repo)
        return "skipped", None

    monkeypatch.setattr(cli, "trigger_case", fake_trigger)
    cli.main(["trigger", "--tool", "coderabbit"])
    assert repos and all(repo.startswith("org-coderabbit1/") for repo in repos)


def test_cleanup_eval_refuses_empty_owner() -> None:
    from prlab_eval.cleanup import cleanup_eval

    with pytest.raises(ValueError, match="explicit owner"):
        cleanup_eval(owner="")
