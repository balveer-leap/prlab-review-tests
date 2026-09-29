# prlab-review-tests

Pass/fail tests for PR review tools on the cricket scoring estate.

Cases describe a product change and the **claim** a review must assert. They are not tied to a vendor. Each tool is a plugin: trigger text and bot login. Multi-repo context is configured in the review product (for Greptile, a portal Repo Cluster), not written onto the trap PR.

Every case id starts with `test-` and names the intent (`test-stats-not-out-display-increments-wickets`). `intent` is the one-line goal. `tests` is the cricket trap.

Review-tool skills live in `cases/capabilities.json`. A case points at one or more ids (`"capability": "contract-leak"` or `"capabilities": ["contract-leak", "leak-propagation"]`). The same skill can be reused across cases and later tools. Ids name what the review product can do (cross-repo impact, omitted guard as true), not design principles or cricket-specific bug labels.

Product repositories stay blind. A product PR must not mention this harness, hop numbers, or expected findings.

## Quick start

Before the first run you need:

- Python 3.11 or newer (stock macOS `python3` is 3.9: `brew install python@3.12`, or use `uv`).
- `gh auth login` as a member, with write access, of every org in `cases/owners.json`. Setup pushes
  eval branches and opens PRs there. Ask the org owner for an invite, or provision your own orgs
  (below).
- The 11 product repos cloned next to this repo as `cricket-*`. Setup applies each patch in them.

```bash
# next to this repo, once
cd .. && for r in protocol scoring broadcast stats fantasy highlights \
                  social archive notifications live-gateway mobile; do
  git clone -q https://github.com/srajat-leap/prlab-cricket-$r cricket-$r
done && cd prlab-review-tests

python3 -m venv .venv && source .venv/bin/activate && pip install -e .
export GROQ_API_KEY=...                  # judge; free at console.groq.com

# 1. See the results: live comparison page, follows new runs without a reload.
#    The published results ship in reports/, so this works straight after a clone:
#    no GitHub login, judge key or product clones needed.
python3 -m prlab_eval.live --open        # http://127.0.0.1:8765

# 2. Run every reviewer at once (in a second terminal): audit, open PRs, trigger, wait, score
python3 -m prlab_eval all --setup --trigger --wait 900 --judge-provider groq

# The audit alone: must print PASS for every org before a score means anything
python3 -m prlab_eval.audit --all

# Only some reviewers, or re-score reviews that already landed
python3 -m prlab_eval all --tools claude-plain,claude-custom_skill --setup --wait 900 --judge-provider groq
python3 -m prlab_eval all --judge-provider groq
```

`all` stops before anything runs if any org fails the leak audit. An org can be exempted by name in
`cases/audit_exempt.json` (empty today); its tools are then scored from their open PRs only and marked
as not leak-audited.

Each reviewer runs in its own GitHub org from `cases/owners.json`. An entry of the form `org/repo`
(as for `codeant-monorepo`) points a tool at one repo holding every service as a folder; each case's
patch then applies under its service folder. To use your own orgs,
provision them with [`estate/`](estate/README.md) (repos, vendor apps, Claude workflow, secrets)
and put them in `owners.json`. Reports land in `reports/<tool>/`, and the page picks them up
within a second. One free Groq key covers about one full scoring run a day; don't run two
scoring jobs on the same key. See [Run every reviewer at once](#run-every-reviewer-at-once) and
[Live comparison page](#live-comparison-page-browser) for details.

## Layout

```
cases/capabilities.json   # shared review-tool skills (referenced by id)
cases/owners.json         # tool -> GitHub owner holding that tool's product repos
cases/cases.json          # shared traps (`capability` is a catalog id)
patches/                  # code-only diffs
src/prlab_eval/tools/     # one module per review product
tests/unit/               # matcher and case tests
tests/eval/               # live setup → execute → assert
reports/                  # published results are committed; new runs are gitignored
viewer/                   # comparison page served by `python3 -m prlab_eval.live`
estate/                   # provision one GitHub org per tool; see estate/README.md
src/prlab_eval/audit.py   # leak audit that `all` runs before setup and scoring
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
python3 -m prlab_eval setup --tool greptile
```

2. **Trigger** — mention the tool once per open eval PR. Skips PRs that already have the mention:

```bash
python3 -m prlab_eval trigger --tool greptile
```

3. **Execute + assert** — collect comments and score claims. Default is a temperature-0 LLM judge. Add `--fast` to skip the LLM and score by claim tokens only. Add `--wait` if reviews are still landing. Add `--cleanup` to close the eval PRs and delete their branches after the report is written.

4. **Cleanup** — close leftover eval PRs and delete `eval/*` branches without scoring. Reports stay.

```bash
python3 -m prlab_eval cleanup --tool greptile
# or: python3 scripts/cleanup_prs.py --tool greptile
```

Free judge options (first match wins if you set nothing):

| Provider | Cost | Setup |
|---|---|---|
| **Groq** | Free tier | [console.groq.com](https://console.groq.com) → `export GROQ_API_KEY=...` |
| **Gemini** | Free tier | [aistudio.google.com](https://aistudio.google.com) → `export GEMINI_API_KEY=...` |
| **Ollama** | Local, free | `ollama pull llama3.2` then `--judge-provider ollama` |
| **GitHub Models** | Free with `gh` | `gh auth login` then `--judge-provider github` |
| **Bifrost gateway** | Company gateway | `export BIFROST_BASE_URL=https://<gateway-host> BIFROST_API_KEY=sk-bf-...` then `--judge-provider bifrost` (default model Claude Haiku 4.5; `--judge-model` picks another) |
| **Panel** (recommended) | Company gateway | Same Bifrost env, then `--judge-provider panel`: Haiku 4.5 and Sonnet 4.5 vote on every claim, DeepSeek V3.2 breaks a split (`--judge-model "A,B;C"` picks others) |

### How a claim is judged

Each claim in `cases/cases.json` lists its `parts` (what the review must state, split so one half cannot
carry the other) and `not_enough` (related findings that do not meet it, taken from the case's `tests`
notes). The judge rules on every part and quotes the review for each; a claim passes only when every
part is met and every quote is found in the review. The report keeps each part's ruling, and for the
panel each member's vote.

Measured on 207 claims (the 7 reviewers' reviews of the 23 cases), read and labelled by hand:

| Judge | Correct | Wrongly passed | Wrongly failed |
|---|---|---|---|
| Haiku 4.5, whole claim (before) | 181 | 19 | 7 |
| Sonnet 4.5, whole claim (before) | 183 | 15 | 9 |
| Haiku 4.5, by parts | 197 | 5 | 5 |
| Sonnet 4.5, by parts | 199 | 3 | 5 |
| Panel, by parts | 201 | 2 | 4 |

The whole-claim judges passed reviews that shared the claim's vocabulary but described a different defect.
The parts and wording were tuned on these same claims, so a fresh set of cases is the real test.

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
- `reports/comparison.json` — latest run per tool, combined; written by `prlab_eval comparison`.

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
{"greptile": "org-greptile", "coderabbit": "org-coderabbit1", "qodo": "org-qodo1",
 "claude-plain": "org-claudplain", "claude-custom_skill": "org-claudeskill"}
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

Setup adds a git remote named after the owner to each product clone and pushes eval branches there; `origin` stays `srajat-leap`. The target owner's `main` must have the same files as `srajat-leap` `main` (the Claude orgs also carry `.github/workflows/claude-review.yml`), or the patches will not apply. In the two Claude orgs `main` is a single root commit with that tree, so no history (and no deleted `TRAPS.md`) is reachable from a checkout.

To add a tool, register it and add its owner to `owners.json`. A unit test fails if a registered tool has no owner.

## Run every reviewer at once

```bash
python3 -m prlab_eval all --setup --trigger --wait 900 --judge-provider groq --cleanup
```

This runs the whole estate end to end. Each tool is looked up in `owners.json`, so
every vendor is set up, triggered and scored in its own owner, and no `--owner` is needed.

Before anything is opened or scored, `all` audits every owner for leaks
(`src/prlab_eval/audit.py`): the answer-key repo in the org, `TRAPS.md` reachable from any
branch or PR ref, `trap/*` branches, PR bodies citing the answer key, PR files outside the
patch, and a Claude checkout deeper than `fetch-depth: 1`. If any owner fails, `all` stops
with the fix for each finding. `--skip-audit` runs anyway, and says so. An org listed in
`cases/audit_exempt.json` is not audited; its tools are named as unaudited in the output.

Because each vendor lives in a different owner, this is a sequence of single-tool runs,
not one pytest session. Setup and trigger happen for every tool first, so auto-review
vendors and Action workflows are already posting while the rest are still being prepared;
scoring comes last. A reviewer that misses a trap makes pytest exit 1, which is a result
and not a breakage, so the sequence keeps going; only a usage or judge error stops a run,
and that tool is marked `did not score` in the summary.

Each tool still writes its own `reports/<tool>/` files, exactly as a single-tool run does.
At the end the combined standings are printed, best F1 first:

```
TOOL                 OWNER              PASSED   P      R      F1     REPORT
claude-custom_skill  org-claudeskill    13/14    100%   93%    97%    review-eval-claude-custom_skill-…json
greptile             org-greptile       13/14    77%    93%    85%    review-eval-greptile-…json
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `--tools greptile,qodo` | only these reviewers (default: every registered tool) |
| `--only case-a,case-b` | only these cases; unknown ids fail before anything runs |
| `--setup` / `--trigger` | open the PRs / mention each tool, before scoring |
| `--fast` | keyword judge, no LLM key |
| `--cleanup` | close each tool's PRs once it has scored |
| `--dry-run` | print the per-tool pytest commands and stop (no audit, no GitHub calls) |
| `--skip-audit` | run even if an owner fails the leak audit; scores may be inflated |

If the PRs are already open and reviewed, drop `--setup --trigger` and just score:

```bash
python3 -m prlab_eval all --judge-provider groq
```

`all` refreshes `reports/comparison.json` when it finishes.

## Rescore saved reports

A scoring change does not need the PRs, GitHub, or the vendors: every report keeps the comments the tool posted.

```bash
python3 -m prlab_eval rescore --judge-provider groq             # latest run per tool
python3 -m prlab_eval rescore --all-runs --judge-provider groq  # every run in each tool folder
python3 -m prlab_eval rescore --judge-provider bifrost --rejudge   # a new judge re-judges every claim
python3 -m prlab_eval rescore --report reports/qodo/<file>.json # one file
```

Rescoring re-checks claims the quote check rejected (offline), rates every comment for precision, and writes a new `…-rescored-<stamp>` report beside the source. The source file is not touched. The new report names its source (`rescored_from`) and keeps its `run_at`, so "latest run" still means the latest collected reviews, with the rescored copy preferred.

## Cost

`cases/pricing.json` holds each reviewer's cheapest paid plan that includes PR review (list price, with
its source and the date it was checked) and, for the Claude reviewers, the measured API cost of the
review runs behind the published results. The comparison page shows per review, per developer per
month (at the assumed PRs per developer) and the cost of the 23-PR eval. The per-run Claude costs
are in `cases/costs/claude-runs.json`: `total_cost_usd` from each GitHub Actions log, which Claude Code
computes at list price (checked against the published Sonnet 5.5 rates to the cent). Recheck prices
before quoting them; vendors change plans often.

## Comparison data

```bash
python3 -m prlab_eval comparison
```

Reads the latest run per tool from `reports/` and writes `reports/comparison.json`. `--out PATH` writes somewhere else.

`all` and `rescore` run this for you. The reports the published comparison uses (latest run per tool, for each
judge) are committed, so a fresh clone shows the same page. New reports are gitignored, so your own runs stay
local and appear beside the published ones; to publish a new set, `git add -f` the files the page now picks.

## Live comparison page (browser)

`viewer/index.html` renders the comparison, fed by `canvas.build_payload()`: latest run per tool, `superseded/` ignored.

```bash
python3 -m prlab_eval.live            # http://127.0.0.1:8765  (--port, --host, --open, --reports DIR)
```

The server scans `reports/` every second and pushes an update over server-sent events whenever a report is added, changed or removed, so the page follows new runs, rescores and moves without a reload. The page keeps the chosen case and reviewers in the browser.

- `?case=<case-id>` opens one case (shareable link).
- **Judge** dropdown: every judge's scores are kept. A rescore with a new judge writes a new report
  beside the old one, and the page shows one judge's set at a time (`?judge=<label>` opens one).
  A case a tool has no result for shows as "not run", and the standings say how many cases each tool ran.
- `?theme=dark|light` forces a theme; the **theme** button toggles and remembers it.
- `?once` loads a single snapshot without the live stream (printing, screenshots).

Standard library only; binds to 127.0.0.1.

## Add a tool

1. Create `src/prlab_eval/tools/<name>.py` with `name`, `bot_logins`, `trigger_body`, `collect`, and `trigger`.
2. Register it in `src/prlab_eval/tools/__init__.py` and give it an owner in `cases/owners.json`.
3. Run `pytest tests/eval --run-eval --tool <name>`.

`tools/github_review.py` fetches the three comment streams, parses nested `<details>`, and dedupes. Add a captured-comment fixture under `tests/unit/fixtures/` and test `collect` against it.

Cases do not change.

## Isolation

The runner account may post the tool's trigger comment. Unexpected `[bot]` logins fail isolation unless listed in `--allow-bots`. Leave Cursor and other reviewers off a run until you opt them in.

Isolation is also structural: each tool gets its own GitHub org, so no product can see another's repos or comment on another's PRs. `estate/` provisions those orgs, and `estate/README.md` records what the estate must never hand a reviewer — an answer key, a PR body that names the finding, or git history holding a deleted one.

## Product remotes

`srajat-leap/prlab-cricket-*`. This harness is `srajat-leap/prlab-review-tests`.
