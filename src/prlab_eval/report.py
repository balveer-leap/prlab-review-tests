from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from prlab_eval.cases import load_capabilities
from prlab_eval.harness import EvalResult
from prlab_eval.judge import ClaimVerdict
from prlab_eval.metrics import (
    CaseMetrics,
    CommentVerdict,
    aggregate_metrics,
    pct,
    review_comments,
    score_metrics,
)

REPORT_DIR = Path(__file__).resolve().parents[2] / "reports"


def _cell(text: str, limit: int = 160) -> str:
    collapsed = " ".join((text or "").split()).replace("|", "\\|")
    if not collapsed:
        return "(none)"
    if len(collapsed) > limit:
        return collapsed[: limit - 1] + "…"
    return collapsed


def isolation_label(row: EvalResult) -> str:
    if row.isolation_passed:
        return "yes"
    extras = ",".join(row.unexpected_bots)
    return f"no ({extras})" if extras else "no"


def _join_tokens(patterns: tuple[str, ...]) -> str:
    return ", ".join(f"`{item}`" for item in patterns) if patterns else "—"


def _claim_token_rows(claim: ClaimVerdict) -> tuple[str, str, str]:
    return (
        _join_tokens(claim.tokens_expected),
        _join_tokens(claim.tokens_matched),
        _join_tokens(claim.tokens_missing),
    )


def result_comments(row: EvalResult) -> list[str]:
    return row.comments or review_comments((), row.actual)


def result_metrics(row: EvalResult) -> CaseMetrics:
    if row.metrics.comments or row.metrics.true_positives or row.metrics.false_negatives:
        return row.metrics
    return score_metrics(row.claims, result_comments(row))


def _result_capability_ids(row: EvalResult) -> tuple[str, ...]:
    if row.capability_ids:
        return row.capability_ids
    if row.capability_id:
        return (row.capability_id,)
    return ("unspecified",)


def capability_groups(results: list[EvalResult]) -> list[tuple[str, str, list[EvalResult]]]:
    catalog = {}
    try:
        catalog = load_capabilities()
    except Exception:
        catalog = {}
    groups: dict[str, list[EvalResult]] = {}
    names: dict[str, str] = {}
    for row in results:
        for key in _result_capability_ids(row):
            groups.setdefault(key, []).append(row)
            spec = catalog.get(key)
            names.setdefault(key, spec.name if spec else row.capability or key)
    return [(key, names[key], groups[key]) for key in groups]


def judge_label(judge: dict[str, str]) -> str:
    if judge.get("mode") == "fast":
        return "token (fast)"
    provider = judge.get("provider") or "llm"
    return f"{provider} / {judge['model']}" if judge.get("model") else provider


def _comment_verdict(row: EvalResult, index: int) -> CommentVerdict | None:
    return next((v for v in row.comment_verdicts if v.index == index), None)


def comment_label(row: EvalResult, index: int) -> str:
    verdict = _comment_verdict(row, index)
    return verdict.label if verdict else "unrated"


def _judge_verdict(claim: ClaimVerdict) -> str:
    status = "PASS" if claim.passed else "FAIL"
    reason = claim.reason or ("asserted" if claim.passed else "not asserted")
    return f"{status} — {reason}"


def reviewer_dir(tool: str, out_dir: Path | None = None) -> Path:
    return (out_dir or REPORT_DIR) / tool


def report_stem(tool: str, label: str = "", stamp: str = "") -> str:
    """review-eval-<tool>[-<label>]-<stamp>. Label names the run (owner, judge)."""
    parts = ["review-eval", tool, *([label] if label else []), stamp]
    return "-".join(part for part in parts if part)


RESCORED = "rescored"


def is_rescored(path: Path) -> bool:
    return f"-{RESCORED}-" in path.name


def report_run_at(path: Path) -> str:
    """When the report's reviews were collected (a rescore keeps its source's time)."""
    payload = json.loads(path.read_text())
    return payload.get("run_at") or payload.get("generated_at") or ""


def unused_stem(directory: Path, stem: str) -> str:
    """Never overwrite a finished report: add -2, -3 ... if the name is taken."""
    candidate, n = stem, 1
    while any((directory / f"{candidate}{ext}").exists() for ext in (".md", ".json", ".html")):
        n += 1
        candidate = f"{stem}-{n}"
    return candidate


def write_reports(
    results: list[EvalResult],
    tool: str,
    out_dir: Path | None = None,
    label: str = "",
    stamp: str | None = None,
    owner: str = "",
    tool_options: dict[str, str] | None = None,
    judge: dict[str, str] | None = None,
    rescored_from: str = "",
    run_at: str = "",
) -> Path:
    # One folder per reviewer; every run gets its own files and nothing is overwritten.
    directory = reviewer_dir(tool, out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    overall = aggregate_metrics([result_metrics(row) for row in results])
    judge = dict(judge or {})
    payload = {
        "tool": tool,
        "owner": owner,
        "tool_options": dict(tool_options or {}),
        "judge": judge,
        **({"rescored_from": rescored_from} if rescored_from else {}),
        # When the reviews were collected; a rescore keeps its source's run_at.
        "run_at": run_at or stamp,
        "generated_at": stamp,
        "passed": sum(1 for row in results if row.finding_passed and row.isolation_passed),
        "failed": sum(1 for row in results if not (row.finding_passed and row.isolation_passed)),
        "precision": overall.precision,
        "recall": overall.recall,
        "f1": overall.f1,
        "true_positives": overall.true_positives,
        "false_negatives": overall.false_negatives,
        "false_positives": overall.false_positives,
        "by_capability": [
            {
                "id": key,
                "name": name,
                "cases": [row.case_id for row in rows],
                **asdict(aggregate_metrics([result_metrics(row) for row in rows])),
            }
            for key, name, rows in capability_groups(results)
        ],
        "results": [asdict(row) for row in results],
    }
    stem = unused_stem(directory, report_stem(tool, label, stamp))
    json_path = directory / f"{stem}.json"
    md_path = directory / f"{stem}.md"
    json_path.write_text(json.dumps(payload, indent=2) + "\n")

    lines = [
        f"# Review eval — {tool}" + (f" @ {owner}" if owner else ""),
        "",
        (
            f"Generated {stamp}. {payload['passed']} passed, {payload['failed']} failed. "
            f"Precision {pct(overall.precision)}, recall {pct(overall.recall)}, "
            f"F1 {pct(overall.f1)}."
        ),
        "",
        f"Judge: {judge_label(judge)}." + (f" Rescored from `{rescored_from}`." if rescored_from else ""),
        "",
        "Expected finding = the trap the review must state. Actual PR comment = what the tool wrote. "
        "Judge verdict = whether that comment asserts the expected finding (not the comment itself).",
        "Recall = expected findings asserted / expected findings.",
        (
            "Precision = relevant PR comments / all PR comments. Each comment is labelled "
            "[trap] (holds the verified quote for the planted finding), [defect] (the judge, "
            "shown the diff but not the trap, rated it a genuine defect) or [noise]."
            if judge.get("mode") != "fast"
            else "Precision = PR comments holding the verified quote for the planted finding / all PR "
            "comments. The fast judge cannot rate other comments, so any second real finding counts as noise."
        ),
        "Capability is the review-tool skill the case is measuring.",
        "Isolated is yes if only the selected review bot commented.",
        "",
        "| Case | Isolated | P | R | F1 | Capability | Intent | Expected finding | Actual PR comment | Judge verdict | PR |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in results:
        metrics = result_metrics(row)
        comments = result_comments(row)
        expected = "<br>".join(claim.must_assert for claim in row.claims) or "(none)"
        actual = "<br>".join(
            f"{idx}. [{comment_label(row, idx)}] {item}" for idx, item in enumerate(comments, start=1)
        ) or "(none)"
        verdicts = "<br>".join(
            f"{'✓' if claim.passed else '✗'} {_judge_verdict(claim)}"
            for claim in row.claims
        ) or "(none)"
        lines.append(
            f"| {row.case_id} | {isolation_label(row)} | "
            f"{pct(metrics.precision)} | {pct(metrics.recall)} | {pct(metrics.f1)} | "
            f"{_cell(row.capability or '—', 80)} | {_cell(row.intent or row.case_id, 120)} | "
            f"{_cell(expected, 140)} | "
            f"{_cell(actual, 140)} | {_cell(verdicts, 120)} | {row.pr_url} |"
        )

    lines.extend(["", "## By capability", "", "| Capability | Cases | P | R | F1 | What we ask of the tool |", "|---|---|---|---|---|---|"])
    for key, name, rows in capability_groups(results):
        grouped = aggregate_metrics([result_metrics(row) for row in rows])
        asks = next((row.capability_asks for row in rows if row.capability_asks), "")
        lines.append(
            f"| {name} | {len(rows)} | {pct(grouped.precision)} | {pct(grouped.recall)} | "
            f"{pct(grouped.f1)} | {_cell(asks, 200)} |"
        )
        _ = key

    lines.extend(["", "## Case details", ""])
    for row in results:
        metrics = result_metrics(row)
        comments = result_comments(row)
        status = "PASS" if row.finding_passed else "FAIL"
        lines.append(f"### {row.case_id} — {status}")
        lines.append("")
        lines.append(f"PR: {row.pr_url}")
        if row.intent:
            lines.append(f"Intent: {row.intent}")
        if row.capability:
            lines.append(f"Capability: {row.capability}")
        if getattr(row, "judge_mode", "llm") == "fast":
            lines.append("Judge: token (fast)")
        if row.capability_asks:
            lines.append("")
            lines.append("Review-tool skill this case asks:")
            lines.append("")
            lines.append(row.capability_asks)
            lines.append("")
        if row.tests:
            lines.append("")
            lines.append("What this tests:")
            lines.append("")
            lines.append(row.tests)
            lines.append("")
        lines.append(f"Isolated: {isolation_label(row)}")
        lines.append(
            f"Precision: {pct(metrics.precision)} ({metrics.relevant_comments}/{metrics.comments} comments relevant). "
            f"Recall: {pct(metrics.recall)} ({metrics.true_positives}/{metrics.true_positives + metrics.false_negatives} expected findings). "
            f"F1: {pct(metrics.f1)}."
        )
        lines.append("")
        lines.append("Actual PR comment(s):")
        lines.append("")
        if comments:
            for idx, comment in enumerate(comments, start=1):
                verdict = _comment_verdict(row, idx)
                reason = f" — {verdict.reason}" if verdict and verdict.reason else ""
                lines.append(f"{idx}. [{comment_label(row, idx)}]{reason}")
                lines.append("")
                lines.append("```")
                lines.append(comment)
                lines.append("```")
                lines.append("")
        else:
            lines.append("```")
            lines.append("(no review)")
            lines.append("```")
            lines.append("")
        for claim in row.claims:
            lines.append(f"#### Claim `{claim.claim_id}` — {'PASS' if claim.passed else 'FAIL'}")
            lines.append("")
            lines.append("Expected finding (what a correct review must mean):")
            lines.append("")
            lines.append(f"> {claim.must_assert}")
            lines.append("")
            lines.append(f"Judge verdict: {_judge_verdict(claim)}")
            lines.append("")
            lines.append("Judge evidence (substring the judge copied, not the full PR comment):")
            lines.append("")
            lines.append("```")
            lines.append(claim.quote or "(none)")
            lines.append("```")
            lines.append("")
            lines.append("Tokens (keyword diagnostic, not pass/fail):")
            lines.append("")
            lines.append("| | Patterns |")
            lines.append("|---|---|")
            lines.append(f"| Expected | {_join_tokens(claim.tokens_expected)} |")
            lines.append(f"| Hit | {_join_tokens(claim.tokens_matched)} |")
            lines.append(f"| Missed | {_join_tokens(claim.tokens_missing)} |")
            lines.append("")

    markdown = "\n".join(lines) + "\n"
    md_path.write_text(markdown)
    return md_path


def terminal_summary(results: list[EvalResult]) -> str:
    if not results:
        return ""
    overall = aggregate_metrics([result_metrics(row) for row in results])
    lines = [
        "",
        "eval cases",
        f"{'CASE':<68} {'FINDING':<8} {'ISOLATED':<10} {'P':<6} {'R':<6} {'F1':<6}",
    ]
    for row in results:
        metrics = result_metrics(row)
        finding = "PASS" if row.finding_passed else "FAIL"
        lines.append(
            f"{row.case_id:<68} {finding:<8} {isolation_label(row):<10} "
            f"{pct(metrics.precision):<6} {pct(metrics.recall):<6} {pct(metrics.f1):<6}"
        )
    passed = sum(1 for row in results if row.finding_passed)
    lines.append(
        f"{passed}/{len(results)} cases passed  "
        f"P={pct(overall.precision)} R={pct(overall.recall)} F1={pct(overall.f1)}"
    )
    return "\n".join(lines)
