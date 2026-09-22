# Hotpath — Test & Audit Notes

_Historical audit run on Windows 11, Python 3.13, RTX 5060 Laptop (Blackwell sm_120), torch 2.11.0+cu128,
real models `gpt-4.1` (planner) + `gpt-4.1-mini` (worker). Counts below reflect that audit, not the current suite._

## TL;DR

The core is real and works end to end — offline, with real models, and on the GPU. During the
audit I found and fixed **3 bugs** (one of which materially crippled multi-iteration runs), then
**built out the rest of the roadmap** (retry-with-feedback, interleaved parent re-benchmark, beam
search, a self-vs-total profile view, `hotpath export`, and a winning offline GPU demo patch).
That historical test suite reached **45 passing** (up from 39). Current counts are reported by `pytest -q`.

## Build-out completed (post-audit)

| Feature | What it does | Verified |
|---|---|---|
| Retry with feedback | Failed patch/correctness → re-ask worker once with the failure fed back, recorded as its own experiment. `search.max_patch_retries`. | Unit tests + live GPU run (a `.item()` fix went correctness-fail → correct) |
| Interleaved parent re-benchmark | `benchmark.rebenchmark_parent` re-measures the parent next to each candidate to kill drift. | Unit test |
| Beam search | `search.beam_width` / `--beam N` keeps & expands the top-N heads; width 1 is the old greedy. | 2 unit tests (width 1 vs 2) + live offline run |
| Profile self-vs-total view | Dashboard bars show self time nested in cumulative time. | Server test |
| `hotpath export <dest>` | PR bundle: optimized tree, `changes.patch`, `REPORT.md` (benchmark table + accepted chain), optional `--ablate`. | Unit test + live CLI run (77.5× bundle) |
| Offline GPU win | New mock patch skips the causal mask during single-token decode (bit-identical, reliably faster). | Live offline GPU run: **1.243×**, correctness-gated |

Greedy behavior is unchanged (all pre-existing tests still pass), so nothing regressed.

---

## ✅ Verified working (with evidence)

| Surface | Result |
|---|---|
| Test suite | **39/39 pass** (was 38/39 before the runner fix) |
| Offline mock loop (`demo_repo`) | End-to-end, all 5 verdict statuses fire, ~70–79×, stable across repeated runs |
| Real models — demo repo | **77.5×**, 3 accepted; legitimate stat-based rejections incl. a bootstrap-CI reject `[0.843, 1.245]` spanning 1.0 |
| GPU correctness gate (`tests/check.py`) | **Passes on CUDA**, exact token match, 0.0 logit diff |
| GPU benchmark (`bench.py`) | ~136–197 tokens/sec, CUDA-event timed |
| Real models — GPU target | Accepted real wins: `.item()` sync removal, causal-mask caching, LayerNorm+MLP fusion (1.1×–1.5×, varies by run); correctly rejected KV-prealloc / SDPA / torch.compile |
| Ablation (`hotpath ablate`) | Leave-one-out contributions, real numbers |
| Dashboard API | `/api/runs`, `/api/state`, `/api/experiments/{id}` serve real SQLite data |
| Export (`--export` / `checkout_into`) | Works on Windows (tar present); exported source contains the accepted edits |

The "AI proposes, harness proves" thesis is demonstrably real: on the GPU, plausible optimizations
(SDPA, torch.compile, KV-prealloc) were **caught and rejected** by the correctness/speed gates.

---

## 🔧 Bugs fixed during this audit

1. **`runner.py` — Windows timeout crash (was failing 1 test; would crash the live demo).**
   Timeout handling called `os.killpg` / `signal.SIGKILL` (POSIX-only) → `AttributeError` on
   Windows, so timeouts were misclassified as `error` and a hung child could take down a run.
   Now cross-platform: `taskkill /F /T` on Windows, `killpg` on POSIX, with the closed-pipe
   case guarded.

2. **`profilelib.py` — GPU profile was all zeros (planner flew blind).**
   CUPTI fails to initialize on this driver/GPU (`CUPTI_ERROR_INVALID_DEVICE`), so every CUDA
   time came back `0.0`. Added a CPU-time fallback: when device timing is unavailable the profile
   is built from `self_cpu_time_total` and labeled `torch.profiler (cpu-time fallback)`. The
   planner now sees real hotspots (e.g. `aten::cat` = the growing KV cache).

3. **`orchestrator.py` + `workspace.py` — agent read stale source (HIGH impact).**
   `agent.plan()` and `agent.generate_patches()` were handed `self.target` (the pristine, on-disk
   source), which is never updated with accepted edits. From iteration 2 on, any hypothesis
   touching an already-optimized function failed with "search text not found." Fixed by
   materializing the current `head_commit` into a stable worktree (`Workspace.materialize`) and
   passing that to the agent. **Effect measured:** demo real-model run went from 3 stale-source
   `patch_failed` (67.9×) to zero, with legitimate stat rejections instead (77.5×).

All three are covered by the existing suite (still 39/39) plus live re-runs.

---

## 🐞 Needs fixing / attention (prioritized)

1. **High `rejected_correctness` rate from the worker on the GPU target.** ✅ *Addressed:* the
   retry-with-feedback loop now re-asks the worker with the test output; a live GPU run showed it
   turning a `.item()` correctness failure into correct code. It cannot rescue changes that are
   fundamentally non-bit-identical (SDPA) — that is the correctness gate doing its job. Still worth
   considering a stronger worker model (`gpt-4.1` instead of `-mini`) for GPU tensor code.

2. **Offline GPU demo shows no accepted win.** ✅ *Fixed:* added `03_skip_decode_mask.json`, a
   bit-identical patch that skips building the causal mask during single-token decode. Offline GPU
   run now ends at **1.243×** with the win correctness-gated. (`01`/`02` are still correctly
   rejected on the tiny model, which keeps the demo honest.)

3. **Demo transformer is too small for big/stable GPU numbers** (4 layers, d=256). KV-prealloc is
   slower here because the model isn't copy-bound; real-model wins are modest (1.1×–1.5×) and vary
   run-to-run. The headline number should come from the **real Dryft model**, which is bigger and
   where these optimizations matter.

4. **CUPTI unavailable on this machine** → no true GPU kernel attribution (mitigated by the
   CPU-time fallback in fix #2). If accurate device-time profiling is wanted for the demo, this is
   an environment issue (driver/permissions/CUPTI), not a code bug. The CPU-time proxy is a
   reasonable stand-in for now.

5. **Historical note, since resolved:** `Orchestrator.export_best()` was limited to its
   in-memory run. The current `hotpath export --run-id` command uses the store-backed
   bundle builder for past runs.

6. **Cleanup:** a stray `{hotpath` directory and empty `dashboard/` at the repo root are junk from
   a shell brace-expansion `mkdir`. Safe to delete.

---

## ⏳ Still unverified (needs your keys/assets)

- ~~**Sentry**~~ — verified live; see the note above and `docs/SENTRY.md`. Project-side visibility
  is the one link still needing a human check.
- ~~**Baseten worker endpoint**~~ — **verified live on 2026-09-22.** `worker_base_url` pointed at
  `https://inference.baseten.co/v1` with `moonshotai/Kimi-K2.7-Code`, which wrote every patch in
  the `inflect` run, including the two accepted changes that shipped as
  https://github.com/Nijjea1/inflect/pull/1 (1.472x). Worker quality on a GPU target is still
  untested; this is a CPU-target result.
- **Real Dryft model swap** — replace `model.py` + `reference_model.py`, keep their `check.py`
  correctness definition. This is the headline number.

---

## Current validation boundary

The repeatable evidence in this repository is the offline mock loop and automated tests.
Live OpenAI calls, Sentry delivery, third-party worker endpoints, the real Dryft model,
and H100 performance require operator-run validation on the intended host. The historical
run notes above do not establish a Baseten, Dryft, or dedicated-H100 result.

## Improvements worth doing (from the roadmap, in priority order)

1. Retry failed patch once with failure feedback (item #1 above) — ~15 lines in `agent.py`.
2. Re-benchmark the parent interleaved with each candidate to remove cross-iteration drift.
3. Flame-graph + ablation views in the dashboard (data already present).
4. Seed the transformer search with known-good moves (preallocated KV, sync removal, SDPA,
   torch.compile, CUDA graphs) so the agent always has proven building blocks to combine.

---

## ⚠️ Security

The OpenAI API key was shared in chat and written to a local, git-ignored `.env`. **Rotate it**
at platform.openai.com/api-keys — treat the shared one as compromised.
