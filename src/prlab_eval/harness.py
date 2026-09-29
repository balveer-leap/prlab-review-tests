from __future__ import annotations

import time
from dataclasses import dataclass, field

from prlab_eval.cases import Case, case_diff
from prlab_eval.judge import ClaimJudge, ClaimVerdict, CommentRating, visible_review_text
from prlab_eval.metrics import (
    CaseMetrics,
    CommentVerdict,
    classify_comments,
    review_comments,
    score_metrics,
)
from prlab_eval.prs import ensure_pr
from prlab_eval.scoring import Review, check_isolation
from prlab_eval.tools.base import PullRequest, ReviewTool
from prlab_eval.trigger import trigger_pr


class ReviewerBlocked(RuntimeError):
    """The reviewer's account posted a notice (trial ended, paused) instead of reviewing."""


@dataclass
class EvalResult:
    case_id: str
    tool: str
    pr_url: str
    finding_passed: bool
    isolation_passed: bool
    intent: str = ""
    tests: str = ""
    capability_id: str = ""
    capability: str = ""
    capability_asks: str = ""
    capability_ids: tuple[str, ...] = ()
    claims: list[ClaimVerdict] = field(default_factory=list)
    actual: str = ""
    comments: list[str] = field(default_factory=list)
    metrics: CaseMetrics = field(default_factory=CaseMetrics)
    unexpected_bots: tuple[str, ...] = ()
    judge_mode: str = "llm"
    comment_verdicts: list[CommentVerdict] = field(default_factory=list)


class ReviewError(AssertionError):
    pass


def failed_claims(result: EvalResult) -> list[ClaimVerdict]:
    return [claim for claim in result.claims if not claim.passed]


def rate_comments(judge: object, case: Case, comments: list[str]) -> list[CommentRating] | None:
    """Ratings from a judge that can rate comments; None for one that cannot (fast)."""
    rate = getattr(judge, "rate_comments", None)
    if rate is None:
        return None
    return rate(case_diff(case), comments) if comments else []


@dataclass
class ReviewHarness:
    tool: ReviewTool
    judge: ClaimJudge
    trigger: bool = False
    wait_seconds: int = 0
    poll_seconds: int = 15
    allow_bots: frozenset[str] = frozenset()
    judge_mode: str = "llm"
    opened: list[PullRequest] = field(default_factory=list)
    owner: str = ""

    def setup(self, case: Case) -> PullRequest:
        """Open the eval PR if needed. Reuses an already-open PR."""
        pr = ensure_pr(case, tool=self.tool, all_tools=True)
        self.opened.append(pr)
        return pr

    def execute(self, pr: PullRequest) -> Review:
        """Trigger the selected tool if asked, then collect its comments."""
        if self.trigger:
            trigger_pr(pr, self.tool)
        deadline = time.time() + self.wait_seconds
        review = self.tool.collect(pr)
        blocked = getattr(self.tool, "blocked", None)
        if blocked is not None and not review.text:
            notice = blocked(pr)
            if notice:
                raise ReviewerBlocked(f"{self.tool.name} did not review {pr.url}: {notice}")
        # A tool that finished without a finding (CodeAnt's "Reviewed" status,
        # CodeRabbit's "No actionable comments") will not post more: stop waiting.
        finished = getattr(self.tool, "finished", None)
        while self.wait_seconds and not review.text and time.time() < deadline:
            if finished is not None and finished(pr):
                break
            time.sleep(self.poll_seconds)
            review = self.tool.collect(pr)
        return review

    def score(self, review: Review, case: Case) -> EvalResult:
        comments = review_comments(review.comments, review.text)
        visible = "\n\n".join(comments) or visible_review_text(review.text)
        claims = [self.judge.judge(claim, review.text) for claim in case.claims]
        verdicts = classify_comments(claims, comments, rate_comments(self.judge, case, comments))
        allowed = set(self.tool.bot_logins) | set(self.allow_bots)
        isolation = check_isolation(review.logins, allowed)
        return EvalResult(
            case_id=case.id,
            intent=case.intent,
            tests=case.tests,
            capability_id=case.capability.id,
            capability=case.capability.name,
            capability_asks=case.capability.asks,
            capability_ids=tuple(item.id for item in case.capabilities),
            tool=self.tool.name,
            pr_url=review.pr_url or "",
            finding_passed=bool(claims) and all(claim.passed for claim in claims),
            isolation_passed=isolation.passed,
            claims=claims,
            actual=visible,
            comments=comments,
            metrics=score_metrics(claims, comments, verdicts),
            unexpected_bots=isolation.unexpected_bots,
            judge_mode=self.judge_mode,
            comment_verdicts=verdicts,
        )

    def errored(self, case: Case, error: BaseException, pr_url: str = "") -> EvalResult:
        """A failed row for a case that raised before it could be scored.

        Without it the case drops out of the report, and 10/10 can hide 4
        cases that never ran. No judge call: the judge may be what failed.
        """
        reason = f"case errored: {type(error).__name__}: {error}"[:300]
        claims = [
            ClaimVerdict(claim.id, claim.must_assert, False, "", reason, claim.tokens, (), claim.tokens)
            for claim in case.claims
        ]
        return EvalResult(
            case_id=case.id,
            intent=case.intent,
            tests=case.tests,
            capability_id=case.capability.id,
            capability=case.capability.name,
            capability_asks=case.capability.asks,
            capability_ids=tuple(item.id for item in case.capabilities),
            tool=self.tool.name,
            pr_url=pr_url,
            finding_passed=False,
            isolation_passed=True,
            claims=claims,
            actual=reason,
            comments=[],
            metrics=score_metrics(claims, [], []),
            unexpected_bots=(),
            judge_mode=self.judge_mode,
            comment_verdicts=[],
        )

    def assert_review(self, review: Review, case: Case) -> EvalResult:
        result = self.score(review, case)
        if not review.text:
            raise ReviewError(
                f"{self.tool.name} left no review on {review.pr_url or case.id}"
            )
        missed = failed_claims(result)
        if missed:
            details = "; ".join(
                f"{item.claim_id}: {item.reason}" for item in missed
            )
            raise ReviewError(f"{self.tool.name} missed claims on {review.pr_url}: {details}")
        if not result.isolation_passed:
            raise ReviewError(
                f"unexpected bots {result.unexpected_bots} on {review.pr_url}"
            )
        return result

    def cleanup(self) -> None:
        from prlab_eval.cleanup import cleanup_eval

        cleanup_eval(owner=self.owner)
