"""The verification harness. The model never touches this; the harness decides what passes.

run_experiment never raises on a bad candidate. Every failure mode becomes a structured
status with a reason, so the run continues and the planner can learn from it.
"""
from __future__ import annotations

import asyncio
import time
import traceback
from pathlib import Path

from hotpath import observability as obs
from hotpath.benchmark import BenchmarkParseError, compare, noise_cv, stats_from_output
from hotpath.profiler import ProfileParseError, parse_profile_output
from hotpath.runner import tail
from hotpath.execution import run_target
from hotpath.schema import (BenchmarkStats, CorrectnessResult, Experiment, ExperimentStatus, HotpathConfig,
                            ProfileSummary)
from hotpath.store import Store
from hotpath.workspace import LockedFileError, PatchError, Workspace


class QuietLock:
    """Readers (tests) may overlap up to a limit; a writer (benchmark) runs alone.

    A benchmark that shares the machine with a sibling's test run is measuring contention,
    not code. With exclusive=False (GPU targets where tests and benches use different
    resources) benchmarks only serialize against each other.
    """

    def __init__(self, max_readers: int, max_writers: int, exclusive: bool = True):
        self.max_readers = max_readers
        self.max_writers = max_writers
        self.exclusive = exclusive
        self.active_readers = 0
        self.active_writers = 0
        self.waiting_writers = 0
        self.cond = asyncio.Condition()

    async def read(self):
        return _Ctx(self, writer=False)

    async def write(self):
        return _Ctx(self, writer=True)


class _Ctx:
    def __init__(self, lock: QuietLock, writer: bool):
        self.l, self.writer = lock, writer

    async def __aenter__(self):
        l = self.l
        async with l.cond:
            if self.writer:
                l.waiting_writers += 1
                try:
                    await l.cond.wait_for(lambda: l.active_writers < l.max_writers
                                          and (not l.exclusive or l.active_readers == 0))
                finally:
                    l.waiting_writers -= 1
                    l.cond.notify_all()
                l.active_writers += 1
            else:
                # Writers get priority so a queued benchmark is not starved by a stream of tests.
                await l.cond.wait_for(lambda: l.active_readers < l.max_readers
                                      and (not l.exclusive or (l.active_writers == 0 and l.waiting_writers == 0)))
                l.active_readers += 1

    async def __aexit__(self, *a):
        l = self.l
        async with l.cond:
            if self.writer:
                l.active_writers -= 1
            else:
                l.active_readers -= 1
            l.cond.notify_all()


class Harness:
    def __init__(self, cfg: HotpathConfig, ws: Workspace, store: Store):
        self.cfg = cfg
        self.ws = ws
        self.store = store
        self.lock = QuietLock(cfg.search.max_parallel_tests, cfg.search.max_parallel_benchmarks,
                              exclusive=cfg.benchmark.exclusive)

    # -- primitives ---------------------------------------------------------
    async def run_tests(self, cwd: Path) -> CorrectnessResult:
        with obs.span("hotpath.test", "correctness tests", cwd=str(cwd)):
            res = await run_target(self.cfg.test_cmd, cwd, self.cfg.timeouts.test, self.cfg.execution)
        return CorrectnessResult(passed=(res.exit_code == 0 and not res.timed_out), exit_code=res.exit_code,
                                 duration_s=res.duration_s, timed_out=res.timed_out,
                                 output_tail=tail(res.stdout + "\n" + res.stderr))

    async def run_benchmark(self, cwd: Path) -> BenchmarkStats:
        async with await self.lock.write():
            with obs.span("hotpath.bench", "benchmark", cwd=str(cwd)) as sp:
                res = await run_target(self.cfg.bench_cmd, cwd, self.cfg.timeouts.bench, self.cfg.execution)
                if res.timed_out:
                    raise TimeoutError(f"benchmark timed out after {self.cfg.timeouts.bench}s")
                if res.exit_code != 0:
                    raise BenchmarkParseError(f"benchmark exited {res.exit_code}: {tail(res.stderr, 20)}")
                stats = stats_from_output(res.stdout, res.duration_s, tail(res.stdout + res.stderr, 20),
                                          required_workloads=self.cfg.benchmark.required_workloads)
                sp.set_data("median", stats.median)
                sp.set_data("cv", stats.cv)
        return stats

    async def run_profile(self, commit: str) -> ProfileSummary:
        if not self.cfg.profile_cmd:
            return ProfileSummary(note="no profile_cmd configured", commit=commit)
        wt = self.ws.create_worktree(commit, f"profile_{commit[:8]}")
        try:
            with obs.span("hotpath.profile", "profile", commit=commit):
                res = await run_target(self.cfg.profile_cmd, wt, self.cfg.timeouts.profile, self.cfg.execution)
            if res.exit_code != 0 or res.timed_out:
                return ProfileSummary(note=f"profile command failed: {tail(res.stderr, 10)}", commit=commit)
            try:
                return parse_profile_output(res.stdout, self.cfg.profile.retain, commit)
            except ProfileParseError as e:
                return ProfileSummary(note=str(e), commit=commit)
        finally:
            self.ws.remove_worktree(wt)

    async def measure_baseline(self, commit: str) -> tuple[BenchmarkStats, float, list[float]]:
        """Run the baseline several times. The spread of medians is the noise floor every candidate must beat."""
        wt = self.ws.create_worktree(commit, f"baseline_{commit[:8]}")
        try:
            with obs.span("hotpath.baseline", "baseline benchmark", repeats=self.cfg.benchmark.baseline_repeats):
                tests = await self.run_tests(wt)
                if not tests.passed:
                    raise RuntimeError("baseline does not pass its own tests:\n" + tests.output_tail)
                runs = [await self.run_benchmark(wt) for _ in range(self.cfg.benchmark.baseline_repeats)]
        finally:
            self.ws.remove_worktree(wt)
        medians = [r.median for r in runs]
        # Pool all samples so the CI has more data; noise comes from run-to-run medians.
        pooled = stats_from_output_samples(runs)
        return pooled, noise_cv(medians), medians

    # -- the experiment lifecycle ------------------------------------------
    async def run_experiment(self, exp: Experiment, parent_bench: BenchmarkStats, baseline_bench: BenchmarkStats,
                             noise: float) -> Experiment:
        t0 = time.perf_counter()
        wt: Path | None = None
        save = lambda: self.store.save_experiment(exp)  # noqa: E731
        try:
            with obs.span("hotpath.experiment", exp.hypothesis.idea, experiment_id=exp.id, strategy=exp.hypothesis.strategy):
                wt = self.ws.create_worktree(exp.parent_commit, exp.id)
                exp.log(f"worktree at {wt} from {exp.parent_commit[:8]}")
                # 1. Apply edits (locked paths are checked before anything is written)
                t = time.perf_counter()
                try:
                    with obs.span("hotpath.patch", "apply edits", n_edits=len(exp.edits)):
                        exp.files_changed, exp.diff = self.ws.apply_edits(wt, exp.edits, self.cfg.editable, self.cfg.locked)
                except LockedFileError as e:
                    exp.set_status(ExperimentStatus.locked_file, f"blocked: {e}")
                    return exp
                except PatchError as e:
                    exp.set_status(ExperimentStatus.patch_failed, str(e))
                    return exp
                finally:
                    exp.timings["patch"] = time.perf_counter() - t
                exp.log(f"changed {exp.files_changed}")
                # 2. Correctness gate
                exp.set_status(ExperimentStatus.testing); save()
                t = time.perf_counter()
                async with await self.lock.read():
                    exp.correctness = await self.run_tests(wt)
                exp.timings["test"] = time.perf_counter() - t
                if exp.correctness.timed_out:
                    exp.set_status(ExperimentStatus.timeout, f"tests exceeded {self.cfg.timeouts.test}s")
                    return exp
                if not exp.correctness.passed:
                    exp.set_status(ExperimentStatus.rejected_correctness,
                                   f"tests failed (exit {exp.correctness.exit_code})")
                    return exp
                exp.log("correctness: passed")
                # 3. Speed gate
                exp.set_status(ExperimentStatus.benchmarking); save()
                t = time.perf_counter()
                try:
                    exp.benchmark = await self.run_benchmark(wt)
                except TimeoutError as e:
                    exp.set_status(ExperimentStatus.timeout, str(e))
                    return exp
                except BenchmarkParseError as e:
                    exp.set_status(ExperimentStatus.error, f"benchmark produced no valid measurement: {e}")
                    return exp
                finally:
                    exp.timings["bench"] = time.perf_counter() - t
                # Interleaved re-benchmark: measure the parent again right now, adjacent to the
                # candidate, so drift since the start-of-run baseline cannot bias the decision.
                if self.cfg.benchmark.rebenchmark_parent and exp.parent_commit:
                    pwt = self.ws.create_worktree(exp.parent_commit, f"parent_{exp.id}")
                    try:
                        fresh_parent = await self.run_benchmark(pwt)
                    except (TimeoutError, BenchmarkParseError) as exc:
                        exp.set_status(ExperimentStatus.error, f"parent rebenchmark failed: {exc}")
                        return exp
                    finally:
                        self.ws.remove_worktree(pwt)
                    if fresh_parent is not None:
                        exp.log(f"re-benchmarked parent: median {fresh_parent.median:.5f} "
                                f"(was {parent_bench.median:.5f})")
                        parent_bench = fresh_parent
                exp.parent_benchmark = parent_bench.model_copy(deep=True)
                exp.comparison = compare(parent_bench, exp.benchmark, baseline_bench, self.cfg.benchmark, noise)
                exp.log(f"benchmark: median {exp.benchmark.median:.5f} {exp.benchmark.metric}, {exp.comparison.reason}")
                if not exp.comparison.significant:
                    exp.set_status(ExperimentStatus.rejected_speed, exp.comparison.reason)
                    return exp
                # 4. Keep it
                exp.commit = self.ws.commit(wt, exp.id, f"hotpath: {exp.hypothesis.idea}")
                exp.set_status(ExperimentStatus.accepted)
                obs.breadcrumb("hotpath", f"accepted {exp.id}: {exp.comparison.speedup_vs_parent:.3f}x")
                return exp
        except Exception as e:  # never let one experiment kill the run
            exp.log(traceback.format_exc())
            exp.set_status(ExperimentStatus.error, f"{type(e).__name__}: {e}")
            obs.capture(e, run_id=exp.run_id, experiment_id=exp.id)
            return exp
        finally:
            exp.timings["total"] = time.perf_counter() - t0
            if wt is not None:
                self.ws.remove_worktree(wt)
            save()


def stats_from_output_samples(runs: list[BenchmarkStats]) -> BenchmarkStats:
    from hotpath.benchmark import compute_stats
    if any(r.metric != runs[0].metric or r.higher_is_better != runs[0].higher_is_better
           or set(r.workload_samples) != set(runs[0].workload_samples) for r in runs[1:]):
        raise BenchmarkParseError("baseline benchmark metric or workload set changed across repeats")
    samples = [s for r in runs for s in r.samples]
    workloads = {workload_id: [value for run in runs for value in run.workload_samples[workload_id]]
                 for workload_id in runs[0].workload_samples}
    return compute_stats(samples, runs[0].metric, runs[0].higher_is_better,
                         duration_s=sum(r.duration_s for r in runs), output_tail=runs[-1].output_tail,
                         workload_samples=workloads)
