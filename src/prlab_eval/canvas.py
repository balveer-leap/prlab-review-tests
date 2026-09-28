"""Build the comparison canvas from whatever is in reports/.

Reports are gitignored, so every clone has its own results. This reads the
latest run per tool, writes the combined data to reports/comparison.json, and
fills viewer/review-tool-comparison.canvas.tsx with it. The filled copy goes to
this workspace's Cursor canvases folder, which Cursor only watches directly.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from prlab_eval.cases import ROOT, load_cases, load_owners
from prlab_eval.report import REPORT_DIR, is_rescored, judge_label, report_run_at, reviewer_dir
from prlab_eval.tools import TOOLS

TEMPLATE = ROOT / "viewer" / "review-tool-comparison.canvas.tsx"
CANVAS_NAME = TEMPLATE.name
DATA_BLOCK = re.compile(r"/\* PRLAB_DATA \*/.*?/\* END_PRLAB_DATA \*/", re.S)
COMMENT_LIMIT = 900
STAMP = re.compile(r"-(\d{8}T\d{6}Z)")


class CanvasError(RuntimeError):
    pass


def workspace_canvas_dir(root: Path = ROOT, home: Path | None = None) -> Path:
    """~/.cursor/projects/<workspace path with separators as dashes>/canvases."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(root.resolve())).strip("-")
    return (home or Path.home()) / ".cursor" / "projects" / slug / "canvases"


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


def comparison_report(tool: str, out_dir: Path | None = None) -> Path | None:
    """The run the comparison shows for ``tool``: its newest default-options run.

    Same ordering as ``runall.latest_report`` (run time, then the rescored copy,
    then mtime) and superseded/ is ignored, but runs made with non-default tool
    options are skipped. Falls back to the newest run of any kind.
    """
    reports = sorted(
        reviewer_dir(tool, out_dir).glob("review-eval-*.json"),
        key=lambda p: (report_run_at(p), is_rescored(p), p.stat().st_mtime),
    )
    comparable = [p for p in reports if default_options(p)]
    pool = comparable or reports
    return pool[-1] if pool else None


def build_payload(out_dir: Path | None = None) -> dict:
    owners = load_owners()
    tools = []
    for tool in TOOLS:
        path = comparison_report(tool, out_dir)
        if path is not None:
            tools.append(tool_run(tool, path, owners))
    if not tools:
        raise CanvasError(f"no reports under {out_dir or REPORT_DIR}; run an eval first")
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
    return {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "tools": tools,
        "cases": cases,
    }


def render(payload: dict, template: str) -> str:
    if not DATA_BLOCK.search(template):
        raise CanvasError(f"{TEMPLATE} has no /* PRLAB_DATA */ block")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    block = f"/* PRLAB_DATA */ {data} /* END_PRLAB_DATA */"
    return DATA_BLOCK.sub(lambda _: block, template, count=1)


def write_canvas(out: Path | None = None, reports: Path | None = None) -> Path:
    """Write reports/comparison.json and the filled canvas. Returns the canvas path."""
    payload = build_payload(reports)
    target_dir = reports or REPORT_DIR
    (target_dir / "comparison.json").write_text(json.dumps(payload, indent=2) + "\n")
    if out is None:
        canvases = workspace_canvas_dir()
        if not canvases.parent.is_dir():
            raise CanvasError(
                f"no Cursor project folder at {canvases.parent}; open this repo in Cursor "
                "once, or pass --out PATH"
            )
        out = canvases / CANVAS_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(payload, TEMPLATE.read_text()))
    return out
