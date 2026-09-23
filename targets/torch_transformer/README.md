# torch_transformer target (Dryft mode)

A small GPT-style decoder with a deliberately naive `generate()`:
the KV cache grows with `torch.cat` every step and there is a CPU-GPU sync (`.item()`) per token.
This is the shape of the Dryft challenge: the harness measures **tokens/sec** with synchronized
device timing, and the locked tests require greedy tokens to match a frozen reference model exactly
and logits to stay within tolerance.

- `model.py`: editable. The model and generation loop.
- `tests/reference_model.py`: locked. A frozen copy of the original model + naive decode.
- `tests/check.py`: locked. Exact-token match on several prompts and lengths; logits within `atol`.
- `bench.py`: locked. Fixed varied prompt/batch workloads with CUDA/ROCm events or synchronized
  wall-clock timing on Intel XPU, Apple MPS, and CPU.
- `hotprofile.py`: locked. `torch.profiler` summary.
- `kernels/*.py`: editable. Kernel implementations belong here, with explicit backend dispatch and
  a correct PyTorch fallback when a kernel applies only to selected devices/shapes/dtypes.

Swap in the real Dryft model by replacing `model.py` and pointing `reference_model.py` at Dryft's
reference, and keep their correctness definition in `check.py`.

Device selection order is CUDA/ROCm, Intel XPU, Apple MPS, then CPU. Set
`HOTPATH_TORCH_DEVICE=cuda`, `xpu`, `mps`, or `cpu` to require one explicitly; an unavailable
requested device fails instead of silently measuring CPU. Run `hotpath doctor --require-gpu`,
then `python tests/check.py` and `python bench.py` once before starting a search.
