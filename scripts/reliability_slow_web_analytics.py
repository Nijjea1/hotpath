"""Run the offline slow_web_analytics demo ten times and verify repeatable evidence.

This intentionally wipes only the target's generated `.hotpath` workspace before each
run. Source files and the target's Git history stay intact, so each measurement begins
from the same target code while avoiding resume state from a previous run.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "targets" / "slow_web_analytics"
CONFIG = ROOT / "configs" / "slow_web_analytics.yaml"
WORKDIR = TARGET / ".hotpath"
REPORT = ROOT / "reports" / "slow_web_analytics_reliability.json"
EXPECTED_SHIPPED = ("Aggregate request totals in one dictionary pass",)
EXPECTED_STATUS_COUNTS = {
    "accepted": 1,
    "rejected_correctness": 1,
    "locked_file": 1,
}


def evidence_for_latest_run() -> dict[str, object]:
    from hotpath.store import Store

    run = Store(WORKDIR / "hotpath.db").latest_run()
    if run is None:
        raise RuntimeError("run completed without a persisted run record")
    experiments = Store(WORKDIR / "hotpath.db").list_experiments(run.id)
    by_id = {e.id: e for e in experiments}
    shipped = []
    current = by_id.get(run.head_experiment_id or "")
    while current is not None:
        shipped.append(current.hypothesis.idea)
        current = by_id.get(current.parent_id or "")
    shipped.reverse()
    status_counts = dict(sorted(Counter(e.status.value for e in experiments).items()))
    if not shipped:
        raise RuntimeError(f"run {run.id} shipped no accepted change")
    if tuple(shipped) != EXPECTED_SHIPPED or status_counts != EXPECTED_STATUS_COUNTS:
        raise RuntimeError(f"run {run.id} has unexpected evidence: shipped={shipped}, statuses={status_counts}")
    worktrees = WORKDIR / "worktrees"
    leaks = sorted(p.name for p in worktrees.iterdir()) if worktrees.exists() else []
    if leaks:
        raise RuntimeError(f"run {run.id} leaked worktrees: {leaks}")
    return {"run_id": run.id, "shipped": shipped, "status_counts": status_counts,
            "best_speedup": run.best_speedup, "baseline_noise_cv": run.baseline_noise_cv}


def main() -> int:
    observations: list[dict[str, object]] = []
    expected = (EXPECTED_SHIPPED, tuple(sorted(EXPECTED_STATUS_COUNTS.items())))
    for attempt in range(1, 11):
        # The script may delete only this generated workspace, whose resolved path is verified
        # before every attempt. It never touches source files or the target's Git history.
        expected_workdir = TARGET.resolve() / ".hotpath"
        if WORKDIR.is_symlink() or WORKDIR.resolve() != expected_workdir:
            raise RuntimeError(f"unsafe generated workdir: {WORKDIR}")
        if WORKDIR.exists():
            shutil.rmtree(WORKDIR)
        started = time.perf_counter()
        local_env = {**os.environ, "SENTRY_DSN": "",
                     "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
                     "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
        result = subprocess.run([sys.executable, "-m", "hotpath.cli", "run", str(CONFIG), "--autocommit"],
                                cwd=ROOT, text=True, capture_output=True, timeout=90,
                                env=local_env)
        elapsed = time.perf_counter() - started
        if result.returncode:
            raise RuntimeError(f"attempt {attempt} failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")
        row = evidence_for_latest_run()
        row["attempt"] = attempt
        row["elapsed_s"] = elapsed
        signature = (tuple(row["shipped"]), tuple(sorted(row["status_counts"].items())))
        if signature != expected:
            raise RuntimeError(f"attempt {attempt} was not stable: got {signature}, expected {expected}")
        observations.append(row)
        print(f"attempt {attempt}/10: {elapsed:.2f}s, {row['best_speedup']:.3f}x, {row['shipped']}")
    report = {"target": str(TARGET), "attempts": observations, "stable_signature": {
        "shipped": list(expected[0]), "status_counts": dict(expected[1])}}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["stable_signature"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
