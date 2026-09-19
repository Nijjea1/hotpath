"""Every failure mode must become a structured status, never an exception out of run_experiment."""
import asyncio
from pathlib import Path

import pytest

from hotpath.harness import Harness
from hotpath.schema import Edit, Experiment, ExperimentStatus, HotpathConfig, Hypothesis
from hotpath.store import Store
from hotpath.workspace import Workspace

H = Hypothesis(idea="test", strategy="s", target_file="mod.py", rationale="r", risk="low")
FAST = Edit(file="mod.py", search="    out = []\n    for i in range(n):\n        if i not in out:\n            out.append(i)\n",
            replace="    out = []\n    seen = set()\n    for i in range(n):\n        if i not in seen:\n            seen.add(i)\n            out.append(i)\n")
WRONG = Edit(file="mod.py", search="    return len(out)", replace="    return len(out) + 1")
SLOW = Edit(file="mod.py", search="    return len(out)", replace="    sum(x * x for x in range(n * 400))\n    return len(out)")
HANG = Edit(file="mod.py", search="    return len(out)", replace="    import time; time.sleep(30)\n    return len(out)")
CRASH = Edit(file="mod.py", search="def work(n):", replace="def work(n):\n    raise SystemExit(3)")


@pytest.fixture
def harness(cfg: HotpathConfig, ws: Workspace, store: Store) -> Harness:
    return Harness(cfg, ws, store)


@pytest.fixture
async def baseline(harness: Harness, ws: Workspace):
    bench, noise, medians = await harness.measure_baseline(ws.head())
    return bench, noise


def _exp(ws, edits, run_id="run_t"):
    e = Experiment(run_id=run_id, iteration=1, hypothesis=H, parent_commit=ws.head(), edits=edits)
    return e


async def test_baseline_measures_noise_and_requires_passing_tests(harness, ws, baseline):
    bench, noise = baseline
    assert bench.n >= 16 and bench.median > 0 and noise >= 0.0


async def test_accepted_path(harness, ws, store, baseline):
    bench, noise = baseline
    e = await harness.run_experiment(_exp(ws, [FAST]), bench, bench, noise)
    assert e.status == ExperimentStatus.accepted, e.reject_reason
    assert e.correctness.passed and e.benchmark and e.comparison.significant and e.commit
    assert e.files_changed == ["mod.py"] and "seen = set()" in e.diff
    assert {"patch", "test", "bench", "total"} <= set(e.timings)
    assert store.get_experiment(e.id).status == ExperimentStatus.accepted
    assert not (ws.worktrees_dir / e.id).exists(), "worktree cleaned up"


async def test_wrong_output_is_rejected_by_tests_and_never_benchmarked(harness, ws, baseline):
    bench, noise = baseline
    e = await harness.run_experiment(_exp(ws, [WRONG]), bench, bench, noise)
    assert e.status == ExperimentStatus.rejected_correctness
    assert e.correctness and not e.correctness.passed and e.benchmark is None and e.commit is None


async def test_slower_is_rejected_by_benchmark(harness, ws, baseline):
    bench, noise = baseline
    e = await harness.run_experiment(_exp(ws, [SLOW]), bench, bench, noise)
    assert e.status == ExperimentStatus.rejected_speed
    assert e.correctness.passed and e.benchmark is not None and e.commit is None
    assert "slower" in e.reject_reason


async def test_locked_file_is_blocked_before_anything_runs(harness, ws, baseline):
    bench, noise = baseline
    e = await harness.run_experiment(_exp(ws, [Edit(file="tests/check.py", search="assert work(50) == 50", replace="pass")]), bench, bench, noise)
    assert e.status == ExperimentStatus.locked_file and e.correctness is None and e.diff == ""


async def test_malformed_patch(harness, ws, baseline):
    bench, noise = baseline
    e = await harness.run_experiment(_exp(ws, [Edit(file="mod.py", search="nope", replace="x")]), bench, bench, noise)
    assert e.status == ExperimentStatus.patch_failed and "not found" in e.reject_reason


async def test_test_timeout_becomes_status(harness, ws, baseline, cfg):
    cfg.timeouts.test = 2
    bench, noise = baseline
    e = await harness.run_experiment(_exp(ws, [HANG]), bench, bench, noise)
    assert e.status == ExperimentStatus.timeout and e.correctness.timed_out


async def test_crash_in_tests(harness, ws, baseline):
    bench, noise = baseline
    e = await harness.run_experiment(_exp(ws, [CRASH]), bench, bench, noise)
    assert e.status == ExperimentStatus.rejected_correctness and e.correctness.exit_code == 3


async def test_benchmark_that_prints_no_json_is_error_not_crash(harness, ws, baseline, cfg):
    bench, noise = baseline
    cfg.bench_cmd = "python -c \"print('no measurement')\""
    e = await harness.run_experiment(_exp(ws, [FAST]), bench, bench, noise)
    assert e.status == ExperimentStatus.error and "no valid measurement" in e.reject_reason


async def test_benchmarks_run_serially_but_tests_in_parallel(harness, ws, store, baseline, cfg, monkeypatch):
    """With two accepted-quality candidates, the bench semaphore keeps benchmarks from overlapping."""
    bench, noise = baseline
    import hotpath.harness as hmod
    order: list[str] = []
    real = hmod.run_target

    async def spy(cmd, cwd, timeout, execution):
        tag = "bench" if cmd == cfg.bench_cmd else "test"
        order.append(tag + ":start")
        r = await real(cmd, cwd, timeout, execution)
        order.append(tag + ":end")
        return r

    monkeypatch.setattr(hmod, "run_target", spy)
    await asyncio.gather(harness.run_experiment(_exp(ws, [FAST]), bench, bench, noise),
                         harness.run_experiment(_exp(ws, [FAST]), bench, bench, noise),
                         harness.run_experiment(_exp(ws, [FAST]), bench, bench, noise))
    # Nothing else may be running while a benchmark is in flight.
    depth = 0
    for ev in order:
        if ev == "bench:start":
            assert depth == 0, f"benchmark started while something else ran: {order}"
            depth = -1
        elif ev == "bench:end":
            depth = 0
        elif ev == "test:start":
            assert depth >= 0, f"test started during a benchmark: {order}"
            depth += 1
        else:
            depth -= 1
    assert order.count("bench:start") == 3


async def test_profile_summary(harness, ws):
    p = await harness.run_profile(ws.head())
    assert p.tool == "cProfile" and p.hotspots and p.hotspots[0].file == "mod.py"

async def test_parent_rebenchmark_failure_is_not_stale_fallback(harness, ws, baseline, cfg, monkeypatch):
    from hotpath.benchmark import BenchmarkParseError
    bench, noise = baseline
    cfg.benchmark.rebenchmark_parent = True
    count = 0
    async def measure(cwd):
        nonlocal count
        count += 1
        if count == 2: raise BenchmarkParseError('parent failed')
        return bench
    monkeypatch.setattr(harness, 'run_benchmark', measure)
    exp = await harness.run_experiment(_exp(ws, [FAST]), bench, bench, noise)
    assert exp.status == ExperimentStatus.error
    assert 'parent rebenchmark failed' in exp.reject_reason
    assert exp.comparison is None and exp.commit is None
