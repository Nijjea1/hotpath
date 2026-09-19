"""Tokens/sec across varied prompt lengths, measured with CUDA events when available."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import torch
from hotpath.benchlib import torch_run
from model import build_model, generate

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
model = build_model(seed=0, device=DEVICE)
g = torch.Generator().manual_seed(123)
# Realistic decode workload: long generations across a couple of prompt shapes. Long sequences are
# what make the KV cache growth and per-token launch overhead actually matter; the varied shapes are
# a hidden-workload defense so tuning to one length does not win.
WORKLOADS = [(torch.randint(0, 256, (1, p), generator=g).to(DEVICE), n) for p, n in [(32, 256), (160, 256)]]


def step() -> int:
    total = 0
    for prompt, n_new in WORKLOADS:
        generate(model, prompt, n_new)
        total += n_new
    return total


torch_run(step, warmup=2, trials=6 if DEVICE == "cuda" else 5, seed=0, metric="tokens_per_s", device=DEVICE)
