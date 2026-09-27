# `hotpath go`: one command, from a repository to a verified pull request

```bash
git clone https://github.com/Nijjea1/hotpath && cd hotpath

hotpath.cmd https://github.com/you/your-repo        # Windows
./hotpath.sh https://github.com/you/your-repo       # macOS / Linux
```

The wrapper creates `.venv`, installs Hotpath into it, and runs `hotpath go`. Nothing else to set up.
`owner/repo`, any git URL, or a path to a local checkout work as the target too.

```
[1/8] Setup ......... Python 3.12 ✓  git ✓  gh ✓  keys ✓ (workers on Baseten)
[2/8] Fetch ......... cloned into workspaces/you__your-repo @ a1b2c3d, PR base main (you have push access)
[3/8] Assess ........ python (Python), Tier 1 · tests: `python -m pytest -q` · no benchmark (one will be generated)
[4/8] Baseline ...... local · 412 passed · 3/3 runs green, not flaky · 38.2s each
[5/8] Benchmark ..... generated: parses 5,000 records through parse_records and rollup
[6/8] Configure ..... setup commit 9f0c11ab on hotpath-setup/20260920-101500
[7/8] Optimize ...... 11 candidates · 2 accepted · 3.41x vs baseline
[8/8] Publish ....... https://github.com/you/your-repo/pull/42 (draft)
```

Every stage either finishes or stops with the reason and what to do about it.

## Before you spend anything: `hotpath check`

```bash
hotpath check https://github.com/you/your-repo     # or a local path
```

A read-only look that takes about a second and never runs the repository's code. It reports what
would stop a run, suggests the flag that avoids it, and prices the run. Exit code is 0 for go, 1 for
caution, 2 for stop, so it can gate a script.

It exists because the guided flow's gates all fire *after* the expensive part — cloning, building a
virtualenv, running the suite three times — and the four things that stopped the first live runs were
all visible in the source the whole time:

| What it finds | Why it matters |
|---|---|
| Property tests without `deadline=None` | Hypothesis fails a test that runs slower than its deadline, so the suite goes red when the machine is busy — and a benchmark keeps it busy. A correctness check that is itself a timing measurement cannot decide a timing experiment. |
| Tests behind an optional marker (`external`, `network`, …) | They need libraries that are not installed, and a red baseline stops the run. The suggested `--test-cmd` deselects them. |
| A module past the source ceiling | A worker is shown a whole file and refuses one it cannot fit, so no patch to that file is ever written. |
| A benchmark that will be coarse, or absent | Whether "faster" will be a workload over one hot function or the whole suite's runtime, which raises the bar a lot. |
| `pytest-randomly`, compiled extensions | Randomised order reads as flakiness; work happening in C means the Python you may edit is a wrapper. |

## What each stage does

**1. Setup.** Checks Python ≥3.11 and git; finds how to open a PR (`gh` → `GITHUB_TOKEN` → a pre-filled
link); checks whether Docker is running; asks for `OPENAI_API_KEY` once and saves it to a git-ignored
`.env` (Baseten workers are used automatically when `BASETEN_API_KEY` is set). `--provider mock` runs
offline with recorded patches and needs no keys.

**2. Fetch.** Clones into `workspaces/`, or uses a local checkout as-is (refusing a dirty one, and
restoring your branch afterwards). Records the default branch and the exact commit the run will measure.
With `gh` or a token it checks push access first, so you learn about a missing fork before the work, not
after it. It never pushes to the default branch.

**3. Assess.** Static and read-only; it never runs the repository's code. Reports the ecosystem, the test
command, an existing benchmark if there is one, which files the agent may edit, and which are locked.
`hotpath assess <path>` runs this on its own.

| Tier | Ecosystems | What you get |
|---|---|---|
| 1 | Python | Everything: profiling, a generated benchmark, patching |
| 2 | Node, Rust, Go | Correctness plus timing of an existing command; no profiling or generated benchmark yet |
| 0 | anything else | Stops, and says what is missing |

**4. Baseline.** Installs the dependencies — **never the project itself**, because Hotpath measures each
candidate from its own git worktree and an installed copy would shadow it — then runs the test suite
three times on the untouched code. Failing tests stop the run (there is nothing to prove a change
against). So do flaky tests, which would make Hotpath reject good changes at random.

**5. Benchmark.** A benchmark defines "faster", so it is chosen in this order and always shown to you for
approval:
0. **the benchmark this repository was measured against last time**, re-validated before use. Three runs
   against one repository once produced 1.47x, nothing, and 1.45x, because each asked a model for a
   fresh workload and got a different one — three numbers that were never comparable. The chosen
   workload is remembered outside the repository and reused, so a second run measures the same thing.
   Reuse is never blind: a remembered workload goes through the same validation as a new one and is
   discarded if the project has moved under it. `--no-reuse-benchmark` turns it off,
   `--regenerate-benchmark` forces a new one. Every run prints a **benchmark id**, and two runs with
   different ids measured different things;
1. the repository's own Hotpath benchmark;
2. **a generated one**: Hotpath profiles the test suite, asks the planner model to write a deterministic
   `workload()` over the hottest project functions, and validates it by running it — measurable but
   bounded runtime, quiet enough to detect a few-percent change, most of its time inside the project's
   own code, the same result in two fresh processes, and no imports from the test suite or mocking
   libraries. Failures are fed back and it tries again. If every attempt is usable but none is quiet
   enough, the least noisy one is kept and labelled: Hotpath's acceptance threshold rises with measured
   noise, so a noisy benchmark demands a bigger win rather than producing a wrong one;
3. an existing benchmark command, timed by `hotpath.benchwrap`;
4. the test suite's own runtime. Coarse, and the pull request says so.

**6. Configure.** Commits `.hotpath.yaml`, the benchmark files and the `hotpath-verify` CI check on a
`hotpath-setup/<timestamp>` branch, with a `Hotpath-Setup: 1` trailer. It is the pull request's first
commit, so a reviewer can see exactly what "correct" and "faster" meant. Afterwards those files are
locked: the CI check fails any later commit that touches them.

**7. Optimize.** The normal search. `--iterations`, `--candidates`, `--beam` size it; `--max-tokens` and
`--max-minutes` bound it (the search stops cleanly and keeps what it has proved). The live tree is served
at `http://127.0.0.1:8765` and opens in your browser on its own; `--no-dashboard` turns that off. If
nothing beats the noise floor, that is reported as a result and no PR is opened — and the dashboard is
where each rejection reason is written down, so it stays up in that case too.

**8. Publish.** One commit per verified change, each carrying the exact tree the harness tested, the
measured speedup and its confidence interval. A draft PR by default (`--ready` for ready-for-review),
after one confirmation (`--yes` skips it). `--no-pr` builds the branch locally instead. The pull request
opens in your browser, and the dashboard keeps serving until you press Ctrl-C:

```
[8/8] Publish ....... https://github.com/you/your-repo/pull/42 (draft)

done in 6m 12s.

  PR:        https://github.com/you/your-repo/pull/42
  dashboard: http://127.0.0.1:8765  (still running)

  Ctrl-C to stop the dashboard.
```

Without a terminal attached — a script, a CI job — nothing blocks: the run prints how to reopen the
dashboard and exits.

## Where the target's code runs

`--sandbox auto` (the default) uses Docker when it is running: a container built from **Hotpath's**
template, never the repository's Dockerfile, with dependencies installed at build time and no network
afterwards (see [ISOLATION.md](ISOLATION.md)). Otherwise it asks before running the code on your machine,
in a virtualenv under `workspaces/.venvs/`. `--sandbox local` or `--yes` is that consent; `--sandbox
docker` requires the container.

## Options worth knowing

| Option | Why |
|---|---|
| `--yes` | Accept every default, including pushing the PR branch. For unattended runs. |
| `--provider mock --mock-patches DIR` | Fully offline: replays recorded patches, still really verifies them |
| `--test-cmd`, `--bench-cmd` | Override detection (for example a focused subset of a slow suite) |
| `--no-generate` | Never ask a model to write a benchmark |
| `--max-tokens N`, `--max-minutes M` | Budgets. Tokens are counted from the models' own usage numbers |
| `--sandbox {auto,local,docker}` | Where candidate code runs |
| `--no-pr` | Build the branch locally, push nothing |
| `--resume` | Continue the last `go` run on that repository |
| `--no-reuse-benchmark` | Generate a fresh benchmark instead of reusing the remembered one (two runs then are not comparable) |
| `--regenerate-benchmark` | Write a new benchmark even if the remembered one still validates |
| `--no-dashboard` | Do not serve the live tree (it is served, and held open afterwards, by default) |
| `--no-open` | Serve the dashboard but open no browser tab, for the PR or for the dashboard |

## When it stops

| Situation | What it says |
|---|---|
| Unsupported ecosystem | which tiers exist, and what was missing |
| Failing or flaky baseline tests | the failing output, or how many of the runs passed |
| No usable benchmark | why each candidate was rejected (too fast, too noisy, non-deterministic, mostly outside your code) |
| No verified speedup | what was tried and why each was rejected; **no PR** |
| No push access | fork it and pass your fork's URL |
| Base branch moved during the run | the measurement no longer describes the merge; rerun or pass `--allow-moved-base` to `hotpath pr` |

## Safety

- Candidate code runs in a container, or locally only with explicit consent.
- The agent can never edit tests, benchmarks, CI, or `.hotpath.yaml`: enforced in code before a byte is
  written, re-checked before publishing, and re-checked again by the CI workflow on GitHub's machines.
- Keys live in a git-ignored `.env` and never enter containers, commits, or model prompts.
- Nothing is pushed without a confirmation, and never to the default branch.
