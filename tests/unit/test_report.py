from prlab_eval.harness import EvalResult
from prlab_eval.judge import ClaimVerdict
from prlab_eval.report import isolation_label, terminal_summary, write_reports


def test_write_reports_separates_comment_from_judge(tmp_path) -> None:
    results = [
        EvalResult(
            case_id="test-stats-not-out-display-increments-wickets",
            intent="Catch incrementing ledger wickets when last_event.display is NOT_OUT",
            tests="Stats increments wickets on NOT_OUT even though scoring said the batter was not out.",
            capability_id="display-vs-source-of-truth",
            capability="Display vs source of truth",
            capability_asks="Can the tool tell a presentation string from the counted-wicket flag?",
            tool="greptile",
            pr_url="https://example.test/pr/2",
            finding_passed=True,
            isolation_passed=True,
            claims=[
                ClaimVerdict(
                    claim_id="not-out-counted-as-wicket",
                    must_assert="NOT_OUT should not increment wickets.",
                    passed=True,
                    quote="NOT_OUT should not increment the wicket total.",
                    reason="asserted",
                    tokens_expected=("NOT_OUT", "wicket"),
                    tokens_matched=("NOT_OUT", "wicket"),
                    tokens_missing=(),
                )
            ],
            actual="NOT_OUT should not increment the wicket total.",
            comments=["NOT_OUT should not increment the wicket total."],
        ),
        EvalResult(
            case_id="test-social-appeal-kind-posted-as-wicket",
            intent="Catch posting WICKET when highlight kind is appeal",
            tests="Social posts WICKET for appeal clips.",
            tool="greptile",
            pr_url="https://example.test/pr/3",
            finding_passed=False,
            isolation_passed=True,
            claims=[
                ClaimVerdict(
                    claim_id="appeal-posted-as-wicket",
                    must_assert="Posting WICKET for kind=appeal treats an appeal as a wicket.",
                    passed=False,
                    quote="",
                    reason="not asserted",
                    tokens_expected=("appeal", "WICKET"),
                    tokens_matched=("WICKET",),
                    tokens_missing=("appeal",),
                )
            ],
            actual="This posts WICKET for every clip.",
            comments=["This posts WICKET for every clip.", "README is stale."],
        ),
    ]
    latest = write_reports(results, "greptile", out_dir=tmp_path)
    text = latest.read_text()
    assert "Expected finding" in text
    assert "What this tests:" in text
    assert "Capability:" in text
    assert "By capability" in text
    assert "Display vs source of truth" in text
    assert "Intent:" in text
    assert "NOT_OUT" in text
    assert "Actual PR comment" in text
    assert "Judge verdict" in text
    assert "Judge evidence" in text
    assert "NOT_OUT should not increment wickets." in text
    assert "This posts WICKET for every clip." in text
    assert "Precision" in text
    assert "Recall" in text
    assert "not asserted" in text
    assert isolation_label(results[0]) == "yes"
    payload = latest.with_suffix(".json").read_text()
    assert '"precision"' in payload
    assert '"recall"' in payload
    assert '"must_assert"' in payload
    assert '"by_capability"' in payload
    table = terminal_summary(results)
    assert "test-stats-not-out-display-increments-wickets" in table
    assert "P=" in table
    assert "R=" in table


def test_reports_are_named_by_run_and_never_overwritten(tmp_path) -> None:
    from prlab_eval.harness import EvalResult

    row = EvalResult(case_id="test-x", tool="qodo", pr_url="u", finding_passed=True, isolation_passed=True)
    first = write_reports([row], "qodo", out_dir=tmp_path, label="org-qodo1-groq", stamp="20260924T000000Z")
    second = write_reports([row], "qodo", out_dir=tmp_path, label="org-qodo1-groq", stamp="20260924T000000Z")
    assert first.parent == tmp_path / "qodo"
    assert not list(tmp_path.rglob("latest*"))
    names = sorted(path.name for path in (tmp_path / "qodo").glob("review-eval-*.md"))
    assert names == [
        "review-eval-qodo-org-qodo1-groq-20260924T000000Z-2.md",
        "review-eval-qodo-org-qodo1-groq-20260924T000000Z.md",
    ]
    assert first != second


def test_report_stem_flattens_a_monorepo_owner() -> None:
    from prlab_eval.report import report_stem

    stem = report_stem("codeant-monorepo", "org-codeant/prlab-cricket-estate-bifrost", "20260930T084939Z")
    assert "/" not in stem and stem.endswith("prlab-cricket-estate-bifrost-20260930T084939Z")
