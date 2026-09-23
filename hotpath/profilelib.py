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
                      # pstats has aggregated caller edges, but no observed stack samples. Turning
                      # those edges into a tree would fabricate a call hierarchy for recursion and
                      # multi-caller functions, so the dashboard must say it is unavailable.
                      "flamegraph_unavailable_reason": (
                          "cProfile stores aggregated caller edges, not observed call stacks"),
                      "hotspots": rows[:top]}), flush=True)


def torch_run(fn: Callable[[], object], *, top: int = 30) -> None:
    """Profile a torch workload; hotspots are kernel/op names with device time."""
    import torch
    from torch.profiler import ProfilerActivity, profile
    from hotpath.benchlib import torch_backend, torch_device, torch_synchronize

    device = torch_device(torch_module=torch)
    backend = torch_backend(device, torch_module=torch)
    accelerator_activity = (getattr(ProfilerActivity, "CUDA", None) if backend in {"cuda", "rocm"}
                            else getattr(ProfilerActivity, "XPU", None) if backend == "xpu" else None)
    acts = [ProfilerActivity.CPU] + ([accelerator_activity] if accelerator_activity is not None else [])
    with profile(activities=acts, record_shapes=False, with_stack=True) as prof:
        fn()
        torch_synchronize(device, torch_module=torch)
    avgs = prof.key_averages()

    def _device_times(a):
        return (getattr(a, "self_device_time_total", None) or getattr(a, "self_cuda_time_total", 0.0),
                getattr(a, "device_time_total", None) or getattr(a, "cuda_time_total", 0.0))

    # Prefer GPU kernel time, but fall back to CPU op time when device timing is unavailable
    # (e.g. CUPTI failed to initialize on this driver/GPU — common on Windows/WDDM and new
    # architectures). Without this fallback the whole profile is zeros and the planner is blind.
    use_device_time = accelerator_activity is not None and sum(_device_times(a)[0] for a in avgs) > 0.0
    tool = f"torch.profiler ({backend} device time)" if use_device_time else f"torch.profiler ({backend}; cpu-time fallback)"
    rows, total = [], 0.0
    for a in avgs:
        if use_device_time:
            self_t, tot_t = _device_times(a)
        else:
            self_t, tot_t = a.self_cpu_time_total, a.cpu_time_total
        total += self_t
        rows.append({"function": a.key, "file": "", "line": 0, "self_time": self_t / 1e6, "total_time": tot_t / 1e6, "pct": 0.0, "calls": a.count})
    for r in rows:
        r["pct"] = 100.0 * r["self_time"] * 1e6 / (total or 1)
    rows.sort(key=lambda r: r["self_time"], reverse=True)
    output = {"hotpath_profile": 1, "tool": tool, "total_time": total / 1e6,
              "n_functions_total": len(rows), "completeness_known": True,
              "device": device, "backend": backend, "hotspots": rows[:top]}
    if use_device_time:
        # CPU parentage does not reliably apportion asynchronous device work between parents. A
        # nested device-time graph would look precise while assigning kernel time to the wrong op.
        output["flamegraph_unavailable_reason"] = (
            "torch profiler records CPU event parentage, but not a reliable device-time call hierarchy")
    else:
        output["flamegraph"] = _torch_cpu_event_tree(prof.events(), max_events=max(top * 10, top))
        output["flamegraph_source"] = "torch.profiler CPU event tree"
        if not output["flamegraph"]:
            output["flamegraph_unavailable_reason"] = "torch profiler did not expose CPU event parentage"
    print(json.dumps(output), flush=True)


def _torch_cpu_event_tree(events, *, max_events: int) -> list[dict]:
    """Return a bounded tree of actual torch CPU events, never an inferred op hierarchy.

    `FunctionEvent.cpu_parent`/`cpu_children` describe recorded event nesting. Values are kept in
    CPU time because this helper is called only for the CPU-time fallback path. The event cap keeps
    profile JSON bounded; dropping a child never changes a remaining parent-child relationship.
    """
    event_list = list(events)
    known = {id(e): e for e in event_list}
    roots = [e for e in event_list if id(getattr(e, "cpu_parent", None)) not in known]
    budget = [max_events]

    def frame(event):
        if budget[0] <= 0:
            return None
        budget[0] -= 1
        children = []
        for child in getattr(event, "cpu_children", ()) or ():
            if id(child) not in known:
                continue
            nested = frame(child)
            if nested is not None:
                children.append(nested)
        return {
            "function": str(getattr(event, "key", getattr(event, "name", "torch event"))),
            "file": "", "line": 0,
            "self_time": float(getattr(event, "self_cpu_time_total", 0.0)) / 1e6,
            "total_time": float(getattr(event, "cpu_time_total", 0.0)) / 1e6,
            "calls": 1,
            "children": children,
        }

    tree = []
    for event in roots:
        nested = frame(event)
        if nested is not None:
            tree.append(nested)
    return tree
