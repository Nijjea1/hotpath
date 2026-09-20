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
`--max-minutes` bound it (the search stops cleanly and keeps what it has proved). `--dashboard` serves the
live tree at `http://127.0.0.1:8765`. If nothing beats the noise floor, that is reported as a result and
no PR is opened.

**8. Publish.** One commit per verified change, each carrying the exact tree the harness tested, the
measured speedup and its confidence interval. A draft PR by default (`--ready` for ready-for-review),
after one confirmation (`--yes` skips it). `--no-pr` builds the branch locally instead.

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
