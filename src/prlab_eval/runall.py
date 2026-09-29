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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from prlab_eval.audit import AuditError, OrgAudit, audit_orgs, exempt_orgs
from prlab_eval.cases import ROOT, OwnerError, load_cases, owner_mismatch, resolve_owner
from prlab_eval.report import ERRORED, REPORT_DIR, errored_cases, is_rescored, report_run_at, reviewer_dir
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
        # pytest exits 1 both for a missed trap and for a run that wrote nothing.
        return self.exit_code in SCORED_EXITS and self.report is not None


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


def new_report(tool: str, before: set[Path], out_dir: Path | None = None) -> Path | None:
    """The newest report for ``tool`` that was not in ``before``, if any case in it was scored.

    A report whose every case errored (judge down, GitHub failing) measured
    nothing about the reviewer, so it does not count as a score.
    """
    fresh = set(reviewer_dir(tool, out_dir).glob("review-eval-*.json")) - before
    if not fresh:
        return None
    report = max(fresh, key=lambda p: (report_run_at(p), p.stat().st_mtime))
    errored, total = errored_cases(report)
    if total and errored == total:
        _say(f"warning: every case of {tool} errored ({report.name}); it did not score")
        return None
    if errored:
        _say(f"warning: {errored}/{total} cases of {tool} errored and count as missed ({report.name})")
    return report


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
        f"{'TOOL':<20} {'OWNER':<18} {'PASSED':<8} {'P':<6} {'R':<6} {'F1':<6} REPORT",
    ]
    for outcome, payload in rows:
        if payload is None:
            lines.append(
                f"{outcome.tool:<20} {outcome.owner:<18} "
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
            f"{outcome.tool:<20} {outcome.owner:<18} {passed:<8} "
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
    skip_audit: bool = False,
    audit: Callable[[list[str]], list[OrgAudit]] = audit_orgs,
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
        shared = sorted({owners[name] for name in names if list(owners.values()).count(owners[name]) > 1})
        raise RunAllError(
            f"two or more tools resolve to the same GitHub owner ({', '.join(shared)}). Their bots "
            "would review each other's PRs, and --cleanup for one would close the others'. "
            "Unset $PRLAB_OWNER, or pass --owner only with a single --tools entry."
        )

    _say(f"running {len(names)} reviewer(s): {', '.join(names)}")
    for name in names:
        _say(f"  {name:<20} -> {owners[name]}")

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

    # A score only means something if the reviewer saw nothing but the PR.
    exempt = {} if skip_audit else exempt_orgs()
    unaudited = [name for name in names if skip_audit or owners[name] in exempt]
    if skip_audit:
        _say("warning: --skip-audit: orgs were not checked for leaks; these scores may be inflated")
    else:
        for org in sorted(distinct & exempt.keys()):
            tools_there = ", ".join(name for name in names if owners[name] == org)
            _say(f"warning: {tools_there} ({org}) is not leak-audited: {exempt[org]}")
        orgs = sorted(distinct - exempt.keys())
        _say(f"[audit] {', '.join(orgs) or '(nothing left to audit)'}")
        try:
            results = audit(orgs)
        except AuditError as exc:
            raise RunAllError(f"leak audit could not read GitHub, so nothing ran: {exc}") from exc
        for result in results:
            _say("\n".join(result.summary()))
        failed = [result.org for result in results if not result.ok]
        if failed:
            raise RunAllError(
                f"leak audit failed for {', '.join(failed)}: a reviewer there can read more than the PR. "
                "Fix the lines above (estate/README.md explains each), or pass --skip-audit to run anyway."
            )

    # An exempt org is kept exactly as it is: its existing PRs and reviews are
    # scored, and nothing is opened, pushed or posted there.
    kept = exempt_orgs()
    if setup:
        for name in names:
            if owners[name] in kept:
                _say(f"[setup] {name} skipped: {owners[name]} is kept as it is; its open PRs are scored")
                continue
            _say(f"[setup] {name}")
            _run_setup(only, name, owners[name])

    if trigger:
        for name in names:
            if owners[name] in kept:
                _say(f"[trigger] {name} skipped: nothing is posted in {owners[name]}")
                continue
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
        before = set(reviewer_dir(name).glob("review-eval-*.json"))
        completed = subprocess.run(argv, cwd=str(ROOT), check=False)
        # Only a report this run wrote. pytest exits 1 both for a missed trap and
        # for cases that all errored, and the latter writes nothing new.
        report = new_report(name, before) if completed.returncode in SCORED_EXITS else None
        if completed.returncode in SCORED_EXITS and report is None:
            _say(f"warning: {name} exited {completed.returncode} but wrote no new report")
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
    if unaudited:
        print(f"not leak-audited: {', '.join(unaudited)} (see cases/audit_exempt.json)")
    print(f"\nreports under {REPORT_DIR}")
    from prlab_eval.canvas import CanvasError, write_comparison  # deferred: canvas imports this module

    try:
        print(f"comparison: {write_comparison()}")
        stale = [item.tool for item in outcomes if not item.scored and latest_report(item.tool) is not None]
        if stale:
            _say(f"warning: the comparison still shows an earlier run for {', '.join(stale)}; "
                 "this run did not score them")
    except CanvasError as exc:
        _say(f"warning: comparison not refreshed: {exc}")
    return 0 if all(item.scored for item in outcomes) else 1
