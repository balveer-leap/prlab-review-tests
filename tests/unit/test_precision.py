import json
from dataclasses import asdict
from pathlib import Path

import pytest

from prlab_eval.canvas import CanvasError, build_payload, render, workspace_canvas_dir
from prlab_eval.cases import Claim, load_cases
from prlab_eval.harness import EvalResult, ReviewHarness
from prlab_eval.judge import (
    QUOTE_NOT_FOUND,
    CallableJudge,
    ClaimVerdict,
    CommentRating,
    LlmJudge,
    TokenJudge,
    parse_comment_ratings,
    quote_is_from_review,
    verdict_for,
)
from prlab_eval.metrics import DEFECT, NOISE, TRAP, classify_comments, score_metrics
from prlab_eval.report import write_reports
from prlab_eval.rescore import recheck_claim, rescore_report, result_from_dict, source_label
from prlab_eval.runall import latest_report
from prlab_eval.scoring import Review
from prlab_eval.tools.greptile import GreptileTool

# Greptile on test-protocol-omitted-confirm-counts-wicket-single-repo: comment 1 is
# the planted trap, comment 2 a different real bug in the same validator.
TRAP_COMMENT = (
    "cricket_protocol/models.py:50\n **Unconfirmed appeal counts as wicket** If a producer "
    "sends an unconfirmed appeal as `kind=\"lbw\"` but omits `umpire_confirmed`, this validator "
    "sets the flag to `True`. The [scoring engine](https://github.com/x/y/blob/HEAD/scoring/engine.py) "
    "uses that flag to count the dismissal."
)
SECOND_BUG = (
    "cricket_protocol/models.py:55\n **Contradictory events pass validation** When a producer "
    "explicitly sends `{\"kind\": \"none\", \"umpire_confirmed\": true}`, this validator silently "
    "changes the flag to `False` instead of rejecting the payload."
)
NITPICK = "Consider renaming `_default_confirm` to something more descriptive."


def _claim() -> Claim:
    return Claim(id="omitted-confirm", must_assert="Omitting umpire_confirmed counts a wicket.", tokens=())


def _passed(quote: str) -> ClaimVerdict:
    review = "\n\n".join([TRAP_COMMENT, SECOND_BUG])
    return verdict_for(_claim(), review, asserts=True, quote=quote, reason="asserted")


# --- quote normaliser -------------------------------------------------------


def test_quote_ignores_markdown_bold() -> None:
    review = 'The change makes `notify()` send a push titled **"WICKET"** for events where the batter was **not** given out.'
    quote = 'The change makes notify() send a push titled "WICKET" for events where the batter was not given out.'
    assert quote_is_from_review(quote, review)


def test_quote_ignores_markdown_link_targets() -> None:
    quote = "The scoring engine uses that flag to count the dismissal"
    assert quote_is_from_review(quote, TRAP_COMMENT)


def test_quote_still_rejects_invented_text() -> None:
    assert not quote_is_from_review("The scoring engine deletes the match", TRAP_COMMENT)


# --- comment ratings --------------------------------------------------------


def test_parse_comment_ratings_fills_skipped_comments_as_not_defects() -> None:
    raw = json.dumps({"comments": [{"index": 2, "defect": True, "reason": "real"}, {"index": 9, "defect": True}]})
    ratings = parse_comment_ratings(raw, 3)
    assert [r.defect for r in ratings] == [False, True, False]
    assert ratings[0].reason == "judge did not rate this comment"


def test_llm_rate_comments_sends_diff_not_claim(monkeypatch) -> None:
    judge = LlmJudge(api_key="k", model="m", base_url="https://example.test", provider="groq")
    sent: dict = {}

    def fake_complete(payload: dict) -> str:
        sent.update(payload)
        return json.dumps({"comments": [{"index": 1, "defect": True, "reason": "real bug"}]})

    monkeypatch.setattr(judge, "_complete", fake_complete)
    ratings = judge.rate_comments("diff --git a/x b/x", [SECOND_BUG])
    user = sent["messages"][1]["content"]
    assert "diff --git a/x b/x" in user
    assert "Comment 1:" in user
    assert "Omitting umpire_confirmed" not in user
    assert ratings == [CommentRating(index=1, defect=True, reason="real bug")]


def test_token_judge_cannot_rate_comments() -> None:
    assert not hasattr(TokenJudge(), "rate_comments")


# --- precision --------------------------------------------------------------


def test_second_real_bug_counts_toward_precision() -> None:
    claims = [_passed("this validator sets the flag to True")]
    ratings = [CommentRating(1, True, "trap"), CommentRating(2, True, "rewrites a contradiction")]
    verdicts = classify_comments(claims, [TRAP_COMMENT, SECOND_BUG], ratings)
    assert [v.label for v in verdicts] == [TRAP, DEFECT]
    metrics = score_metrics(claims, [TRAP_COMMENT, SECOND_BUG], verdicts)
    assert metrics.precision == 1
    assert metrics.defect_comments == 1
    assert metrics.false_positives == 0


def test_nitpick_is_noise_even_when_rated() -> None:
    claims = [_passed("this validator sets the flag to True")]
    ratings = [CommentRating(1, True, ""), CommentRating(2, False, "naming only")]
    metrics = score_metrics(claims, [TRAP_COMMENT, NITPICK], classify_comments(claims, [TRAP_COMMENT, NITPICK], ratings))
    assert metrics.precision == 0.5
    assert metrics.false_positives == 1


def test_without_ratings_only_the_trap_comment_counts() -> None:
    claims = [_passed("this validator sets the flag to True")]
    verdicts = classify_comments(claims, [TRAP_COMMENT, SECOND_BUG])
    assert [v.label for v in verdicts] == [TRAP, NOISE]
    assert score_metrics(claims, [TRAP_COMMENT, SECOND_BUG]).precision == 0.5


def test_missed_trap_can_still_have_relevant_comments() -> None:
    missed = verdict_for(_claim(), SECOND_BUG, asserts=False, quote="", reason="different problem")
    verdicts = classify_comments([missed], [SECOND_BUG], [CommentRating(1, True, "real")])
    metrics = score_metrics([missed], [SECOND_BUG], verdicts)
    assert metrics.recall == 0
    assert metrics.precision == 1


def test_harness_rates_comments_with_the_case_diff() -> None:
    case = next(item for item in load_cases() if item.id == "test-stats-not-out-display-increments-wickets")
    seen: list[str] = []

    def rate(diff: str, text: str) -> bool:
        seen.append(diff)
        return "README" not in text

    harness = ReviewHarness(
        tool=GreptileTool(),
        judge=CallableJudge(lambda claim, text: (True, "NOT_OUT should not increment", "ok"), rate=rate),
    )
    review = Review(
        text="NOT_OUT should not increment the wicket total.\n\nUpdate the README.",
        comments=("NOT_OUT should not increment the wicket total.", "Update the README."),
        logins={"greptile-apps[bot]"},
        pr_url="https://example.test/pr/1",
    )
    result = harness.score(review, case)
    assert [v.label for v in result.comment_verdicts] == [TRAP, NOISE]
    assert seen and seen[0].startswith("diff --git")


# --- rescore ----------------------------------------------------------------


def _result(claim: ClaimVerdict, comments: list[str]) -> EvalResult:
    return EvalResult(
        case_id="test-protocol-omitted-confirm-counts-wicket-single-repo",
        tool="greptile",
        pr_url="https://example.test/pr/1",
        finding_passed=claim.passed,
        isolation_passed=True,
        claims=[claim],
        actual="\n\n".join(comments),
        comments=comments,
        metrics=score_metrics([claim], comments),
    )


def test_recheck_passes_a_claim_rejected_for_markdown() -> None:
    rejected = ClaimVerdict(
        claim_id="c", must_assert="m", passed=False,
        quote="The scoring engine uses that flag to count the dismissal",
        reason=QUOTE_NOT_FOUND, tokens_expected=(), tokens_matched=(), tokens_missing=(),
    )
    assert recheck_claim(rejected, TRAP_COMMENT).passed


def test_recheck_leaves_real_misses_alone() -> None:
    missed = ClaimVerdict(
        claim_id="c", must_assert="m", passed=False, quote="",
        reason="different problem", tokens_expected=(), tokens_matched=(), tokens_missing=(),
    )
    assert recheck_claim(missed, TRAP_COMMENT) is missed


def test_result_from_dict_round_trips() -> None:
    row = _result(_passed("this validator sets the flag to True"), [TRAP_COMMENT])
    assert result_from_dict(json.loads(json.dumps(asdict(row)))) == row


def test_source_label_strips_stamp_and_rescored() -> None:
    tool = "coderabbit"
    assert source_label(Path("review-eval-coderabbit-org-c-groq-linked-20260925T055015Z.json"), tool) == "org-c-groq-linked"
    assert source_label(Path("review-eval-coderabbit-org-c-groq-rescored-20260928T010101Z-2.json"), tool) == "org-c-groq"


def test_rescore_writes_a_new_report_and_becomes_latest(tmp_path: Path) -> None:
    claim = ClaimVerdict(
        claim_id="omitted-confirm-counts-wicket", must_assert="m", passed=False,
        quote="The scoring engine uses that flag to count the dismissal",
        reason=QUOTE_NOT_FOUND, tokens_expected=(), tokens_matched=(), tokens_missing=(),
    )
    source_md = write_reports(
        [_result(claim, [TRAP_COMMENT, SECOND_BUG])], "greptile", out_dir=tmp_path,
        label="srajat-leap-llm", stamp="20260924T103052Z", owner="srajat-leap",
    )
    source = source_md.with_suffix(".json")

    rater = CallableJudge(lambda *_: (False, "", ""), rate=lambda diff, text: True)
    rater.provider, rater.model = "groq", "openai/gpt-oss-120b"
    out = rescore_report(source, rater, out_dir=tmp_path).with_suffix(".json")  # type: ignore[arg-type]

    payload = json.loads(out.read_text())
    assert out.name.startswith("review-eval-greptile-srajat-leap-llm-rescored-")
    assert payload["rescored_from"] == source.name
    assert payload["run_at"] == "20260924T103052Z"
    assert payload["judge"]["provider"] == "groq"
    assert payload["passed"] == 1
    assert payload["precision"] == 1
    assert json.loads(source.read_text())["passed"] == 0
    assert latest_report("greptile", tmp_path) == out


# --- canvas -----------------------------------------------------------------


def test_workspace_canvas_dir_matches_cursor_slug(tmp_path: Path) -> None:
    root = Path("/home/me/Desktop/prlab/prlab-review-tests")
    assert workspace_canvas_dir(root, home=tmp_path) == (
        tmp_path / ".cursor/projects/home-me-Desktop-prlab-prlab-review-tests/canvases"
    )


def test_render_replaces_only_the_data_block() -> None:
    template = "const A = 1;\nconst DATA = /* PRLAB_DATA */ {} /* END_PRLAB_DATA */;\nconst B = 2;"
    out = render({"tools": [1]}, template)
    assert out == 'const A = 1;\nconst DATA = /* PRLAB_DATA */ {"tools":[1]} /* END_PRLAB_DATA */;\nconst B = 2;'


def test_render_refuses_a_template_without_markers() -> None:
    with pytest.raises(CanvasError):
        render({}, "const DATA = {};")


def test_build_payload_reads_latest_run_and_judge(tmp_path: Path) -> None:
    write_reports(
        [_result(_passed("this validator sets the flag to True"), [TRAP_COMMENT])],
        "qodo", out_dir=tmp_path, label="org-qodo1-groq", stamp="20260924T104725Z",
        owner="org-qodo1", judge={"mode": "llm", "provider": "groq", "model": "openai/gpt-oss-120b"},
    )
    payload = build_payload(tmp_path)
    [tool] = payload["tools"]
    assert tool["tool"] == "qodo"
    assert tool["judge"] == "groq / openai/gpt-oss-120b"
    assert tool["stamp"] == "20260924T104725Z"
    row = tool["rows"]["test-protocol-omitted-confirm-counts-wicket-single-repo"]
    assert row["cm"][0]["label"] == "unrated"
    assert {case["id"] for case in payload["cases"]} == {case.id for case in load_cases()}


def test_build_payload_needs_reports(tmp_path: Path) -> None:
    with pytest.raises(CanvasError):
        build_payload(tmp_path)
