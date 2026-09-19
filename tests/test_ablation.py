from pathlib import Path

import pytest

from hotpath import ablation
from hotpath.benchmark import compute_stats
from hotpath.schema import CorrectnessResult, Experiment, ExperimentStatus, Hypothesis, RunState


async def test_ablation_pairs_fresh_full_benchmark_with_omission(cfg, store, monkeypatch, tmp_path):
    historical_full = compute_stats([5, 5, 5])
    baseline = compute_stats([100, 100, 100])
    run = RunState(config_name="tiny", target=str(tmp_path), base_commit="base", head_commit="head",
                   baseline_benchmark=baseline, head_benchmark=historical_full)
    exp = Experiment(run_id=run.id, iteration=1, parent_commit="base", commit="head", status=ExperimentStatus.accepted,
                     hypothesis=Hypothesis(idea="remove work", strategy="s", target_file="mod.py", rationale="r", risk="low"))
    run.head_experiment_id = exp.id
    store.save_experiment(exp)

    class FakeWorkspace:
        removed = []
        def __init__(self, *_): pass
        def create_worktree(self, commit, name): return Path(name)
        def remove_worktree(self, path): self.removed.append(path)
        def apply_edits(self, *_): raise AssertionError("single accepted change should be omitted")

    class FakeHarness:
        def __init__(self, *_): pass
        async def run_tests(self, _): return CorrectnessResult(passed=True, exit_code=0, duration_s=0)
        async def run_benchmark(self, path):
            return compute_stats([50, 50, 50] if path.name.startswith("ablate_full_") else [100, 100, 100])

    monkeypatch.setattr(ablation, "Workspace", FakeWorkspace)
    monkeypatch.setattr(ablation, "Harness", FakeHarness)
    report = await ablation.ablate(cfg, store, run)
    assert report.full_median == 50
    assert report.rows[0].median_full == 50
    assert report.rows[0].median_without == 100
    assert report.rows[0].contribution == pytest.approx(2)
    assert len(FakeWorkspace.removed) == 2
    assert "full" in ablation.render(report)


async def test_ablation_reports_structured_error_when_full_stack_fails(cfg, store, monkeypatch, tmp_path):
    run = RunState(config_name="tiny", target=str(tmp_path), base_commit="base", head_commit="head",
                   baseline_benchmark=compute_stats([100, 100, 100]))
    exp = Experiment(run_id=run.id, iteration=1, parent_commit="base", commit="head", status=ExperimentStatus.accepted,
                     hypothesis=Hypothesis(idea="remove work", strategy="s", target_file="mod.py", rationale="r", risk="low"))
    run.head_experiment_id = exp.id
    store.save_experiment(exp)

    class FakeWorkspace:
        removed = []
        def __init__(self, *_): pass
        def create_worktree(self, commit, name): return Path(name)
        def remove_worktree(self, path): self.removed.append(path)

    class FakeHarness:
        def __init__(self, *_): pass
        async def run_tests(self, _): return CorrectnessResult(passed=False, exit_code=1, duration_s=0)
        async def run_benchmark(self, _): raise AssertionError("must not benchmark an incorrect head")

    monkeypatch.setattr(ablation, "Workspace", FakeWorkspace)
    monkeypatch.setattr(ablation, "Harness", FakeHarness)
    report = await ablation.ablate(cfg, store, run)
    assert report.full_median is None and report.error
    assert [r.status for r in report.rows] == ["error"]
    assert len(FakeWorkspace.removed) == 1
    assert "not measured" in ablation.render(report)


async def test_ablation_marks_a_neutral_change_in_a_composed_stack_removable(cfg, store, monkeypatch, tmp_path):
    """A can be accepted historically yet add no measurable value once B is present.

    The ablation must compare each omission with a freshly measured full stack,
    so this neutral result is not hidden by an old stored benchmark.
    """
    run, exps = _chain_run(store, tmp_path, ["A", "B"])
    run.head_commit = "head"

    class FakeWorkspace:
        removed = []
        applied_by_worktree = {}
        def __init__(self, *_): pass
        def create_worktree(self, commit, name):
            path = Path(name)
            self.applied_by_worktree[path.name] = []
            return path
        def remove_worktree(self, path): self.removed.append(path)
        def apply_edits(self, wt, edits, *_): self.applied_by_worktree[wt.name].append(edits[0].search)

    class FakeHarness:
        def __init__(self, *_): pass
        async def run_tests(self, _): return CorrectnessResult(passed=True, exit_code=0, duration_s=0)
        async def run_benchmark(self, path):
            # Full A+B is 50.  Removing A leaves B's full benefit; removing B
            # loses it, proving this is a composed-stack rather than a one-edit case.
            if path.name.startswith("ablate_full_"):
                return compute_stats([50] * 5)
            applied = FakeWorkspace.applied_by_worktree[path.name]
            return compute_stats([50] * 5 if applied == ["B"] else [100] * 5)

    monkeypatch.setattr(ablation, "Workspace", FakeWorkspace)
    monkeypatch.setattr(ablation, "Harness", FakeHarness)
    report = await ablation.ablate(cfg, store, run)
    rows = {row.idea: row for row in report.rows}
    assert rows["A"].contribution == pytest.approx(1.0)
    assert rows["A"].pulls_weight is False
    assert "costs nothing measurable" in rows["A"].reason
    assert rows["B"].contribution == pytest.approx(2.0)
    assert rows["B"].pulls_weight is True


# --------------------------------------------------------------------------- #
# Pruning
# --------------------------------------------------------------------------- #

def _chain_run(store, tmp_path, ideas):
    """A run whose accepted chain is `ideas`, in order, each with one edit."""
    from hotpath.schema import Edit
    run = RunState(config_name="tiny", target=str(tmp_path), base_commit="base", head_commit="head",
                   baseline_benchmark=compute_stats([100, 100, 100, 100, 100]))
    parent = None
    for idea in ideas:
        e = Experiment(run_id=run.id, iteration=1, parent_id=parent, parent_commit="c", commit=f"c_{idea}",
                       status=ExperimentStatus.accepted, edits=[Edit(file="mod.py", search=idea, replace=idea + "!")],
                       hypothesis=Hypothesis(idea=idea, strategy="s", target_file="mod.py", rationale="r", risk="low"))
        store.save_experiment(e)
        parent = e.id
    run.head_experiment_id = parent
    return run, {e.hypothesis.idea: e for e in store.list_experiments(run.id)}


def _report(run, exps, pulls):
    return ablation.AblationReport(run_id=run.id, full_median=50, metric="seconds", rows=[
        ablation.AblationRow(experiment_id=exps[i].id, idea=i, status="measured", pulls_weight=p) for i, p in pulls.items()])


def _fakes(monkeypatch, *, tests_pass=True, pruned_samples=(50,) * 5, unapplicable=()):
    class FakeWorkspace:
        applied, removed, committed = [], [], []
        def __init__(self, *_): pass
        def create_worktree(self, commit, name): return Path(name)
        def remove_worktree(self, path): self.removed.append(path)
        def apply_edits(self, wt, edits, *_):
            if edits[0].search in unapplicable:
                raise ablation.PatchError(f"search text not found: {edits[0].search}")
            self.applied.append(edits[0].search)
        def commit(self, wt, name, message):
            self.committed.append(name)
            return "pruned_sha"

    class FakeHarness:
        def __init__(self, *_): pass
        async def run_tests(self, _): return CorrectnessResult(passed=tests_pass, exit_code=0 if tests_pass else 1, duration_s=0)
        async def run_benchmark(self, path):
            return compute_stats([50] * 5 if path.name.startswith("prune_full_") else list(pruned_samples))

    monkeypatch.setattr(ablation, "Workspace", FakeWorkspace)
    monkeypatch.setattr(ablation, "Harness", FakeHarness)
    return FakeWorkspace


async def test_prune_drops_changes_that_do_not_pull_their_weight_together(cfg, store, monkeypatch, tmp_path):
    run, exps = _chain_run(store, tmp_path, ["A", "B", "C"])
    ws = _fakes(monkeypatch)
    result = await ablation.prune(cfg, store, run, _report(run, exps, {"A": True, "B": False, "C": True}))
    assert result.status == "pruned", result.reason
    assert result.dropped == [exps["B"].id] and result.kept == [exps["A"].id, exps["C"].id]
    assert ws.applied == ["A", "C"], "kept changes are rebuilt on the base, in chain order"
    assert result.commit == "pruned_sha" and ws.committed == [f"pruned_{run.id}"]
    assert result.speedup_vs_baseline == pytest.approx(2.0)
    assert len(ws.removed) == 2
    assert "Prune: pruned" in ablation.render(_report(run, exps, {}).model_copy(update={"prune": result}))


async def test_prune_with_nothing_removable_builds_nothing(cfg, store, monkeypatch, tmp_path):
    run, exps = _chain_run(store, tmp_path, ["A"])
    ws = _fakes(monkeypatch)
    result = await ablation.prune(cfg, store, run, _report(run, exps, {"A": True}))
    assert result.status == "nothing_to_prune" and ws.applied == [] and ws.removed == []


@pytest.mark.parametrize("fakes, reason", [
    ({"pruned_samples": (100,) * 5}, "matter jointly"),   # each removable alone, but not together
    ({"tests_pass": False}, "fails correctness"),
    ({"unapplicable": ("C",)}, "do not apply without the dropped ones"),
])
async def test_prune_keeps_the_full_stack_unless_the_pruned_one_is_verified(cfg, store, monkeypatch, tmp_path, fakes, reason):
    run, exps = _chain_run(store, tmp_path, ["A", "B", "C"])
    ws = _fakes(monkeypatch, **fakes)
    result = await ablation.prune(cfg, store, run, _report(run, exps, {"A": False, "B": False, "C": True}))
    assert result.status == "kept_full" and reason in result.reason
    assert result.commit is None and ws.committed == []
    assert len(ws.removed) == 2, "both worktrees are cleaned up on every path"
