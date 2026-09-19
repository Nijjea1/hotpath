"""Derived view models for the dashboard: the experiment tree and the progress chart.

Both are pure functions of `(RunState, [Experiment], HotpathConfig | None)` and compute nothing the
harness did not already measure. They live on the server rather than in the dashboard's inline
`<script>` for one reason: the derivations below are subtle enough to need tests, and a single-file
HTML dashboard has nowhere to run them.

What is genuinely derived, and how far it can be trusted:

* **Beam membership.** No run records which heads were in the beam at each iteration. It is
  recoverable from parentage: the orchestrator plans on every beam node and stamps that node's id as
  `parent_id` on each child, so a node was expanded at iteration `it` iff some experiment of that
  iteration names it as parent. The one blind spot is a beam node the planner returned no hypotheses
  for — it produces no children and is indistinguishable from an unexpanded node. `expanded_at` is
  therefore a lower bound, and the UI says "expanded at" rather than "was in the beam".
* **Direction of improvement.** `higher_is_better` flips what "best so far" means. Running a
  `max()` over raw seconds would report the *worst* result as the best, which is why the running
  best is computed here once rather than in two places in JS.
* **The noise band** is `[baseline/T, baseline*T]` for the acceptance threshold
  `T = max(min_speedup, 1 + noise_multiplier * noise_cv)` — the region where a measurement is not
  distinguishable from baseline. The formula is the same for both metric directions.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Literal, Optional

from pydantic import BaseModel, Field

from hotpath.schema import (REJECTED_STATUSES, Experiment, ExperimentStatus, HotpathConfig,
                            RunState)

BASELINE_ID = "baseline"

#: Fallbacks matching `BenchmarkConfig` defaults, for a server started with `--db` and no config.
FALLBACK_MIN_SPEEDUP = 1.03
FALLBACK_NOISE_MULTIPLIER = 2.0

NodeKind = Literal["baseline", "accepted", "not_selected", "rejected", "pending"]


def node_kind(status: ExperimentStatus) -> NodeKind:
    if status == ExperimentStatus.accepted:
        return "accepted"
    if status == ExperimentStatus.not_selected:
        return "not_selected"
    return "rejected" if status in REJECTED_STATUSES else "pending"


# --------------------------------------------------------------------------- #
# Experiment tree
# --------------------------------------------------------------------------- #

class TreeNode(BaseModel):
    id: str
    kind: NodeKind
    status: str
    iteration: int
    column: int = Field(description="Layout column; equals the iteration, 0 for the baseline")
    row: int
    parent_id: Optional[str] = None
    retry_of: Optional[str] = None
    retry_depth: int = Field(0, description="0 for a first attempt, 1 for its retry, and so on")
    expanded_at: list[int] = Field(default_factory=list,
                                   description="Iterations at which this node was planned on; a lower bound")
    on_head_chain: bool = False
    is_head: bool = False
    title: str = ""
    subtitle: str = ""
    speedup_vs_parent: Optional[float] = None
    speedup_vs_baseline: Optional[float] = None
    raw_median: Optional[float] = None
    reject_reason: str = ""


class TreeEdge(BaseModel):
    from_id: str
    to_id: str
    kind: Literal["lineage", "retry"]
    on_head_chain: bool = False
    spans: int = Field(1, description="Columns crossed; >1 when an older head was re-expanded later")


class RunTree(BaseModel):
    nodes: list[TreeNode]
    edges: list[TreeEdge]
    columns: int
    max_row: int
    metric: str = "seconds"
    higher_is_better: bool = False
    beam_width: Optional[int] = None


def _head_chain(run: RunState, by_id: dict[str, Experiment]) -> set[str]:
    """The accepted lineage from the current head back to the baseline: what actually ships."""
    chain: set[str] = set()
    cur = run.head_experiment_id
    while cur and cur in by_id and cur not in chain:
        chain.add(cur)
        cur = by_id[cur].parent_id
    return chain


def build_tree(run: RunState, exps: list[Experiment], cfg: HotpathConfig | None = None) -> RunTree:
    """Lay out the experiment forest: columns by iteration, rows grouped so subtrees stay contiguous."""
    by_id = {e.id: e for e in exps}
    bench = run.baseline_benchmark

    head_chain = _head_chain(run, by_id)

    # Beam membership, derived from parentage (see module docstring for the blind spot).
    expanded: dict[str, set[int]] = defaultdict(set)
    for e in exps:
        expanded[e.parent_id or BASELINE_ID].add(e.iteration)

    # Retry chains. A retry shares its original's lineage parent, so it is a sibling in the forest;
    # walking `retry_of` gives the attempt number and lets the layout keep the pair together.
    retries_of: dict[str, list[str]] = defaultdict(list)
    for e in sorted(exps, key=lambda x: (x.created_at, x.id)):
        if e.retry_of:
            retries_of[e.retry_of].append(e.id)

    def retry_depth(exp: Experiment) -> int:
        depth, seen, cur_id = 0, {exp.id}, exp.retry_of
        while cur_id and cur_id in by_id and cur_id not in seen:
            seen.add(cur_id)
            depth += 1
            cur_id = by_id[cur_id].retry_of
        return depth

    # Depth-first ordering over the forest. Primary (non-retry) children are visited in creation
    # order, and each is followed immediately by its retry chain, so "failed -> retried -> accepted"
    # reads as one unit instead of being scattered across the column.
    children: dict[str, list[str]] = defaultdict(list)
    for e in sorted(exps, key=lambda x: (x.iteration, x.created_at, x.id)):
        if not e.retry_of:
            children[e.parent_id or BASELINE_ID].append(e.id)

    order: dict[str, int] = {}

    def walk(node_id: str) -> None:
        if node_id in order:
            return  # already placed; also the cycle guard if parentage were ever corrupted
        order[node_id] = len(order)
        for child in children.get(node_id, []):
            walk(child)
            for r in _retry_chain(child, retries_of):
                order.setdefault(r, len(order))
                for grandchild in children.get(r, []):
                    walk(grandchild)

    walk(BASELINE_ID)
    # Anything unreachable (an orphan whose parent was never stored) still deserves a slot.
    for e in sorted(exps, key=lambda x: (x.iteration, x.created_at, x.id)):
        order.setdefault(e.id, len(order))

    # Columns, then a left-to-right sweep assigning rows. Sorting each column by its parent's row
    # keeps a child near its parent and preserves sibling order between columns, which is what
    # actually removes edge crossings once beam_width > 1.
    columns: dict[int, list[str]] = defaultdict(list)
    columns[0].append(BASELINE_ID)
    for e in exps:
        columns[max(1, e.iteration)].append(e.id)

    rows: dict[str, int] = {BASELINE_ID: 0}
    parent_of = {e.id: (e.parent_id or BASELINE_ID) for e in exps}
    for col in sorted(columns):
        if col == 0:
            continue
        ids = sorted(columns[col], key=lambda i: (rows.get(parent_of.get(i, BASELINE_ID), 0), order.get(i, 0)))
        for row, node_id in enumerate(ids):
            rows[node_id] = row

    nodes: list[TreeNode] = [TreeNode(
        id=BASELINE_ID, kind="baseline", status="baseline", iteration=0, column=0, row=0,
        expanded_at=sorted(expanded.get(BASELINE_ID, set())),
        on_head_chain=True, is_head=not head_chain,
        title="Baseline",
        subtitle=f"{bench.median:.5f} {bench.metric}" if bench else "measuring…",
        raw_median=bench.median if bench else None,
    )]
    for e in exps:
        c = e.comparison
        nodes.append(TreeNode(
            id=e.id, kind=node_kind(e.status), status=e.status.value, iteration=e.iteration,
            column=max(1, e.iteration), row=rows.get(e.id, 0),
            parent_id=e.parent_id, retry_of=e.retry_of, retry_depth=retry_depth(e),
            expanded_at=sorted(expanded.get(e.id, set())),
            on_head_chain=e.id in head_chain, is_head=e.id == run.head_experiment_id,
            title=e.hypothesis.idea,
            subtitle=_node_subtitle(e),
            speedup_vs_parent=c.speedup_vs_parent if c else None,
            speedup_vs_baseline=c.speedup_vs_baseline if c else None,
            raw_median=e.benchmark.median if e.benchmark else None,
            reject_reason=e.reject_reason or "",
        ))

    edges: list[TreeEdge] = []
    for e in exps:
        parent = e.parent_id or BASELINE_ID
        if parent != BASELINE_ID and parent not in by_id:
            continue  # parent was never stored; drawing a dangling edge would be a lie
        edges.append(TreeEdge(
            from_id=parent, to_id=e.id, kind="lineage", on_head_chain=e.id in head_chain,
            spans=max(1, max(1, e.iteration) - (max(1, by_id[parent].iteration) if parent in by_id else 0)),
        ))
        if e.retry_of and e.retry_of in by_id:
            edges.append(TreeEdge(from_id=e.retry_of, to_id=e.id, kind="retry"))

    return RunTree(
        nodes=nodes, edges=edges, columns=max(columns) + 1 if columns else 1,
        max_row=max(rows.values(), default=0),
        metric=bench.metric if bench else "seconds",
        higher_is_better=bench.higher_is_better if bench else False,
        beam_width=cfg.search.beam_width if cfg else None,
    )


def _retry_chain(node_id: str, retries_of: dict[str, list[str]]) -> list[str]:
    """Every retry descending from `node_id`, in attempt order, guarding against a cyclic `retry_of`."""
    out: list[str] = []
    frontier = list(retries_of.get(node_id, []))
    seen = {node_id}
    while frontier:
        nxt = frontier.pop(0)
        if nxt in seen:
            continue
        seen.add(nxt)
        out.append(nxt)
        frontier.extend(retries_of.get(nxt, []))
    return out


def _node_subtitle(e: Experiment) -> str:
    c = e.comparison
    sp = f"{c.speedup_vs_parent:.3f}x" if c else ""
    if e.status == ExperimentStatus.accepted:
        return f"✓ {sp} · {c.speedup_vs_baseline:.2f}x base" if c else "✓ accepted"
    if e.status == ExperimentStatus.not_selected:
        return f"✓ {sp} · not selected"
    if e.status in REJECTED_STATUSES:
        label = e.status.value.replace("rejected_", "")
        return f"✗ {label}" + (f" {sp}" if sp else "")
    return f"… {e.status.value}"


# --------------------------------------------------------------------------- #
# Progress chart
# --------------------------------------------------------------------------- #

class ChartPoint(BaseModel):
    index: int = Field(description="Chronological position among all experiments, measured or not")
    experiment_id: str
    iteration: int
    status: str
    kind: NodeKind
    title: str
    measured: bool
    raw: Optional[float] = None
    samples: list[float] = Field(default_factory=list)
    speedup_vs_baseline: Optional[float] = None
    speedup_vs_parent: Optional[float] = None
    ci_low: Optional[float] = Field(None, description="Parent-relative CI; never plot on the baseline-relative axis")
    ci_high: Optional[float] = Field(None, description="Parent-relative CI; never plot on the baseline-relative axis")
    best_raw: Optional[float] = Field(None, description="Running best raw value over accepted experiments")
    best_speedup: float = Field(1.0, description="Running best speedup over accepted experiments")
    retry_of: Optional[str] = None


class NoiseBand(BaseModel):
    lower: float
    upper: float
    threshold_speedup: float
    noise_cv: float
    derived_from_config: bool = Field(True, description="False when the acceptance parameters were defaulted")


class ChartSeries(BaseModel):
    metric: str = "seconds"
    higher_is_better: bool = False
    baseline_raw: Optional[float] = None
    raw_available: bool = False
    raw_unavailable_reason: str = ""
    threshold_speedup: float = FALLBACK_MIN_SPEEDUP
    noise_band: Optional[NoiseBand] = None
    points: list[ChartPoint] = Field(default_factory=list)


def build_chart(run: RunState, exps: list[Experiment], cfg: HotpathConfig | None = None) -> ChartSeries:
    """Build both chart series — raw metric and speedup — with the running best in each."""
    bench = run.baseline_benchmark
    metric = bench.metric if bench else "seconds"
    higher_is_better = bench.higher_is_better if bench else False
    baseline_raw = bench.median if bench else None

    # Raw mode needs one consistent unit. `compare()` already refuses to rate a candidate whose
    # metric changed, but a mixed run could still reach here, and silently plotting two units on one
    # axis would be worse than disabling the mode.
    metrics = {e.benchmark.metric for e in exps if e.benchmark}
    raw_reason = ""
    if baseline_raw is None:
        raw_reason = "no baseline benchmark yet"
    elif len(metrics - {metric}) > 0:
        raw_reason = f"experiments report mixed metrics ({', '.join(sorted(metrics | {metric}))})"

    noise = run.baseline_noise_cv
    bcfg = cfg.benchmark if cfg else None
    threshold = max(bcfg.min_speedup if bcfg else FALLBACK_MIN_SPEEDUP,
                    1.0 + (bcfg.noise_multiplier if bcfg else FALLBACK_NOISE_MULTIPLIER) * noise)
    band = None
    if baseline_raw is not None and threshold > 0 and bcfg is not None:
        band = NoiseBand(lower=baseline_raw / threshold, upper=baseline_raw * threshold,
                         threshold_speedup=threshold, noise_cv=noise, derived_from_config=bcfg is not None)

    points: list[ChartPoint] = []
    best_speedup = 1.0
    best_raw = baseline_raw
    for i, e in enumerate(sorted(exps, key=lambda x: (x.created_at, x.id))):
        c, b = e.comparison, e.benchmark
        # Only an accepted experiment advances the frontier: it is the one that became a head.
        if e.status == ExperimentStatus.accepted:
            if c:
                best_speedup = max(best_speedup, c.speedup_vs_baseline)
            if b is not None:
                best_raw = b.median if best_raw is None else (
                    max(best_raw, b.median) if higher_is_better else min(best_raw, b.median))
        points.append(ChartPoint(
            index=i, experiment_id=e.id, iteration=e.iteration, status=e.status.value,
            kind=node_kind(e.status), title=e.hypothesis.idea, measured=b is not None,
            raw=b.median if b else None, samples=list(b.samples) if b else [],
            speedup_vs_baseline=c.speedup_vs_baseline if c else None,
            speedup_vs_parent=c.speedup_vs_parent if c else None,
            ci_low=c.ci_low if c else None, ci_high=c.ci_high if c else None,
            best_raw=best_raw, best_speedup=best_speedup, retry_of=e.retry_of,
        ))

    return ChartSeries(
        metric=metric, higher_is_better=higher_is_better, baseline_raw=baseline_raw,
        raw_available=not raw_reason, raw_unavailable_reason=raw_reason,
        threshold_speedup=threshold, noise_band=band, points=points,
    )


# --------------------------------------------------------------------------- #
# Funnel
# --------------------------------------------------------------------------- #

class FunnelRow(BaseModel):
    label: str
    count: int
    meaning: str


class Funnel(BaseModel):
    """How many attempts survived each gate. `accepted` and `shipped` are separate on purpose:
    with beam search, a correct and faster change can be accepted and still not reach the final head,
    so counting every acceptance as a kept change would overstate what the agent delivered."""
    attempts: int
    hypotheses: int
    retries: int
    accepted: int
    shipped: int
    shipped_ids: list[str]
    rows: list[FunnelRow]


def build_funnel(run: RunState, exps: list[Experiment]) -> Funnel:
    by_id = {e.id: e for e in exps}
    chain = _head_chain(run, by_id)
    shipped_ids = [e.id for e in exps if e.id in chain]
    retries = sum(1 for e in exps if e.retry_of)
    accepted = sum(1 for e in exps if e.comparison is not None and e.comparison.significant)
    rows = [
        FunnelRow(label="proposed", count=len(exps), meaning="attempts, including retries"),
        FunnelRow(label="patch applied", count=sum(1 for e in exps if e.files_changed),
                  meaning="edits applied cleanly to editable files"),
        FunnelRow(label="passed correctness", count=sum(1 for e in exps if e.correctness and e.correctness.passed),
                  meaning="the locked test command exited 0"),
        FunnelRow(label="accepted", count=accepted,
                  meaning="correct and faster than its parent beyond the noise threshold and CI"),
        FunnelRow(label="shipped", count=len(shipped_ids),
                  meaning="in the final head's lineage: the changes an export contains"),
    ]
    return Funnel(attempts=len(exps), hypotheses=len(exps) - retries, retries=retries, accepted=accepted,
                  shipped=len(shipped_ids), shipped_ids=shipped_ids, rows=rows)
