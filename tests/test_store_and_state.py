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
    base = compute_stats([10, 11, 10])
    candidate = compute_stats([4, 4.1, 4])
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
