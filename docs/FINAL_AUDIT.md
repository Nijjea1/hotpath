# Consolidated branch audit — 2026-09-18

This audit applies to `implement/full-plan` after the prior feature work was combined. `main` remains unchanged. The branch has one checkout and no unresolved merge conflicts. The old task worktrees and local branch names were removed after their uncommitted diffs were backed up in ignored `.tmp/worktree-backups/`.

## Verified behavior

- The full local suite passes with `\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`: **222 passed, 6 skipped**. The skips depend on optional browser/Docker setup or Windows symlink privileges. Python 3.13 on Windows also emits one non-failing asyncio transport warning from the Sentry test.
- With the local Docker runner image and Chromium available, `tests/test_isolation.py` and `tests/test_dashboard_browser.py` pass together: **12 passed**. This exercises a real container and real browser, not an H100.
- The offline provider drives the real worktree, correctness, benchmark, persistence, and dashboard pipeline. A previously recorded Docker-backed CPU demo accepted a measured 1.363x change and rejected an incorrect one. These numbers are machine-specific historical evidence, not a current GPU result.
- The agent receives the current beam head's source, a complete target file, a correctness contract, and retry feedback. Scripted-provider tests cover beam parentage and retry wiring. The harness rejects locked edits and never benchmarks a correctness failure.
- The dashboard shows an honest baseline-to-head hotspot comparison, raw metric/speedup chart, retry and beam links, exact rejection reasons, and diffs. The hotspot panel is a flat per-function comparison, not a call-stack flame graph.
- Run records round-trip through SQLite with config snapshots and execution metadata. Sentry is optional and tested as a no-op when disabled; in-memory transport tests cover envelopes when enabled.

## Review fixes in this branch

1. Docker staging now lists tracked files through the hardened Git wrapper, so target-local Git settings cannot activate host-side fsmonitor/filter helpers during staging. A regression test checks the sanitized invocation.
2. Source sent to the planner/worker no longer follows symlinks or includes locked files. A regression test covers an in-repository symlink to locked Python source.
3. All dashboard and API routes reject non-local clients; the CLI refuses non-loopback binding. Configuration summaries show commands with known and inline credentials filtered, and strip endpoint credentials. API tests cover both cases.
4. Integration tests launch target subprocesses with the same Python environment as pytest, so the documented test command works without manually modifying `PATH`.

## Remaining limits and release gates

- **No Dryft/H100 claim yet.** The bundled transformer is a development target. The H100 Docker image in `configs/dryft_h100.yaml` is a placeholder requiring an operator-reviewed build and a dedicated Linux GPU host. Run the real Dryft model, its reference tests, and varied workloads there before reporting a speedup.
- **Live model quality is unmeasured after the worker changes.** OpenAI planner and worker roles are configured, but a fresh keyed run is needed to measure worker correctness failures and repeatability. Baseten is only an optional compatible endpoint, with no deployment validated.
- **Correctness is contract-relative.** Isolated execution prevents direct host access under the documented Docker threat model, but target code can still influence a test or benchmark executed in its own container. Locked files and passing tests do not prove semantic equivalence against an adversarial target; independent operator-owned verification is needed for that claim.
- **Profiling and ablation are bounded.** Stored profiles retain top functions rather than call stacks. The profile comparison warns when tools disagree and bounds missing rows; it is not a significance test. Ablation remeasures the full stack alongside each omitted change and reports paired medians. Pruning is opt-in (`--prune`) and verified jointly; it never rewrites the run's head.
- **Telemetry and UI deployment need operator validation.** Sentry delivery has not been checked against a real project. The dashboard is intentionally local-only; remote use requires an authenticated reverse proxy to the loopback listener.

## Repository state

The intended local branches are `main` and `implement/full-plan`. A remote-tracking `origin/sentry` branch still exists on the remote; this audit did not push or delete remote refs. Push only `implement/full-plan` when ready. Keep the historical `AUDIT.md` results separate from current claims.
