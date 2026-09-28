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
