# Changelog

All notable changes to Hotpath. The format follows [Keep a Changelog](https://keepachangelog.com/),
and the project uses [Semantic Versioning](https://semver.org/). Evidence for any performance number
lives in `docs/EVIDENCE_LEDGER.md`, not here.

## [Unreleased]

### Added
- `SECURITY.md`, `CONTRIBUTING.md`, this changelog, and GitHub issue / pull-request templates.
- `ruff check` in CI, `make lint`, and `make test-fast` (skips tests marked `slow`).
- The site's docs page lists every CLI command from a table that a test keeps in step with
  `hotpath --help`, and the landing page shows the `jaraco/inflect` pull request next to the
  TinyGPT H100 run.
- `hotpath go` explains a missing prerequisite (no model key, no `gh`, no Docker) as a next step,
  not a traceback.

### Changed
- README leads with a `pipx` install and says plainly that the `hotpath` name on PyPI is someone
  else's project. Docs describe all nine `go` stages, including Verify.
- Internal hackathon-era notes, audits, and runbooks are no longer published; user-facing docs moved to the site.

## [0.1.0] — unreleased

The first version that runs end to end on repositories nobody here wrote.

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

### Fixed (found by live runs)
- A CRLF checkout on Windows read as a dirty repository.
- The worker's source budget was smaller than an ordinary module.
- Committed benchmark files imported Hotpath and broke the target's CI collection.
- Test dependencies declared in `setup.py` / `setup.cfg` were not detected.
- Three `ciwatch` defects found against a real pull request.
