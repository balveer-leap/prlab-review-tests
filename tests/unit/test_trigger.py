from prlab_eval.trigger import comment_has_trigger


def test_detects_exact_mention() -> None:
    assert comment_has_trigger(["@greptileai"], "@greptileai")


def test_detects_mention_in_longer_comment() -> None:
    assert comment_has_trigger(["please look at this\n@greptileai\n"], "@greptileai")


def test_is_case_insensitive() -> None:
    assert comment_has_trigger(["@GreptileAI"], "@greptileai")


def test_missing_mention_is_false() -> None:
    assert not comment_has_trigger(["looks good", ""], "@greptileai")


def test_empty_trigger_never_matches() -> None:
    assert not comment_has_trigger(["@greptileai"], "  ")


def test_run_retries_a_transient_network_error(monkeypatch) -> None:
    from prlab_eval import github

    calls: list[int] = []

    class Flaky:
        def __init__(self, args, **kwargs):
            calls.append(1)
            self.pid = 0
            self.returncode = 1 if len(calls) < 3 else 0

        def communicate(self, timeout=None):
            return ("ok", "") if self.returncode == 0 else ("", "read: connection reset by peer")

    monkeypatch.setattr(github.subprocess, "Popen", Flaky)
    monkeypatch.setattr(github.time, "sleep", lambda s: None)
    assert github.run(["gh", "api", "x"]) == "ok"
    assert len(calls) == 3


def test_run_does_not_retry_a_real_failure(monkeypatch) -> None:
    import pytest

    from prlab_eval import github

    calls: list[int] = []
    class NotFound:
        def __init__(self, args, **kwargs):
            calls.append(1)
            self.pid, self.returncode = 0, 1

        def communicate(self, timeout=None):
            return "", "HTTP 404: Not Found"

    monkeypatch.setattr(github.subprocess, "Popen", NotFound)
    with pytest.raises(github.GitHubError):
        github.run(["gh", "api", "x"])
    assert len(calls) == 1


def test_run_kills_a_hung_command_and_retries(monkeypatch) -> None:
    import subprocess

    from prlab_eval import github

    killed: list[int] = []

    class Hangs:
        count = 0

        def __init__(self, args, **kwargs):
            Hangs.count += 1
            self.pid, self.returncode = 4242, 0
            self.first = Hangs.count == 1

        def communicate(self, timeout=None):
            if self.first and timeout is not None:
                raise subprocess.TimeoutExpired("git", timeout)
            return "pushed", ""

    monkeypatch.setattr(github.subprocess, "Popen", Hangs)
    monkeypatch.setattr(github.os, "killpg", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(github.time, "sleep", lambda s: None)
    assert github.run(["git", "push"]) == "pushed"
    assert killed == [4242]
