# prlab-review-tests

Pass/fail tests for PR review tools on the cricket scoring estate.

Cases describe a product change and the **claim** a review must assert. They are not tied to a vendor. Each tool is a plugin: trigger text and bot login. Multi-repo context is configured in the review product (for Greptile, a portal Repo Cluster), not written onto the trap PR.

Every case id starts with `test-` and names the intent (`test-stats-not-out-display-increments-wickets`). `intent` is the one-line goal. `tests` is the cricket trap.

Review-tool skills live in `cases/capabilities.json`. A case points at one or more ids (`"capability": "contract-leak"` or `"capabilities": ["contract-leak", "leak-propagation"]`). The same skill can be reused across cases and later tools. Ids name what the review product can do (cross-repo impact, omitted guard as true), not design principles or cricket-specific bug labels.

Product repositories stay blind. A product PR must not mention this harness, hop numbers, or expected findings.

## Layout

```
cases/capabilities.json   # shared review-tool skills (referenced by id)
cases/owners.json         # tool -> GitHub owner holding that tool's product repos
cases/cases.json          # shared traps (`capability` is a catalog id)
patches/                  # code-only diffs
src/prlab_eval/tools/     # one module per review product
tests/unit/               # matcher and case tests
tests/eval/               # live setup → execute → assert
reports/                  # written by pytest (gitignored: each clone has its own runs)
viewer/                   # comparison canvas template, filled by `prlab_eval canvas`
```

`--tool` selects the plugin (trigger and bot). Cases and capabilities stay shared.

| Column | Meaning |
|---|---|
| Capability | The review-tool skill this case measures. Reports also roll up P/R by capability. |
| Expected finding | The trap. What a correct review must mean. |
| Actual PR comment | The tool's GitHub comment(s), not the judge. |
| Judge verdict | Whether those comments assert the expected finding, plus a short reason. |
| Judge evidence | Substring the judge copied from the comment. |
| Recall | Expected findings asserted / expected findings. |
| Precision | Relevant PR comments / all PR comments. A comment is `trap` (holds the verified quote for the planted finding), `defect` (the judge, shown the diff but not the trap, rates it a genuine defect), or `noise` (style, docs, test requests, summaries, repeats, wrong claims). Trap and defect count as relevant. With `--fast` there is no rater, so only the trap comment counts. |
| Isolation | Process check. Only the selected tool's bot may review. Use `--allow-bots` to opt another bot in. |
| Tokens | Diagnostic keyword check. Not used for pass/fail. |

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Tests

Case **data** lives in `cases/cases.json`. Each case is one pytest node:

```
tests/eval/test_reviews.py::test_review_tool_flags_regression[test-stats-not-out-display-increments-wickets] FAILED
```

Do not write one hand-copied test function per trap. Add a JSON case; pytest picks it up.

```bash
pytest                    # unit tests
pytest tests/eval --run-eval --tool greptile --fast
pytest tests/eval --run-eval --tool greptile --judge-provider gemini --cleanup
```

`--fast` scores each claim by its token patterns only. No LLM key, no judge pre-check. Use it for a quick pass after reviews have landed. Token match is weaker than the LLM judge and can pass a review that only mentions the keywords.

## Eval a tool (live GitHub)

1. **Setup** — open eval PRs. Creates each `eval/*` branch if it is missing (after cleanup, for example):

```bash
python3 -m prlab_eval setup
```

2. **Trigger** — mention the tool once per open eval PR. Skips PRs that already have the mention:

```bash
python3 -m prlab_eval trigger --tool greptile
```

3. **Execute + assert** — collect comments and score claims. Default is a temperature-0 LLM judge. Add `--fast` to skip the LLM and score by claim tokens only. Add `--wait` if reviews are still landing. Add `--cleanup` to close the eval PRs and delete their branches after the report is written.

4. **Cleanup** — close leftover eval PRs and delete `eval/*` branches without scoring. Reports stay.

```bash
python3 -m prlab_eval cleanup
# or: python3 scripts/cleanup_prs.py
```

Free judge options (first match wins if you set nothing):

| Provider | Cost | Setup |
|---|---|---|
| **Groq** | Free tier | [console.groq.com](https://console.groq.com) → `export GROQ_API_KEY=...` |
| **Gemini** | Free tier | [aistudio.google.com](https://aistudio.google.com) → `export GEMINI_API_KEY=...` |
| **Ollama** | Local, free | `ollama pull llama3.2` then `--judge-provider ollama` |
| **GitHub Models** | Free with `gh` | `gh auth login` then `--judge-provider github` |

```bash
export GROQ_API_KEY=...
# default Groq model is openai/gpt-oss-120b
pytest tests/eval --run-eval --tool greptile --judge-provider groq --cleanup

# or Qwen 3.8 27B
pytest tests/eval --run-eval --tool greptile --judge-provider groq --judge-model qwen/qwen3.8-27b --cleanup
```

Eval pings the LLM judge once before any case unless `--fast` is set. A bad key or model stops the session immediately.

```bash
python3 -m prlab_eval judge-check --provider openai
```

`--trigger` on pytest still works as a one-shot. It uses the same skip-if-already-mentioned check. Prefer the separate `trigger` command so you can inspect the comments before scoring.

Reports written under `reports/`:

- `reports/<tool>/review-eval-<tool>-<owner>-<judge>-<stamp>.md` / `.json` / `.html` — one set per run, one row per case. Nothing is overwritten; a clash gets `-2`, `-3`.
- The JSON records the judge (`"judge": {"mode", "provider", "model"}`) and each comment's label and reason (`comment_verdicts`).
- `reports/comparison.json` — latest run per tool, combined; written by `prlab_eval canvas`.

Each case costs one extra judge call: the comment rater gets the case's patch and the numbered comments, and returns trap-blind defect/noise ratings for precision.

## Registered tools

| Tool | Bot login(s) | Trigger | What counts as a comment |
|---|---|---|---|
| `greptile` | `greptile-apps[bot]` | `@greptileai` | Every bot comment, review body, and inline comment |
| `coderabbit` | `coderabbitai[bot]` | `@coderabbitai review` | Inline comments, plus each item under *Nitpick* and *Outside diff range* in the review body. The walkthrough, *Additional comments*, *Duplicate comments*, and rate-limit notices are not findings. |
| `qodo` | `qodo-code-review[bot]`, `qodo-merge-pro[bot]`, `qodo-merge[bot]` | `/review` | Inline comments, plus any numbered finding in *Code Review by Qodo* that was not posted inline. *PR Summary by Qodo* is not a finding. |
| `codeant` | `codeant-ai[bot]` | `@codeant-ai: review` | Inline comments (badge links and the AI-agent prompt stripped). The per-PR *Review Status* table (`codeant-review-status` marker) is not a finding. |

Multi-repo context for these tools is set up in each product (CodeRabbit linked repositories, Qodo org indexing), same as the Greptile Repo Cluster.

CodeRabbit and Qodo auto-review on PR open. Only install one vendor per owner, or isolation fails on every case.

## One owner per tool

Each vendor runs in its own GitHub owner, because CodeRabbit and Qodo auto-review every PR in the repos where they are installed. `cases/owners.json` maps each tool to the owner that holds its copy of the product repos:

```json
{"greptile": "srajat-leap", "coderabbit": "org-coderabbit1", "qodo": "org-qodo1",
 "claude-plain": "org-claudplain", "claude-skill": "org-claudeskill"}
```

Every live command resolves the owner in this order: `--owner`, then `$PRLAB_OWNER`, then `owners.json`. If none apply, it stops. It never falls back to the repos written in `cases.json`, because that would score one vendor against another vendor's PRs, or close them.

- A `--owner` that differs from the tool's configured owner prints a warning.
- `cleanup` needs `--tool` or `--owner`.
- The owner is recorded in each report (title and `owner` field in the JSON).
- `load_cases()` with no owner returns the raw `cases.json` repos, for data checks only.

```bash
python3 -m prlab_eval setup   --tool coderabbit
python3 -m prlab_eval trigger --tool coderabbit
pytest tests/eval --run-eval --tool coderabbit --judge-provider groq --wait 900
python3 -m prlab_eval cleanup --tool coderabbit
```

Setup adds a git remote named after the owner to each product clone and pushes eval branches there; `origin` stays `srajat-leap`. The target owner's `main` must match `srajat-leap` `main` (the Claude orgs also carry a `claude-review.yml` commit), or the patches will not apply.

To add a tool, register it and add its owner to `owners.json`. A unit test fails if a registered tool has no owner.

## Run every reviewer at once

```bash
python3 -m prlab_eval all --setup --trigger --wait 900 --judge-provider groq --cleanup
```

This runs the whole estate end to end. Each tool is looked up in `owners.json`, so
every vendor is set up, triggered and scored in its own owner, and no `--owner` is needed.

Because each vendor lives in a different owner, this is a sequence of single-tool runs,
not one pytest session. Setup and trigger happen for every tool first, so auto-review
vendors and Action workflows are already posting while the rest are still being prepared;
scoring comes last. A reviewer that misses a trap makes pytest exit 1, which is a result
and not a breakage, so the sequence keeps going; only a usage or judge error stops a run,
and that tool is marked `did not score` in the summary.

Each tool still writes its own `reports/<tool>/` files, exactly as a single-tool run does.
At the end the combined standings are printed, best F1 first:

```
TOOL             OWNER              PASSED   P      R      F1     REPORT
claude-skill     org-claudeskill    13/14    100%   93%    97%    review-eval-claude-skill-…json
greptile         srajat-leap        13/14    77%    93%    85%    review-eval-greptile-…json
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `--tools greptile,qodo` | only these reviewers (default: every registered tool) |
| `--only case-a,case-b` | only these cases; unknown ids fail before anything runs |
| `--setup` / `--trigger` | open the PRs / mention each tool, before scoring |
| `--fast` | keyword judge, no LLM key |
| `--cleanup` | close each tool's PRs once it has scored |
| `--dry-run` | print the per-tool pytest commands and stop |

If the PRs are already open and reviewed, drop `--setup --trigger` and just score:

```bash
python3 -m prlab_eval all --judge-provider groq
```

`all` refreshes the comparison canvas when it finishes.

## Rescore saved reports

A scoring change does not need the PRs, GitHub, or the vendors: every report keeps the comments the tool posted.

```bash
python3 -m prlab_eval rescore --judge-provider groq             # latest run per tool
python3 -m prlab_eval rescore --all-runs --judge-provider groq  # every run in each tool folder
python3 -m prlab_eval rescore --report reports/qodo/<file>.json # one file
```

Rescoring re-checks claims the quote check rejected (offline), rates every comment for precision, and writes a new `…-rescored-<stamp>` report beside the source. The source file is not touched. The new report names its source (`rescored_from`) and keeps its `run_at`, so "latest run" still means the latest collected reviews, with the rescored copy preferred.

## Comparison canvas

```bash
python3 -m prlab_eval canvas
```

Reads the latest run per tool from `reports/`, writes `reports/comparison.json`, and fills `viewer/review-tool-comparison.canvas.tsx` into this workspace's Cursor canvases folder (`~/.cursor/projects/<workspace>/canvases/`), which is the only place Cursor picks canvases up. Open that file in Cursor to compare tools case by case, with links to each PR. `--out PATH` writes somewhere else.

`all` and `rescore` run this for you. Reports are gitignored, so each clone shows its own runs; commit a set of reports if a team should share one.

## Live comparison page (browser)

`viewer/review-tool-comparison.canvas.tsx` is the Cursor canvas and only renders inside Cursor. `viewer/index.html` is a browser copy of the same layout, fed the same data (`canvas.build_payload()`: latest run per tool, `superseded/` ignored).

```bash
python3 -m prlab_eval.live            # http://127.0.0.1:8765  (--port, --host, --open, --reports DIR)
```

The server scans `reports/` every second and pushes an update over server-sent events whenever a report is added, changed or removed, so the page follows new runs, rescores and moves without a reload. The page keeps the chosen case and reviewers in the browser.

- `?case=<case-id>` opens one case (shareable link).
- `?theme=dark|light` forces a theme; the **theme** button toggles and remembers it.
- `?once` loads a single snapshot without the live stream (printing, screenshots).

Standard library only; binds to 127.0.0.1. When the canvas layout changes, mirror it in `viewer/index.html`.

## Add a tool

1. Create `src/prlab_eval/tools/<name>.py` with `name`, `bot_logins`, `trigger_body`, `collect`, and `trigger`.
2. Register it in `src/prlab_eval/tools/__init__.py` and give it an owner in `cases/owners.json`.
3. Run `pytest tests/eval --run-eval --tool <name>`.

`tools/github_review.py` fetches the three comment streams, parses nested `<details>`, and dedupes. Add a captured-comment fixture under `tests/unit/fixtures/` and test `collect` against it.

Cases do not change.

## Isolation

The runner account may post the tool's trigger comment. Unexpected `[bot]` logins fail isolation unless listed in `--allow-bots`. Leave Cursor and other reviewers off a run until you opt them in.

## Product remotes

`srajat-leap/prlab-cricket-*`. This harness is `srajat-leap/prlab-review-tests`.
