# torch_transformer target (Dryft mode)

A small GPT-style decoder with a deliberately naive `generate()`:
the KV cache grows with `torch.cat` every step and there is a CPU-GPU sync (`.item()`) per token.
This is the shape of the Dryft challenge: the harness measures **tokens/sec** with CUDA events,
and the locked tests require greedy tokens to match a frozen reference model exactly and logits
to stay within tolerance.

- `model.py`: editable. The model and generation loop.
- `tests/reference_model.py`: locked. A frozen copy of the original model + naive decode.
- `tests/check.py`: locked. Exact-token match on several prompts and lengths; logits within `atol`.
- `bench.py`: locked. `hotpath.benchlib.torch_run` with warmup, CUDA events, varied prompt lengths.
- `hotprofile.py`: locked. `torch.profiler` summary.

Swap in the real Dryft model by replacing `model.py` and pointing `reference_model.py` at Dryft's
reference, and keep their correctness definition in `check.py`.

This target was written for a GPU box and falls back to CPU; it was not executed in the
environment Hotpath was authored in (no torch installed there), so run `python tests/check.py`
and `python bench.py` once by hand before starting a search.
