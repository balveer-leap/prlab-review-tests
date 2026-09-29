# estate — provision one GitHub org per review tool

Each review product gets its own org holding a copy of the 11-service cricket
estate. Isolation is by org, so no product can see another's repos or comment on
another's PRs.

Nothing here opens eval PRs. `prlab_eval setup` does that, and it is the only
supported way — see [Keeping the estate blind](#keeping-the-estate-blind).

| Script | When |
|---|---|
| `scripts/10-new-org-from-main.sh ORG` | every org, once: 11 repos, `main` as one history-free commit |
| `scripts/15-squash-main.sh ORG` | an org created before `10-` squashed on its own |
| `scripts/20-install-claude-workflow.sh ORG normal\|best` | Claude orgs only |
| `scripts/25-coderabbit-linked-repos.sh ORG` | CodeRabbit org only |
| `scripts/26-greptile-context-repos.sh ORG` | Greptile org only |
| `scripts/27-codeant-config.sh ORG` | CodeAnt org only |
| `scripts/28-monorepo.sh ORG [REPO]` | a single-repo copy of the estate, for a reviewer with no cross-repo feature |
| `scripts/90-audit-org.py ORG` (or `--all`) | any time; `prlab_eval all` runs the same audit itself |
| `scripts/05-delete-estate.sh ORG` | an org the audit says only fresh repos can fix (`CONFIRM=ORG`, needs `delete_repo`) |

`workflows/` holds the two Claude Code Action variants that
`20-install-claude-workflow.sh` pushes to `.github/workflows/claude-review.yml`.

## Before you start

- `gh auth login`, with rights to create repos in the target org.
- The 11 product repos cloned next to this repo as `cricket-*`. The scripts
  default to the directory holding this one; pass a second argument to override.

  ```bash
  cd ..
  for r in protocol scoring broadcast stats fantasy highlights \
           social archive notifications live-gateway mobile; do
    git clone -q https://github.com/srajat-leap/prlab-cricket-$r cricket-$r
  done
  cd prlab-review-tests
  ```

- One GitHub org per tool, created in the browser. The GitHub API cannot create
  orgs. Put each tool's org in `cases/owners.json`.
- A free judge key: [console.groq.com](https://console.groq.com) →
  `export GROQ_API_KEY=...`.

## Runbook

### 1. Create the org's estate

```bash
cd estate
DRY_RUN=1 ./scripts/10-new-org-from-main.sh org-yourtool   # preview
./scripts/10-new-org-from-main.sh org-yourtool
```

Creates the 11 repos and pushes each clone's `main` files as one root commit, with
nothing else: no history, no eval branches, no workflow, no review config. Repos
are private unless you set `VISIBILITY=public`. Re-running it fills in any missing repo and
leaves every existing `main` alone; `REPLACE_MAIN=1` replaces them, after which
re-run `20-`/`25-` and re-open that tool's eval PRs.

### 2. Install the review product — on that org only

| Tool | Install | Notes |
|---|---|---|
| greptile | Greptile app on the org (all repositories); then `./scripts/26-greptile-context-repos.sh ORG` | Cross-repo context is a `greptile.json` on each repo's `main` listing the other 10 repos under `context.repos`. Eval branches inherit it, so it never appears in a PR diff. |
| coderabbit | [app.coderabbit.ai](https://app.coderabbit.ai) → add the org → all repositories; then `./scripts/25-coderabbit-linked-repos.sh ORG` | 10 reviews per hour on the included plan, so a 14-PR run takes two batches. Linked repos need a paid plan or the trial. |
| qodo | [app.qodo.ai](https://app.qodo.ai) → add the org → all repositories | Qodo indexes sibling repos in the background. Wait about an hour after step 1 before opening PRs, or it reviews with no cross-repo context. |
| codeant | CodeAnt app on the org; then `./scripts/27-codeant-config.sh ORG` | `.codeant/configuration.json` turns on every analysis (SAST, secrets, SCA, IaC, …) and `.codeant/instructions.json` adds domain-neutral guidance (downstream impact, OWASP Top 10). Dashboard only, at app.codeant.ai/x/settings/ai-rules: the **Pentesting Agent** and each agent's reporting threshold. No cross-repo feature is documented. CodeAnt caches reviews by PR number: after re-creating a repo, close the eval PRs and open fresh ones. |
| codeant-monorepo | same app, same org; `CONFIG_FROM=prlab-cricket-scoring ./scripts/28-monorepo.sh ORG` | One repo, `prlab-cricket-estate`, holding every service as a folder, so a change and its consumers are in view together. `cases/owners.json` maps the tool to `ORG/prlab-cricket-estate`; that `owner/repo` form makes every case target the one repo and apply its patch under its service folder. |
| claude-plain | [github.com/apps/claude](https://github.com/apps/claude) on the org; `./scripts/20-install-claude-workflow.sh ORG normal` | Out of the box: one repo, one-line prompt. |
| claude-custom_skill | same app; `./scripts/20-install-claude-workflow.sh ORG best` | Clones the 10 sibling repos into `.estate/` (depth 1) before reviewing, then reviews with Read/Grep/Glob/LS only: no shell. |

Install each vendor on **one org only**. A vendor that can see two orgs reviews
both tools' PRs, and every case fails isolation.

Both Claude variants need an org secret. Run it yourself so the token never
passes through anyone else:

```bash
claude setup-token                                  # uses your Claude plan
gh secret set CLAUDE_CODE_OAUTH_TOKEN --org ORG --visibility all
```

If the Claude org's repos are private, the estate-aware variant also needs
`ESTATE_TOKEN` (a fine-grained PAT with Contents: read on the org). Without it the
sibling clones come back empty and the run silently becomes the out-of-the-box
one; the job log prints `SKIP … (clone failed …)` when that happens.

`pull_request` workflows run from the base branch, so the workflow must be on
`main` before any eval PR is opened.

### 3. Audit, run, audit again

Back in the repo root:

```bash
python3 -m prlab_eval all --setup --trigger --wait 900 --judge-provider groq
```

`all` audits every org first and stops if one fails, then opens each tool's PRs
in its own org, triggers the review, waits, and scores. A later `all` without
`--setup` audits again, so the open PRs are checked too (a PR that changes a file
its patch does not fails). Run the audit alone with
`python3 estate/scripts/90-audit-org.py --all`.

Reports land in `reports/<tool>/`. `python3 -m prlab_eval.live --open` serves the
comparison at <http://127.0.0.1:8765> and follows new runs.

A free Groq key allows about 200,000 tokens a day for `openai/gpt-oss-120b`,
roughly one full scoring run of every tool. Two scoring jobs on one key share that
budget and stall; use one key per job, or a paid tier.

## Keeping the estate blind

A review product must reach its verdict from the PR and the code, not from a
document that states the answer. These rules keep that true, and each has been
violated at least once. `90-audit-org.py` checks every one that can be checked
from GitHub.

**Eval PRs carry no hint.** PR bodies describe the product change only.
Branch names are the one open gap: several `eval/*` names still describe the
defect (`eval/scoring-leak-raw-ball`), and every reviewer sees the head branch.
Neutral names in `cases/cases.json` close it; the audit does not check names. An earlier generation used `trap/*` branches with bodies
ending in `See TRAPS.md`, which pointed every reviewer straight at the answer
key. Those PRs are not reproducible from this repo by design; `prlab_eval setup`
is the only path. A closed PR cannot be deleted, so an org that still has one
needs fresh repos.

**No answer key in the product repos.** A per-repo `TRAPS.md` and a README
naming each downstream consumer were removed in `8089e6d`.

**No history.** Deleting a file does not remove it from history, and vendors
index whole repos, so `main` in every org is one commit with no parents. For
the Claude Action, both workflows also pin `fetch-depth: 1`, and the estate-aware
variant clones its siblings with `--depth 1`. Do not raise either.

**Only the patch's files.** A PR must change exactly what its patch changes.
`prlab_eval setup` stages the patch alone (`git apply --index`), so a stray
`.DS_Store` in a clone cannot reach one tool's PR and not another's.

**The answer key lives elsewhere.** This harness repo holds every trap and every
expected finding. Never create it in an org a reviewer is installed on. It is
public today, so a reviewer that can reach the network could also fetch it:
neither Claude workflow allows a shell or web tools, and the audit fails any
workflow that gives Claude an unrestricted `Bash`.

**Never the source estate.** `05-`, `10-` and `15-` refuse the org every clone's
`origin` points at (`srajat-leap`): deleting or squashing it would remove what
each new org is copied from. Greptile has its own org (`org-greptile`) like every other tool.
`cases/audit_exempt.json` can still name an org to leave unaudited; it is empty.

**Docstrings are deliberate.** The product code does state its own invariants,
for example `stats/ledger.py` saying wickets must come from `snapshot.wickets`.
That is normal code a real service would carry, it is identical for every tool,
and it stays.

When adding a tool, check it cannot reach deleted files, sibling repos it was
not granted, or any document naming the expected finding. If it can, that is a
harness bug, not a better reviewer.
