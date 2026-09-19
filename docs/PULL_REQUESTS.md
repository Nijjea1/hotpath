# From a run to a pull request

Hotpath is meant to run inside your own repository and hand you a reviewable PR. This page covers
what it writes, what it refuses to publish, and how each piece was verified.

## Setup: `hotpath init`

```bash
cd my-repo
hotpath init            # interactive; -y accepts the detected defaults
```

| Written | Purpose |
|---|---|
| `.hotpath.yaml` | `test_cmd`, `bench_cmd`, optional `profile_cmd`, `editable`/`locked` globs, models, execution backend. `target: .` |
| `.github/workflows/hotpath-verify.yml` | On every PR: runs `test_cmd`. On `hotpath/*` PRs: fails if a changed path is locked or not editable. |
| `.gitignore` | `.hotpath/` (worktrees, the run database, saved PR descriptions) |
| `hotpath_bench.py` | Only if no benchmark was found: a scaffold to fill in |

Always locked, whatever you answer: tests (`tests/*`, `test_*.py`, `*_test.py`, `conftest.py`),
the scripts named by `test_cmd`/`bench_cmd`/`profile_cmd`, `setup.py`, `.github/*`, and `.hotpath.yaml`.
Add more with `--lock <glob>`.

Models: OpenAI plans. Baseten writes patches when `BASETEN_API_KEY` is set, otherwise OpenAI does.
Keys come from the environment, `./.env`, or `~/.hotpath/.env` (override with `HOTPATH_HOME`).
Execution defaults to Docker only when the `hotpath-runner:local` image exists. Otherwise it is
`local` and `init` says so.

## Publishing: `hotpath run --pr` / `hotpath pr` / dashboard button

1. **Checks** (nothing is written if any fail): the run is `finished` or `stopped`, has an
   accepted head, every change in the head lineage is an accepted significant experiment whose
   commit exists and sits on its recorded parent, and every path in the shipped diff passes the
   run's own editable/locked rules.
2. **Branch** `hotpath/<run id>`, built with `git commit-tree`. This is plumbing only: your
   checkout, index, and current branch are never touched. There is one commit per shipped change,
   carrying the exact tree that was tested and benchmarked. The rebuilt head's tree is compared with
   the verified tree before the ref is written. Authors come from your `git config user.*`. Dates
   come from when each change was verified, so rebuilding a run gives the same SHAs.
3. **Base check**: the PR targets the branch the run measured (recorded as `base_branch`), or
   `--base`. If that branch on the remote is no longer at the measured commit, publishing refuses:
   the speedup describes the old base. `--allow-moved-base` overrides this, and CI then re-runs the
   locked check on the merge.
4. **Push** with your own git credentials (`GIT_TERMINAL_PROMPT=0`, so nothing hangs). Repository
   hooks do not run.
5. **Open or update the PR**, keyed on the head branch so a second publish edits the same PR:
   `gh` (if logged in) → `GITHUB_TOKEN`/`GH_TOKEN` (REST API) → a pre-filled compare link. The
   description is also saved to `.hotpath/prs/<run id>.md`. `--pr-method` forces one method.
6. The outcome (`branch`, `base`, `head_sha`, `method`, `url`/`number` or `compare_url`) is stored on
   the run as `pull_request`. Runs stored before this field existed still load.

Experiment commits now live under `refs/hotpath/experiments/*` instead of dozens of
`hotpath/exp_*` branches, so `git branch` stays clean.

## Demo repository

```bash
python scripts/make_demo_repo.py ../hotpath-demo-analytics
cd ../hotpath-demo-analytics
gh repo create <owner>/hotpath-demo-analytics --public --source . --push
hotpath run --pr --provider mock     # offline replay; drop --provider for real models
```

## What was verified, and where

- `tests/test_pr.py` runs a real optimization (mock model, real harness) and publishes it to bare
  git remotes and a fake GitHub API. It covers: commit trees equal the verified trees, the message
  contents, authorship, deterministic rebuilds, an untouched checkout, refusal when the base moved,
  missing or invalid remotes, non-publishable runs, locked paths in the diff, PR create-then-update
  with no duplicate, the no-credentials link, GitHub errors without the token, legacy runs, and the
  dashboard endpoint (including non-local and non-JSON requests).
- `tests/test_github.py`: remote URL parsing, link length limits, the method ladder, and the `gh` adapter.
- `tests/test_init.py`: detection, the written files, refusing to overwrite, and executing the
  generated workflow's scope check against real commits.
- `tests/test_workspace.py`: edits keep each file's line endings and UTF-8. Before this, an edit
  on Windows rewrote every LF line as CRLF, so every PR was a whole-file diff.
- By hand (2026-09-19): `make_demo_repo.py` → `hotpath run --pr --provider mock` from a
  subdirectory → a 2-commit branch pushed (a 5+/5- diff to `slowlib.py`) → a fresh clone of that
  branch passes `tests/check.py` and the workflow's scope check → the dashboard **Create PR**
  button republishes it.

**Not verified here:** opening a PR on github.com itself (no `gh` or token on this machine) and the
workflow running on GitHub's hosted runners.
