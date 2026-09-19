import pytest

from hotpath.benchmark import (BenchmarkParseError, bootstrap_speedup_ci, compare, compute_stats, noise_cv,
                               parse_benchmark_output, stats_from_output)
from hotpath.schema import BenchmarkConfig

CFG = BenchmarkConfig(min_speedup=1.03, noise_multiplier=2.0, baseline_repeats=3, bootstrap_samples=500)


def _stats(center, n=15, spread=0.01, higher=False):
    return compute_stats([center * (1 + spread * ((i % 5) - 2) / 2) for i in range(n)], higher_is_better=higher)


def test_parse_finds_last_json_line_and_ignores_noise():
    out = "warming up\n{\"note\": 1}\nsome log\n{\"samples\": [1.0, 1.1], \"metric\": \"seconds\"}\ntrailing text"
    assert parse_benchmark_output(out)["samples"] == [1.0, 1.1]
    with pytest.raises(BenchmarkParseError):
        parse_benchmark_output("no json here")
    with pytest.raises(BenchmarkParseError):
        stats_from_output('{"samples": [1.0]}')  # one sample is not a measurement
    with pytest.raises(BenchmarkParseError):
        stats_from_output('{"samples": [1.0, -1.0]}')


def test_stats_and_noise():
    s = compute_stats([1.0, 1.2, 0.9, 1.1, 1.0])
    assert s.median == 1.0 and s.n == 5 and s.cv > 0
    assert noise_cv([1.0, 1.0, 1.0]) == 0.0
    assert 0.09 < noise_cv([1.0, 1.1, 1.2]) < 0.1


def test_real_speedup_is_accepted():
    base, cand = _stats(1.0), _stats(0.5)
    c = compare(base, cand, base, CFG, noise=0.01)
    assert c.significant and 1.9 < c.speedup_vs_parent < 2.1 and c.ci_low > 1.0


def test_slower_is_rejected():
    base, cand = _stats(1.0), _stats(1.2)
    c = compare(base, cand, base, CFG, noise=0.01)
    assert not c.significant and "slower" in c.reason


def test_tiny_improvement_below_threshold_is_rejected():
    base, cand = _stats(1.0), _stats(0.985)  # 1.5% faster, floor is 3%
    c = compare(base, cand, base, CFG, noise=0.0)
    assert not c.significant and "below the noise-adjusted threshold" in c.reason


def test_noise_raises_the_bar():
    base, cand = _stats(1.0), _stats(0.96)  # 4.2% faster clears the 3% floor...
    assert compare(base, cand, base, CFG, noise=0.0).significant
    c = compare(base, cand, base, CFG, noise=0.05)  # ...but not a 10% noise band
    assert not c.significant and c.threshold == pytest.approx(1.10)


def test_speedup_equal_to_noise_adjusted_threshold_is_accepted():
    # Equality is intentional: the evidence gate is "at least the declared
    # threshold".  The CI still has to exclude 1.0.
    cfg = BenchmarkConfig(min_speedup=1.01, noise_multiplier=2.0, bootstrap_samples=500)
    threshold = 1.03  # 1 + (2 * 1.5% baseline noise)
    base, cand = _stats(1.0, spread=0.0), _stats(1.0 / threshold, spread=0.0)
    c = compare(base, cand, base, cfg, noise=0.015)
    assert c.threshold == pytest.approx(threshold)
    assert c.speedup_vs_parent == pytest.approx(threshold)
    assert c.significant


def test_bootstrap_ci_touching_one_is_rejected_but_strictly_above_one_passes():
    cfg = BenchmarkConfig(min_speedup=1.001, noise_multiplier=0.0, bootstrap_samples=500)
    base = _stats(1.0, spread=0.0)
    # The median says 1.01x, enough to clear the point threshold, but bootstrap
    # resamples can select only the unchanged observations and touch 1.0.
    uncertain = compute_stats([1.0, 0.98])
    touching = compare(base, uncertain, base, cfg, noise=0.0)
    assert touching.speedup_vs_parent > touching.threshold
    assert touching.ci_low == pytest.approx(1.0)
    assert not touching.significant and "includes 1.0" in touching.reason

    faster = _stats(0.99, spread=0.0)
    clear = compare(base, faster, base, cfg, noise=0.0)
    assert clear.ci_low > 1.0 and clear.significant


def test_overlapping_distributions_are_rejected_by_ci():
    # Same center, huge spread: point estimate can look like a speedup but the CI includes 1.
    base = compute_stats([1.0, 0.6, 1.4, 0.8, 1.2, 0.5, 1.5, 0.7, 1.3, 1.0])
    cand = compute_stats([0.95, 0.55, 1.35, 0.75, 1.15, 0.5, 1.45, 0.7, 1.2, 0.9])
    c = compare(base, cand, base, CFG, noise=0.0)
    assert not c.significant


def test_higher_is_better_metric():
    base, cand = _stats(100.0, higher=True), _stats(150.0, higher=True)
    c = compare(base, cand, base, CFG, noise=0.01)
    assert c.significant and c.speedup_vs_parent == pytest.approx(1.5, rel=0.02)


def test_higher_is_better_inversion_rejects_lower_throughput():
    base, cand = _stats(100.0, higher=True), _stats(90.0, higher=True)
    c = compare(base, cand, base, CFG, noise=0.0)
    assert c.speedup_vs_parent == pytest.approx(0.9, rel=0.02)
    assert not c.significant and "slower" in c.reason


def test_metric_mismatch_never_accepted():
    base, cand = _stats(1.0), _stats(2.0, higher=True)
    assert not compare(base, cand, base, CFG, noise=0.0).significant


def test_baseline_metric_or_direction_mismatch_never_produces_cumulative_speedup():
    parent, cand = _stats(1.0), _stats(0.5)
    wrong_direction = _stats(100.0, higher=True)
    c = compare(parent, cand, wrong_direction, CFG, noise=0.0)
    assert not c.significant
    assert c.speedup_vs_parent == c.speedup_vs_baseline == 0.0
    assert "baseline" in c.reason


def test_speedup_vs_baseline_uses_baseline_not_parent():
    baseline, parent, cand = _stats(2.0), _stats(1.0), _stats(0.5)
    c = compare(parent, cand, baseline, CFG, noise=0.0)
    assert c.speedup_vs_parent == pytest.approx(2.0, rel=0.02)
    assert c.speedup_vs_baseline == pytest.approx(4.0, rel=0.02)


def test_bootstrap_is_deterministic():
    base, cand = _stats(1.0), _stats(0.8)
    assert bootstrap_speedup_ci(base, cand, 200) == bootstrap_speedup_ci(base, cand, 200)

@pytest.mark.parametrize("samples", [[float('nan'), 1], [float('inf'), 1], [None, 1], ['invalid', 1], [0, 1]])
def test_invalid_samples_rejected(samples):
    from hotpath.benchmark import BenchmarkParseError, compute_stats
    with pytest.raises(BenchmarkParseError): compute_stats(samples)
