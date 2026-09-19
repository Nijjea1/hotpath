# Running Hotpath on a Baseten H100 workstation

**Hotpath runs *on* the H100.** It profiles, tests, and benchmarks your transformer directly on the
GPU, so the harness must live where the GPU is. The planner (OpenAI) and worker (Baseten Model API)
are network calls that go out from the H100 box. Nothing runs on the laptop for this.

Two Baseten products, don't confuse them:
- **Model API** (`inference.baseten.co/v1`) — the LLMs that are Hotpath's planner/worker. Already wired.
- **H100 workstation / training** — the GPU that runs *your transformer* for tokens/sec. Set up below.

## 1. Provision the H100 (Baseten)
1. Visit the **Baseten booth** so they enable training/workstation access for your workspace.
2. Install + sign in to the Baseten CLI, then **create an interactive GPU workstation** with an H100
   and connect to it. Follow Baseten's docs: "Create an interactive GPU workstation" and
   "Connect to a training environment." You end up with a shell on the H100.

## 2. Set up the repo on the H100
```bash
git clone https://github.com/Nijjea1/hotpath.git && cd hotpath
python -m venv .venv && source .venv/bin/activate    # Linux: source is correct here
pip install -e ".[dev]"
pip install torch --index-url https://download.pytorch.org/whl/cu128   # if torch isn't preinstalled
nvidia-smi                                            # expect an H100
python -c "import torch; print(torch.cuda.get_device_name(0), torch.cuda.is_available())"
```

## 3. Keys (network calls from the H100)
Create `.env` on the H100 (it's gitignored):
```
OPENAI_API_KEY=sk-...          # planner
BASETEN_API_KEY=...            # workers (Baseten Model API)
SENTRY_DSN=...                 # optional, Sentry track
```
⚠️ On a `backend: local` run, model-written code runs with the host env and open network — it can
read these keys. Use **spend-capped** keys, and **delete the VM** when done (it's disposable).

## 4. Baseline tokens/sec (before optimizing)
```bash
cd targets/torch_transformer
python tests/check.py     # correctness gate must pass (exit 0)
python bench.py           # prints {"metric":"tokens_per_s","samples":[...]} — your H100 baseline
cd ../..
```

## 5. Run the optimization search on the H100
```bash
hotpath run configs/dryft_local.yaml --autocommit
```
- `dryft_local.yaml` = local execution (no Docker image to build), Baseten worker
  (`moonshotai/Kimi-K2.7-Code`) + OpenAI planner (`gpt-4.1`), beam width 2, 6 iterations,
  `rebenchmark_parent` + `baseline_repeats: 5` for stable H100 measurement.
- Watch it live: `hotpath serve configs/dryft_local.yaml` on the H100, then from the laptop
  `ssh -L 8765:localhost:8765 <workstation>` and open http://127.0.0.1:8765.

## 6. Lock in the numbers
```bash
hotpath ablate  configs/dryft_local.yaml
hotpath export  configs/dryft_local.yaml submission/ --ablate
```
`submission/REPORT.md` has baseline vs optimized tokens/sec, the accepted chain, and the ablation.
Copy `submission/` (and `targets/torch_transformer/.hotpath/hotpath.db`) into Baseten's checkpoint
dir so it survives after the workstation stops.

## Expect bigger wins here than on the laptop
On Linux/H100, **Triton works**, so `torch.compile(mode="reduce-overhead")` and CUDA graphs — the
biggest launch-overhead cuts — are available (they were impossible on the Windows laptop). The H100
is also fast enough that per-token launch overhead dominates the tiny stand-in model, so the
compile/CUDA-graph strategies should land large, stable speedups. For the real headline, either:
- **Swap in Dryft's actual model** (`targets/torch_transformer/SWAP_DRYFT.md`), mirroring their exact
  `check.py` correctness, or
- Enlarge the stand-in model (more layers / d_model) for a more realistic, compute-bound demo.

## If model code must be isolated (shared/long-lived host)
Use `configs/dryft_h100.yaml` instead — it runs each candidate in a reviewed Docker container
(`hotpath-runner:h100-reviewed`, built per `docs/ISOLATION.md`). For a disposable hackathon VM,
`dryft_local.yaml` is the fast path.
