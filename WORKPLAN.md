# Hotpath — Two-Person Work Plan (pre-Sentry/Baseten)

Goal for today: **make everything that does NOT depend on Sentry or Baseten rock solid, tested,
and demo-ready.** Sentry + Baseten live integration is deferred to tomorrow — we only *prep* those
paths today (they already no-op without credentials, so nothing blocks).

Current state: 45 tests passing; core loop + retry-with-feedback + beam search + interleaved
re-benchmark + `hotpath export` + self-vs-total profile view all built and verified on Windows and
on the RTX 5060. See `AUDIT.md` for the full audit.

---

## The seam (how we avoid stepping on each other)

`schema.py` is the contract between the two halves. Own your side's files; **coordinate before
editing shared files.**

- **Person A — Harness & Verification** (the part that must be trustworthy)
  Owns: `workspace.py`, `runner.py`, `benchmark.py`, `profiler.py`, `harness.py`, `ablation.py`,
  `benchlib.py`, `profilelib.py`, `export.py`, `targets/**`, and tests `test_harness.py`,
  `test_benchmark.py`, `test_workspace.py`.
- **Person B — Agent, Product & Dashboard** (the part that makes it legible)
  Owns: `context.py`, `agent.py`, `providers/**`, `store.py`, `server/**`, `observability.py`,
  `configs/**`, and tests `test_orchestrator.py`, `test_server.py`, `test_store_and_state.py`.
- **Shared — change together, in small PRs, announce first:** `schema.py`, `orchestrator.py`,
  `README.md`, `CLAUDE.md`, `Makefile`, `pyproject.toml`.

### Git workflow
- One feature branch per task (`a1-windows-hardening`, `b1-flamegraphs`, …). PR into `main`.
- **`main` must always be green:** `pytest -q` before every merge.
- Don't both edit `schema.py`/`orchestrator.py` at once. If you must, ping and merge that PR first.
- After each merge, the other person rebases.

### Definition of done (every task)
Code + tests + `pytest -q` green + one manual run of the affected surface + a one-line note in the PR.

---

## Person A — Harness & Verification

### A1. Cross-platform / robustness hardening
- [ ] Audit `workspace.py` worktree lifecycle for leaks on Windows (create → use → remove; the
      `_head_*`, `parent_*`, `profile_*`, `baseline_*` worktrees). Confirm `cleanup()` removes all.
- [ ] Confirm `checkout_into`/`diff_commits` (tar + git archive) work when paths contain spaces
      (our repo path has spaces — already exercised, add a test with a spaced temp dir).
- [ ] `runner.py`: the Windows kill-tree path (`taskkill /F /T`) — add a test that a runaway child
      process is actually killed on timeout (spawn a sleep, assert it dies).
- [ ] Verify behavior when `git` or `tar` is missing → clear error, not a stack trace.

### A2. A second, bulletproof general-repo demo target
The live demo needs a repo that finds + verifies a win in ~1 minute, every time.
- [ ] New `targets/slow_pyapp/` (or similar): a deliberately slow but realistic Python script
      (e.g. an O(n²) data-processing loop, a repeated recompute, a naive dedupe). Include
      `tests/check.py` (locked), `bench.py` (locked), `hotprofile.py` (locked).
- [ ] Recorded mock patches so it runs **offline** (no API key) and reliably accepts ≥1, rejects ≥1.
- [ ] `configs/slow_pyapp.yaml`. Run it 10× and confirm the accept set is stable (see A6).

### A3. GPU target polish for the Dryft handoff
- [ ] Add a `kernels/` editable surface to `targets/torch_transformer/` (even a tiny pure-PyTorch
      "kernel" module) so `editable: ["model.py", "kernels/*.py"]` is exercised before the real model.
- [ ] Strengthen the hidden-workload defense in `bench.py`: vary prompt length **and** batch size,
      so an input-specific win can't pass. Document why in a comment.
- [ ] Write `targets/torch_transformer/SWAP_DRYFT.md`: exact steps to drop in the real model +
      reference + their correctness definition tomorrow.

### A4. Statistics & decision rigor (tests)
- [ ] `test_benchmark.py`: bootstrap CI excludes/ю includes 1.0 correctly on crafted samples;
      threshold = `max(min_speedup, 1 + noise_multiplier×noise)` under low and high noise;
      higher-is-better inversion (tokens/sec) gives the right verdict.
- [ ] Ablation: leave-one-out re-measures and drops a change that doesn't pull its weight (craft a
      case where one accepted change is actually neutral).

### A5. Failure-mode coverage (every reject is a named status)
Add/confirm a test for each: ambiguous search match (2 hits), search text not found, missing file,
empty search, no-op edit (search==replace), benchmark exits non-zero, unparseable benchmark JSON,
test timeout, benchmark timeout, profile failure, worker process crash. Each → the correct
`ExperimentStatus`, run never crashes.

### A6. Reliability harness
- [ ] Script `make reliability` (or a shell loop) that runs `hotpath run configs/demo_repo.yaml`
      N times and asserts the same changes are accepted each time. Fix any flakiness (usually noise
      threshold or worktree cleanup).

---

## Person B — Agent, Product & Dashboard

### B1. Before/after flame graphs in the dashboard
- [ ] Render `baseline_profile` vs `head_profile` side by side so "the bottleneck shrinking" is
      visible (self-vs-total bars already exist for the head — mirror them for baseline).
- [ ] Handle the case where they're equal (no accepted change yet).

### B2. Metric-aware charts + tree legibility
- [ ] Chart: for higher-is-better metrics (tokens/sec) show the **raw metric climbing**, not only
      speedup×. Read `benchmark.metric` / `higher_is_better`.
- [ ] Tree: show retry experiments (`retry_of`) and beam branches (multiple heads) clearly —
      distinct edge/badge for retries, and don't let two heads render on top of each other.
- [ ] Polish the "click a red node → exact reason" flow — this is the demo's trust moment.

### B3. Dashboard + API robustness (tests)
- [ ] `test_server.py`: assert `/api/state` and `/api/experiments/{id}` expose the new fields
      (`retry_of`, and beam heads), and that empty/no-run states return cleanly.
- [ ] Manual: `hotpath serve configs/demo_repo.yaml`, start a run from the UI, watch it live, click
      through accepted + each rejected reason. No console errors.

### B4. Reduce worker correctness failures (biggest quality lever)
- [ ] Refine `providers/prompts.py` worker prompt: emphasize copy-exact search text, minimal edits,
      behavior-preserving; make the retry feedback (`previous_failure`) include the test output tail
      (already passed through — confirm it lands and is used well).
- [ ] Add a `FakeProvider` in tests to assert plan/patch request wiring (history, previous_failure,
      per-node parent) without hitting a network — covers retry + beam prompt construction.
- [ ] **Baseten prep only:** confirm `provider.worker_base_url` + `worker_api_key_env` flow through
      `build_provider` and the OpenAI-compatible client. Add `configs/demo_repo_baseten.yaml`
      (commented endpoint) so tomorrow is a one-line change. Do NOT call it live yet.

### B5. Store / state integrity (tests)
- [ ] `test_store_and_state.py`: round-trip an Experiment with `retry_of` set and a run with a
      multi-head beam; confirm `list_experiments` ordering and `latest_run` are stable.

### B6. Sentry prep (no live calls today)
- [ ] Confirm every stage still has a span/transaction and that all of `observability.py` is a clean
      no-op with no `SENTRY_DSN` (run the full suite with DSN unset — it already is).
- [ ] Write `docs/SENTRY.md`: the exact list of transactions/spans/logs we emit, and the one-line
      env setup for tomorrow. Note the "debugging moment" template to fill in live (judging criterion).

---

## Shared / coordinate (either person, announce first)

- [ ] **Config validation:** friendly errors for a bad config (missing `test_cmd`, target not found,
      editable/locked overlap). One helper in `config.py` + a test. (Touches schema-ish — coordinate.)
- [ ] **README numbers:** once A2/A6 land, put a real offline results table (accept/reject reasons)
      in `README.md`. Keep `demo_repo` table current.
- [ ] **Demo script:** write `docs/DEMO.md` — the 3-beat script (baseline+noise → tree with a red
      node explained → accepted chain + ablation + diff). Rehearsal notes.

---

## ⛔ Blocked until tomorrow (Sentry + Baseten access)
- Live Sentry: run with a real `SENTRY_DSN`, confirm transactions/spans/logs appear, capture one
  real debugging moment.
- Live Baseten: deploy a worker model, point `worker_base_url` at it, run a real search.
- (Everything above is *prepped* today so these become flip-a-switch.)

## ⛔ Blocked until the event (Dryft)
- Real Dryft model + their correctness definition + kernel/Triton edits on the real code.
- Confirm with organizers what pre-built scaffolding is allowed (rules require building during the event).

---

## Integration checkpoints
1. **Kickoff:** both `pip install -e ".[dev]"`, run `pytest -q` (expect 45 green), one
   `hotpath run configs/demo_repo.yaml` and one `hotpath serve` to confirm identical baselines.
2. **Mid-day merge:** land A1–A2 and B1–B2, run full suite + offline demo together.
3. **End of day:** everything green, both demo targets rehearsed, README numbers in, Sentry/Baseten
   configs staged for tomorrow.
