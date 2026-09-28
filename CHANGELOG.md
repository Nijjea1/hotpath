# Changelog

All notable changes to Hotpath. The format follows [Keep a Changelog](https://keepachangelog.com/),
and the project uses [Semantic Versioning](https://semver.org/). Evidence for any performance number
lives in `docs/EVIDENCE_LEDGER.md`, not here.

## [Unreleased]

## [0.1.0] — 2026-09-28

The first release, and the first version that runs end to end on repositories nobody here wrote.
Install with `pipx install hotpath-agent`; the command is `hotpath`.

### Added
- **Harness:** isolated git worktrees with locked-path enforcement before any write; async
  subprocesses with hard timeouts and reliable process-tree cleanup; a noise-aware accept/reject
  decision with a 2,000-resample bootstrap CI; an exclusive quiet-machine lock.
- **Search:** planner + parallel workers, retry-with-feedback, interleaved parent re-benchmark,
  beam search, leave-one-out ablation with verified joint pruning, `hotpath export`.
- **Execution:** a fail-closed Docker backend (no network, read-only source, non-root, quotas) and
  an explicit trusted local backend; portable accelerator helpers for CUDA, ROCm, XPU, MPS, and CPU.
- **Product:** `hotpath go` (nine stages from a URL to a draft PR, then the repository's own CI),
  `check` (seconds-long preflight with a cost estimate), `assess`, `doctor` (with `--verify-keys`),
  `init`, `run --pr`, and `pr` (one commit per verified change, rebuilt from the exact tested trees).
- **Benchmarks:** generated from the test suite's hot paths and validated by running them;
  remembered per repository so two runs are measured against the same thing.
- **Dashboard:** local-only FastAPI app with the experiment tree, metric-aware chart with a noise
  band, bottleneck diff with honest bounds, and exact rejection reasons.
- **Observability:** optional Sentry traces, logs, and metrics; a no-op without a DSN.
- **Docs:** the site's `/docs` page covers install, workflows, every command (generated from the
  parser, with a test that fails on drift), the full config reference, troubleshooting, and an FAQ.
  `SECURITY.md`, `CONTRIBUTING.md`, and issue / pull-request templates.
- **Release:** published to PyPI as `hotpath-agent` from a tag-driven workflow using trusted
  publishing; `ruff` in CI; `make test-fast` and `make lint`.

### Fixed
- `hotpath go` no longer hangs without a terminal on Windows (the `NUL` device reports `isatty()`);
  every stop names its stage and a next step, and an unexpected error does too instead of a traceback.
- A key typed at the prompt is saved to `~/.hotpath/.env`, never the directory being optimized.
- Found by live runs: a CRLF checkout read as a dirty repository; the worker's source budget was
  smaller than an ordinary module; committed benchmark files imported Hotpath and broke the target's
  CI; test dependencies in `setup.py` / `setup.cfg` were not detected; three `ciwatch` defects.
