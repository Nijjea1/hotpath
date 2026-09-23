# Hotpath release status

This is the canonical current-status document. `AUDIT.md`, `WORKPLAN.md`, `PLAN.md`, and
`docs/FINAL_AUDIT.md` are historical records and their old test counts are not current claims.

## Release target

Version 0.1 is a cross-platform CLI for Windows, Linux, and macOS. It supports local execution on
trusted targets and fail-closed Docker execution for untrusted targets. Accelerator-aware bundled
helpers select CUDA/ROCm, Intel XPU, Apple MPS, or CPU, and kernel edits remain subject to locked
correctness and workload-aware performance gates.

## Automated gates

- Full pytest suite on Windows, Ubuntu, and macOS using the primary Python version.
- Full pytest suite on every other declared Python version on Ubuntu.
- Real Linux Docker boundary probe.
- Python wheel/sdist build and clean-environment CLI/doctor smoke test.
- Site dependency install, strict TypeScript check, production build, and prerender.

## Hardware evidence gates

Before making a performance claim for a GPU family, retain:

1. `hotpath doctor --json` output (credential booleans removed if published).
2. GPU/driver/framework versions and immutable target/config revisions.
3. Locked correctness output for the exported final tree.
4. Raw aggregate and per-workload samples.
5. Ablation of the shipped stack.

The checked-in TinyGPT H100 artifacts are historical evidence, not proof for every GPU and not a
Dryft-model result. See `docs/EVIDENCE_LEDGER.md` for the exact boundary.

## Local validation — 2026-09-23

- Windows 11, Python 3.13.14: **400 passed, 5 expected platform skips, 0 failures, 0 warnings**.
- Real Chromium dashboard regression: passed using installed Chrome.
- Real Docker isolation boundary: passed using Docker Desktop's Linux engine.
- Guided offline `hotpath go`: all nine stages passed from a fresh repository; one verified change
  shipped locally at 1.47x with a 95% CI of [1.45, 1.49].
- `assess`, `run`, `serve`, `ablate`, `export`, and `pr --no-push`: passed command-level smoke tests.
- Wheel and sdist: built successfully; wheel installed in a fresh venv and its CLI/doctor passed.
- Site: 0 npm advisories, strict TypeScript passed, production build/prerender passed.

The five skips are the POSIX-only process-group test and Windows symlink-permission cases; the
Windows descendant-kill regression passed. Cross-platform CI is configured for Ubuntu, Windows,
and macOS, but those hosted runs must complete before this document claims those hosts as observed.
