from __future__ import annotations

import pytest

from prlab_eval.cases import Case
from prlab_eval.harness import ReviewHarness, failed_claims


@pytest.mark.eval
def test_review_tool_flags_regression(harness: ReviewHarness, case: Case, record_eval) -> None:
    pr = None
    try:
        # setup — open or reuse the eval PR
        pr = harness.setup(case)

        # execute — optional trigger + collect that tool's comments
        review = harness.execute(pr)

        # score — judge each claim and rate each comment
        result = record_eval(harness.score(review, case))
    except Exception as error:
        # Still counts, as a failure: a case that raised must not vanish from the report.
        record_eval(harness.errored(case, error, pr.url if pr else ""))
        raise

    # assert — every claim is asserted, and no unexpected review bots
    assert review.text, f"{harness.tool.name} left no review on {pr.url}"
    missed = failed_claims(result)
    assert not missed, (
        f"{harness.tool.name} missed {[item.claim_id + ': ' + item.reason for item in missed]} on {pr.url}"
    )
    assert result.isolation_passed, (
        f"unexpected bots {result.unexpected_bots} on {pr.url}"
    )

    # cleanup — optional; enabled with --cleanup on the session fixture
