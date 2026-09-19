# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What Hotpath is

An AI agent that makes code faster **and proves every change is correct**. Point it at a repo;
it profiles the code, asks a planner model for optimization hypotheses, has worker models write
each one as a patch, then runs every patch through a harness the models cannot touch — an
isolated git worktree, the locked test suite, and a noise-aware benchmark. A change is kept only
when it is both correct and faster by more than the measured noise. Everything else is recorded
with the reason it was rejected.

> Most AI coding tools generate code. Hotpath generates evidence.

```
profile ─▶ plan ─▶ generate patches ─▶ verify correctness ─▶ benchmark ─▶ accept / reject ─▶ repeat
          (AI)        (AI)              (harness)             (harness)     (harness)
```

The core invariant: **Hotpath never accepts a change because a model says it is faster.**
Correctness is a plain exit code from the locked `test_cmd`; speed is a bootstrap-CI decision
over repeated benchmark trials against a machine-measured noise floor.

## Commands

```bash
pip install -e ".[dev]"        # or: make install

hotpath run configs/demo_repo.yaml          # full loop, offline mock provider (no API keys)
hotpath serve configs/demo_repo.yaml        # dashboard at http://127.0.0.1:8765
hotpath ablate configs/demo_repo.yaml       # leave-one-out re-measure of the accepted chain
hotpath export configs/demo_repo.yaml out/  # PR bundle: optimized tree + changes.patch + REPORT.md
hotpath run configs/demo_repo_beam.yaml     # offline run exercising beam branching + a retry chain

pytest -q                                   # make test
```

Useful `run` flags: `--iterations N`, `--beam N` (beam width; 1 = greedy), `--provider {mock,openai}`
(overrides both planner and worker), `--export <dir>` (write the best accepted source tree out),
`--resume <run_id>` (continue a stopped or failed run from its stored beam; refuses if measurement
settings changed), `--autocommit` (snapshot uncommitted target changes into a commit; the default refuses).
`serve` takes an optional config plus `--db/--host/--port`; `--db` without a config is read-only.
`ablate` takes `--run-id` (defaults to latest), `--json`, and `--prune` (drop removable changes
together, re-verify, keep only if not measurably slower). `export` takes a `dest`, `--run-id`,
`--ablate` (include ablation table), and `--prune` (ship the verified pruned stack).

Dryft configs: `configs/dryft_local.yaml` (trusted host, for a throwaway GPU VM) and
`configs/dryft_h100.yaml` (Docker); a test keeps them identical apart from `execution`.

With real models: `export OPENAI_API_KEY=sk-...` then use `configs/demo_repo_openai.yaml` or
`--provider openai`. The planner and worker use separate OpenAI roles. Workers can point at
an OpenAI-compatible endpoint via `provider.worker_base_url` / `provider.worker_api_key_env`,
but no third-party endpoint is validated here. Optional Sentry via `SENTRY_DSN`; see
`docs/SENTRY.md`.

Execution uses Docker by default. The bundled demo configs explicitly opt into
`execution.backend: local` as a trusted smoke test. Arbitrary repositories require a
reviewed, pinned runner image; see `docs/ISOLATION.md` and `docs/DEMO.md`.

## Architecture

Two sides meet at the Pydantic contracts in `hotpath/schema.py`. The design intent is that the
harness side and the agent/product side can be developed independently — the mock provider stands
in for models, the `demo_repo`/tests fixtures stand in for a target.

**Harness (models cannot touch this — it is the guarantee):**
- `workspace.py` — git worktrees, locked-path enforcement (checked *before* a byte is written),
  search/replace patching
- `runner.py` — async subprocesses with hard timeouts
- `benchmark.py` — parsing, stats, bootstrap CI, the accept/reject decision
- `profiler.py` — profile JSON → compact summary
- `harness.py` — `run_experiment`: patch → test → bench → decision; the `QuietLock`
- `ablation.py` — leave-one-out re-measurement of the accepted chain
- `benchlib.py` / `profilelib.py` — helpers targets import (perf_counter / CUDA events; cProfile / torch.profiler)

**Agent / product:**
- `context.py` — AST-based retrieval of hotspot functions for the planner
- `agent.py` — planner + parallel workers
- `providers/` — `base.py` (Provider protocol), `openai_provider.py` (structured outputs),
  `mock.py` (replay), `prompts.py`
- `store.py` — SQLite persistence
- `profilediff.py` — before/after bottleneck diff; bounds absences instead of claiming eliminations
- `server/app.py` + `server/static/index.html` — FastAPI API + single-file dashboard (reads the same store)
- `server/views.py` — derived view models (tree layout, retry links, beam membership, chart series).
  Derivations live here, not in the dashboard's inline `<script>`, so they can be unit-tested.
- `observability.py` — Sentry spans/transactions, no-op without a DSN

**Shared, change together:** `schema.py`, `orchestrator.py` (the search loop + run state), `configs/`.

## The accept/reject decision (benchmark.py)

1. **Baseline noise** — untouched code benchmarked `baseline_repeats` times; CoV of medians is the noise floor.
2. **Threshold** — `max(min_speedup, 1 + noise_multiplier × noise)`. Floor is 3% on a quiet machine; rises automatically on a noisy one.
3. **Point estimate** — `parent_median / candidate_median` (inverted for higher-is-better metrics like tokens/sec).
4. **Bootstrap CI** — 2,000 resamples; the 95% interval of the median ratio must exclude 1.0.
5. **Quiet machine** — benchmarks take an exclusive lock (`QuietLock`); nothing else runs during a benchmark. Set `benchmark.exclusive: false` for GPU targets where tests and benchmarks use different resources.

Verdict statuses to know: `locked_file`, `accepted`, `rejected_correctness`, `rejected_speed`, `patch_failed`.

## Config shape

```yaml
name: demo_repo
target: ../demo_repo
test_cmd: python tests/check.py         # exit 0 = correct
bench_cmd: python bench.py              # prints {"samples": [...], "metric": "seconds"}
profile_cmd: python hotprofile.py       # prints {"hotspots": [...]}
editable: ["*.py"]
locked: ["tests/*", "bench.py", "hotprofile.py", "data.py"]
benchmark: {min_speedup: 1.03, noise_multiplier: 2.0, baseline_repeats: 3, exclusive: true}
search: {iterations: 3, candidates_per_iteration: 3, max_parallel_tests: 2, max_parallel_workers: 4}
profile: {retain: 40}                   # hotspots kept for the bottleneck diff (planner still sees context.max_hotspots)
provider: {planner: mock, worker: mock, mock_patches_dir: ../demo_repo/mock_patches}
```

Targets: `demo_repo/` (slow Python + locked tests + recorded mock patches) and
`targets/torch_transformer/` (GPU target: tokens/sec, frozen reference model).

## Current status

The current validation boundary is the offline mock loop and automated test suite. Live
OpenAI calls require an operator key. This repository makes no validation claim for a
Baseten endpoint, a real Dryft model swap, an H100 run, or performance on a particular
GPU. Sentry SDK setup and in-memory envelope construction are tested; delivery and
project visibility require a DSN and an operator check documented in `docs/SENTRY.md`.

The dated audit notes below preserve historical evidence and should not be read as a
current Dryft, Baseten, or dedicated-H100 result.

- Offline loop works end-to-end on `demo_repo` with the mock provider; ~45x on a typical run, 2 of 7 candidates kept. The mock replaces the **model**, not the verification — every patch still goes through the real worktree, tests, and benchmark, and several recorded patches are deliberately wrong to exercise the reject paths.
- Tests cover patch isolation, benchmark decision, every failure mode, state persistence, the full loop, the API, retry-with-feedback, interleaved re-benchmark, beam search, the export bundle, Sentry envelopes via an in-memory transport, the bottleneck diff's refusals/bounds, and the tree/chart derivations.
- Post-MVP features shipped (see `AUDIT.md` / README): retry-with-feedback (`search.max_patch_retries`), interleaved parent re-benchmark (`benchmark.rebenchmark_parent`), beam search (`search.beam_width` / `--beam`), a self-vs-total profile view, and `hotpath export`. `beam_width=1` is the original greedy loop, so these are additive.
- Every dashboard number is read from SQLite rows written by the harness — no placeholders or hardcoded success states.
- **Verified live (2026-09-17 audit, see `AUDIT.md`):** real models (`gpt-4.1` / `gpt-4.1-mini`) on the demo repo (~77×) and on the GPU transformer target (RTX 5060, torch 2.11+cu128) with accepted correctness-proven speedups. Three bugs fixed in the process — a Windows timeout crash (`runner.py`), an all-zeros GPU profile (`profilelib.py`), and the agent reading stale source across iterations (`orchestrator.py`/`workspace.py`).
- **Sentry verified live:** the DSN loads from `.env`, `/api/observability` reports enabled, `/sentry-debug` returns its intentional 500, and the SDK delivers error/transaction/log/metric/profile envelopes with no transport errors. Visibility inside the Sentry UI is the one link that still needs a human check.
- **B1/B2 dashboard work shipped** (`PLAN.md` holds the design and its reasoning): the bottleneck diff, the metric-aware chart with a shaded noise band, retry rendering, and beam-aware tree layout. New endpoints: `/api/runs/{id}/tree`, `/api/runs/{id}/chart`, `/api/runs/{id}/profile_diff`.
- **Still unverified:** the Baseten worker endpoint and the real Dryft model swap.
- **GPU works on the primary dev laptop (re-verified 2026-09-19).** `nvidia-smi` shows the RTX 5060 Laptop GPU at bus `00000000:64:00.0`; `torch.cuda.is_available()` is `True`; `Get-PnpDevice -Class Display` reports it `Status: OK / CM_PROB_NONE`; and the offline GPU loop runs at ~1.5–2× on `configs/torch_transformer.yaml` (222 tests pass, 6 env-gated skips). **Correction:** the earlier "RTX 5060 absent (`CM_PROB_PHANTOM`)" note was a misattribution — on this machine the only `CM_PROB_PHANTOM` devices are two `USB3.0 5K Graphic Docking` adapters (a USB display dock that is simply unplugged), not the dGPU. `CM_PROB_PHANTOM` (Windows problem code 45) means a device is known to Windows but not currently on the bus; if it ever affects the actual GPU, see `docs/GPU_TROUBLESHOOTING.md`.

## Roadmap (post-MVP)

Interleaved parent re-benchmarking to kill cross-iteration drift · retry a failed patch once with
the failure fed back (`PatchRequest.previous_failure` is already plumbed) · beam search over the
tree instead of greedy best-child · flame-graph rendering · `hotpath export` that opens a PR with
diff + benchmark table + ablation.

## The bottleneck diff's honesty rules (profilediff.py)

These exist because a naive profile diff fabricates claims, which is the one thing this repo must not do.

1. **Truncation is not elimination.** Profiles store top-N. A row absent from the later profile gets an *upper bound* from `ProfileSummary.cutoff_self_time`, never a `-100%`. `profile.retain` (40) is deliberately deeper than `context.max_hotspots` (12) so the bound is tight; the planner's prompt is unchanged, and a test enforces that.
2. **Never compare `pct`.** It is normalised per profile, so halving the total doubles an unchanged function's share. Absolute self seconds only.
3. **Refuse, don't smooth.** Different `tool` values (the CUPTI/CPU-fallback flip), a failed profile, or `head_commit == base_commit` return `comparable=False` with a reason.
4. **`unchanged_epsilon` is a display threshold, not significance.** A profile is one observation; there is no profile noise floor. It is echoed in the response so the UI can say so.
5. **The benchmark is the authority.** When the profile's total-time ratio and the measured speedup disagree, `coherence_warning` says so and says which to trust.

## Conventions & gotchas

- Python ≥ 3.11, Pydantic v2, async subprocesses. `schema.py` is the source of truth for all data shapes — change contracts there, deliberately, and update both sides.
- When touching the harness, the bar is: every failure mode becomes a *structured status*, every number is *defensible*. Never let a model-authored path influence a measurement.
- Locked paths are enforced in `workspace.py` in code — the planner is *told* about them too, but the code is the guarantee. Don't weaken that.
- Platform is Windows (PowerShell primary; Bash tool available). The quick-start `source .venv/bin/activate` is POSIX — use `.venv\Scripts\Activate.ps1` locally.
- **Activate the venv before `pytest`.** Targets run `python bench.py` as a subprocess, so invoking `.venv\Scripts\python.exe -m pytest` without `.venv\Scripts` on `PATH` makes those subprocesses resolve to system Python, where `hotpath` is not installed; ~19 tests then fail with `ModuleNotFoundError: No module named 'hotpath'`. That is a PATH problem, not a regression.
- There is a stray `{hotpath` directory (and empty `dashboard/`) at the repo root — leftovers from a shell brace-expansion `mkdir` that didn't expand. They are junk, not part of the package (`pyproject` builds `hotpath*` + `server*`). Safe to delete, but confirm before doing so.
