import json
from pathlib import Path

import pytest

from prlab_eval.cases import load_cases
from prlab_eval.runall import (
    RunAllError,
    ToolOutcome,
    case_filter,
    latest_report,
    leaderboard,
    pytest_argv,
    tool_names,
)
from prlab_eval.tools import TOOLS


def test_tool_names_defaults_to_every_registered_tool() -> None:
    assert tool_names(None) == list(TOOLS)


def test_tool_names_subset_keeps_registry_order() -> None:
    assert tool_names("qodo, greptile") == [
        name for name in TOOLS if name in {"qodo", "greptile"}
    ]


def test_tool_names_rejects_unknown() -> None:
    with pytest.raises(RunAllError) as exc:
        tool_names("greptile,nope")
    assert "nope" in str(exc.value)


def test_case_filter_builds_a_k_expression() -> None:
    ids = [case.id for case in load_cases()][:2]
    assert case_filter(",".join(ids)) == f"{ids[0]} or {ids[1]}"


def test_case_filter_rejects_unknown_case_id() -> None:
    with pytest.raises(RunAllError) as exc:
        case_filter("not-a-case")
    assert "not-a-case" in str(exc.value)


def test_pytest_argv_pins_tool_and_owner() -> None:
    argv = pytest_argv("qodo", "org-qodo1")
    assert argv[1:] == ["-m", "pytest", "tests/eval", "--run-eval", "--tool", "qodo",
                        "--owner", "org-qodo1"]


def test_pytest_argv_fast_drops_judge_flags() -> None:
    argv = pytest_argv("qodo", "org-qodo1", fast=True, judge_provider="groq")
    assert "--fast" in argv
    assert "--judge-provider" not in argv


def test_pytest_argv_passes_judge_and_wait() -> None:
    argv = pytest_argv(
        "greptile", "srajat-leap", only="a or b", wait=90,
        judge_provider="groq", judge_model="llama-3.3-70b-versatile", cleanup=True,
    )
    assert argv[argv.index("-k") + 1] == "a or b"
    assert argv[argv.index("--wait") + 1] == "90"
    assert argv[argv.index("--judge-provider") + 1] == "groq"
    assert "--cleanup" in argv


def _report(tmp_path: Path, tool: str, **payload) -> Path:
    folder = tmp_path / tool
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"review-eval-{tool}.json"
    path.write_text(json.dumps({"passed": 0, "failed": 0, "precision": 0.0,
                                "recall": 0.0, "f1": 0.0, **payload}))
    return path


def test_latest_report_prefers_the_newest_file(tmp_path: Path) -> None:
    folder = tmp_path / "qodo"
    folder.mkdir()
    old = folder / "review-eval-qodo-a.json"
    new = folder / "review-eval-qodo-b.json"
    old.write_text("{}")
    new.write_text("{}")
    import os

    os.utime(old, (1, 1))
    assert latest_report("qodo", tmp_path) == new


def test_latest_report_is_none_without_a_run(tmp_path: Path) -> None:
    assert latest_report("qodo", tmp_path) is None


def test_leaderboard_ranks_by_f1(tmp_path: Path) -> None:
    weak = _report(tmp_path, "qodo", passed=4, failed=10, f1=0.3)
    strong = _report(tmp_path, "greptile", passed=13, failed=1, f1=0.61)
    text = leaderboard([
        ToolOutcome("qodo", "org-qodo1", 1, weak),
        ToolOutcome("greptile", "srajat-leap", 1, strong),
    ])
    body = [line for line in text.splitlines() if line.startswith(("qodo", "greptile"))]
    assert body[0].startswith("greptile")
    assert "13/14" in body[0]


def test_leaderboard_flags_a_tool_that_never_scored(tmp_path: Path) -> None:
    scored = _report(tmp_path, "qodo", passed=1, failed=1, f1=0.5)
    text = leaderboard([
        ToolOutcome("qodo", "org-qodo1", 1, scored),
        ToolOutcome("claude-skill", "org-claudeskill", 4, None),
    ])
    lines = text.splitlines()
    assert "did not score (exit 4)" in lines[-1]
    assert lines[-1].startswith("claude-skill")
