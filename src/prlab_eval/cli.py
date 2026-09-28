from __future__ import annotations

import argparse
import sys
from pathlib import Path

from prlab_eval.canvas import CanvasError, write_canvas
from prlab_eval.cases import OwnerError, load_cases, owner_mismatch, resolve_owner
from prlab_eval.cleanup import cleanup_eval
from prlab_eval.judge import JudgeConfigError, LlmJudge, precheck_judge
from prlab_eval.prs import ensure_pr
from prlab_eval.rescore import run_rescore
from prlab_eval.runall import RunAllError, run_all, tool_names
from prlab_eval.tools import TOOLS, get_tool
from prlab_eval.trigger import trigger_case


def _wanted(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    return {item.strip() for item in raw.split(",") if item.strip()}


def _owner(tool_name: str | None, owner: str | None) -> str:
    """Resolve the target owner or stop. Never falls back to cases.json silently."""
    try:
        resolved = resolve_owner(tool_name, owner)
    except OwnerError as exc:
        raise SystemExit(f"error: {exc}") from exc
    warning = owner_mismatch(tool_name, resolved)
    if warning:
        print(warning, file=sys.stderr)
    print(f"owner: {resolved}", file=sys.stderr)
    return resolved


def _run_setup(only: str | None, tool_name: str | None, owner: str | None = None) -> int:
    wanted = _wanted(only)
    tool = get_tool(tool_name) if tool_name else None
    for case in load_cases(owner=_owner(tool_name, owner)):
        if wanted and case.id not in wanted:
            continue
        pr = ensure_pr(case, tool=tool, all_tools=tool is None)
        print(f"{case.id}\t{case.intent}\t{pr.url}")
    return 0


def _run_trigger(tool_name: str, only: str | None, owner: str | None = None) -> int:
    tool = get_tool(tool_name)
    wanted = _wanted(only)
    missing = 0
    for case in load_cases(owner=_owner(tool_name, owner)):
        if wanted and case.id not in wanted:
            continue
        status, pr = trigger_case(case, tool)
        url = pr.url if pr else "-"
        print(f"{case.id}\t{status}\t{url}")
        if status == "no_pr":
            missing += 1
    return 1 if missing else 0


def setup_prs(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open eval PRs. Does not trigger a review tool.")
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument(
        "--tool",
        help="select a review-tool plugin (default: every registered tool)",
    )
    parser.add_argument("--owner", help="GitHub owner holding the product repos (default: the tool's entry in cases/owners.json)")
    args = parser.parse_args(argv)
    return _run_setup(args.only, args.tool, args.owner)


def _run_cleanup(only: str | None, tool_name: str | None = None, owner: str | None = None) -> int:
    wanted = _wanted(only)
    if not tool_name and not owner:
        raise SystemExit("error: cleanup closes PRs and deletes branches; name the target with --tool or --owner")
    for line in cleanup_eval(wanted, owner=_owner(tool_name, owner)):
        print(line)
    return 0


def _run_canvas(out: Path | None = None) -> int:
    try:
        path = write_canvas(out)
    except CanvasError as exc:
        raise SystemExit(f"error: {exc}") from exc
    print(f"canvas: {path}")
    return 0


def _run_rescore(args: argparse.Namespace) -> int:
    try:
        tools = tool_names(args.tools)
    except RunAllError as exc:
        raise SystemExit(f"error: {exc}") from exc
    try:
        run_rescore(
            tools=tools,
            reports=args.report,
            all_runs=args.all_runs,
            judge_provider=args.judge_provider,
            judge_model=args.judge_model,
        )
    except JudgeConfigError as exc:
        raise SystemExit(f"error: {exc}") from exc
    return 0 if args.no_canvas else _run_canvas()


def _run_judge_check(provider: str | None, model: str | None) -> int:
    judge = LlmJudge.from_env(provider=provider, model=model)
    print(precheck_judge(judge))
    return 0


def cleanup_prs(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Close eval PRs and delete eval branches. Reports stay."
    )
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--tool", help="clean this tool's owner (from cases/owners.json)")
    parser.add_argument("--owner", help="GitHub owner holding the product repos (default: the tool's entry in cases/owners.json)")
    args = parser.parse_args(argv)
    return _run_cleanup(args.only, args.tool, args.owner)


def trigger_reviews(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ask a review tool to look at open eval PRs. Skips PRs that already have the mention."
    )
    parser.add_argument(
        "--tool",
        required=True,
        help=f"review tool to mention ({', '.join(sorted(TOOLS))})",
    )
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--owner", help="GitHub owner holding the product repos (default: the tool's entry in cases/owners.json)")
    args = parser.parse_args(argv)
    return _run_trigger(args.tool, args.only, args.owner)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PR review eval harness")
    sub = parser.add_subparsers(dest="command")

    setup_p = sub.add_parser("setup", help="open eval PRs")
    setup_p.add_argument("--only", help="comma-separated case ids")
    setup_p.add_argument("--tool", help="select a review-tool plugin")
    setup_p.add_argument("--owner", help="GitHub owner holding the product repos (default: the tool's entry in cases/owners.json)")

    trigger_p = sub.add_parser(
        "trigger",
        help="mention a review tool on open eval PRs once",
    )
    trigger_p.add_argument(
        "--tool",
        required=True,
        help=f"review tool to mention ({', '.join(sorted(TOOLS))})",
    )
    trigger_p.add_argument("--only", help="comma-separated case ids")
    trigger_p.add_argument("--owner", help="GitHub owner holding the product repos (default: the tool's entry in cases/owners.json)")

    all_p = sub.add_parser(
        "all",
        help="run every registered reviewer over the same cases, one after another",
    )
    all_p.add_argument(
        "--tools",
        help=f"comma-separated subset (default: every registered tool: {', '.join(TOOLS)})",
    )
    all_p.add_argument("--only", help="comma-separated case ids")
    all_p.add_argument("--setup", action="store_true", help="open each tool's eval PRs first")
    all_p.add_argument(
        "--trigger",
        action="store_true",
        help="mention each tool on its PRs before anything is scored",
    )
    all_p.add_argument("--wait", type=int, default=0, help="seconds to wait per case for a review")
    all_p.add_argument("--fast", action="store_true", help="keyword judge; no LLM key needed")
    all_p.add_argument("--judge-provider", dest="judge_provider")
    all_p.add_argument("--judge-model", dest="judge_model")
    all_p.add_argument(
        "--cleanup",
        action="store_true",
        help="close each tool's PRs once it has scored",
    )
    all_p.add_argument(
        "--owner",
        help="force one owner for every tool (default: each tool's entry in cases/owners.json)",
    )
    all_p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the per-tool pytest command lines and stop",
    )

    rescore_p = sub.add_parser(
        "rescore",
        help="re-judge saved reports offline (no GitHub, no vendors); writes -rescored reports",
    )
    rescore_p.add_argument(
        "--tools",
        help=f"comma-separated subset (default: every registered tool: {', '.join(TOOLS)})",
    )
    rescore_p.add_argument(
        "--report",
        action="append",
        type=Path,
        help="rescore this report JSON (repeatable); overrides --tools",
    )
    rescore_p.add_argument(
        "--all-runs",
        action="store_true",
        help="rescore every run in each tool folder, not only the latest",
    )
    rescore_p.add_argument("--judge-provider", dest="judge_provider")
    rescore_p.add_argument("--judge-model", dest="judge_model")
    rescore_p.add_argument(
        "--no-canvas",
        action="store_true",
        help="do not refresh the comparison canvas afterwards",
    )

    canvas_p = sub.add_parser(
        "canvas",
        help="rebuild the comparison canvas from reports/ (latest run per tool)",
    )
    canvas_p.add_argument(
        "--out",
        type=Path,
        help="write the canvas here (default: this workspace's Cursor canvases folder)",
    )

    check_p = sub.add_parser("judge-check", help="verify the LLM judge before scoring")
    check_p.add_argument("--provider", dest="judge_provider")
    check_p.add_argument("--model", dest="judge_model")

    cleanup_p = sub.add_parser(
        "cleanup",
        help="close eval PRs and delete eval branches; reports stay",
    )
    cleanup_p.add_argument("--only", help="comma-separated case ids")
    cleanup_p.add_argument("--tool", help="clean this tool's owner (from cases/owners.json)")
    cleanup_p.add_argument("--owner", help="GitHub owner holding the product repos (default: the tool's entry in cases/owners.json)")

    args = parser.parse_args(argv)
    if args.command == "all":
        try:
            return run_all(
                tools=args.tools,
                only=args.only,
                setup=args.setup,
                trigger=args.trigger,
                wait=args.wait,
                fast=args.fast,
                judge_provider=args.judge_provider,
                judge_model=args.judge_model,
                cleanup=args.cleanup,
                owner=args.owner,
                dry_run=args.dry_run,
            )
        except RunAllError as exc:
            raise SystemExit(f"error: {exc}") from exc
    if args.command == "rescore":
        return _run_rescore(args)
    if args.command == "canvas":
        return _run_canvas(args.out)
    if args.command == "trigger":
        return _run_trigger(args.tool, args.only, args.owner)
    if args.command == "setup":
        return _run_setup(args.only, args.tool, args.owner)
    if args.command == "cleanup":
        return _run_cleanup(args.only, args.tool, args.owner)
    if args.command == "judge-check":
        return _run_judge_check(args.judge_provider, args.judge_model)
    return _run_setup(None, None)
