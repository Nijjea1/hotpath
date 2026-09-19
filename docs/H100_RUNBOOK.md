# H100 Runbook — getting a winning run tomorrow

Everything here is prepared so tomorrow is execution, not building. Order matters: **correctness
and a clean baseline first, then the search, then the story.**

## Evidence boundary before you start

`submission/h100_2026-09-19/` preserves three recorded searches against the bundled TinyGPT
stand-in: 1.460x, 1.379x, and 1.468x aggregate tokens/sec. Its README identifies an H100
workstation, but the artifact does not retain an independently verifiable host/driver/environment
record. It is useful as a dashboard rehearsal and experiment-history example; it is not evidence
for Dryft's actual model.

The current benchmark runs a fixed workload matrix every trial: p32_b1, p32_b2, p160_b1, and
p160_b2, respectively `(prompt length, batch size, generated tokens)` of `(32,1,128)`,
`(32,2,128)`, `(160,1,128)`, and `(160,2,128)`. The headline is total generated tokens divided
by total synchronized elapsed time. A candidate needs the aggregate threshold and bootstrap-CI
result, plus at least 98% of its parent's median tokens/sec on every required shape.

## 0. Environment (once, at the start)
```bash
pip install -e ".[dev]"          # install Hotpath; install a CUDA-compatible PyTorch build separately
export OPENAI_API_KEY=...        # planner
export BASETEN_API_KEY=...       # workers
export SENTRY_DSN=...            # optional observability track
python -c "import torch; print(torch.cuda.get_device_name(0))"   # expect H100
python -c "import torch; print(torch.version.cuda, torch.__version__)"
CFG=configs/dryft_local.yaml     # or configs/dryft_h100.yaml; every command below uses $CFG
```
**Pick one execution mode.** Both configs run the identical search (a test enforces that they differ
only in `execution`):

- `configs/dryft_local.yaml` (**recommended for the event**) runs target commands directly on a
  **throwaway GPU VM** (for example a Vultr GPU instance). No image to build, so no hours lost before
  the first search. The VM is the sandbox: model-written code runs with the host environment and
  open network, so it can read the API keys. Use spend-capped project keys, keep nothing else on the
  VM, rotate the keys and delete the VM afterward. Dryft runs submissions on their own H100s, so this
  choice only affects your development box.
- `configs/dryft_h100.yaml` isolates every command in Docker. Use it on any machine that is shared
  or long-lived. Setup below.

Docker mode: model-written code runs in Docker, not on the host. `configs/dryft_h100.yaml` uses
`execution.backend: docker` with `gpu: device=0` and the image `hotpath-runner:h100-reviewed`.
Install the NVIDIA Container Toolkit, then build that image from the Hotpath checkout on a
CUDA/PyTorch base with the model's dependencies and weights baked in (see `docs/ISOLATION.md`;
source staging is capped at 512 MiB, so weights cannot come from the target tree). Check it:
```bash
docker run --rm --gpus device=0 hotpath-runner:h100-reviewed python -c "import torch; print(torch.cuda.get_device_name(0))"
```
A missing image fails the run closed before the baseline.

## 1. Swap in the real model (≈ first hour — nothing else matters until this is green)
Follow `targets/torch_transformer/SWAP_DRYFT.md`. Then by hand:
```bash
cd targets/<dryft-repo>
python tests/check.py     # exit 0 — mirrors Dryft's exact correctness definition
python bench.py           # prints one tokens_per_s JSON line
python hotprofile.py      # prints one hotspots JSON line (CUPTI works on Linux → real device times)
```
If the untouched model fails its own `check.py`, fix that first — Hotpath refuses to optimize code
it can't verify (by design).

## 2. Wire Baseten (workers) — the "big model plans, fast model explores" story
In `$CFG` set `provider.worker_base_url` to your Baseten deployment's
`.../environments/production/sync/v1` URL and `worker_model` to the served name. Smoke test:
```bash
hotpath run $CFG --iterations 1     # one iteration, confirm workers respond
```

## 3. The real search
```bash
hotpath serve $CFG &                # dashboard for the live/overnight view
hotpath run $CFG                    # or start it from the dashboard
```
The dashboard only answers loopback clients. From your laptop, tunnel to the VM instead of
binding it publicly: `ssh -L 8765:127.0.0.1:8765 <user>@<h100-host>`, then open
http://127.0.0.1:8765.
- H100 is stable → keep `rebenchmark_parent: true`, `baseline_repeats: 5`. Small real wins will
  register instead of being rejected as noise (unlike our laptop).
- `beam_width: 2` explores more of the tree; accepted changes stack and it re-profiles each new head.
- Let a long search run (overnight if allowed) — that's the "hundreds of attempts" story.
- A planner timeout, rate limit, or 5xx is retried with backoff, and a persistent failure only skips
  that head for one iteration. If the planner fails on every head for 3 iterations in a row, or the
  process dies, continue without a new baseline:
  `hotpath run $CFG --resume <run_id> [--iterations N]` with the config the run used. Resume refuses if the
  test, benchmark, or execution settings changed since the run started.

## 4. Lock in the numbers
```bash
hotpath ablate $CFG                 # what each accepted change actually contributed
hotpath export $CFG submission/ --ablate   # optimized tree + changes.patch + REPORT.md
```
`REPORT.md` is your headline: final tokens/sec speedup, the accepted chain, and the ablation table.
Also save `nvidia-smi`, the target commit, driver, CUDA/PyTorch versions, config snapshot, and the
aggregate plus each workload's samples. Without those, a result remains a recorded artifact rather
than a hardware-attested Dryft measurement.

## 5. Take the results to the judging table (offline)
The dashboard answers only loopback clients and can start runs, so never expose it on venue Wi-Fi.
Instead, carry the database to your laptop. Diffs, profiles, measurements and each run's config
snapshot all live inside it, so nothing else from the VM is needed:
```bash
# on the VM, after the run has finished (a live copy of a WAL database can miss recent rows)
python -c "import sqlite3; sqlite3.connect('targets/<dryft-repo>/.hotpath/hotpath.db').backup(sqlite3.connect('demo.db'))"
# on your laptop
scp <user>@<vm>:demo.db . && hotpath serve --db demo.db      # http://127.0.0.1:8765
```
Served with `--db` and no config, the dashboard is read-only: it cannot start runs. For live updates
during the search, use the SSH tunnel from step 3 instead.

## Prize-track mapping (have the evidence ready)
- **Dryft** — fastest *correct* decode. Headline = tokens/sec speedup from `export`, every change
  proven against their reference. Show the tree: green kept, red rejected with the reason.
- **Baseten** — workers served on Baseten (`worker_base_url`). Note the parallel-worker throughput.
- **OpenAI** — planner on `gpt-4.1` (structured outputs). Have the Codex-building story ready.
- **Sentry** — `SENTRY_DSN` set: each experiment is a trace (propose→patch→test→bench spans),
  planner/worker reasoning in Logs. **Write down one real debugging moment as it happens** — that's
  literally their judging criterion.
- **Warp** — same engine on any repo: run `configs/demo_repo.yaml` live (~70×, deterministic) as
  the "general developer tool" proof.

## The demo (3 beats — rehearse ≥3×)
1. Baseline + noise floor stated out loud.
2. The tree after a search: click a **red** `rejected_correctness` node and read its stored reason —
   "It proposed this change. It changed the model's output, so Hotpath rejected it before timing it.
   We don't benchmark wrong code." Never quote a speedup for these nodes: none was measured. Click a
   `locked_file` node — "it tried to edit the test file, and was blocked before anything ran."
3. The accepted chain + ablation + a diff you'd actually PR. The line that lands: *the AI was wrong
   about most of these, and you know exactly how.*

## Which optimizations will actually win on H100 (unlike the laptop)
On Linux/H100 the launch-overhead wins that were impossible on our Windows laptop are available:
- `torch.compile(mode="reduce-overhead")` / **CUDA graphs** — biggest per-token launch-overhead cut.
- **SDPA / flash attention** — big on a compute-bound model with long sequences.
- **KV-cache preallocation** — pays off on a real (non-toy) model where torch.cat copying is large.
- **Fused norms**, **removed CPU-GPU syncs**, **TF32** (if the tolerance allows).
The strategy menu in both Dryft configs already lists these; the planner proposes, the
harness proves.

## Reminders
- Confirm with organizers what pre-built scaffolding is allowed (rules require building during the event).
- Rotate the OpenAI key that was shared earlier.
