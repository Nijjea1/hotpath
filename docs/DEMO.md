# Demo and deployment guide

Hotpath has two intended operating modes:

* The bundled `demo_repo` is a trusted local smoke test. Its configs explicitly set
  `execution.backend: local` so the demo works without building a container.
* Arbitrary repositories run with the Docker backend by default. Use a reviewed,
  pinned runner image and the isolation rules in [`ISOLATION.md`](ISOLATION.md).

## Offline demo

On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
hotpath run configs/demo_repo.yaml
hotpath serve configs/demo_repo.yaml
```

Pre-demo checklist:

- `git status` and `git -C demo_repo status` both clean. `demo_repo/` has its own local git
  repository (created on its first run), so pulling changes that touch it leaves it dirty and the
  live run refuses to start. Commit there, or run once with `hotpath run configs/demo_repo.yaml --autocommit`.
- Close other applications; a busy machine raises the noise floor and the acceptance threshold with it.

The mock provider supplies recorded candidate patches. Tests and benchmarks still run
through the real harness, so this verifies orchestration and verdict handling without
API keys. `configs/demo_repo_beam.yaml` also exercises beam branches and retry feedback.

### Second live-demo safety target

`configs/slow_web_analytics.yaml` provides an independent, deliberately inefficient web-analytics
target. Its ten-run reliability check consistently shipped only the one-pass aggregation change,
recorded one locked-file rejection and one correctness rejection, and cleaned every worktree.
The latest local gate ran in 5.7–9.1 seconds per attempt, with 72.8–137.8x measured improvement. The variance is from its
baseline noise; the shipped set and verdict classes were stable. Its ignored runtime evidence is
`reports/slow_web_analytics_reliability.json`.

### Three-beat presentation

1. **Measure:** show the baseline metric and widest hotspot in the before view.
2. **Reject:** click a red node and read its exact stored failure; if it failed correctness,
   no speed measurement is claimed. Show the retry link when the worker recovered.
3. **Keep:** open the accepted diff, show the measured improvement and after hotspot,
   and point to the run ID and execution environment attached to the result.

The before/after panel compares retained hotspot costs. It is not a call-stack flame
graph, and a missing function is reported as bounded or unknown when its profile is
incomplete.

## OpenAI planner and worker

Set `OPENAI_API_KEY` in the environment and use `configs/demo_repo_openai.yaml`:

```powershell
$env:OPENAI_API_KEY = "sk-..."
hotpath run configs/demo_repo_openai.yaml
```

The planner and worker are separate OpenAI model roles configured in that file. The
repository contains the provider implementation and tests for request construction;
live model execution requires the operator's key and should be treated as an external
validation step. Keep credentials out of target repositories and Docker images.
Workers require the complete target file within `context.max_source_chars`. A larger
file fails explicitly so an incomplete excerpt cannot silently produce an invalid edit.

## GPU target on a dedicated Linux host

`targets/torch_transformer` is a small TinyGPT stand-in useful for exercising tokens/sec
measurements. It is not a validation of the real Dryft model. The recorded artifacts in
`submission/h100_2026-09-19/` contain three TinyGPT runs at 1.460x, 1.379x, and 1.468x;
the artifact README identifies an H100 workstation, but the repository has no independent
host/driver/environment capture for those runs. Use them as a stored dashboard story, not
as a Dryft result.

The current GPU configs declare a fixed four-shape matrix: p32_b1, p32_b2, p160_b1, and
p160_b2. Each headline sample is total generated tokens divided by total synchronized elapsed
time across that complete matrix. A candidate must pass the aggregate statistical gate and
retain at least 98% of its parent's median throughput on every required shape. Run fresh GPU
work on a dedicated Linux host with an operator-reviewed CUDA/PyTorch image:

`configs/dryft_h100.yaml` is the competition config, and `docs/H100_RUNBOOK.md` is the
step-by-step playbook. Its `hotpath-runner:h100-reviewed` image name is an operator-supplied
placeholder; build and validate that image on the target host before starting a run.

```yaml
execution:
  backend: docker
  image: your-reviewed-image@sha256:YOUR_DIGEST
  gpu: device=0
search:
  max_parallel_benchmarks: 1
benchmark:
  exclusive: true
```

Establish a Dryft result only by running Dryft's intended model with its official reference
tests, then recording the target commit, GPU identity, driver, CUDA/PyTorch versions, config,
aggregate samples, and per-shape samples beside the export.

## What is validated

The automated suite validates configuration, harness isolation contracts, persistence,
provider behavior, benchmark decisions, and the dashboard. The Baseten handoff records a
historical CPU endpoint exercise; the suite does not revalidate that endpoint, a real Dryft
model swap, or a particular GPU environment.
