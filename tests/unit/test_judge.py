from prlab_eval.cases import Claim
from prlab_eval.judge import (
    TokenJudge,
    parse_judge_payload,
    quote_is_from_review,
    verdict_for,
    visible_review_text,
)


def test_visible_review_text_strips_html() -> None:
    html = '<a href="#"><img alt="P1"></a> **Omission Becomes Confirmation**'
    assert "Omission Becomes Confirmation" in visible_review_text(html)
    assert "<img" not in visible_review_text(html)


def test_quote_must_appear_in_review() -> None:
    review = "Omitting umpire_confirmed counts a wicket."
    assert quote_is_from_review("umpire_confirmed counts a wicket", review)
    assert not quote_is_from_review("fantasy will pay 20 points", review)


def test_quote_ignores_backticks_and_smart_quotes() -> None:
    review = 'When a raw wicket omits `umpire_confirmed`, this returns `"wicket"`.'
    assert quote_is_from_review(
        'When a raw wicket omits umpire_confirmed, this returns "wicket".',
        review,
    )
    assert quote_is_from_review("raw-ball behavior", "tests for the new raw_ball behavior")


def test_quote_ignores_dropped_line_anchors() -> None:
    review = "**cricket-stats**, `stats/ledger.py:21-27` copies `snapshot.wickets`. `fantasy/points.py:24` pays."
    assert quote_is_from_review(
        "cricket-stats, stats/ledger.py copies snapshot.wickets. fantasy/points.py pays.", review
    )


def test_quote_accepts_ellipsis_over_verbatim_fragments() -> None:
    review = (
        "raw_ball is now included in every ScoreSnapshot that scoring returns from "
        "POST /matches/{id}/balls. It carries the full protocol BallEvent, including extras."
    )
    assert quote_is_from_review(
        "raw_ball is now included in every ScoreSnapshot ... It carries the full protocol BallEvent",
        review,
    )
    assert quote_is_from_review(
        "raw_ball is now included in every ScoreSnapshot \u2026 It carries the full protocol BallEvent",
        review,
    )


def test_elided_quote_still_rejects_invented_text() -> None:
    review = "raw_ball is now included in every ScoreSnapshot. It carries the full protocol BallEvent."
    assert not quote_is_from_review(
        "raw_ball is now included in every ScoreSnapshot ... fantasy pays wicket points", review
    )
    assert not quote_is_from_review("raw_ball ... BallEvent", review)


def test_quote_accepts_verbatim_pieces_in_any_order() -> None:
    # Judges often cite the consequence before its cause (seen with Haiku and Sonnet).
    review = (
        "1. **The snapshot contract is broken.**\n"
        " - `ScoreSnapshot`'s docstring (`scoring/snapshot.py:50-53`) forbids extras.type and umpire_confirmed.\n"
        " - `MatchPack` now publishes `extras.type` and `wicket.umpire_confirmed`, both of which are on that list."
    )
    assert quote_is_from_review(
        "MatchPack now publishes extras.type and wicket.umpire_confirmed, both of which are on that list. "
        "... The snapshot contract is broken",
        review,
    )
    assert quote_is_from_review(
        "MatchPack now publishes extras.type and wicket.umpire_confirmed, both of which are on that list. "
        "The snapshot contract is broken.",
        review,
    )
    # One invented sentence among real ones is still too much (under 80% verbatim).
    assert not quote_is_from_review(
        "The snapshot contract is broken. Fantasy pays wicket points for every unconfirmed appeal it sees.",
        review,
    )


def test_visible_review_text_drops_greptile_fix_prompt() -> None:
    html = (
        "**Missing Confirmation Becomes Wicket** When a raw wicket omits `umpire_confirmed`."
        "<details><summary>Prompt To Fix With AI</summary>duplicate prompt</details>"
    )
    visible = visible_review_text(html)
    assert "Missing Confirmation Becomes Wicket" in visible
    assert "duplicate prompt" not in visible


def test_hallucinated_quote_fails_the_claim() -> None:
    claim = Claim(
        id="omitted-confirm-counts-wicket",
        must_assert="Omitted confirmation counts a wicket.",
        tokens=("wicket",),
    )
    verdict = verdict_for(
        claim,
        "This default is convenient for older clients.",
        asserts=True,
        quote="this will count an unconfirmed LBW as a wicket",
        reason="model invented the bug",
    )
    assert not verdict.passed
    assert "unconfirmed LBW" in verdict.quote
    assert "not found" in verdict.reason


def test_empty_review_fails_without_quoting() -> None:
    claim = Claim(id="x", must_assert="A bug.", tokens=("wicket",))
    verdict = verdict_for(claim, "", asserts=True, quote="wicket", reason="n/a")
    assert not verdict.passed
    assert verdict.reason == "no review"


def test_parse_judge_payload_accepts_fenced_json() -> None:
    parsed = parse_judge_payload('```json\n{"asserts": true, "quote": "q", "reason": "r"}\n```')
    assert parsed == {"asserts": True, "quote": "q", "reason": "r"}


def test_parse_judge_payload_extracts_json_from_reasoning() -> None:
    parsed = parse_judge_payload('thinking...\n{"asserts": false, "quote": "", "reason": "no"}\n')
    assert parsed == {"asserts": False, "quote": "", "reason": "no"}


def test_token_judge_passes_when_all_tokens_hit() -> None:
    claim = Claim(
        id="omitted-confirm-counts-wicket",
        must_assert="Omitted confirmation counts a wicket.",
        tokens=("umpire_confirmed", "(omitted|default|missing)", "wicket"),
    )
    review = "If umpire_confirmed is omitted, this default counts a wicket."
    verdict = TokenJudge().judge(claim, review)
    assert verdict.passed
    assert verdict.reason.startswith("fast:")
    assert not verdict.tokens_missing


def test_token_judge_fails_when_a_token_is_missing() -> None:
    claim = Claim(
        id="omitted-confirm-counts-wicket",
        must_assert="Omitted confirmation counts a wicket.",
        tokens=("umpire_confirmed", "wicket"),
    )
    verdict = TokenJudge().judge(claim, "The schema default is convenient.")
    assert not verdict.passed
    assert "missing tokens" in verdict.reason


def test_broadcast_style_quote_passes() -> None:
    claim = Claim(
        id="raw-ball-bypasses-last-event",
        must_assert="Animating from raw_ball can treat a missing confirmation as a wicket.",
        tokens=("raw_ball", "(umpire_confirmed|extras)"),
    )
    review = (
        "src/animator.js:11\n"
        "**Missing Confirmation Becomes Wicket** When a raw wicket omits "
        "`umpire_confirmed`, this condition treats it as confirmed and returns "
        '`"wicket"` before checking `last_event`.'
    )
    verdict = verdict_for(
        claim,
        review,
        asserts=True,
        quote="When a raw wicket omits umpire_confirmed, this condition treats it as confirmed",
        reason="stated",
    )
    assert verdict.passed
    assert "umpire_confirmed" in verdict.quote
    assert "(umpire_confirmed|extras)" in verdict.tokens_matched


def test_retry_wait_reads_provider_hint() -> None:
    from prlab_eval.judge import retry_wait

    assert retry_wait("7", "") == 8
    assert retry_wait(None, "Please try again in 12.5s. Need more tokens?") == 13.5
    assert retry_wait(None, "Please try again in 1m2s.") == 63
    assert retry_wait(None, "no hint") == 20.0


def test_retry_wait_honours_a_tokens_per_day_wait() -> None:
    from prlab_eval.judge import MAX_RETRY_WAIT, retry_wait

    assert retry_wait(None, "tokens per day (TPD) ... Please try again in 12m4.464s.") == 725.464
    assert retry_wait(None, "Please try again in 45m0s.") == MAX_RETRY_WAIT


def test_judge_retries_after_rate_limit(monkeypatch) -> None:
    from prlab_eval import judge as judge_mod

    calls = {"n": 0}

    def flaky(self, payload):
        calls["n"] += 1
        if calls["n"] < 3:
            raise judge_mod.RateLimited("slow down", 0)
        return "ok"

    monkeypatch.setattr(judge_mod.LlmJudge, "_complete_once", flaky)
    monkeypatch.setattr(judge_mod.time, "sleep", lambda _: None)
    llm = judge_mod.LlmJudge(api_key="k", model="m", base_url="u", provider="groq", json_mode=False)
    assert llm._complete({}) == "ok"
    assert calls["n"] == 3


def test_judge_retries_a_timed_out_call(monkeypatch) -> None:
    import io
    import json as _json

    from prlab_eval import judge as judge_mod

    calls: list[int] = []

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def flaky(request, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("The read operation timed out")
        return Reply(_json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode())

    monkeypatch.setattr(judge_mod.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(judge_mod.time, "sleep", lambda s: None)
    judge = judge_mod.LlmJudge(api_key="k", model="m", base_url="https://x.test", provider="bifrost")
    assert judge._complete({"messages": []}) == "ok"
    assert len(calls) == 2


def test_quote_ignores_dropped_straight_quotes() -> None:
    review = 'a NOT_OUT event (wicket_counted=False) sends a push titled "WICKET" with body runs/wickets.'
    assert quote_is_from_review("a NOT_OUT event (wicket_counted=False) sends a push titled WICKET with body runs/wickets.", review)


def test_quote_may_skip_a_sentence_between_two_it_keeps() -> None:
    review = (
        "The raw_ball wicket branch returns wicket whenever umpire_confirmed !== false. "
        "Scoring says the wicket doesn't count, and the animation says it does. "
        "Treating a missing umpire_confirmed as confirmed is risky."
    )
    stitched = (
        "The raw_ball wicket branch returns wicket whenever umpire_confirmed !== false. "
        "Treating a missing umpire_confirmed as confirmed is risky."
    )
    assert quote_is_from_review(stitched, review)


def test_stitched_quote_rejects_invented_sentences_but_not_reordered_ones() -> None:
    review = "Anyone can wipe any live match. The route has no auth. CORS allows every origin."
    assert not quote_is_from_review(
        "Anyone can wipe any live match. The fantasy service pays double points for every wicket ever recorded.", review
    )
    assert quote_is_from_review("CORS allows every origin. Anyone can wipe any live match. The route has no auth.", review)


def test_judge_prompt_rejects_generic_consumers() -> None:
    from prlab_eval.judge import SYSTEM_PROMPT

    assert "any consumer" in SYSTEM_PROMPT and "must name it" in SYSTEM_PROMPT


def _two_part_claim():
    from prlab_eval.cases import Claim

    return Claim(
        id="omitted-confirm",
        must_assert="Omitting umpire_confirmed counts an appeal as a wicket.",
        parts=("A missing umpire_confirmed is treated as confirmed.", "An unconfirmed appeal is counted as a wicket."),
        not_enough=("A schema default that disagrees with the model.",),
    )


REVIEW = "If umpire_confirmed is missing, the validator sets it to True. An LBW appeal then counts as a wicket."


def test_claim_passes_only_when_every_part_is_met_with_its_own_quote() -> None:
    from prlab_eval.judge import parts_verdict

    claim = _two_part_claim()
    both = {"parts": [
        {"part": 1, "met": True, "quote": "If umpire_confirmed is missing, the validator sets it to True."},
        {"part": 2, "met": True, "quote": "An LBW appeal then counts as a wicket."},
    ]}
    verdict = parts_verdict(claim, REVIEW, both)
    assert verdict.passed and len(verdict.parts) == 2
    # One half stated is not the claim: the judge may not fill the gap.
    half = {"parts": [both["parts"][0], {"part": 2, "met": False, "quote": "", "why": "no consequence stated"}]}
    verdict = parts_verdict(claim, REVIEW, half)
    assert not verdict.passed and verdict.reason.startswith("part 2 not met")


def test_a_part_with_an_invented_quote_is_not_met() -> None:
    from prlab_eval.judge import QUOTE_NOT_FOUND, parts_verdict

    parsed = {"parts": [
        {"part": 1, "met": True, "quote": "If umpire_confirmed is missing, the validator sets it to True."},
        {"part": 2, "met": True, "quote": "Fantasy then pays twenty bowling points for every appeal it sees."},
    ]}
    verdict = parts_verdict(_two_part_claim(), REVIEW, parsed)
    assert not verdict.passed
    assert verdict.parts[1]["why"] == QUOTE_NOT_FOUND


def test_a_part_the_judge_skipped_counts_as_not_met() -> None:
    from prlab_eval.judge import parts_verdict

    parsed = {"parts": [{"part": 1, "met": True, "quote": "If umpire_confirmed is missing, the validator sets it to True."}]}
    assert not parts_verdict(_two_part_claim(), REVIEW, parsed).passed


def test_prompt_lists_parts_and_what_is_not_enough() -> None:
    from prlab_eval.judge import claim_prompt

    text = claim_prompt(_two_part_claim(), REVIEW)
    assert "1. A missing umpire_confirmed" in text and "2. An unconfirmed appeal" in text
    assert "Not enough on its own:\n- A schema default" in text


class _Vote:
    """A panel member that returns a fixed verdict and counts its calls."""

    base_url = "https://gateway.test/openai/v1"

    def __init__(self, model: str, passed: bool):
        self.model, self.passed, self.calls = model, passed, 0

    def judge(self, claim, review_text):
        from prlab_eval.judge import ClaimVerdict

        self.calls += 1
        return ClaimVerdict(claim.id, claim.must_assert, self.passed, "", f"{self.model} says {self.passed}", (), (), ())


def test_panel_takes_the_agreed_verdict_without_the_tiebreaker() -> None:
    from prlab_eval.judge import PanelJudge

    tiebreak = _Vote("bedrock/deepseek.v3.2", False)
    panel = PanelJudge([_Vote("a", True), _Vote("b", True)], tiebreak)  # type: ignore[list-item]
    verdict = panel.judge(_two_part_claim(), REVIEW)
    assert verdict.passed and tiebreak.calls == 0 and len(verdict.votes) == 2
    assert verdict.reason.startswith("panel agrees (pass)")


def test_panel_split_is_decided_by_the_tiebreaker() -> None:
    from prlab_eval.judge import PanelJudge

    tiebreak = _Vote("bedrock/deepseek.v3.2", False)
    panel = PanelJudge([_Vote("a", True), _Vote("b", False)], tiebreak)  # type: ignore[list-item]
    verdict = panel.judge(_two_part_claim(), REVIEW)
    assert not verdict.passed and tiebreak.calls == 1
    assert verdict.votes[-1]["tiebreak"] is True and "decides fail" in verdict.reason


def test_panel_label_is_readable_and_shared_by_both_report_kinds() -> None:
    from prlab_eval.judge import PANEL_DEFAULT
    from prlab_eval.report import judge_label

    assert judge_label({"provider": "panel", "model": PANEL_DEFAULT}) == (
        "panel / claude-haiku-4-5 + claude-sonnet-4-5, tiebreak deepseek.v3.2"
    )


def test_malformed_judge_reply_is_retried(monkeypatch) -> None:
    from prlab_eval.judge import LlmJudge

    judge = LlmJudge(api_key="k", model="m", base_url="https://x.test", provider="bifrost")
    replies = iter(['{"parts": [{"part": 1 "met": true}]}', '{"asserts": true, "quote": "counts a wicket", "reason": "ok"}'])
    monkeypatch.setattr(judge, "_ask_json", lambda system, user: next(replies))
    from prlab_eval.cases import Claim

    verdict = judge.judge(Claim(id="c", must_assert="It counts a wicket."), "This counts a wicket.")
    assert verdict.passed
