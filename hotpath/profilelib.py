"""Helpers a target repository uses to emit Hotpath-compatible profile output."""
from __future__ import annotations

import cProfile
import json
import os
import pstats
from typing import Callable


def run(fn: Callable[[], object], *, top: int = 30, repeat: int = 1, root: str | None = None) -> None:
    root = os.path.abspath(root or os.getcwd())
    pr = cProfile.Profile()
    pr.enable()
    for _ in range(repeat):
        fn()
    pr.disable()
    st = pstats.Stats(pr)
    total = st.total_tt or 1e-12
    rows = []
    for (file, line, func), (cc, nc, tt, ct, _callers) in st.stats.items():
        if os.path.isabs(file):
            af = os.path.abspath(file)
            if not af.startswith(root + os.sep):
                continue  # library code outside the target is not actionable
            rel = os.path.relpath(af, root)
        elif file == "~":
            rel = ""  # C builtin (e.g. list.count): shown so the planner sees where time goes, but not editable
        else:
            continue  # frozen / stdlib modules
        rows.append({"function": func, "file": rel, "line": line,
                     "self_time": tt / repeat, "total_time": ct / repeat, "pct": 100.0 * tt / total, "calls": nc})
    rows.sort(key=lambda r: r["self_time"], reverse=True)
    print(json.dumps({"hotpath_profile": 1, "tool": "cProfile", "total_time": total / repeat,
                      "n_functions_total": len(rows), "completeness_known": True,
                      "hotspots": rows[:top]}), flush=True)


def torch_run(fn: Callable[[], object], *, top: int = 30) -> None:
    """Profile a torch workload; hotspots are kernel/op names with device time."""
    import torch
    from torch.profiler import ProfilerActivity, profile

    acts = [ProfilerActivity.CPU] + ([ProfilerActivity.CUDA] if torch.cuda.is_available() else [])
    with profile(activities=acts, record_shapes=False, with_stack=True) as prof:
        fn()
        if torch.cuda.is_available():
            torch.cuda.synchronize()
    avgs = prof.key_averages()

    def _device_times(a):
        return (getattr(a, "self_device_time_total", None) or getattr(a, "self_cuda_time_total", 0.0),
                getattr(a, "device_time_total", None) or getattr(a, "cuda_time_total", 0.0))

    # Prefer GPU kernel time, but fall back to CPU op time when device timing is unavailable
    # (e.g. CUPTI failed to initialize on this driver/GPU — common on Windows/WDDM and new
    # architectures). Without this fallback the whole profile is zeros and the planner is blind.
    use_cuda = torch.cuda.is_available() and sum(_device_times(a)[0] for a in avgs) > 0.0
    tool = "torch.profiler" if use_cuda else "torch.profiler (cpu-time fallback)"
    rows, total = [], 0.0
    for a in avgs:
        if use_cuda:
            self_t, tot_t = _device_times(a)
        else:
            self_t, tot_t = a.self_cpu_time_total, a.cpu_time_total
        total += self_t
        rows.append({"function": a.key, "file": "", "line": 0, "self_time": self_t / 1e6, "total_time": tot_t / 1e6, "pct": 0.0, "calls": a.count})
    for r in rows:
        r["pct"] = 100.0 * r["self_time"] * 1e6 / (total or 1)
    rows.sort(key=lambda r: r["self_time"], reverse=True)
    print(json.dumps({"hotpath_profile": 1, "tool": tool, "total_time": total / 1e6,
                      "n_functions_total": len(rows), "completeness_known": True,
                      "hotspots": rows[:top]}), flush=True)
