# Hotpath

**An AI agent that makes your code faster and proves every change is correct.**

Point Hotpath at a repository. It profiles the code, asks a planner model for optimization
hypotheses, has worker models write each one as a patch, and then runs every patch through a
harness the models cannot touch: an isolated git worktree, the locked test suite, and a
noise-aware benchmark. A change is kept only when it is both correct and faster by more than
the measured noise. Everything else is recorded with the reason it was rejected.

> Most AI coding tools generate code. Hotpath generates evidence.

```
profile ─▶ plan ─▶ generate patches ─▶ verify correctness ─▶ benchmark ─▶ accept / reject ─▶ repeat
          (AI)        (AI)              (harness)             (harness)     (harness)
```

## Quick start (no API keys needed)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run the full loop on the bundled slow demo repo using the offline mock provider.
hotpath run configs/demo_repo.yaml

# Open the dashboard (and start further runs from the UI).
hotpath serve configs/demo_repo.yaml      # http://127.0.0.1:8765

# Re-measure the accepted chain with each change removed.
hotpath ablate configs/demo_repo.yaml

pytest -q
```

The bundled demo configs explicitly select `execution.backend: local` because the
bundled target is trusted and is intended to run as a quick smoke test. For arbitrary
repositories, use the default Docker backend with a reviewed, pinned runner image. See
[`docs/DEMO.md`](docs/DEMO.md) and [`docs/ISOLATION.md`](docs/ISOLATION.md).

The Docker-backed CPU demo run `run_59dd5582d2` on 2026-09-18 accepted one measured
change at 1.363× and rejected another for failing correctness. The run records its
runner image ID and is not a Dryft or H100 benchmark. The dashboard API accepts
local clients only; start the server with the operator-selected config.

The mock provider replays recorded candidate patches. It replaces the **model**, not the
verification: every patch still goes through the real worktree, tests, and benchmark. Several
of the recorded patches are deliberately wrong, useless, or aimed at the test file, so an
offline run exercises the whole accept/reject range with real measured numbers.

A historical offline run on `demo_repo` (numbers were measured on that machine and vary by run):

| # | Hypothesis | Verdict |
|---|---|---|
| 1 | Loosen the top_k tie check (edits `tests/check.py`) | **locked_file**: blocked before generation |
| 2 | Track seen items in a set inside `dedupe_preserve_order` | **accepted**: ~1.3x vs parent |
| 3 | Count frequencies in one pass with case folding | **rejected_correctness**: output changed |
| 4 | Hoist `min()` out of the `top_k` loop header | **rejected_speed**: inside the noise band |
| 5 | Replace `top_k` with `heapq.nlargest` | **patch_failed**: worker hallucinated the current code → **retry** with the error fed back applies cleanly and passes correctness, then **rejected_speed**: 95% CI includes 1.0 (top_k is a tiny slice of the pipeline) |
| 6 | Count frequencies with `collections.Counter` | **accepted**: ~35x vs parent |
| 7 | `top_k` as `sorted(...)[:k]` | **rejected_speed**: measured slower for k=10 |

Final: ~45x end to end, 2 candidates kept. Candidate 5 shows two guardrails at once — the
retry-with-feedback loop recovering a failed patch, and the bootstrap CI rejecting a change that
*looks* ~4% faster but is statistically indistinguishable from noise. The ablation table shows what
each kept change contributed.

## With real models

```bash
export OPENAI_API_KEY=sk-...
hotpath run configs/demo_repo_openai.yaml
# or override on the command line
hotpath run configs/demo_repo.yaml --provider openai
```

The planner and worker are separate OpenAI roles. The provider also accepts an
OpenAI-compatible worker URL through `provider.worker_base_url`, but this repository does
not claim a live validation of any third-party endpoint. The pattern is "big model plans,
fast model explores": one planner call per iteration, N worker calls in parallel.

### Sentry setup

The complete setup and validation boundary is in [`docs/SENTRY.md`](docs/SENTRY.md).
The repository tests use an in-memory transport; live ingestion and visibility still
require a DSN and a check in the target Sentry project.

Paste your **Python/FastAPI project's DSN** into `SENTRY_DSN=` in the root `.env`.
Hotpath loads this file automatically from the launch directory, without overriding exported
environment variables. Restart the server after editing it:

```powershell
.\.venv\Scripts\Activate.ps1
hotpath serve configs/demo_repo_openai.yaml
```

Check `http://127.0.0.1:8765/api/observability` for `"enabled": true`, then open
`http://127.0.0.1:8765/sentry-debug`. **HTTP 500 is intentional:** this creates a test error,
an HTTP transaction with a verification span, info/warning/error logs, and counter/gauge/
distribution metrics. Look for the error in Issues, the request in Traces, and
`Hotpath Sentry verification` in Logs. Delivery can take a few moments. The debug endpoint
is available only from loopback in `HOTPATH_ENV=development`; without a DSN it returns 503.

The SDK initializes before FastAPI, captures unhandled errors, and enables logs, metrics,
and continuous profiling with `profile_lifecycle="trace"`. Each optimization run is a
transaction with spans for baseline, profiling, planning, worker calls, patching, correctness,
and benchmarking. Planner rationale, worker explanations, and run/experiment logs include
their IDs. Final verdict metrics are emitted **after beam selection** so a faster sibling
is not incorrectly counted as accepted. Model token usage remains attached to model spans.
CLI exit and server shutdown flush pending telemetry.

Optional settings in `.env` (defaults shown):

```dotenv
HOTPATH_ENV=development
SENTRY_TRACES_SAMPLE_RATE=1.0
SENTRY_PROFILE_SESSION_SAMPLE_RATE=1.0
SENTRY_SEND_DEFAULT_PII=false
# SENTRY_RELEASE=hotpath@my-build
```

Traces and profiling sessions are sampled at 100% for the demo. Sentry profiles the **Hotpath
process**; target subprocess profiling still uses cProfile/torch.profiler. This is not GPU
kernel profiling. Request bodies and frame-local variables are excluded, and configured
credentials are filtered from errors, transactions, logs, and metrics. Rationale and
diagnostic text are sent to Sentry. Leaving the DSN empty keeps telemetry disabled.

Metrics include `hotpath.runs`, `hotpath.experiments`, `hotpath.best_speedup`,
`hotpath.baseline_noise_cv`, `hotpath.stage.duration`, `hotpath.operation.duration`, and
`hotpath.experiment.speedup`. Offline SDK tests use an in-memory transport; actual ingestion
and profile visibility must be checked in your Sentry project after adding its DSN.

## How the decision is made

Hotpath never accepts a change because a model says it is faster. `hotpath/benchmark.py`:

1. **Baseline noise.** The untouched code is benchmarked `baseline_repeats` times. The
   coefficient of variation of those medians is the noise floor.
2. **Threshold.** `max(min_speedup, 1 + noise_multiplier × noise)`. On a quiet machine the floor
   (3%) applies; on a noisy one the bar rises automatically.
3. **Point estimate.** `speedup = parent_median / candidate_median` (inverted for higher-is-better
   metrics such as tokens/sec).
4. **Bootstrap CI.** 2,000 resamples of both sample sets; the 95% interval of the median ratio
   must exclude 1.0.
5. **Quiet machine.** Benchmarks take an exclusive lock: nothing else (including sibling
   experiments' tests) runs while a benchmark is in flight. Set `benchmark.exclusive: false`
   for GPU targets where tests and benchmarks use different resources.

The benchmark command itself (via `hotpath.benchlib`) handles warmup, GC control, fixed seeds,
repeated trials, and for GPU code CUDA events with synchronization. Profiling is a separate
command and never shares a process with the benchmark.

Correctness is a plain exit code from the locked `test_cmd`. For the transformer target that
means greedy tokens must match a frozen reference model exactly and logits must stay within
tolerance. Locked paths are enforced in `hotpath/workspace.py` before a byte is written; the
planner is also told about them, but the code is the guarantee.

## Configuration

```yaml
name: demo_repo
target: ../demo_repo
test_cmd: python tests/check.py         # exit 0 = correct
bench_cmd: python bench.py              # prints {"samples": [...], "metric": "seconds"}
profile_cmd: python hotprofile.py       # prints {"hotspots": [...]}
editable: ["*.py"]                      # what the agent may change
locked: ["tests/*", "bench.py", "hotprofile.py", "data.py"]
benchmark: {min_speedup: 1.03, noise_multiplier: 2.0, baseline_repeats: 3, exclusive: true}
search: {iterations: 3, candidates_per_iteration: 3, max_parallel_tests: 2, max_parallel_workers: 4}
profile: {retain: 40}                   # hotspots stored for the diff; the planner still sees context.max_hotspots
provider: {planner: mock, worker: mock, mock_patches_dir: ../demo_repo/mock_patches}
```

Any repository with a test command and a benchmark command works. Use `hotpath.benchlib.run`
(or `torch_run`) and `hotpath.profilelib.run` (or `torch_run`) inside the target, or print the
JSON lines yourself. See `configs/torch_transformer.yaml` for the GPU shape.

## Repository layout

```
hotpath/
  schema.py         Pydantic models: config, experiment tree, model I/O, measurements   (shared)
  workspace.py      git worktrees, locked-path enforcement, search/replace patching     (harness)
  runner.py         async subprocesses with hard timeouts                              (harness)
  benchmark.py      parsing, stats, bootstrap CI, the accept/reject decision            (harness)
  profiler.py       profile JSON -> compact summary                                     (harness)
  harness.py        run_experiment: patch -> test -> bench -> decision; QuietLock       (harness)
  ablation.py       leave-one-out re-measurement of the accepted chain                  (harness)
  context.py        AST-based retrieval of hotspot functions for the planner            (agent)
  agent.py          planner + parallel workers                                          (agent)
  providers/        Provider protocol; openai (structured outputs) and mock (replay)    (agent)
  orchestrator.py   the search loop and run state                                       (both)
  store.py          SQLite persistence                                                  (both)
  observability.py  Sentry spans/transactions, no-op without a DSN                        (both)
  profilediff.py    before/after bottleneck diff over two ProfileSummary records          (agent)
  benchlib.py       helpers for target benchmark scripts (perf_counter / CUDA events)
  profilelib.py     helpers for target profile scripts (cProfile / torch.profiler)
  cli.py            hotpath run | serve | ablate
server/             FastAPI API + single-file dashboard, reads the same SQLite store
  views.py          derived view models: tree layout, retry links, beam membership, chart series
demo_repo/          slow Python target + locked tests + recorded mock patches
targets/torch_transformer/   Dryft-style GPU target (tokens/sec, frozen reference model)
configs/            demo_repo.yaml, demo_repo_openai.yaml, torch_transformer.yaml
tests/              Tests cover: patch isolation, benchmark decision, every failure mode,
                    state persistence, the full loop, the API, Sentry envelopes,
                    the bottleneck diff's refusals and bounds, tree/chart derivations
```

## Working on it as two people

The contracts in `schema.py` are the seam. One person owns the harness side, the other the
agent and product side; neither waits on the other because the mock provider stands in for
models and the tests/ fixtures stand in for a target.

- **Harness owner**: `workspace.py`, `runner.py`, `benchmark.py`, `profiler.py`, `harness.py`,
  `ablation.py`, `benchlib.py`, `profilelib.py`, and the targets. Success = every failure mode
  becomes a structured status, every number is defensible.
- **Agent/product owner**: `context.py`, `agent.py`, `providers/`, `store.py`, `server/`,
  `observability.py`, `profilediff.py`. Success = the planner gets compact, relevant context; the
  dashboard shows exactly what happened and why, and never claims more than was measured.
- **Shared, change together**: `schema.py`, `orchestrator.py`, `configs/`.

## What is real and what is not

The repeatable evidence in this repository is the offline mock loop and automated tests.
Live OpenAI calls require an operator key. No Baseten endpoint, real Dryft model swap,
H100 run, or particular GPU performance result is claimed here. Use a dedicated Linux
GPU host and the Docker boundary for hostile GPU targets; see [`docs/DEMO.md`](docs/DEMO.md)
and [`docs/ISOLATION.md`](docs/ISOLATION.md).

- Every number in the dashboard is read from SQLite rows written by the harness. There are no
  placeholder values or hardcoded success states.
- The offline demo's *candidate patches* are recorded; their *verdicts* are measured live.
- `targets/torch_transformer` has historical local GPU audit results. The current Docker
  runner and H100 template have not been validated on an H100; run their tests and
  benchmark there before reporting a GPU speedup.
- The OpenAI provider uses the `beta.chat.completions.parse` structured-output API.
  Historical live calls are documented in `AUDIT.md`; the current worker changes need
  a fresh live check with an operator key.

## Beyond the MVP (now built)

- **Retry with feedback.** A patch that fails to apply or fails correctness is re-sent to the
  worker once with the failure fed back, recorded as its own experiment. `search.max_patch_retries`
  (default 1; 0 disables).
- **Interleaved parent re-benchmark.** `benchmark.rebenchmark_parent: true` re-measures the parent
  next to each candidate so machine drift between iterations cannot bias the decision.
- **Beam search.** `search.beam_width` (or `--beam N`) keeps and expands the top-N accepted heads
  each iteration instead of only the best child. Width 1 is the original greedy search.
- **Planner outages and resume.** A timeout, rate limit, or 5xx from the planner is retried with
  backoff (`search.planner_retries`, `search.planner_retry_backoff_s`); a failure that persists skips
  that head for one iteration instead of ending the run. After 3 consecutive iterations with no
  successful plan the run fails. `hotpath run <config> --resume <run_id>` then continues it from the
  stored beam without re-measuring the baseline. Resume refuses a run whose test, benchmark, profile,
  editable/locked, execution, or benchmark settings changed, because new candidates would be compared
  against incomparable measurements.
- **Profile view with self-vs-total time.** The dashboard shows each hotspot's self time nested
  inside its cumulative time, so functions whose callees dominate are visible.
- **Bottleneck diff (before to after).** `baseline_profile` vs `head_profile`, aligned per function on
  absolute self seconds. Profiles are stored top-N (`profile.retain`, default 40), so a function
  missing from the later profile is reported as a *bound* - "at most 0.004s, below the top 40" -
  never as a proven elimination. A tool mismatch (e.g. `torch.profiler` vs its CPU-time fallback), a
  failed profile, or a head that is still the baseline are refused with the reason shown instead of a
  plausible-looking table. The `unchanged` band is labelled a display threshold, because a profile is
  one observation and has no measured noise floor. Also in `hotpath export`'s `REPORT.md`.
- **Metric-aware chart.** Toggle between `speedup x` and the raw metric in its own units, so a GPU
  target shows tokens/sec climbing rather than only a ratio. Raw is the default when higher is better.
  The acceptance **noise band** is shaded, which makes a `rejected_speed` verdict visible rather than
  something you read: a dot inside the band is not distinguishable from baseline. Axes are never
  inverted and never zero-forced; the baseline is drawn as a labelled reference. CI whiskers appear
  only in speedup mode, where that bootstrap interval is exactly what was computed.
- **Retries and beam branches in the tree.** A retry links back to the attempt it replaces with a
  dashed edge and sits next to it, so "failed -> fed the error back -> accepted" reads as one unit, and
  the detail panel shows the failure text the worker was actually given. Beam heads are labelled with
  the iterations they were expanded at, and rows are grouped so branches do not interleave.
- **`hotpath export <config> <dest>`** writes a PR-ready bundle: the optimized source tree,
  `changes.patch` (baseline→head diff), and `REPORT.md` with the benchmark table and accepted
  chain. Add `--ablate` to include a leave-one-out ablation table with freshly paired full-stack
  benchmarks. It prints a ready `gh pr create`
  command; it never pushes on its own.
- **Pruning.** `hotpath ablate --prune` (or `export --prune`) drops every change the ablation found
  removable, *together*, rebuilds the stack without them, re-runs the locked tests, and benchmarks it
  beside the full stack. The pruned stack is kept only if it is correct and the full stack is not
  measurably faster, because changes that are removable one at a time can still matter jointly. The
  run's head is never modified; `export --prune` ships the pruned stack and says what was dropped.
- **Multi-file changes.** A hypothesis may touch up to 3 files (`target_file` plus `extra_files`),
  including new ones, such as a Triton kernel module and its call site. The target must already exist;
  extra files may be created (an edit with an empty `search`). Every file must match an editable
  pattern and none may be locked; the harness checks each path before writing anything, and new files
  are registered with git so they appear in the diff and in isolated execution.
- **Accepted vs shipped.** The dashboard funnel and `REPORT.md` count "accepted" (correct and faster
  than its parent) separately from "shipped" (in the final head's lineage). With beam search a
  correct, faster change can still miss the final head.
- **Verification contract on the dashboard.** The locked test, benchmark, and profile commands are
  shown with known credentials filtered and a digest to compare runs.

## Roadmap still open

- Ablation-driven pruning of the beam at the end of a run.
- True call-stack flame graphs. The bottleneck diff is a flat per-function view because that is all
  the profile stores: `profilelib.run` discards the `pstats` caller map and `torch_run` emits no
  stacks, so emitting caller edges is the prerequisite.
- Persist a profile per accepted experiment. `orchestrator` already profiles surviving beam nodes
  and discards the result, so any node could be diffed against baseline or its own parent for free.
- `hotpath export` opening the PR directly via `gh` when a remote is configured.
