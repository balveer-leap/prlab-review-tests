"""Re-score saved reports without GitHub or the review vendors.

A report JSON keeps every comment the tool posted and every claim verdict, so a
scoring fix can be applied after the fact:

- claims the old quote check rejected are checked again with the current
  normaliser, offline;
- each comment is rated by the LLM judge (shown the diff, not the trap), which
  is what precision now counts.

The source report is left alone. The result is a new report beside it, labelled
``-rescored``, that names its source and keeps the source's ``run_at``.
"""

from __future__ import annotations

import dataclasses
import json
import re
import sys
from pathlib import Path

from prlab_eval.cases import Case, case_diff, load_cases
from prlab_eval.harness import EvalResult
from prlab_eval.judge import (
    QUOTE_NOT_FOUND,
    ClaimVerdict,
    CommentRating,
    LlmJudge,
    precheck_judge,
    quote_is_from_review,
    visible_review_text,
)
from prlab_eval.metrics import NOISE, CaseMetrics, CommentVerdict, classify_comments, score_metrics
from prlab_eval.report import (
    REPORT_DIR,
    RESCORED,
    is_rescored,
    report_run_at,
    reviewer_dir,
    write_reports,
)

STAMP_SUFFIX = re.compile(r"-\d{8}T\d{6}Z(?:-\d+)?$")


def _known(cls, row: dict) -> dict:
    names = {f.name for f in dataclasses.fields(cls)}
    return {key: value for key, value in row.items() if key in names}


def result_from_dict(row: dict) -> EvalResult:
    """Rebuild an EvalResult from report JSON. Tolerates reports from older harnesses."""
    claims = [
        ClaimVerdict(
            **{
                **_known(ClaimVerdict, item),
                "tokens_expected": tuple(item.get("tokens_expected") or ()),
                "tokens_matched": tuple(item.get("tokens_matched") or ()),
                "tokens_missing": tuple(item.get("tokens_missing") or ()),
                "parts": tuple(item.get("parts") or ()),
                "votes": tuple(item.get("votes") or ()),
            }
        )
        for item in row.get("claims") or []
    ]
    fields = _known(EvalResult, row)
    fields.update(
        claims=claims,
        metrics=CaseMetrics(**_known(CaseMetrics, row.get("metrics") or {})),
        comment_verdicts=[CommentVerdict(**_known(CommentVerdict, v)) for v in row.get("comment_verdicts") or []],
        capability_ids=tuple(row.get("capability_ids") or ()),
        unexpected_bots=tuple(row.get("unexpected_bots") or ()),
        comments=list(row.get("comments") or []),
    )
    return EvalResult(**fields)


def recheck_claim(claim: ClaimVerdict, review_text: str) -> ClaimVerdict:
    """Pass a claim the judge asserted but whose quote the old normaliser could not find."""
    if claim.passed or claim.reason != QUOTE_NOT_FOUND or not claim.quote:
        return claim
    if not quote_is_from_review(claim.quote, visible_review_text(review_text)):
        return claim
    return dataclasses.replace(
        claim,
        passed=True,
        reason="asserted; quote verified once markdown formatting is ignored",
    )


def current_claims(
    row: EvalResult, case: Case | None, judge: LlmJudge | None, review_text: str, *, rejudge: bool = False
) -> list[ClaimVerdict]:
    """Stored verdicts, re-judged where the case's claim is new or its wording changed.

    A stored verdict answers the question as it was worded then. When
    cases.json tightens a claim, the old pass or fail no longer means anything.
    """
    if case is None or judge is None:
        return list(row.claims)
    stored = {claim.claim_id: claim for claim in row.claims}
    claims: list[ClaimVerdict] = []
    for claim in case.claims:
        previous = stored.get(claim.id)
        if previous is not None and previous.must_assert == claim.must_assert and not rejudge:
            claims.append(previous)
        else:
            claims.append(judge.judge(claim, review_text))
    return claims


def stored_ratings(row: EvalResult) -> list[CommentRating] | None:
    """The source run's comment ratings, or None if they don't line up with its comments.

    A trap comment was accepted as stating the planted bug, which is a defect in
    the diff, so it keeps counting as relevant if its claim is later re-judged.
    """
    verdicts = row.comment_verdicts
    if not row.comments or len(verdicts) != len(row.comments):
        return None
    # A fast-judge run never rated its comments; "noise" there means unrated.
    if any(v.reason == "not rated" for v in verdicts):
        return None
    return [CommentRating(v.index, v.label != NOISE, v.reason) for v in verdicts]


def rescore_result(
    row: EvalResult,
    case: Case | None,
    rater: LlmJudge | None,
    *,
    reuse_ratings: bool = False,
    rejudge: bool = False,
) -> EvalResult:
    comments = list(row.comments)
    review_text = "\n\n".join(comments) or row.actual
    claims = current_claims(row, case, rater, review_text, rejudge=rejudge)
    claims = [recheck_claim(claim, review_text) for claim in claims]
    ratings = stored_ratings(row) if reuse_ratings else None
    if ratings is None and rater is not None and case is not None:
        ratings = rater.rate_comments(case_diff(case), comments) if comments else []
    verdicts = classify_comments(claims, comments, ratings)
    return dataclasses.replace(
        row,
        claims=claims,
        finding_passed=bool(claims) and all(claim.passed for claim in claims),
        metrics=score_metrics(claims, comments, verdicts),
        comment_verdicts=verdicts,
        judge_mode="llm" if rater is not None else row.judge_mode,
    )


def source_label(path: Path, tool: str) -> str:
    """Run label of a report file: review-eval-<tool>-<label>-<stamp>.json -> <label>."""
    stem = STAMP_SUFFIX.sub("", path.stem)
    prefix = f"review-eval-{tool}-"
    label = stem[len(prefix):] if stem.startswith(prefix) else stem
    return re.sub(rf"(?:-{RESCORED})+$", "", label)


def sources_for(tool: str, all_runs: bool, out_dir: Path | None = None) -> list[Path]:
    """Collected runs for ``tool``, oldest first. Only the newest unless ``all_runs``."""
    runs = sorted(
        (p for p in reviewer_dir(tool, out_dir).glob("review-eval-*.json") if not is_rescored(p)),
        key=lambda p: (report_run_at(p), p.stat().st_mtime),
    )
    return runs if all_runs else runs[-1:]


def rescore_report(
    path: Path,
    rater: LlmJudge | None,
    out_dir: Path | None = None,
    *,
    reuse_ratings: bool = False,
    rejudge: bool = False,
) -> Path:
    payload = json.loads(path.read_text())
    tool = payload.get("tool") or path.parent.name
    cases = {case.id: case for case in load_cases()}
    rows = [result_from_dict(row) for row in payload.get("results") or []]
    rescored = [
        rescore_result(row, cases.get(row.case_id), rater, reuse_ratings=reuse_ratings, rejudge=rejudge)
        for row in rows
    ]
    judge = (
        {"mode": "llm", "provider": rater.provider, "model": rater.model}
        if rater is not None
        else dict(payload.get("judge") or {"mode": "llm"})
    )
    source_judge = payload.get("judge")
    if source_judge:
        judge["claims_judged_by"] = "/".join(
            str(source_judge[key]) for key in ("provider", "model") if source_judge.get(key)
        ) or str(source_judge.get("mode") or "")
    return write_reports(
        rescored,
        tool,
        out_dir=out_dir,
        label=f"{source_label(path, tool)}-{RESCORED}",
        owner=payload.get("owner") or "",
        tool_options=payload.get("tool_options") or {},
        judge=judge,
        rescored_from=path.name,
        run_at=payload.get("run_at") or payload.get("generated_at") or "",
    )


def _summary(payload: dict) -> str:
    total = payload["passed"] + payload["failed"]
    return (
        f"{payload['passed']}/{total} passed  P {payload['precision']:.0%}  "
        f"R {payload['recall']:.0%}  F1 {payload['f1']:.0%}"
    )


def run_rescore(
    *,
    tools: list[str],
    reports: list[Path] | None = None,
    all_runs: bool = False,
    judge_provider: str | None = None,
    judge_model: str | None = None,
    out_dir: Path | None = None,
    reuse_ratings: bool = False,
    rejudge: bool = False,
) -> list[Path]:
    rater = LlmJudge.from_env(provider=judge_provider, model=judge_model)
    print(precheck_judge(rater), file=sys.stderr, flush=True)
    paths = list(reports or [])
    if not paths:
        for tool in tools:
            paths.extend(sources_for(tool, all_runs, out_dir))
    written: list[Path] = []
    for path in paths:
        shown = path.relative_to(REPORT_DIR) if path.is_relative_to(REPORT_DIR) else path
        print(f"[rescore] {shown}", file=sys.stderr, flush=True)
        md_path = rescore_report(path, rater, out_dir, reuse_ratings=reuse_ratings, rejudge=rejudge)
        json_path = md_path.with_suffix(".json")
        before = json.loads(path.read_text())
        after = json.loads(json_path.read_text())
        print(f"  before  {_summary(before)}")
        print(f"  after   {_summary(after)}")
        print(f"  wrote   {json_path}")
        written.append(json_path)
    return written
