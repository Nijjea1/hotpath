"""Derived dashboard views: tree layout, retry links, beam membership, chart direction."""
from datetime import timedelta

import pytest

from hotpath.schema import (BenchmarkStats, Experiment, ExperimentStatus, Hypothesis, RunState,
                            SpeedComparison, now)
from server.views import BASELINE_ID, build_chart, build_funnel, build_tree


def bench(median: float, metric: str = "seconds", higher_is_better: bool = False) -> BenchmarkStats:
    samples = [median * 0.99, median, median * 1.01]
    return BenchmarkStats(metric=metric, higher_is_better=higher_is_better, samples=samples,
                          n=len(samples), median=median, mean=median, stdev=0.0, cv=0.0)


def comparison(vs_parent: float, vs_baseline: float, significant: bool = True) -> SpeedComparison:
    return SpeedComparison(speedup_vs_parent=vs_parent, speedup_vs_baseline=vs_baseline,
                           ci_low=vs_parent * 0.95, ci_high=vs_parent * 1.05, threshold=1.03,
                           significant=significant, reason="test")


class Builder:
    """Assembles a run's experiments with deterministic, strictly increasing creation times."""

    def __init__(self, metric: str = "seconds", higher_is_better: bool = False, baseline: float = 1.0):
        self.run = RunState(config_name="t", target=".", baseline_benchmark=bench(baseline, metric, higher_is_better),
                            baseline_noise_cv=0.01)
        self.exps: list[Experiment] = []
        self._t = now()

    def add(self, iteration: int, status: ExperimentStatus, *, parent: str | None = None,
            retry_of: str | None = None, idea: str = "idea", median: float | None = None,
            vs_parent: float = 1.0, vs_baseline: float = 1.0) -> Experiment:
        self._t += timedelta(seconds=1)
        e = Experiment(run_id=self.run.id, parent_id=parent, iteration=iteration, retry_of=retry_of,
                       hypothesis=Hypothesis(idea=idea, strategy="s", target_file="m.py",
                                             rationale="r", risk="low"),
                       status=status, created_at=self._t, updated_at=self._t)
        if median is not None:
            m = self.run.baseline_benchmark
            e.benchmark = bench(median, m.metric, m.higher_is_better)
            e.comparison = comparison(vs_parent, vs_baseline, status == ExperimentStatus.accepted)
        self.exps.append(e)
        return e

    def head(self, e: Experiment) -> None:
        self.run.head_experiment_id = e.id
        self.run.best_speedup = e.comparison.speedup_vs_baseline if e.comparison else 1.0


# --------------------------------------------------------------------------- #
# Beam membership
# --------------------------------------------------------------------------- #

def test_beam_expansion_is_derived_from_parentage():
    b = Builder()
    a1 = b.add(1, ExperimentStatus.accepted, median=0.5, vs_parent=2.0, vs_baseline=2.0)
    a2 = b.add(1, ExperimentStatus.accepted, median=0.6, vs_parent=1.6, vs_baseline=1.6)
    # Iteration 2 plans on both heads: that is what makes them beam nodes.
    b.add(2, ExperimentStatus.rejected_speed, parent=a1.id, median=0.49, vs_parent=1.01)
    b.add(2, ExperimentStatus.accepted, parent=a2.id, median=0.3, vs_parent=2.0, vs_baseline=3.3)
    b.head(a1)
    tree = build_tree(b.run, b.exps)
    by_id = {n.id: n for n in tree.nodes}
    assert by_id[a1.id].expanded_at == [2] and by_id[a2.id].expanded_at == [2]
    assert by_id[BASELINE_ID].expanded_at == [1]


def test_beam_node_with_no_hypotheses_is_an_acknowledged_blind_spot():
    """A head the planner had nothing to say about leaves no trace; expanded_at is a lower bound."""
    b = Builder()
    kept = b.add(1, ExperimentStatus.accepted, median=0.5, vs_parent=2.0, vs_baseline=2.0)
    b.head(kept)
    tree = build_tree(b.run, b.exps)
    assert next(n for n in tree.nodes if n.id == kept.id).expanded_at == []


# --------------------------------------------------------------------------- #
# Retries
# --------------------------------------------------------------------------- #

def test_retry_chain_keeps_lineage_and_gains_a_retry_edge():
    b = Builder()
    failed = b.add(1, ExperimentStatus.rejected_correctness, idea="use a set")
    retry = b.add(1, ExperimentStatus.accepted, retry_of=failed.id, idea="use a set",
                  median=0.5, vs_parent=2.0, vs_baseline=2.0)
    b.head(retry)
    tree = build_tree(b.run, b.exps)
    by_id = {n.id: n for n in tree.nodes}
    assert by_id[failed.id].retry_depth == 0 and by_id[retry.id].retry_depth == 1

    retry_edges = [e for e in tree.edges if e.kind == "retry"]
    assert len(retry_edges) == 1 and retry_edges[0].from_id == failed.id and retry_edges[0].to_id == retry.id
    # Lineage is still drawn: a retry is a child of the same commit its original was.
    lineage = {(e.from_id, e.to_id) for e in tree.edges if e.kind == "lineage"}
    assert (BASELINE_ID, retry.id) in lineage and (BASELINE_ID, failed.id) in lineage
    # The pair must sit adjacent so "failed -> retried -> accepted" reads as one unit.
    assert abs(by_id[retry.id].row - by_id[failed.id].row) == 1


def test_retry_of_a_retry_counts_attempts():
    b = Builder()
    first = b.add(1, ExperimentStatus.patch_failed)
    second = b.add(1, ExperimentStatus.rejected_correctness, retry_of=first.id)
    third = b.add(1, ExperimentStatus.accepted, retry_of=second.id, median=0.5, vs_parent=2.0, vs_baseline=2.0)
    depths = {n.id: n.retry_depth for n in build_tree(b.run, b.exps).nodes if n.id != BASELINE_ID}
    assert depths == {first.id: 0, second.id: 1, third.id: 2}


def test_an_accepted_retry_can_itself_be_expanded():
    b = Builder()
    failed = b.add(1, ExperimentStatus.rejected_correctness)
    retry = b.add(1, ExperimentStatus.accepted, retry_of=failed.id, median=0.5, vs_parent=2.0, vs_baseline=2.0)
    child = b.add(2, ExperimentStatus.accepted, parent=retry.id, median=0.25, vs_parent=2.0, vs_baseline=4.0)
    b.head(child)
    tree = build_tree(b.run, b.exps)
    by_id = {n.id: n for n in tree.nodes}
    assert by_id[retry.id].expanded_at == [2]
    assert by_id[child.id].on_head_chain and by_id[retry.id].on_head_chain
    assert by_id[failed.id].on_head_chain is False


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #

def test_rows_are_unique_within_every_column():
    b = Builder()
    heads = [b.add(1, ExperimentStatus.accepted, median=0.5 + i / 10, vs_parent=1.5, vs_baseline=1.5)
             for i in range(3)]
    for h in heads:
        for _ in range(3):
            b.add(2, ExperimentStatus.rejected_speed, parent=h.id, median=0.5, vs_parent=1.0)
    b.head(heads[0])
    tree = build_tree(b.run, b.exps)
    seen: set[tuple[int, int]] = set()
    for n in tree.nodes:
        assert (n.column, n.row) not in seen, "two nodes would be drawn on top of each other"
        seen.add((n.column, n.row))
    assert tree.max_row == max(n.row for n in tree.nodes)


def test_siblings_are_contiguous_so_branches_do_not_interleave():
    b = Builder()
    left = b.add(1, ExperimentStatus.accepted, median=0.5, vs_parent=2.0, vs_baseline=2.0)
    right = b.add(1, ExperimentStatus.accepted, median=0.6, vs_parent=1.6, vs_baseline=1.6)
    left_kids = [b.add(2, ExperimentStatus.rejected_speed, parent=left.id, median=0.5) for _ in range(2)]
    right_kids = [b.add(2, ExperimentStatus.rejected_speed, parent=right.id, median=0.6) for _ in range(2)]
    b.head(left)
    rows = {n.id: n.row for n in build_tree(b.run, b.exps).nodes}
    lr = sorted(rows[k.id] for k in left_kids)
    rr = sorted(rows[k.id] for k in right_kids)
    assert lr[1] - lr[0] == 1 and rr[1] - rr[0] == 1, "a parent's children must be adjacent"
    assert max(lr) < min(rr) or max(rr) < min(lr), "branches must not interleave"


def test_columns_follow_iterations_and_reexpansion_spans_more_than_one():
    b = Builder()
    old = b.add(1, ExperimentStatus.accepted, median=0.5, vs_parent=2.0, vs_baseline=2.0)
    b.add(2, ExperimentStatus.rejected_speed, parent=old.id, median=0.5)
    late = b.add(3, ExperimentStatus.accepted, parent=old.id, median=0.25, vs_parent=2.0, vs_baseline=4.0)
    b.head(late)
    tree = build_tree(b.run, b.exps)
    assert {n.column for n in tree.nodes} == {0, 1, 2, 3} and tree.columns == 4
    edge = next(e for e in tree.edges if e.to_id == late.id and e.kind == "lineage")
    assert edge.spans == 2, "an older head re-expanded two iterations later crosses two columns"


def test_orphaned_parent_draws_no_dangling_edge():
    b = Builder()
    b.add(1, ExperimentStatus.accepted, parent="exp_vanished", median=0.5, vs_parent=2.0, vs_baseline=2.0)
    tree = build_tree(b.run, b.exps)
    assert [e for e in tree.edges] == [], "an edge to a missing parent would be fabricated"
    assert len(tree.nodes) == 2, "the node itself is still placed"


def test_baseline_is_head_until_something_is_accepted():
    b = Builder()
    b.add(1, ExperimentStatus.rejected_speed, median=1.0)
    tree = build_tree(b.run, b.exps)
    base = next(n for n in tree.nodes if n.id == BASELINE_ID)
    assert base.is_head and base.on_head_chain and base.subtitle.endswith("seconds")


def test_empty_run_still_yields_a_baseline_node():
    run = RunState(config_name="t", target=".")
    tree = build_tree(run, [])
    assert [n.id for n in tree.nodes] == [BASELINE_ID] and tree.edges == []
    assert tree.nodes[0].subtitle == "measuring…"


# --------------------------------------------------------------------------- #
# Chart: direction of "better" is the thing that must not be wrong
# --------------------------------------------------------------------------- #

def test_running_best_takes_the_minimum_for_lower_is_better():
    b = Builder(metric="seconds", higher_is_better=False, baseline=1.0)
    b.add(1, ExperimentStatus.accepted, median=0.5, vs_parent=2.0, vs_baseline=2.0)
    b.add(1, ExperimentStatus.rejected_speed, median=0.9, vs_parent=1.1, vs_baseline=1.1)
    c = build_chart(b.run, b.exps)
    assert c.higher_is_better is False
    assert c.points[-1].best_raw == pytest.approx(0.5), "a max() here would call the slowest run best"
    assert c.points[-1].best_speedup == pytest.approx(2.0)


def test_running_best_takes_the_maximum_for_higher_is_better():
    b = Builder(metric="tokens_per_s", higher_is_better=True, baseline=100.0)
    b.add(1, ExperimentStatus.accepted, median=140.0, vs_parent=1.4, vs_baseline=1.4)
    b.add(2, ExperimentStatus.accepted, median=180.0, vs_parent=1.28, vs_baseline=1.8)
    c = build_chart(b.run, b.exps)
    assert c.metric == "tokens_per_s" and c.higher_is_better
    assert [p.best_raw for p in c.points] == pytest.approx([140.0, 180.0])
    assert c.points[-1].best_speedup == pytest.approx(1.8)


def test_only_accepted_experiments_advance_the_frontier():
    b = Builder(metric="tokens_per_s", higher_is_better=True, baseline=100.0)
    b.add(1, ExperimentStatus.rejected_correctness)
    b.add(1, ExperimentStatus.not_selected, median=500.0, vs_parent=5.0, vs_baseline=5.0)
    c = build_chart(b.run, b.exps)
    assert c.points[-1].best_raw == pytest.approx(100.0), "a sibling that lost never became the head"
    assert c.points[-1].best_speedup == pytest.approx(1.0)


def test_unmeasured_experiments_stay_in_the_series():
    b = Builder()
    b.add(1, ExperimentStatus.locked_file)
    b.add(1, ExperimentStatus.accepted, median=0.5, vs_parent=2.0, vs_baseline=2.0)
    c = build_chart(b.run, b.exps)
    assert [p.measured for p in c.points] == [False, True]
    assert [p.index for p in c.points] == [0, 1], "indices cover every experiment, not only measured ones"
    assert c.points[0].raw is None and c.points[0].samples == []


def test_noise_band_brackets_the_baseline_in_metric_units(cfg):
    b = Builder(baseline=2.0)
    b.run.baseline_noise_cv = 0.05
    b.add(1, ExperimentStatus.rejected_speed, median=1.95, vs_parent=1.02)
    c = build_chart(b.run, b.exps, cfg)
    # threshold = max(1.03, 1 + 2 * 0.05) = 1.10
    assert c.threshold_speedup == pytest.approx(1.10)
    assert c.noise_band.lower == pytest.approx(2.0 / 1.10)
    assert c.noise_band.upper == pytest.approx(2.0 * 1.10)
    assert c.noise_band.lower < b.run.baseline_benchmark.median < c.noise_band.upper
    assert c.noise_band.derived_from_config is True


def test_historical_config_unknown_does_not_fabricate_band_or_beam_width():
    b = Builder()
    assert build_chart(b.run, []).noise_band is None
    assert build_tree(b.run, []).beam_width is None


def test_corrupt_parent_cycle_does_not_hang_tree():
    b = Builder()
    first = b.add(1, ExperimentStatus.accepted)
    second = b.add(2, ExperimentStatus.accepted, parent=first.id)
    first.parent_id = second.id
    b.head(second)
    assert len(build_tree(b.run, b.exps).nodes) == 3


def test_noise_band_floors_at_min_speedup_on_a_quiet_machine():
    b = Builder(baseline=1.0)
    b.run.baseline_noise_cv = 0.0
    assert build_chart(b.run, b.exps).threshold_speedup == pytest.approx(1.03)


def test_raw_mode_is_disabled_when_metrics_are_mixed():
    b = Builder(metric="seconds", baseline=1.0)
    e = b.add(1, ExperimentStatus.accepted, median=0.5, vs_parent=2.0, vs_baseline=2.0)
    e.benchmark = bench(120.0, "tokens_per_s", True)
    c = build_chart(b.run, b.exps)
    assert c.raw_available is False and "mixed metrics" in c.raw_unavailable_reason


def test_raw_mode_is_disabled_before_a_baseline_exists():
    run = RunState(config_name="t", target=".")
    c = build_chart(run, [])
    assert c.raw_available is False and "no baseline" in c.raw_unavailable_reason
    assert c.noise_band is None and c.points == []


def test_config_supplies_the_threshold_when_available(cfg):
    b = Builder(baseline=1.0)
    b.run.baseline_noise_cv = 0.2
    cfg.benchmark.min_speedup = 1.5
    cfg.benchmark.noise_multiplier = 1.0
    c = build_chart(b.run, b.exps, cfg)
    assert c.threshold_speedup == pytest.approx(1.5), "min_speedup floors the noise-derived value"
    assert c.noise_band.derived_from_config is True
    assert build_tree(b.run, b.exps, cfg).beam_width == cfg.search.beam_width


# --------------------------------------------------------------------------- #
# Funnel
# --------------------------------------------------------------------------- #

def test_funnel_separates_accepted_from_shipped_under_beam_search():
    """A head that entered the beam but lost to another branch keeps status `accepted`; it was
    accepted, but it is not in the export, so it must not be counted as shipped."""
    b = Builder()
    a = b.add(1, ExperimentStatus.accepted, median=0.5, vs_parent=2.0, vs_baseline=2.0)
    other = b.add(1, ExperimentStatus.accepted, median=0.6, vs_parent=1.6, vs_baseline=1.6)
    lost = b.add(1, ExperimentStatus.not_selected, median=0.7, vs_parent=1.4, vs_baseline=1.4)
    lost.comparison.significant = True
    b.add(1, ExperimentStatus.rejected_correctness)
    a2 = b.add(2, ExperimentStatus.accepted, parent=a.id, median=0.25, vs_parent=2.0, vs_baseline=4.0)
    b.add(2, ExperimentStatus.rejected_speed, parent=other.id, median=0.61, vs_parent=0.98, vs_baseline=1.6)
    for e in b.exps:
        if e.status != ExperimentStatus.rejected_correctness:
            e.files_changed = ["m.py"]
    b.head(a2)
    f = build_funnel(b.run, b.exps)
    rows = {r.label: r.count for r in f.rows}
    assert rows == {"proposed": 6, "patch applied": 5, "passed correctness": 0, "accepted": 4, "shipped": 2}
    assert f.shipped_ids == [a.id, a2.id] and f.accepted == 4
    assert sum(1 for e in b.exps if e.status == ExperimentStatus.accepted) == 3, "the old count would claim 3 kept"


def test_funnel_counts_retries_as_attempts_not_hypotheses():
    b = Builder()
    first = b.add(1, ExperimentStatus.patch_failed)
    retry = b.add(1, ExperimentStatus.accepted, retry_of=first.id, median=0.5, vs_parent=2.0, vs_baseline=2.0)
    b.head(retry)
    f = build_funnel(b.run, b.exps)
    assert (f.attempts, f.hypotheses, f.retries, f.shipped) == (2, 1, 1, 1)


def test_funnel_before_anything_ships():
    b = Builder()
    b.add(1, ExperimentStatus.rejected_speed, median=0.99, vs_parent=1.01, vs_baseline=1.01)
    f = build_funnel(b.run, b.exps)
    assert f.shipped == 0 and f.shipped_ids == [] and f.accepted == 0
