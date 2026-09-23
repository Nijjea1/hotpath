"""Helpers a target repository uses to emit Hotpath-compatible benchmark output.

    from hotpath.benchlib import run
    run(lambda: work(), warmup=3, trials=15)

For accelerator workloads use `torch_run`. It selects CUDA (including ROCm),
Intel XPU, Apple MPS, or CPU and synchronizes the selected backend correctly.
"""
from __future__ import annotations

import gc
import json
import random
import sys
import time
from typing import Callable, Optional


def torch_device(preferred: str | None = None, *, torch_module=None) -> str:
    """Choose an available PyTorch device without assuming NVIDIA CUDA.

    ROCm intentionally uses PyTorch's ``cuda`` device/API. ``torch_backend``
    distinguishes it for evidence and reporting. HOTPATH_TORCH_DEVICE can force
    a device; an unavailable forced device fails clearly instead of silently
    benchmarking the CPU.
    """
    import os

    torch = torch_module
    if torch is None:
        import torch as torch_import
        torch = torch_import
    requested = (preferred or os.environ.get("HOTPATH_TORCH_DEVICE") or "").strip().lower()
    checks = {
        "cuda": lambda: bool(torch.cuda.is_available()),
        "xpu": lambda: bool(getattr(torch, "xpu", None) and torch.xpu.is_available()),
        "mps": lambda: bool(getattr(getattr(torch, "backends", None), "mps", None)
                            and torch.backends.mps.is_available()),
        "cpu": lambda: True,
    }
    if requested:
        kind = requested.split(":", 1)[0]
        if kind not in checks:
            raise ValueError(f"unsupported PyTorch device {requested!r}; use cuda, xpu, mps, or cpu")
        if not checks[kind]():
            raise RuntimeError(f"requested PyTorch device {requested!r} is not available")
        return requested
    return next(kind for kind in ("cuda", "xpu", "mps", "cpu") if checks[kind]())


def torch_backend(device: str, *, torch_module=None) -> str:
    """Return a human-readable backend name for retained benchmark evidence."""
    kind = device.split(":", 1)[0]
    if kind != "cuda":
        return kind
    torch = torch_module
    if torch is None:
        import torch as torch_import
        torch = torch_import
    return "rocm" if getattr(getattr(torch, "version", None), "hip", None) else "cuda"


def torch_synchronize(device: str, *, torch_module=None) -> None:
    """Wait for queued work on CUDA/ROCm, Intel XPU, or Apple MPS."""
    torch = torch_module
    if torch is None:
        import torch as torch_import
        torch = torch_import
    kind = device.split(":", 1)[0]
    if kind == "cuda":
        torch.cuda.synchronize(device)
    elif kind == "xpu":
        torch.xpu.synchronize(device)
    elif kind == "mps":
        torch.mps.synchronize()


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
    """Benchmark PyTorch work on any supported accelerator; ``fn`` returns work units."""
    import torch  # local import so CPU-only installs never need torch

    dev = torch_device(device, torch_module=torch)
    backend = torch_backend(dev, torch_module=torch)
    torch.manual_seed(seed)
    if dev.split(":", 1)[0] == "cuda":
        torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.benchmark = False
    samples: list[float] = []
    for _ in range(warmup):
        fn()
        torch_synchronize(dev, torch_module=torch)
    for _ in range(trials):
        gc.collect()
        if dev.split(":", 1)[0] == "cuda":
            torch_synchronize(dev, torch_module=torch)
            start, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
            start.record()
            n = fn()
            end.record()
            torch_synchronize(dev, torch_module=torch)
            dt = start.elapsed_time(end) / 1000.0
        else:
            torch_synchronize(dev, torch_module=torch)
            t0 = time.perf_counter(); n = fn(); torch_synchronize(dev, torch_module=torch)
            dt = time.perf_counter() - t0
        samples.append(float(n) / dt if metric != "seconds" else dt)
    emit(samples, metric=metric, higher_is_better=(metric != "seconds"), device=dev, backend=backend)
    return samples


if __name__ == "__main__":  # smoke test
    run(lambda: sum(range(200000)), warmup=1, trials=3)
    sys.exit(0)
