"""A fixed workload matrix must survive parsing and guard aggregate wins."""
import json

import pytest

from hotpath.benchmark import BenchmarkParseError, compare, compute_stats, stats_from_output
from hotpath.schema import BenchmarkConfig
from hotpath.harness import stats_from_output_samples
from hotpath.schema import CmdResult


IDS = ["p32_b1", "p32_b2"]
CFG = BenchmarkConfig(required_workloads=IDS, min_workload_retention=0.98,
                      min_speedup=1.03, bootstrap_samples=300)


def _output(ids=IDS, values=None):
    values = values or [[100.0, 101.0, 99.0], [200.0, 201.0, 199.0]]
    return json.dumps({"hotpath_benchmark": 1, "metric": "tokens_per_s", "higher_is_better": True,
                       "samples": [140.0, 141.0, 139.0],
                       "workloads": [{"id": wid, "samples": samples} for wid, samples in zip(ids, values)]})


def test_required_matrix_is_parsed_and_persistable():
    stats = stats_from_output(_output(), required_workloads=IDS)
    assert set(stats.workload_samples) == set(IDS)
    assert stats.model_validate_json(stats.model_dump_json()).workload_samples == stats.workload_samples


@pytest.mark.parametrize("payload", [
    _output(ids=["p32_b1"]),
    _output(ids=["p32_b1", "extra"]),
    _output(ids=["p32_b1", "p32_b1"]),
    _output(values=[[100, 101], [200, 201, 199]]),
    _output(values=[[100, 0, 99], [200, 201, 199]]),
])
def test_missing_extra_duplicate_or_malformed_workload_fails(payload):
    with pytest.raises(BenchmarkParseError):
        stats_from_output(payload, required_workloads=IDS)


def test_aggregate_win_with_required_shape_regression_is_rejected():
    parent = compute_stats([100, 101, 99], metric="tokens_per_s", higher_is_better=True,
                           workload_samples={"p32_b1": [100, 101, 99], "p32_b2": [100, 101, 99]})
    candidate = compute_stats([120, 121, 119], metric="tokens_per_s", higher_is_better=True,
                              workload_samples={"p32_b1": [150, 151, 149], "p32_b2": [95, 96, 94]})
    comparison = compare(parent, candidate, parent, CFG, noise=0)
    assert not comparison.significant
    assert comparison.workload_ratios["p32_b2"] == pytest.approx(0.95)
    assert "p32_b2" in comparison.reason


def test_aggregate_win_with_all_shapes_retained_can_pass():
    parent = compute_stats([100, 101, 99], metric="tokens_per_s", higher_is_better=True,
                           workload_samples={"p32_b1": [100, 101, 99], "p32_b2": [100, 101, 99]})
    candidate = compute_stats([120, 121, 119], metric="tokens_per_s", higher_is_better=True,
                              workload_samples={"p32_b1": [150, 151, 149], "p32_b2": [99, 100, 98]})
    comparison = compare(parent, candidate, parent, CFG, noise=0)
    assert comparison.significant
    assert comparison.workload_ratios["p32_b2"] == pytest.approx(0.99)


def test_comparison_refuses_missing_workload_evidence():
    parent = compute_stats([100, 101, 99], metric="tokens_per_s", higher_is_better=True)
    candidate = compute_stats([120, 121, 119], metric="tokens_per_s", higher_is_better=True)
    comparison = compare(parent, candidate, parent, CFG, noise=0)
    assert not comparison.significant and "workload IDs changed" in comparison.reason


def test_baseline_repeats_pool_per_shape_samples():
    first = compute_stats([100, 101, 99], metric="tokens_per_s", higher_is_better=True,
                          workload_samples={"p32_b1": [90, 91, 89], "p32_b2": [110, 111, 109]})
    second = compute_stats([102, 103, 101], metric="tokens_per_s", higher_is_better=True,
                           workload_samples={"p32_b1": [92, 93, 91], "p32_b2": [112, 113, 111]})
    pooled = stats_from_output_samples([first, second])
    assert pooled.n == 6
    assert pooled.workload_samples["p32_b1"] == [90, 91, 89, 92, 93, 91]


def test_baseline_repeats_refuse_a_changing_matrix():
    first = compute_stats([100, 101, 99], workload_samples={"p32_b1": [90, 91, 89]})
    second = compute_stats([102, 103, 101], workload_samples={"p32_b2": [92, 93, 91]})
    with pytest.raises(BenchmarkParseError, match="workload set changed"):
        stats_from_output_samples([first, second])


async def test_harness_enforces_configured_workload_ids(cfg, ws, store, monkeypatch):
    from hotpath.harness import Harness
    import hotpath.harness as harness_module

    cfg.benchmark.required_workloads = IDS

    async def target(*args, **kwargs):
        return CmdResult(cmd="bench", exit_code=0, stdout=_output(ids=["p32_b1"]),
                         stderr="", duration_s=0.1)

    monkeypatch.setattr(harness_module, "run_target", target)
    with pytest.raises(BenchmarkParseError, match="missing=.*p32_b2"):
        await Harness(cfg, ws, store).run_benchmark(ws.target)
