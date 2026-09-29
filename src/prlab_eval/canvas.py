"""Build the tool comparison from whatever is in reports/.

Reports are gitignored, so every clone has its own results. This reads the
latest run per tool and writes the combined data to reports/comparison.json.
``prlab_eval.live`` serves the same payload to viewer/index.html.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from prlab_eval.cases import load_cases, load_owners
from prlab_eval.report import REPORT_DIR, errored_cases, is_rescored, judge_label, report_run_at, reviewer_dir
from prlab_eval.tools import TOOLS

COMMENT_LIMIT = 900
STAMP = re.compile(r"-(\d{8}T\d{6}Z)")


class CanvasError(RuntimeError):
    pass


def _clip(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= COMMENT_LIMIT else text[: COMMENT_LIMIT - 1] + "…"


def _judge(payload: dict, path: Path, owner: str) -> str:
    if payload.get("judge"):
        return judge_label(payload["judge"])
    # Reports from before the judge was recorded only carry it in the file name.
    label = STAMP.split(path.stem)[0].removeprefix(f"review-eval-{payload.get('tool', '')}-")
    return label.removeprefix(f"{owner}-").split("-")[0] or "llm"


def _basis(payload: dict) -> str:
    """rated: every comment was judged on its own; trap-quote: only the trap comment counts."""
    judge = payload.get("judge") or {}
    if judge.get("mode") == "fast":
        return "trap-quote"
    results = payload.get("results") or []
    rated = any(row.get("comment_verdicts") for row in results)
    return "rated" if rated else "trap-quote"


def _row(result: dict) -> dict:
    metrics = result.get("metrics") or {}
    verdicts = {v["index"]: v for v in result.get("comment_verdicts") or []}
    comments = result.get("comments") or []
    return {
        "pr": result.get("pr_url") or "",
        "ok": bool(result.get("finding_passed")) and bool(result.get("isolation_passed")),
        "iso": bool(result.get("isolation_passed")),
        "p": metrics.get("precision", 0.0),
        "r": metrics.get("recall", 0.0),
        "f1": metrics.get("f1", 0.0),
        "nc": metrics.get("comments", len(comments)),
        "rel": metrics.get("relevant_comments", 0),
        "def": metrics.get("defect_comments", 0),
        "bots": list(result.get("unexpected_bots") or []),
        "claims": [
            {
                "id": claim.get("claim_id", ""),
                "ok": bool(claim.get("passed")),
                "why": claim.get("reason") or "",
                "q": claim.get("quote") or "",
            }
            for claim in result.get("claims") or []
        ],
        "cm": [
            {
                "text": _clip(text),
                "label": (verdicts.get(i) or {}).get("label", "unrated"),
                "why": (verdicts.get(i) or {}).get("reason", ""),
            }
            for i, text in enumerate(comments, start=1)
        ],
    }


def tool_run(tool: str, path: Path, owners: dict[str, str]) -> dict:
    payload = json.loads(path.read_text())
    owner = payload.get("owner") or owners.get(tool, "")
    return {
        "tool": tool,
        "owner": owner,
        "judge": _judge(payload, path, owner),
        "basis": _basis(payload),
        "stamp": payload.get("run_at") or payload.get("generated_at") or "",
        "report": path.name,
        "rescored": bool(payload.get("rescored_from")),
        "passed": payload.get("passed", 0),
        "failed": payload.get("failed", 0),
        "p": payload.get("precision", 0.0),
        "r": payload.get("recall", 0.0),
        "f1": payload.get("f1", 0.0),
        "tp": payload.get("true_positives", 0),
        "fn": payload.get("false_negatives", 0),
        "fp": payload.get("false_positives", 0),
        "caps": {
            cap["id"]: {"p": cap.get("precision", 0.0), "r": cap.get("recall", 0.0), "f1": cap.get("f1", 0.0)}
            for cap in payload.get("by_capability") or []
        },
        "rows": {row["case_id"]: _row(row) for row in payload.get("results") or []},
    }


def default_options(path: Path) -> bool:
    """True when the run used the tool's default collection options.

    A ``--tool-option`` run (e.g. CodeRabbit ``linked_findings=true``) counts
    comments differently from every other tool, so it is not like-for-like.
    """
    try:
        return not (json.loads(path.read_text()).get("tool_options") or {})
    except (OSError, ValueError):
        return False


def _all_errored(path: Path) -> bool:
    errored, total = errored_cases(path)
    return bool(total) and errored == total


def _recorded_judge(path: Path) -> bool:
    try:
        return bool(json.loads(path.read_text()).get("judge"))
    except (OSError, ValueError):
        return False


def report_judge(path: Path, owners: dict[str, str] | None = None) -> str:
    """Judge label of one report, e.g. "groq / openai/gpt-oss-120b"."""
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return ""
    owner = payload.get("owner") or (owners or {}).get(payload.get("tool", ""), "")
    return _judge(payload, path, owner)


def comparison_report(tool: str, out_dir: Path | None = None, judge: str | None = None) -> Path | None:
    """The run the comparison shows for ``tool``: its newest default-options run.

    Same ordering as ``runall.latest_report`` (run time, then the rescored copy,
    then mtime) and superseded/ is ignored, but runs made with non-default tool
    options are skipped. Falls back to the newest run of any kind. With
    ``judge``, only reports scored by that judge are considered, so a rescore
    with a new judge never hides the old judge's result.
    """
    reports = sorted(
        reviewer_dir(tool, out_dir).glob("review-eval-*.json"),
        key=lambda p: (report_run_at(p), is_rescored(p), p.stat().st_mtime),
    )
    # A run whose every case errored (reviewer blocked, judge down) measured nothing.
    reports = [p for p in reports if not _all_errored(p)]
    if judge == UNRECORDED:
        reports = [p for p in reports if not _recorded_judge(p)]
    elif judge is not None:
        reports = [p for p in reports if _recorded_judge(p) and report_judge(p) == judge]
    comparable = [p for p in reports if default_options(p)]
    pool = comparable or reports
    return pool[-1] if pool else None


UNRECORDED = "judge not recorded"


def judges_by_recency(out_dir: Path | None = None) -> list[str]:
    """Every judge that scored a report, the one behind the newest report first.

    Reports that do not record their judge are grouped as UNRECORDED, shown
    only when no report records one.
    """
    newest: dict[str, tuple] = {}
    unrecorded: tuple | None = None
    for tool in TOOLS:
        for path in reviewer_dir(tool, out_dir).glob("review-eval-*.json"):
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            recorded = data.get("judge")
            # generated_at before mtime: a fresh clone gives every file the same
            # checkout mtime, and two judges' rescores share their source's run_at.
            key = (report_run_at(path), str(data.get("generated_at") or ""), path.stat().st_mtime)
            # Early reports only hint at a judge in their file name; their
            # rescored copies record it, and those are what the page shows.
            if not recorded:
                unrecorded = max(unrecorded or key, key)
                continue
            label = judge_label(recorded)
            newest[label] = max(newest.get(label, key), key)
    if not newest and unrecorded is not None:
        return [UNRECORDED]
    return sorted(newest, key=lambda label: newest[label], reverse=True)


PRICING_PATH = Path(__file__).resolve().parents[2] / "cases" / "pricing.json"


def load_pricing(path: Path | None = None) -> dict:
    """Plan prices and measured per-review costs (cases/pricing.json); empty if absent."""
    try:
        return json.loads((path or PRICING_PATH).read_text())
    except (OSError, ValueError):
        return {"tools": {}}


def build_payload(out_dir: Path | None = None) -> dict:
    """Latest run per tool, once per judge.

    ``tools`` is the newest judge's view; ``byJudge`` holds every judge's, so
    the page can switch judge without either result overwriting the other.
    """
    owners = load_owners()
    by_judge: dict[str, list[dict]] = {}
    for label in judges_by_recency(out_dir):
        runs = []
        for tool in TOOLS:
            path = comparison_report(tool, out_dir, judge=label)
            if path is not None:
                runs.append(tool_run(tool, path, owners))
        if runs:
            by_judge[label] = runs
    if not by_judge:
        raise CanvasError(f"no reports under {out_dir or REPORT_DIR}; run an eval first")
    judge = next(iter(by_judge))
    tools = by_judge[judge]
    cases = [
        {
            "id": case.id,
            "intent": case.intent,
            "tests": case.tests,
            "cap": case.capability.name,
            "capid": case.capability.id,
            "asks": case.capability.asks,
            "claims": [{"id": claim.id, "must": claim.must_assert} for claim in case.claims],
        }
        for case in load_cases()
    ]
    pricing = load_pricing()
    for runs in by_judge.values():
        for run in runs:
            run["cost"] = pricing["tools"].get(run["tool"])
    return {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "pricing": {key: value for key, value in pricing.items() if key != "tools"},
        "tools": tools,
        "judge": judge,
        "judges": list(by_judge),
        "byJudge": by_judge,
        "cases": cases,
    }


def write_comparison(out: Path | None = None, reports: Path | None = None) -> Path:
    """Write the combined latest-run-per-tool payload. Returns the file path."""
    payload = build_payload(reports)
    target = out or (reports or REPORT_DIR) / "comparison.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n")
    return target
