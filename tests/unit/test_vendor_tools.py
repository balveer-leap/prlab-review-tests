"""CodeRabbit and Qodo plugins, scored against comments captured from real PRs."""

import json
from pathlib import Path

import pytest

from prlab_eval.cases import load_cases
from prlab_eval.metrics import review_comments
from prlab_eval.scoring import check_isolation
from prlab_eval.tools import TOOLS
from prlab_eval.tools.base import PullRequest
from prlab_eval.tools.coderabbit import CodeRabbitTool, review_body_findings
from prlab_eval.tools.github_review import top_level_details
from prlab_eval.tools.qodo import QodoTool, summary_findings

FIXTURES = Path(__file__).parent / "fixtures"
PR = PullRequest(repo="org/prlab-cricket-x", number=1, url="https://example.test/1", branch="eval/x")


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def serve(monkeypatch, rows: dict) -> None:
    def fake_list(path: str):
        if "/issues/" in path:
            return rows["issue"]
        if "/reviews" in path:
            return rows["reviews"]
        return rows["inline"]

    monkeypatch.setattr("prlab_eval.tools.github_review.gh_api_list", fake_list)


def test_new_tools_are_registered() -> None:
    assert {"greptile", "coderabbit", "qodo"} <= set(TOOLS)
    for tool in TOOLS.values():
        assert tool.bot_logins and tool.trigger_body


def test_details_parser_respects_nesting() -> None:
    text = "<details><summary>outer</summary>a<details><summary>inner</summary>b</details>c</details>"
    blocks = top_level_details(text)
    assert [block.summary for block in blocks] == ["outer"]
    assert [block.summary for block in top_level_details(blocks[0].inner)] == ["inner"]


# --- CodeRabbit -------------------------------------------------------------


def test_coderabbit_keeps_findings_and_drops_walkthrough(monkeypatch) -> None:
    serve(monkeypatch, load("coderabbit_pr.json"))
    review = CodeRabbitTool().collect(PR)
    comments = review_comments(review.comments, review.text)
    assert len(comments) == 2
    assert comments[0].startswith("README.md:30")
    assert comments[1].startswith("public/app.js:5-10")
    assert not any("Walkthrough" in item for item in comments)
    assert not any("Prompt for AI Agents" in item for item in comments)


def test_coderabbit_review_body_splits_nitpicks_by_file() -> None:
    body = next(row["body"] for row in load("coderabbit_pr.json")["reviews"] if "Nitpick" in row["body"])
    findings = review_body_findings(body)
    assert len(findings) == 1
    assert findings[0].startswith("public/app.js:5-10\n")
    assert "Cover the browser animation path" in findings[0]


def test_coderabbit_skips_additional_and_duplicate_sections() -> None:
    body = (
        "<details><summary>🔇 Additional comments (1)</summary><blockquote>"
        "<details><summary>a.py (1)</summary><blockquote>`1-2`: looks fine</blockquote></details>"
        "</blockquote></details>"
        "<details><summary>♻️ Duplicate comments (1)</summary><blockquote>"
        "<details><summary>b.py (1)</summary><blockquote>`3`: again</blockquote></details>"
        "</blockquote></details>"
        "<details><summary>⚠️ Outside diff range comments (2)</summary><blockquote>"
        "<details><summary>c.py (2)</summary><blockquote>`7`: first\n\n---\n\n`9-11`: second"
        "</blockquote></details></blockquote></details>"
    )
    assert review_body_findings(body) == ["c.py:7\nfirst", "c.py:9-11\nsecond"]


def test_coderabbit_isolation_flags_other_vendor_on_same_pr(monkeypatch) -> None:
    serve(monkeypatch, load("coderabbit_pr.json"))
    review = CodeRabbitTool().collect(PR)
    isolation = check_isolation(review.logins, set(CodeRabbitTool.bot_logins))
    assert not isolation.passed
    assert isolation.unexpected_bots == ("qodo-code-review[bot]",)


def test_coderabbit_rate_limit_notice_is_not_a_review(monkeypatch) -> None:
    notice = "> [!WARNING]\n> ## Review limit reached\n> You've used all 10 included reviews."
    serve(
        monkeypatch,
        {"issue": [{"user": {"login": "coderabbitai[bot]"}, "body": notice}], "inline": [], "reviews": []},
    )
    assert CodeRabbitTool().collect(PR).text == ""


@pytest.mark.parametrize("tool", [CodeRabbitTool(), QodoTool()], ids=lambda tool: tool.name)
def test_vendor_tools_add_no_files_to_trap_prs(tool) -> None:
    for case in load_cases():
        assert tool.context_files(case) == {}


# --- Qodo -------------------------------------------------------------------


def test_qodo_counts_inline_findings_once(monkeypatch) -> None:
    serve(monkeypatch, load("qodo_pr.json"))
    review = QodoTool().collect(PR)
    comments = review_comments(review.comments, review.text)
    assert len(comments) == 3
    assert comments[0].startswith("cricket_protocol/models.py:50")
    assert "Missing confirmations count as wickets" in comments[0]
    assert not any("PR Summary by Qodo" in item for item in comments)
    assert not any("Agent Prompt" in item for item in comments)


def test_qodo_summary_supplies_findings_not_posted_inline(monkeypatch) -> None:
    rows = load("qodo_pr.json")
    rows["inline"] = rows["inline"][:1]
    serve(monkeypatch, rows)
    comments = review_comments(QodoTool().collect(PR).comments)
    assert len(comments) == 3
    assert sum("Missing confirmations count as wickets" in item for item in comments) == 1
    assert any(item.startswith("cricket_protocol/models.py:") and "Malformed wicket data" in item for item in comments)


def test_qodo_summary_parser_reads_description_and_location() -> None:
    body = next(row["body"] for row in load("qodo_pr.json")["issue"] if "Code Review by Qodo" in row["body"])
    findings = summary_findings(body)
    assert [key for key, _ in findings] == [
        "missing confirmations count as wickets",
        "malformed wicket data passes validation",
        "clients receive conflicting defaults",
    ]
    first = findings[0][1]
    assert first.startswith("cricket_protocol/models.py:R48-50\n")
    assert "increment the score" in first


@pytest.mark.parametrize("tool", [CodeRabbitTool(), QodoTool()], ids=lambda tool: tool.name)
def test_runner_trigger_comment_is_ignored(monkeypatch, tool) -> None:
    serve(
        monkeypatch,
        {"issue": [{"user": {"login": "balveer-leap"}, "body": tool.trigger_body}], "inline": [], "reviews": []},
    )
    review = tool.collect(PR)
    assert review.text == ""
    assert review.logins == {"balveer-leap"}


# --- Claude Code Action -----------------------------------------------------

from prlab_eval.tools.claude import ClaudeActionTool, review_body  # noqa: E402


@pytest.mark.parametrize("variant", ["plain", "skill"])
def test_claude_keeps_review_and_drops_job_header(monkeypatch, variant) -> None:
    rows = load("claude_sticky.json")[variant]
    serve(monkeypatch, {"issue": rows, "inline": [], "reviews": []})
    review = ClaudeActionTool(f"claude-{variant}").collect(PR)
    assert len(review.comments) == 1
    text = review.comments[0]
    assert "Claude finished" not in text
    assert "View job" not in text
    assert "- [x]" not in text
    assert "umpire_confirmed" in text


def test_claude_in_progress_comment_is_not_a_review(monkeypatch) -> None:
    working = "**Claude Code is working…** <img src='spinner.gif' />\n\n- [ ] Gather context\n"
    serve(monkeypatch, {"issue": [{"user": {"login": "claude[bot]"}, "body": working}], "inline": [], "reviews": []})
    assert ClaudeActionTool("claude-plain").collect(PR).text == ""
    assert review_body(working) == ""


def test_claude_variants_are_registered_separately() -> None:
    assert TOOLS["claude-plain"].name == "claude-plain"
    assert TOOLS["claude-skill"].name == "claude-skill"
    assert TOOLS["claude-plain"].bot_logins == {"claude[bot]"}


def test_claude_error_comment_is_not_a_review_and_can_be_retriggered(monkeypatch) -> None:
    from prlab_eval.trigger import comment_has_trigger

    errored = (
        "**Claude encountered an error after 1m 52s** —— [View job](https://example.test)\n\n---\n"
        "### Claude finished reviewing this PR\n\n- [x] Gather context\n"
    )
    serve(monkeypatch, {"issue": [{"user": {"login": "claude[bot]"}, "body": errored}], "inline": [], "reviews": []})
    tool = ClaudeActionTool("claude-plain")
    assert tool.collect(PR).text == ""
    assert not comment_has_trigger([errored], tool.trigger_body)
    finished = load("claude_sticky.json")["plain"][0]["body"]
    assert comment_has_trigger([finished], tool.trigger_body)


def test_claude_error_after_max_turns_still_counts_posted_review(monkeypatch) -> None:
    rows = load("claude_sticky.json")["errored_with_review"]
    serve(monkeypatch, {"issue": rows, "inline": [], "reviews": []})
    review = ClaudeActionTool("claude-plain").collect(PR)
    assert len(review.comments) == 1
    text = review.comments[0]
    assert "forbidden fields" in text
    assert "encountered an error" not in text
    assert "Claude finished reviewing" not in text


# --- CodeRabbit Multi-Repo Analysis (walkthrough "Multi-repo context") --------

from prlab_eval.tools.coderabbit import linked_findings  # noqa: E402


def test_linked_findings_split_per_linked_repo() -> None:
    body = next(row["body"] for row in load("coderabbit_linked_pr.json")["issue"] if "Multi-repo context" in row["body"])
    found = linked_findings(body)
    repos = [item.split("\n", 1)[0] for item in found]
    assert repos == ["linked:prlab-cricket-social", "linked:prlab-cricket-scoring", "linked:prlab-cricket-broadcast"]
    assert "trigger a wicket social post" in found[0]
    assert not any("[::" in item for item in found)


def test_linked_findings_are_off_by_default(monkeypatch) -> None:
    serve(monkeypatch, load("coderabbit_linked_pr.json"))
    assert CodeRabbitTool().collect(PR).text == ""


def test_linked_findings_option_counts_them(monkeypatch) -> None:
    serve(monkeypatch, load("coderabbit_linked_pr.json"))
    tool = CodeRabbitTool()
    tool.configure({"linked_findings": "true"})
    comments = tool.collect(PR).comments
    assert len(comments) == 3
    assert all(item.startswith("linked:") for item in comments)


def test_coderabbit_rejects_unknown_or_bad_options() -> None:
    tool = CodeRabbitTool()
    with pytest.raises(ValueError, match="no option"):
        tool.configure({"nope": "true"})
    with pytest.raises(ValueError, match="true or false"):
        tool.configure({"linked_findings": "maybe"})


def test_coderabbit_instances_do_not_share_options() -> None:
    first, second = CodeRabbitTool(), CodeRabbitTool()
    first.configure({"linked_findings": "true"})
    assert second.options["linked_findings"] is False


# --- CodeAnt AI (fixture: real org-codeant scoring#1) -------------------------

from prlab_eval.tools.codeant import CodeAntTool, is_status  # noqa: E402


def test_codeant_counts_inline_findings_and_skips_status_table(monkeypatch) -> None:
    rows = load("codeant_pr.json")
    serve(monkeypatch, rows)
    comments = review_comments(CodeAntTool().collect(PR).comments)
    assert len(comments) == 2
    assert comments[0].startswith("scoring/engine.py:70")
    assert "exposes `umpire_confirmed`" in comments[0]
    assert not any("Review Status" in item for item in comments)
    assert not any("Fix in Cursor" in item or "badges" in item for item in comments)
    assert not any("Prompt for AI Agent" in item for item in comments)
    assert is_status(rows["issue"][0]["body"])


def test_codeant_isolation_sees_only_its_bot(monkeypatch) -> None:
    serve(monkeypatch, load("codeant_pr.json"))
    review = CodeAntTool().collect(PR)
    assert check_isolation(review.logins, set(CodeAntTool().bot_logins)).passed


def test_codeant_bot_logins_are_configurable(monkeypatch) -> None:
    serve(monkeypatch, {"issue": [], "reviews": [],
                        "inline": [{"user": {"login": "codeantai[bot]"}, "path": "a.py", "line": 1, "body": "finding"}]})
    tool = CodeAntTool()
    assert tool.collect(PR).text == ""
    tool.configure({"bot_logins": "codeantai[bot]"})
    assert tool.collect(PR).text == "a.py:1\nfinding"
    with pytest.raises(ValueError):
        tool.configure({"bot_logins": " , "})
    with pytest.raises(ValueError):
        tool.configure({"nope": "x"})
