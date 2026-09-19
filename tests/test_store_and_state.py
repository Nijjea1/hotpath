from hotpath.schema import Experiment, ExperimentStatus, Hypothesis, RunState, TERMINAL_STATUSES, REJECTED_STATUSES
from hotpath.store import Store

H = Hypothesis(idea="i", strategy="s", target_file="f.py", rationale="r", risk="low")


def test_status_sets_are_consistent():
    assert ExperimentStatus.accepted in TERMINAL_STATUSES
    assert ExperimentStatus.accepted not in REJECTED_STATUSES
    assert ExperimentStatus.not_selected not in REJECTED_STATUSES
    assert ExperimentStatus.testing not in TERMINAL_STATUSES


def test_set_status_records_reason_and_log():
    e = Experiment(run_id="r", iteration=1, hypothesis=H)
    e.set_status(ExperimentStatus.rejected_speed, "too slow")
    assert e.status == ExperimentStatus.rejected_speed and e.reject_reason == "too slow"
    assert any("rejected_speed" in line and "too slow" in line for line in e.logs)


def test_roundtrip(store: Store):
    run = RunState(config_name="c", target="/t")
    store.save_run(run)
    e = Experiment(run_id=run.id, iteration=1, hypothesis=H, parent_id=None)
    store.save_experiment(e)
    e.set_status(ExperimentStatus.accepted)
    store.save_experiment(e)  # upsert
    got = store.get_experiment(e.id)
    assert got.status == ExperimentStatus.accepted and got.hypothesis.idea == "i"
    assert [x.id for x in store.list_experiments(run.id)] == [e.id]
    assert store.latest_run().id == run.id and store.get_run(run.id).config_name == "c"
    assert store.list_experiments("nope") == []


def test_full_evidence_roundtrip_after_reopen(store, cfg):
    from hotpath.benchmark import compute_stats, compare
    from hotpath.schema import Edit, ProfileSummary, Hotspot
    from hotpath.config import config_snapshot
    base = compute_stats([10, 11, 10], workload_samples={"short": [10, 11, 10], "long": [20, 21, 20]})
    candidate = compute_stats([4, 4.1, 4], workload_samples={"short": [4, 4.1, 4], "long": [8, 8.2, 8]})
    profile = ProfileSummary(tool="cProfile", commit="base", total_time=1,
        hotspots=[Hotspot(function="f", file="f.py", self_time=.5, total_time=.5, pct=50)],
        retained=1, n_functions_total=10, completeness_known=True, cutoff_self_time=.5)
    run = RunState(config_name="c", target="/t", config_snapshot=config_snapshot(cfg),
        execution_environment={"backend": "docker", "image_id": "sha256:example"},
        baseline_profile=profile, head_profile=profile, baseline_benchmark=base)
    original = Experiment(run_id=run.id, iteration=1, hypothesis=H)
    retry = Experiment(run_id=run.id, iteration=1, hypothesis=H, retry_of=original.id,
        previous_failure="FAIL tokens\nline two", previous_edits=[Edit(file="f.py", search="a", replace="b")],
        parent_benchmark=base, benchmark=candidate,
        comparison=compare(base, candidate, base, cfg.benchmark, 0))
    store.save_run(run)
    store.save_experiment(original)
    store.save_experiment(retry)
    reopened = Store(store.path)
    assert reopened.get_run(run.id) == run
    assert reopened.get_experiment(retry.id) == retry
    assert reopened.get_experiment(retry.id).benchmark.workload_samples == {
        "short": [4.0, 4.1, 4.0], "long": [8.0, 8.2, 8.0]
    }
    # This config has no required workloads, so the comparison intentionally has none.
    assert reopened.get_experiment(retry.id).comparison.workload_ratios == {}


def test_workload_ratios_and_stats_survive_resume(store, cfg):
    """A resumed run must retain the evidence used for a multi-workload verdict."""
    from hotpath.benchmark import compute_stats, compare

    cfg.benchmark.required_workloads = ["short", "long"]
    base = compute_stats([10, 10, 10], metric="tokens/sec", higher_is_better=True,
                         workload_samples={"short": [10, 10, 10], "long": [20, 20, 20]})
    candidate = compute_stats([12, 12, 12], metric="tokens/sec", higher_is_better=True,
                              workload_samples={"short": [12, 12, 12], "long": [24, 24, 24]})
    comparison = compare(base, candidate, base, cfg.benchmark, 0.0)
    run = RunState(config_name="resume", target="/t", baseline_benchmark=base,
                   head_benchmark=candidate, best_speedup=comparison.speedup_vs_baseline)
    exp = Experiment(run_id=run.id, iteration=1, hypothesis=H, parent_benchmark=base,
                      benchmark=candidate, comparison=comparison)
    store.save_run(run)
    store.save_experiment(exp)

    resumed = Store(store.path)
    loaded_run = resumed.get_run(run.id)
    loaded_exp = resumed.get_experiment(exp.id)
    assert loaded_run.baseline_benchmark.workload_samples["long"] == [20.0, 20.0, 20.0]
    assert loaded_run.head_benchmark.workload_samples["short"] == [12.0, 12.0, 12.0]
    assert loaded_exp.comparison.workload_ratios == {"short": 1.2, "long": 1.2}


def test_legacy_benchmark_payload_defaults_new_evidence_fields(store):
    """Runs written before workload evidence was introduced remain readable."""
    import json
    from hotpath.schema import BenchmarkStats, SpeedComparison

    run = RunState(config_name="legacy-evidence", target="/t")
    payload = run.model_dump(mode="json")
    payload["baseline_benchmark"] = BenchmarkStats(samples=[1, 1.1], n=2, median=1,
                                                     mean=1.05, stdev=.05, cv=.05).model_dump(mode="json")
    payload["head_benchmark"] = payload["baseline_benchmark"]
    raw_comparison = SpeedComparison(speedup_vs_parent=1.1, speedup_vs_baseline=1.1,
                                     ci_low=1.01, ci_high=1.2, threshold=1.03,
                                     significant=True, reason="legacy").model_dump(mode="json")
    exp = Experiment(run_id=run.id, iteration=1, hypothesis=H,
                     comparison=SpeedComparison.model_validate(raw_comparison))
    with store._conn() as conn:
        conn.execute("INSERT INTO runs VALUES (?,?,?,?,?)",
                     (run.id, run.status, run.created_at.isoformat(), run.updated_at.isoformat(),
                      json.dumps(payload)))
        conn.execute("INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?)",
                     (exp.id, exp.run_id, exp.parent_id, exp.iteration, exp.status.value,
                      exp.created_at.isoformat(), exp.updated_at.isoformat(),
                      json.dumps({**exp.model_dump(mode="json"),
                                  "comparison": {k: v for k, v in raw_comparison.items()
                                                  if k != "workload_ratios"}})))
    loaded_run = store.get_run(run.id)
    loaded_exp = store.get_experiment(exp.id)
    assert loaded_run.baseline_benchmark.workload_samples == {}
    assert loaded_exp.comparison.workload_ratios == {}


def test_legacy_payload_defaults(store):
    import json
    run = RunState(config_name="legacy", target="/t")
    raw = run.model_dump(mode="json")
    raw.pop("config_snapshot")
    raw.pop("execution_environment")
    with store._conn() as conn:
        conn.execute("INSERT INTO runs VALUES (?,?,?,?,?)",
                     (run.id, run.status, run.created_at.isoformat(), run.updated_at.isoformat(), json.dumps(raw)))
    got = store.get_run(run.id)
    assert got.config_snapshot == {} and got.execution_environment == {}
