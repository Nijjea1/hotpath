# Baseten / H100 handoff — continue from here

Handoff brief for a teammate (or their AI assistant). Repo: https://github.com/Nijjea1/hotpath
(branch `main`). The repository contains recorded H100-labelled TinyGPT artifacts, but still needs
a hardware-attested run against Dryft's actual model.

## The goal
Hotpath is an agent that optimizes code and **proves** each change is correct + faster. For the
Dryft track we run its `torch_transformer` target (a decode-throughput transformer) **on an H100**
and report **tokens/sec** before vs. after. Baseten provides both the worker LLM (Model API) and
the H100 (Training workstation).

## What is recorded
- **Baseten worker wired.** Planner = OpenAI `gpt-4.1`; worker = Baseten `moonshotai/Kimi-K2.7-Code`
  via `https://inference.baseten.co/v1` (OpenAI-compatible). Chosen after probing the catalog:
  fastest (~0.8s), code-specialized, structured-output `parse` works.
- **Validated end-to-end on CPU demo:** `configs/demo_repo_baseten.yaml` → **72.2× on demo_repo**,
  3 accepted / 2 correctly rejected, worker shown as `compat:moonshotai/Kimi-K2.7-Code`.
- **Recorded TinyGPT GPU searches:** `submission/h100_2026-09-19/` contains three reports with
  aggregate 1.460x, 1.379x, and 1.468x tokens/sec speedups. The artifact README identifies an
  H100 80GB workstation and torch 2.11+cu128, but the repository lacks an independent host,
  driver, and environment capture. These are not Dryft-model results.
- **Configs ready:** `configs/demo_repo_baseten.yaml` (CPU validation), `configs/dryft_local.yaml`
  (H100, `execution: local`, no Docker — the fast path), `configs/dryft_h100.yaml` (H100 in Docker).
- **Docs:** `docs/BASETEN.md`, `docs/H100_WORKSTATION_SETUP.md`, `docs/H100_RUNBOOK.md`,
  `docs/GPU_TROUBLESHOOTING.md`. One-shot script: `scripts/h100_setup.sh`.
- **Local GPU (Windows RTX 5060) works** (~1.5–2× on the tiny transformer); 222 tests pass.

## What still needs validation
1. **Capture a hardware-attested H100 run**: save GPU name, driver, CUDA/PyTorch versions, target
   commit, config snapshot, aggregate samples, and per-shape samples with the export.
2. **Swap in Dryft's actual model** if they provide one (`targets/torch_transformer/SWAP_DRYFT.md`) and
   mirror their exact `tests/check.py` correctness definition. The TinyGPT stand-in is not enough.
3. **Exercise current Baseten worker quality** on the final target; the CPU demo result is historical.
4. Rotate the API keys after the event (they were shared in chat).

## Exact steps to get the H100 (verified commands)

### CLI install (Windows uses `truss`; macOS can use `brew install baseten`)
```bash
pip install uv truss           # truss.exe + uv.exe land in Python's Scripts dir
truss login --api-key <BASETEN_API_KEY>     # or: truss login --browser
```

### Create the interactive H100 workstation
```bash
truss train workstation --accelerator h100 --gpu-count 1
```
This spins up an SSH workstation (a training job holding one H100) and prints the **SSH host** plus
the commands to view logs and stop it. ⚠️ It **bills against your Baseten credits until stopped** —
stop it when done (`truss train stop ...` / the stop command from the output).
**SSH must be enabled for the workspace** — if connection fails, ask Baseten support/booth to enable
SSH sessions for the workspace.

### On the H100 (SSH in, then one command)
```bash
ssh <host-from-the-command-output>          # e.g. training-job-<id>-0.ssh.baseten.co
git clone https://github.com/Nijjea1/hotpath.git && cd hotpath
cat > .env <<'EOF'
OPENAI_API_KEY=sk-your-openai-key
BASETEN_API_KEY=your-baseten-key
EOF
bash scripts/h100_setup.sh
```
`scripts/h100_setup.sh` does: GPU check → venv + `pip install -e .[dev]` → torch/CUDA →
baseline tokens/sec → `hotpath run configs/dryft_local.yaml --autocommit` → export. The final
numbers land in `submission/REPORT.md`. Copy `submission/` to the checkpoint dir so it persists.
The GPU config runs fixed p32_b1, p32_b2, p160_b1, and p160_b2 workloads each trial. Its aggregate
is total generated tokens divided by synchronized elapsed time and each shape must retain at least
98% of its parent's median throughput.

### Watch it live (optional)
On the H100: `hotpath serve configs/dryft_local.yaml`. On your laptop:
`ssh -L 8765:localhost:8765 <host>` then open http://127.0.0.1:8765.

## Key facts to know
- **`--autocommit` is required** — Hotpath measures committed code only; it refuses a dirty target
  without it.
- **On Linux/H100 Triton works**, so `torch.compile(mode="reduce-overhead")` and CUDA graphs — the
  biggest launch-overhead wins — are available (they were impossible on the Windows laptop). Those
  strategies are already in the `dryft_local.yaml` menu; expect much bigger, stabler speedups there.
- The bundled transformer is **tiny and launch-bound**, so the honest headline needs either the
  **real Dryft model** or enlarging the stand-in (more layers / d_model) for a compute-bound demo.
- Keys live in `.env` (gitignored). Never commit them. Rotate after the event.
- A teammate left two files staged but uncommitted (`.github/workflows/ci.yml`,
  `configs/torch_transformer_h100_openai.yaml`) — decide whether to commit them.

## If you hand this to an AI assistant, tell it:
"Continue the Baseten/H100 work in this repo per `docs/BASETEN_HANDOFF.md`. The worker is already on
Baseten (Kimi-K2.7-Code) and validated on the CPU demo. Get an H100 via
`truss train workstation --accelerator h100`, SSH in, run `scripts/h100_setup.sh`, and report the
baseline vs optimized tokens/sec. Swap in Dryft's real model + correctness test if provided."
