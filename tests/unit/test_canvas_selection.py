"""The comparison shows each tool's newest like-for-like run."""

import json
from pathlib import Path

from prlab_eval.canvas import comparison_report


def report(folder: Path, name: str, run_at: str, options: dict | None = None, rescored_from: str = "") -> Path:
    path = folder / "coderabbit" / f"review-eval-coderabbit-{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"tool": "coderabbit", "run_at": run_at, "generated_at": run_at}
    if options is not None:
        body["tool_options"] = options
    if rescored_from:
        body["rescored_from"] = rescored_from
    path.write_text(json.dumps(body))
    return path


def test_skips_runs_with_non_default_tool_options(tmp_path: Path) -> None:
    report(tmp_path, "org-x-groq-20260924T000000Z", "20260924T000000Z")
    linked = report(tmp_path, "org-x-groq-linked-20260925T000000Z", "20260925T000000Z", {})
    report(tmp_path, "org-x-groq-linked-context-20260925T000100Z", "20260925T000100Z", {"linked_findings": "true"})
    assert comparison_report("coderabbit", tmp_path) == linked


def test_prefers_the_rescored_copy_of_the_chosen_run(tmp_path: Path) -> None:
    report(tmp_path, "org-x-groq-linked-20260925T000000Z", "20260925T000000Z", {})
    rescored = report(tmp_path, "org-x-groq-linked-rescored-20260928T000000Z", "20260925T000000Z", {},
                      rescored_from="review-eval-coderabbit-org-x-groq-linked-20260925T000000Z.json")
    report(tmp_path, "org-x-groq-linked-context-rescored-20260928T000100Z", "20260925T000100Z",
           {"linked_findings": "true"}, rescored_from="x.json")
    assert comparison_report("coderabbit", tmp_path) == rescored


def test_falls_back_to_newest_when_every_run_has_options(tmp_path: Path) -> None:
    only = report(tmp_path, "org-x-groq-linked-context-20260925T000100Z", "20260925T000100Z", {"linked_findings": "true"})
    assert comparison_report("coderabbit", tmp_path) == only


def test_ignores_superseded_folder(tmp_path: Path) -> None:
    kept = report(tmp_path, "org-x-groq-20260924T000000Z", "20260924T000000Z")
    old = tmp_path / "coderabbit" / "superseded" / "review-eval-coderabbit-org-x-groq-20260930T000000Z.json"
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps({"tool": "coderabbit", "run_at": "20260930T000000Z"}))
    assert comparison_report("coderabbit", tmp_path) == kept
