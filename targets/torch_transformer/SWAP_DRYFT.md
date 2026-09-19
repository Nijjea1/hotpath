# Swapping in the real Dryft model

Our `torch_transformer/` is a stand-in with the same *shape* as the Dryft challenge: an editable
model, a frozen reference, a correctness gate (exact greedy tokens + logit tolerance), a
tokens/sec benchmark over varied workloads, and a torch.profiler summary. To compete, replace the
stand-in with Dryft's real model — the Hotpath engine does not change.

## What each file is, and what to do with it

| File | Role | Action tomorrow |
|---|---|---|
| `model.py` | **Editable.** The model + generation loop the agent optimizes. | Replace with Dryft's model. Keep it importable as `build_model()` + `generate()` (or adjust `bench.py`/`check.py` imports to match their API). |
| `tests/reference_model.py` | **Locked.** Frozen, correct reference the agent can't touch. | Replace with Dryft's reference (or a frozen copy of their original model). |
| `tests/check.py` | **Locked.** Defines "correct". | **Mirror Dryft's exact correctness definition** — same prompts, same greedy decode, same logit tolerance (`atol`). This is the most important file to get right; learn their definition first. |
| `bench.py` | **Locked.** Prints `tokens_per_s` samples. | Use Dryft's workloads. Keep varied prompt lengths **and batch sizes** (their scoring uses hidden workloads — don't overfit one shape). Use `hotpath.benchlib.torch_run(..., metric="tokens_per_s")`. |
| `hotprofile.py` | **Locked.** torch.profiler summary. | Point it at Dryft's `generate`. On Linux/H100 CUPTI works, so device times are real (no CPU-time fallback needed). |
| `kernels/*.py` | **Editable.** Optional hand-written/Triton kernels. | Create this dir if Dryft's model has fusable ops; add it to `editable` (already in `configs/dryft_h100.yaml`). |

## Correctness is the whole game
A change that is faster but changes the output is worthless. Before the first search:
1. Confirm the untouched Dryft model **passes its own `check.py`** (Hotpath fails the run otherwise — by design).
2. Confirm `bench.py` prints a clean `{"hotpath_benchmark": 1, "metric": "tokens_per_s", "samples": [...]}` line.
3. Run both by hand once: `cd targets/<dryft> && python tests/check.py && python bench.py`.

## Then
```bash
export OPENAI_API_KEY=...            # planner
export BASETEN_API_KEY=...           # workers (set worker_base_url in the config)
hotpath run configs/dryft_h100.yaml
hotpath export configs/dryft_h100.yaml submission/ --ablate
```

## H100 notes (vs the RTX 5060 laptop we developed on)
- **Triton works on Linux/H100**, so `torch.compile(mode="reduce-overhead")` and CUDA graphs — the
  biggest launch-overhead wins — are available (they are not installable on our Windows laptop).
- **Stable clocks → low benchmark noise**, so small real wins register instead of being rejected
  as noise. Keep `rebenchmark_parent: true` and `baseline_repeats: 5`.
- On a **bigger, compute-bound** model, KV-cache preallocation and flash attention (SDPA) pay off
  much more than on our tiny stand-in, where the model was launch-bound and prealloc was rejected.
