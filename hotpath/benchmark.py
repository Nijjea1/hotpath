"""Benchmark parsing and the statistical accept/reject decision.

The benchmark command prints one JSON line {"hotpath_benchmark": 1, "samples": [...]}.
Hotpath never trusts a single number: it uses the median of repeated trials, measures
run-to-run noise by repeating the baseline, and only accepts a change whose bootstrap
confidence interval for the speedup excludes 1.0 AND whose speedup clears a threshold
derived from that noise.
"""
from __future__ import annotations

import json
import math
import random
import statistics

from hotpath.schema import BenchmarkConfig, BenchmarkStats, SpeedComparison


class BenchmarkParseError(Exception):
    pass


def parse_benchmark_output(stdout: str) -> dict:
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "samples" in obj:
            return obj
    raise BenchmarkParseError("benchmark command did not print a JSON line with 'samples'")


def compute_stats(samples: list[float], metric: str = "seconds", higher_is_better: bool = False,
                  duration_s: float = 0.0, output_tail: str = "") -> BenchmarkStats:
    if not isinstance(samples, list):
        raise BenchmarkParseError("benchmark samples must be a list")
    try:
        clean = [float(s) for s in samples]
    except (TypeError, ValueError, OverflowError) as exc:
        raise BenchmarkParseError("benchmark samples must be finite numbers") from exc
    if len(clean) < 2:
        raise BenchmarkParseError(f"need at least 2 benchmark samples, got {len(clean)}")
    if any(not math.isfinite(s) or s <= 0 for s in clean):
        raise BenchmarkParseError("benchmark samples must be finite and positive")
    med = statistics.median(clean)
    mean = statistics.fmean(clean)
    sd = statistics.stdev(clean)
    return BenchmarkStats(metric=metric, higher_is_better=higher_is_better, samples=clean, n=len(clean),
                          median=med, mean=mean, stdev=sd, cv=(sd / mean if mean else 0.0),
                          duration_s=duration_s, output_tail=output_tail)


def stats_from_output(stdout: str, duration_s: float = 0.0, output_tail: str = "") -> BenchmarkStats:
    obj = parse_benchmark_output(stdout)
    return compute_stats(obj["samples"], metric=str(obj.get("metric", "seconds")),
                         higher_is_better=bool(obj.get("higher_is_better", False)),
                         duration_s=duration_s, output_tail=output_tail)


def noise_cv(medians: list[float]) -> float:
    """Run-to-run coefficient of variation of the baseline median."""
    if len(medians) < 2:
        return 0.0
    m = statistics.fmean(medians)
    return statistics.stdev(medians) / m if m else 0.0


def speedup_of(baseline: BenchmarkStats, candidate: BenchmarkStats) -> float:
    if baseline.higher_is_better:
        return candidate.median / baseline.median
    return baseline.median / candidate.median


def bootstrap_speedup_ci(baseline: BenchmarkStats, candidate: BenchmarkStats, n: int = 2000,
                         confidence: float = 0.95, seed: int = 0) -> tuple[float, float]:
    if n < 1 or not 0 < confidence < 1:
        raise ValueError("invalid bootstrap sample count or confidence")
    rng = random.Random(seed)
    b, c = baseline.samples, candidate.samples
    ratios = []
    for _ in range(n):
        bm = statistics.median(rng.choices(b, k=len(b)))
        cm = statistics.median(rng.choices(c, k=len(c)))
        ratios.append(cm / bm if baseline.higher_is_better else bm / cm)
    ratios.sort()
    alpha = (1 - confidence) / 2
    lo = ratios[int(alpha * (n - 1))]
    hi = ratios[int((1 - alpha) * (n - 1))]
    return lo, hi


def compare(parent: BenchmarkStats, candidate: BenchmarkStats, baseline: BenchmarkStats,
            cfg: BenchmarkConfig, noise: float) -> SpeedComparison:
    """Decide whether candidate is meaningfully faster than parent. Pure function: fully testable."""
    if parent.higher_is_better != candidate.higher_is_better or parent.metric != candidate.metric:
        return SpeedComparison(speedup_vs_parent=0.0, speedup_vs_baseline=0.0, ci_low=0.0, ci_high=0.0,
                               threshold=cfg.min_speedup, significant=False,
                               reason="benchmark metric changed between parent and candidate")
    speedup = speedup_of(parent, candidate)
    vs_base = speedup_of(baseline, candidate)
    lo, hi = bootstrap_speedup_ci(parent, candidate, cfg.bootstrap_samples, cfg.confidence)
    threshold = max(cfg.min_speedup, 1.0 + cfg.noise_multiplier * noise)
    if speedup < 1.0:
        reason = f"slower: {speedup:.3f}x vs parent"
        sig = False
    elif speedup < threshold:
        reason = f"{speedup:.3f}x is below the noise-adjusted threshold of {threshold:.3f}x (baseline noise CV {noise:.1%})"
        sig = False
    elif lo <= 1.0:
        reason = f"{speedup:.3f}x but the {cfg.confidence:.0%} CI [{lo:.3f}, {hi:.3f}] includes 1.0: not statistically distinguishable"
        sig = False
    else:
        reason = (f"{speedup:.3f}x vs parent clears point-estimate threshold {threshold:.3f}x; "
                  f"CI [{lo:.3f}, {hi:.3f}] excludes 1.0")
        sig = True
    return SpeedComparison(speedup_vs_parent=speedup, speedup_vs_baseline=vs_base, ci_low=lo, ci_high=hi,
                           threshold=threshold, significant=sig, reason=reason, confidence=cfg.confidence)
