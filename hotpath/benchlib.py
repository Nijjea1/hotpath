"""Helpers a target repository uses to emit Hotpath-compatible benchmark output.

    from hotpath.benchlib import run
    run(lambda: work(), warmup=3, trials=15)

For GPU workloads use `run(..., sync=torch.cuda.synchronize)` or `torch_run` which uses
CUDA events and can report tokens/sec.
"""
from __future__ import annotations

import gc
import json
import random
import sys
import time
from typing import Callable, Optional


def emit(samples: list[float], metric: str = "seconds", higher_is_better: bool = False, **extra) -> None:
    print(json.dumps({"hotpath_benchmark": 1, "metric": metric, "higher_is_better": higher_is_better,
                      "samples": samples, **extra}), flush=True)


def run(fn: Callable[[], object], *, warmup: int = 3, trials: int = 10, seed: int = 0,
        setup: Optional[Callable[[], None]] = None, sync: Optional[Callable[[], None]] = None,
        metric: str = "seconds", disable_gc: bool = True) -> list[float]:
    random.seed(seed)
    try:
        import numpy as np  # type: ignore
        np.random.seed(seed)
    except Exception:
        pass
    for _ in range(warmup):
        if setup: setup()
        fn()
        if sync: sync()
    samples: list[float] = []
    for _ in range(trials):
        if setup: setup()
        gc.collect()
        if disable_gc: gc.disable()
        try:
            t0 = time.perf_counter()
            out = fn()
            if sync: sync()
            dt = time.perf_counter() - t0
        finally:
            if disable_gc: gc.enable()
        if metric == "seconds":
            samples.append(dt)
        else:  # fn returned a count of work units (e.g. tokens); report rate
            samples.append(float(out) / dt)  # type: ignore[arg-type]
    emit(samples, metric=metric, higher_is_better=(metric != "seconds"))
    return samples


def torch_run(fn: Callable[[], int], *, warmup: int = 3, trials: int = 10, seed: int = 0,
              metric: str = "tokens_per_s", device: str | None = None) -> list[float]:
    """Benchmark a GPU function with CUDA events. fn returns the number of tokens (or units) produced."""
    import torch  # local import so CPU-only installs never need torch

    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(seed)
    if dev == "cuda":
        torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.benchmark = False
    samples: list[float] = []
    for _ in range(warmup):
        fn()
        if dev == "cuda": torch.cuda.synchronize()
    for _ in range(trials):
        gc.collect()
        if dev == "cuda":
            torch.cuda.synchronize()
            start, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
            start.record()
            n = fn()
            end.record()
            torch.cuda.synchronize()
            dt = start.elapsed_time(end) / 1000.0
        else:
            t0 = time.perf_counter(); n = fn(); dt = time.perf_counter() - t0
        samples.append(float(n) / dt if metric != "seconds" else dt)
    emit(samples, metric=metric, higher_is_better=(metric != "seconds"), device=dev)
    return samples


if __name__ == "__main__":  # smoke test
    run(lambda: sum(range(200000)), warmup=1, trials=3)
    sys.exit(0)
