"""Tokens/sec across a fixed 2x2 prompt-length/batch-size workload matrix."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import torch
import gc
import time
from hotpath.benchlib import emit
from model import build_model, generate

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
model = build_model(seed=0, device=DEVICE)
g = torch.Generator().manual_seed(123)
# The IDs are declared in the configs and stay in this order for every trial. An aggregate
# sample is total generated tokens / total synchronized elapsed time across all four shapes.
SPECS = [("p32_b1", 32, 1, 128), ("p32_b2", 32, 2, 128),
         ("p160_b1", 160, 1, 128), ("p160_b2", 160, 2, 128)]
WORKLOADS = [(ident, torch.randint(0, 256, (batch, prompt), generator=g).to(DEVICE), n_new)
             for ident, prompt, batch, n_new in SPECS]


def timed_generate(prompt, n_new):
    if DEVICE == "cuda":
        torch.cuda.synchronize()
        start, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        start.record(); generate(model, prompt, n_new); end.record(); torch.cuda.synchronize()
        return start.elapsed_time(end) / 1000.0
    start = time.perf_counter(); generate(model, prompt, n_new)
    return time.perf_counter() - start


for _ in range(2):
    for _, prompt, n_new in WORKLOADS:
        generate(model, prompt, n_new)
    if DEVICE == "cuda": torch.cuda.synchronize()

trials = 6 if DEVICE == "cuda" else 5
per_workload = {ident: [] for ident, _, _ in WORKLOADS}
aggregate = []
for _ in range(trials):
    gc.collect()
    elapsed = 0.0
    generated = 0
    for ident, prompt, n_new in WORKLOADS:
        dt = timed_generate(prompt, n_new)
        tokens = prompt.shape[0] * n_new
        per_workload[ident].append(tokens / dt)
        elapsed += dt
        generated += tokens
    aggregate.append(generated / elapsed)

emit(aggregate, metric="tokens_per_s", higher_is_better=True, device=DEVICE,
     workloads=[{"id": ident, "prompt_tokens": prompt_tokens, "batch_size": batch_size,
                 "generated_tokens": generated_tokens, "samples": per_workload[ident]}
                for ident, prompt_tokens, batch_size, generated_tokens in SPECS])
