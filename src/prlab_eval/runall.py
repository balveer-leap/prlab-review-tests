"""Run every registered reviewer against the same cases, one after another.

Each reviewer lives in its own GitHub owner, so a combined run is a sequence of
single-tool pytest sessions, not one session with many tools. Setup and trigger
run for every tool up front: auto-review vendors and Action workflows then have
the whole preparation window to post before anything is scored.

A reviewer that misses a trap makes pytest exit 1. That is a result, not a
breakage, so the sequence keeps going and only a usage or judge error stops it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from prlab_eval.cases import ROOT, OwnerError, load_cases, owner_mismatch, resolve_owner
from prlab_eval.report import REPORT_DIR, is_rescored, report_run_at, reviewer_dir
from prlab_eval.tools import TOOLS

# pytest: 0 = every case passed, 1 = some case failed. Both wrote a report.
# 2 (interrupted), 3 (internal), 4 (usage), 5 (nothing collected) did not.
SCORED_EXITS = frozenset({0, 1})


class RunAllError(RuntimeError):
    """A tool run failed for a reason that is not a missed trap."""


@dataclass(frozen=True)
class ToolOutcome:
    tool: str
    owner: str
    exit_code: int
    report: Path | None

    @property
    def scored(self) -> bool:
        return self.exit_code in SCORED_EXITS


def tool_names(requested: str | None) -> list[str]:
    """Registered tool names to run, in registry order. ``None`` means all."""
    if not requested:
        return list(TOOLS)
    wanted = [item.strip() for item in requested.split(",") if item.strip()]
    unknown = [name for name in wanted if name not in TOOLS]
    if unknown:
        known = ", ".join(sorted(TOOLS))
        raise RunAllError(f"unknown tool(s) {', '.join(unknown)}. registered: {known}")
    return [name for name in TOOLS if name in wanted]


def case_filter(only: str | None) -> str:
    """``-k`` expression for ``--only``. Validates ids so a typo fails fast."""
    if not only:
        return ""
    wanted = [item.strip() for item in only.split(",") if item.strip()]
    known = {case.id for case in load_cases()}
    unknown = [item for item in wanted if item not in known]
    if unknown:
        raise RunAllError(f"unknown case id(s): {', '.join(unknown)}")
    return " or ".join(wanted)


def pytest_argv(
    tool: str,
    owner: str,
    *,
    only: str = "",
    wait: int = 0,
    fast: bool = False,
    judge_provider: str | None = None,
    judge_model: str | None = None,
    cleanup: bool = False,
) -> list[str]:
    """Command line for one tool's scoring session."""
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "tests/eval",
        "--run-eval",
        "--tool",
        tool,
        "--owner",
        owner,
    ]
    if only:
        argv += ["-k", only]
    if wait:
        argv += ["--wait", str(wait)]
    if fast:
        argv.append("--fast")
    else:
        if judge_provider:
            argv += ["--judge-provider", judge_provider]
        if judge_model:
            argv += ["--judge-model", judge_model]
    if cleanup:
        argv.append("--cleanup")
    return argv


def latest_report(tool: str, out_dir: Path | None = None) -> Path | None:
    """Report for ``tool``'s most recent run, preferring its rescored version.

    Ignores anything filed under superseded/.
    """
    folder = reviewer_dir(tool, out_dir)
    reports = sorted(
        folder.glob("review-eval-*.json"),
        key=lambda p: (report_run_at(p), is_rescored(p), p.stat().st_mtime),
    )
    return reports[-1] if reports else None


def leaderboard(outcomes: list[ToolOutcome]) -> str:
    """Combined standings, best F1 first. Tools that never scored are listed last."""
    rows: list[tuple[ToolOutcome, dict | None]] = []
    for outcome in outcomes:
        payload = None
        if outcome.scored and outcome.report is not None:
            payload = json.loads(outcome.report.read_text())
        rows.append((outcome, payload))
    rows.sort(key=lambda item: (item[1] is None, -(item[1] or {}).get("f1", 0.0)))

    lines = [
        "",
        "combined leaderboard",
        f"{'TOOL':<16} {'OWNER':<18} {'PASSED':<8} {'P':<6} {'R':<6} {'F1':<6} REPORT",
    ]
    for outcome, payload in rows:
        if payload is None:
            lines.append(
                f"{outcome.tool:<16} {outcome.owner:<18} "
                f"{'-':<8} {'-':<6} {'-':<6} {'-':<6} "
                f"did not score (exit {outcome.exit_code})"
            )
            continue
        passed = "{}/{}".format(payload["passed"], payload["passed"] + payload["failed"])
        precision = "{:.0%}".format(payload["precision"])
        recall = "{:.0%}".format(payload["recall"])
        f1 = "{:.0%}".format(payload["f1"])
        name = outcome.report.name if outcome.report else "-"
        lines.append(
            f"{outcome.tool:<16} {outcome.owner:<18} {passed:<8} "
            f"{precision:<6} {recall:<6} {f1:<6} {name}"
        )
    return "\n".join(lines)


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def run_all(
    *,
    tools: str | None = None,
    only: str | None = None,
    setup: bool = False,
    trigger: bool = False,
    wait: int = 0,
    fast: bool = False,
    judge_provider: str | None = None,
    judge_model: str | None = None,
    cleanup: bool = False,
    owner: str | None = None,
    dry_run: bool = False,
) -> int:
    from prlab_eval.cli import _run_setup, _run_trigger  # deferred: cli imports this module

    names = tool_names(tools)
    selection = case_filter(only)

    owners: dict[str, str] = {}
    for name in names:
        try:
            owners[name] = resolve_owner(name, owner)
        except OwnerError as exc:
            raise RunAllError(str(exc)) from exc
        warning = owner_mismatch(name, owners[name])
        if warning:
            _say(warning)

    distinct = {owners[name] for name in names}
    if len(distinct) < len(names):
        _say(
            "warning: two or more tools share a GitHub owner. Their bots will "
            "review each other's PRs and isolation will fail on every case."
        )

    _say(f"running {len(names)} reviewer(s): {', '.join(names)}")
    for name in names:
        _say(f"  {name:<16} -> {owners[name]}")

    if dry_run:
        for name in names:
            argv = pytest_argv(
                name,
                owners[name],
                only=selection,
                wait=wait,
                fast=fast,
                judge_provider=judge_provider,
                judge_model=judge_model,
                cleanup=cleanup,
            )
            print(" ".join(argv))
        return 0

    if setup:
        for name in names:
            _say(f"[setup] {name}")
            _run_setup(only, name, owners[name])

    if trigger:
        for name in names:
            _say(f"[trigger] {name}")
            _run_trigger(name, only, owners[name])

    outcomes: list[ToolOutcome] = []
    for name in names:
        argv = pytest_argv(
            name,
            owners[name],
            only=selection,
            wait=wait,
            fast=fast,
            judge_provider=judge_provider,
            judge_model=judge_model,
            cleanup=cleanup,
        )
        _say(f"[score] {name}")
        completed = subprocess.run(argv, cwd=str(ROOT), check=False)
        report = latest_report(name) if completed.returncode in SCORED_EXITS else None
        outcomes.append(
            ToolOutcome(
                tool=name,
                owner=owners[name],
                exit_code=completed.returncode,
                report=report,
            )
        )
        if completed.returncode not in SCORED_EXITS:
            _say(f"warning: {name} exited {completed.returncode} without a report")

    print(leaderboard(outcomes))
    print(f"\nreports under {REPORT_DIR}")
    from prlab_eval.canvas import CanvasError, write_canvas  # deferred: canvas imports this module

    try:
        print(f"canvas: {write_canvas()}")
    except CanvasError as exc:
        _say(f"warning: canvas not refreshed: {exc}")
    return 0 if all(item.scored for item in outcomes) else 1
