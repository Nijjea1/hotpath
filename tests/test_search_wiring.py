"""Exercise the real search state machine with scripted, timing-independent measurements."""
from collections import deque

import pytest

from fake_provider import FakeProvider
from hotpath.orchestrator import Orchestrator, ResumeError
from hotpath.schema import (BenchmarkStats, CorrectnessResult, Edit, Experiment, ExperimentStatus, Hypothesis,
                            PatchResponse, PlanResponse, ProfileSummary, SpeedComparison)


def idea(name):
    return Hypothesis(idea=name, target_file="mod.py", rationale="less work", strategy="algorithm", risk="low")


def plan(*names):
    return PlanResponse(hypotheses=[idea(name) for name in names], notes="scripted")


def patch(value):
    return PatchResponse(edits=[Edit(file="mod.py", search="return 0", replace=f"return {value}")], reasoning="scripted")


def benchmark(median):
    return BenchmarkStats(samples=[median] * 5, n=5, median=median, mean=median, stdev=0, cv=0)


class FakeWorkspace:
    def __init__(self, root):
        self.root = root
        self.cleaned = False
        self.missing = set()

    def has_commit(self, sha):
        return sha not in self.missing

    def ensure_repo(self, autocommit=False):
        return "base"

    def current_branch(self):
        return "main"

    def materialize(self, commit, name):
        dest = self.root / name
        dest.mkdir(exist_ok=True)
        (dest / "mod.py").write_text(f"# parent {commit}\ndef work():\n    return 0\n")
        return dest

    def cleanup(self):
        self.cleaned = True


class FakeHarness:
    def __init__(self, store, outcomes):
        self.store, self.outcomes = store, deque(outcomes)
        self.parents = []
        self.baselines = 0

    async def measure_baseline(self, commit):
        self.baselines += 1
        return benchmark(100), 0, [100, 100]

    async def run_profile(self, commit):
        return ProfileSummary(commit=commit)

    async def run_experiment(self, exp, parent, baseline, noise):
        self.parents.append((exp.hypothesis.idea, parent.median))
        outcome = self.outcomes.popleft()
        if outcome is None:
            exp.correctness = CorrectnessResult(passed=False, exit_code=1, duration_s=0, output_tail="expected 0, got 1")
            exp.set_status(ExperimentStatus.rejected_correctness, "output mismatch")
        else:
            exp.benchmark = benchmark(outcome)
            exp.comparison = SpeedComparison(speedup_vs_parent=parent.median/outcome,
                speedup_vs_baseline=baseline.median/outcome, ci_low=1.1, ci_high=1.3,
                threshold=1.03, significant=True, reason="controlled measurement")
            exp.commit = exp.hypothesis.idea + "-" + exp.id
            exp.set_status(ExperimentStatus.accepted)
        self.store.save_experiment(exp)
        return exp


def setup(cfg, store, tmp_path, provider, outcomes, resume=None):
    orch = Orchestrator(cfg, store=store, planner=provider, worker=provider, resume=resume)
    orch.ws = FakeWorkspace(tmp_path)
    orch.harness = FakeHarness(store, outcomes)
    return orch


async def test_retry_chain_preserves_failure_edits_and_parent(cfg, store, tmp_path):
    cfg.search.iterations = 1
    cfg.search.max_patch_retries = 2
    provider = FakeProvider(plans=[plan("optimize")], patches=[patch(1), patch(2), patch(0)])
    orch = setup(cfg, store, tmp_path, provider, [None, None, 50])
    run = await orch.execute()
    assert run.status == "finished", run.error
    exps = store.list_experiments(run.id)
    assert len(exps) == 3
    original = next(e for e in exps if not e.retry_of)
    retry1 = next(e for e in exps if e.retry_of == original.id)
    retry2 = next(e for e in exps if e.retry_of == retry1.id)
    assert retry2.status == ExperimentStatus.accepted
    assert run.head_experiment_id == retry2.id
    assert all(e.parent_id is None and e.parent_commit == "base" for e in exps)
    assert provider.patch_requests[1].previous_edits == original.edits
    assert provider.patch_requests[2].previous_edits == retry1.edits
    assert "expected 0, got 1" in provider.patch_requests[2].previous_failure
    assert len({r.target_source for r in provider.patch_requests}) == 1
    assert orch.ws.cleaned


async def test_beam_expands_each_parent_with_its_source_and_fresh_history(cfg, store, tmp_path):
    cfg.search.iterations = 2
    cfg.search.beam_width = 2
    cfg.search.max_patch_retries = 0
    provider = FakeProvider(plans=[plan("A", "B"), plan("A2"), plan("B2")],
                            patches=[patch(1), patch(2), patch(3), patch(4)])
    orch = setup(cfg, store, tmp_path, provider, [50, 60, 40, 45])
    run = await orch.execute()
    assert run.status == "finished", run.error
    exps = {e.hypothesis.idea: e for e in store.list_experiments(run.id)}
    assert exps["A2"].parent_id == exps["A"].id
    assert exps["B2"].parent_id == exps["B"].id
    assert orch.harness.parents == [("A", 100), ("B", 100), ("A2", 50), ("B2", 60)]
    assert exps["A"].commit in provider.patch_requests[2].target_source
    assert exps["B"].commit in provider.patch_requests[3].target_source
    assert provider.plan_requests[1].best_speedup == 2
    assert provider.plan_requests[2].best_speedup == 100/60
    assert any(e.hypothesis.idea == "A2" for e in provider.plan_requests[2].history)
    assert run.head_experiment_id == exps["A2"].id
    assert run.best_speedup == 2.5


async def test_provider_error_becomes_retryable_record(cfg, store, tmp_path):
    cfg.search.iterations = 1
    provider = FakeProvider(plans=[plan("A")], patches=[RuntimeError("API unavailable"), patch(0)])
    orch = setup(cfg, store, tmp_path, provider, [50])
    run = await orch.execute()
    assert run.status == "finished", run.error
    exps = store.list_experiments(run.id)
    assert {e.status for e in exps} == {ExperimentStatus.patch_failed, ExperimentStatus.accepted}
    assert "API unavailable" in provider.patch_requests[1].previous_failure


async def test_planner_failure_skips_the_head_for_one_iteration(cfg, store, tmp_path):
    cfg.search.iterations = 2
    provider = FakeProvider(plans=[RuntimeError("planner returned no parseable plan"), plan("A")], patches=[patch(0)])
    run = await setup(cfg, store, tmp_path, provider, [50]).execute()
    assert run.status == "finished", run.error
    [a] = store.list_experiments(run.id)
    assert a.status == ExperimentStatus.accepted and a.iteration == 2
    assert any("planner failed for head baseline (RuntimeError: planner returned no parseable plan)" in line
               for line in run.logs)


async def test_transient_planner_error_is_retried_within_the_iteration(cfg, store, tmp_path):
    cfg.search.iterations = 1
    cfg.search.planner_retry_backoff_s = 0
    provider = FakeProvider(plans=[TimeoutError(), plan("A")], patches=[patch(0)])
    run = await setup(cfg, store, tmp_path, provider, [50]).execute()
    assert run.status == "finished", run.error
    [a] = store.list_experiments(run.id)
    assert a.status == ExperimentStatus.accepted and a.iteration == 1
    assert any("planner attempt 1/3 failed (TimeoutError); retrying" in line for line in run.logs)


async def test_sustained_planner_outage_fails_the_run_resumably(cfg, store, tmp_path):
    cfg.search.iterations = 10
    provider = FakeProvider(plans=[RuntimeError("invalid API key")] * 3)
    run = await setup(cfg, store, tmp_path, provider, []).execute()
    assert run.status == "failed" and run.iteration == 3
    assert "invalid API key" in run.error and f"--resume {run.id}" in run.error


async def test_resume_continues_from_the_stored_beam_without_a_new_baseline(cfg, store, tmp_path):
    cfg.search.iterations = 1
    run = await setup(cfg, store, tmp_path, FakeProvider(plans=[plan("A")], patches=[patch(0)]), [50]).execute()
    assert run.status == "finished", run.error
    cfg.search.iterations = 2
    cfg.benchmark.rebenchmark_parent = True  # only adds measurements, so it may change on resume
    provider = FakeProvider(plans=[plan("B")], patches=[patch(1)])
    orch = setup(cfg, store, tmp_path, provider, [40], resume=run.id)
    resumed = await orch.execute()
    assert resumed.id == run.id and resumed.status == "finished", resumed.error
    assert orch.harness.baselines == 0
    exps = {e.hypothesis.idea: e for e in store.list_experiments(run.id)}
    assert exps["B"].iteration == 2 and exps["B"].parent_id == exps["A"].id
    assert orch.harness.parents == [("B", 50)]
    assert any(e.hypothesis.idea == "A" for e in provider.plan_requests[0].history)
    assert resumed.head_experiment_id == exps["B"].id and resumed.best_speedup == 2.5


async def test_resume_completes_beam_selection_cut_off_by_a_crash(cfg, store, tmp_path):
    cfg.search.iterations = 1
    orch = setup(cfg, store, tmp_path, FakeProvider(plans=[plan("A", "B")], patches=[patch(0), patch(1)]), [50, 40])
    real_profile, calls = orch.harness.run_profile, []

    async def flaky_profile(commit):
        calls.append(commit)
        if len(calls) == 2:  # the new head's profile, after both candidates were accepted
            raise OSError("profiler died")
        return await real_profile(commit)

    orch.harness.run_profile = flaky_profile
    run = await orch.execute()
    assert run.status == "failed" and run.head_experiment_id is None
    resumed = await setup(cfg, store, tmp_path, FakeProvider(), [], resume=run.id).execute()
    exps = {e.hypothesis.idea: e for e in store.list_experiments(run.id)}
    assert resumed.status == "finished", resumed.error
    assert resumed.head_experiment_id == exps["B"].id and resumed.head_profile.commit == exps["B"].commit
    assert exps["A"].status == ExperimentStatus.not_selected


async def test_resume_records_experiments_cut_off_by_the_interruption(cfg, store, tmp_path):
    cfg.search.iterations = 1
    run = await setup(cfg, store, tmp_path, FakeProvider(plans=[plan("A")], patches=[patch(0)]), [50]).execute()
    stuck = Experiment(run_id=run.id, iteration=1, parent_commit="base", hypothesis=idea("half done"))
    stuck.set_status(ExperimentStatus.benchmarking)
    store.save_experiment(stuck)
    store.save_run(run.model_copy(update={"status": "running"}))  # the process died mid-run
    resumed = await setup(cfg, store, tmp_path, FakeProvider(), [], resume=run.id).execute()
    stuck = store.get_experiment(stuck.id)
    assert stuck.status == ExperimentStatus.error and "interrupted" in stuck.reject_reason
    assert any("process likely died" in line for line in resumed.logs)


async def test_resume_refuses_incomparable_runs_without_touching_them(cfg, store, tmp_path):
    cfg.search.iterations = 1
    run = await setup(cfg, store, tmp_path, FakeProvider(plans=[plan("A")], patches=[patch(0)]), [50]).execute()
    before = store.get_run(run.id).model_dump_json()
    [a] = store.list_experiments(run.id)

    orch = setup(cfg, store, tmp_path, FakeProvider(), [], resume=run.id)
    orch.ws.missing = {a.commit}
    with pytest.raises(ResumeError, match="missing from the target repository"):
        await orch.execute()

    changed = cfg.model_copy(update={"bench_cmd": "python other_bench.py"})
    with pytest.raises(ResumeError, match="bench_cmd"):
        await setup(changed, store, tmp_path, FakeProvider(), [], resume=run.id).execute()

    with pytest.raises(ResumeError, match="no run"):
        setup(cfg, store, tmp_path, FakeProvider(), [], resume="run_missing")
    assert store.get_run(run.id).model_dump_json() == before


async def test_resume_accepts_a_run_snapshotted_before_commands_were_shown(cfg, store, tmp_path):
    from hotpath.config import legacy_redacted_command
    cfg.search.iterations = 1
    run = await setup(cfg, store, tmp_path, FakeProvider(plans=[plan("A")], patches=[patch(0)]), [50]).execute()
    for key in ("test_cmd", "bench_cmd", "profile_cmd"):
        run.config_snapshot[key] = legacy_redacted_command(getattr(cfg, key))
    store.save_run(run)
    cfg.search.iterations = 2
    resumed = await setup(cfg, store, tmp_path, FakeProvider(plans=[plan("B")], patches=[patch(1)]), [40],
                          resume=run.id).execute()
    assert resumed.status == "finished", resumed.error
