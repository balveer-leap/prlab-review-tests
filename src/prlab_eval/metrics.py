from __future__ import annotations

from dataclasses import dataclass

from prlab_eval.judge import ClaimVerdict, CommentRating, quote_is_from_review, visible_review_text

TRAP = "trap"
DEFECT = "defect"
NOISE = "noise"


@dataclass(frozen=True)
class CommentVerdict:
    """Final label for one posted comment (1-based index).

    trap: holds the quote the claim judge verified for a passing claim.
    defect: rated a genuine defect in the diff, even if not the planted one.
    noise: anything else.
    """

    index: int
    label: str
    reason: str = ""

    @property
    def relevant(self) -> bool:
        return self.label != NOISE


@dataclass(frozen=True)
class CaseMetrics:
    true_positives: int = 0
    false_negatives: int = 0
    false_positives: int = 0
    relevant_comments: int = 0
    comments: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    defect_comments: int = 0


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def review_comments(comments: tuple[str, ...] | list[str], fallback: str = "") -> list[str]:
    visible = [visible_review_text(item) for item in comments]
    visible = [item for item in visible if item]
    if visible:
        return visible
    fallback_text = visible_review_text(fallback)
    return [fallback_text] if fallback_text else []


def classify_comments(
    claims: list[ClaimVerdict],
    comments: list[str],
    ratings: list[CommentRating] | None = None,
) -> list[CommentVerdict]:
    """Label every comment. Without ratings (fast judge), only trap comments are relevant."""
    quotes = [claim.quote for claim in claims if claim.passed and claim.quote.strip()]
    rated = {rating.index: rating for rating in ratings or []}
    verdicts: list[CommentVerdict] = []
    for index, comment in enumerate(comments, start=1):
        if quotes and any(quote_is_from_review(quote, comment) for quote in quotes):
            verdicts.append(CommentVerdict(index, TRAP, "holds the quote the claim judge verified"))
        elif index in rated:
            rating = rated[index]
            verdicts.append(CommentVerdict(index, DEFECT if rating.defect else NOISE, rating.reason))
        else:
            verdicts.append(CommentVerdict(index, NOISE, "not rated"))

    # The verified quote can straddle two comments; a caught trap still has a relevant comment.
    if any(claim.passed for claim in claims) and verdicts and not any(v.relevant for v in verdicts):
        verdicts[0] = CommentVerdict(1, TRAP, "claim quote spans comments")
    return verdicts


def score_metrics(
    claims: list[ClaimVerdict],
    comments: list[str],
    verdicts: list[CommentVerdict] | None = None,
) -> CaseMetrics:
    """Recall over expected claims; precision over actual PR comments.

    TP / FN are expected findings the review did or did not assert.
    Precision counts trap and defect comments as relevant; FP are the noise comments.
    """
    if verdicts is None:
        verdicts = classify_comments(claims, comments)
    claim_tp = sum(1 for claim in claims if claim.passed)
    claim_fn = sum(1 for claim in claims if not claim.passed)
    relevant = sum(1 for verdict in verdicts if verdict.relevant)
    defects = sum(1 for verdict in verdicts if verdict.label == DEFECT)

    precision = _ratio(relevant, len(comments))
    recall = _ratio(claim_tp, claim_tp + claim_fn)
    return CaseMetrics(
        true_positives=claim_tp,
        false_negatives=claim_fn,
        false_positives=len(comments) - relevant,
        relevant_comments=relevant,
        comments=len(comments),
        precision=precision,
        recall=recall,
        f1=_f1(precision, recall),
        defect_comments=defects,
    )


def aggregate_metrics(rows: list[CaseMetrics]) -> CaseMetrics:
    tp = sum(row.true_positives for row in rows)
    fn = sum(row.false_negatives for row in rows)
    fp = sum(row.false_positives for row in rows)
    relevant = sum(row.relevant_comments for row in rows)
    comments = sum(row.comments for row in rows)
    precision = _ratio(relevant, comments)
    recall = _ratio(tp, tp + fn)
    return CaseMetrics(
        true_positives=tp,
        false_negatives=fn,
        false_positives=fp,
        relevant_comments=relevant,
        comments=comments,
        precision=precision,
        recall=recall,
        f1=_f1(precision, recall),
        defect_comments=sum(row.defect_comments for row in rows),
    )


def pct(value: float) -> str:
    return f"{value:.0%}"
