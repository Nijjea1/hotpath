"""Correctness gate: greedy tokens must match the frozen reference exactly; logits within tolerance."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import torch  # noqa: E402

from model import build_model, generate, logits_for  # noqa: E402
from reference_model import build_reference, ref_generate  # noqa: E402

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
ATOL = 1e-3 if DEVICE == "cuda" else 1e-4
failures = 0

model = build_model(seed=0, device=DEVICE)
ref = build_reference(seed=0, device=DEVICE)
# Same seed, same architecture: weights must already be identical. Guard against a patch changing init.
for (n1, p1), (n2, p2) in zip(model.state_dict().items(), ref.state_dict().items()):
    if p1.shape != p2.shape or not torch.equal(p1, p2):
        print(f"FAIL weight mismatch {n1}"); failures += 1

for seed, plen, batch, n_new in [(1, 8, 1, 32), (2, 64, 1, 64), (3, 1, 1, 96),
                                 (4, 200, 1, 16), (5, 32, 2, 32), (6, 160, 2, 16)]:
    g = torch.Generator().manual_seed(seed)
    prompt = torch.randint(0, 256, (batch, plen), generator=g).to(DEVICE)
    got = generate(model, prompt, n_new)
    want = ref_generate(ref, prompt, n_new)
    if got.shape != want.shape or not torch.equal(got, want):
        first = (got != want).nonzero(as_tuple=False)[0].tolist() if got.shape == want.shape else None
        print(f"FAIL tokens seed={seed} batch={batch} plen={plen} n_new={n_new}: first divergence at {first}"); failures += 1
    else:
        print(f"ok   tokens seed={seed} batch={batch} plen={plen} n_new={n_new}")
    lg, lw = logits_for(model, want), ref(want)
    diff = (lg - lw).abs().max().item()
    if diff > ATOL:
        print(f"FAIL logits seed={seed}: max abs diff {diff:.2e} > {ATOL}"); failures += 1
    else:
        print(f"ok   logits seed={seed} max abs diff {diff:.2e}")

sys.exit(1 if failures else 0)
