"""Ablation: rebuild the final state with each accepted change left out, and re-measure.

If leaving a change out does not make things slower, it was not pulling its weight.
The output is the table that belongs in the README: what each change contributed.

Pruning (`prune`) goes one step further and only on request: it removes every change that did not
pull its weight *together*, re-verifies correctness, and benchmarks the pruned stack beside the full
one. Two changes that are each removable alone can still matter jointly, so the pruned stack is kept
only if the full stack is not measurably faster. The run's own head is never modified.
"""
from __future__ import annotations

from pathlib import Path
import statistics

from pydantic import BaseModel

from hotpath import observability as obs
from hotpath.benchmark import compare, speedup_of
from hotpath.harness import Harness
from hotpath.schema import BenchmarkStats, Experiment, ExperimentStatus, HotpathConfig, RunState
from hotpath.store import Store
from hotpath.workspace import PatchError, Workspace


class AblationRow(BaseModel):
    experiment_id: str
    idea: str
    status: str                     # measured | could_not_apply | error
    median_without: float | None = None
    median_full: float | None = None
    contribution: float | None = None  # speedup lost when this change is removed (>1 means it helps)
    pulls_weight: bool | None = None   # the full stack is significantly faster than the stack without it
    reason: str = ""


class PruneResult(BaseModel):
    status: str                        # nothing_to_prune | pruned | kept_full
    dropped: list[str] = []            # experiment ids removed from the stack
    kept: list[str] = []
    reason: str
    commit: str | None = None          # the pruned stack, when status == "pruned"
    full_median: float | None = None
    pruned_median: float | None = None
    speedup_vs_baseline: float | None = None


class AblationReport(BaseModel):
    run_id: str
    full_median: float | None       # None when the full stack could not be re-verified
    metric: str
    rows: list[AblationRow]
    error: str | None = None
    prune: PruneResult | None = None


def accepted_chain(store: Store, run: RunState) -> list[Experiment]:
    by_id = {e.id: e for e in store.list_experiments(run.id)}
    chain, cur = [], run.head_experiment_id
    while cur and cur in by_id:
        chain.append(by_id[cur]); cur = by_id[cur].parent_id
    chain.reverse()
    return chain


async def ablate(cfg: HotpathConfig, store: Store, run: RunState) -> AblationReport:
    ws = Workspace(Path(cfg.target), Path(cfg.workdir))
    harness = Harness(cfg, ws, store)
    chain = accepted_chain(store, run)
    rows: list[AblationRow] = []
    full_runs = []
    full_wt = ws.create_worktree(run.head_commit, f"ablate_full_{run.id}")
    with obs.span("hotpath.ablation", run.id, n=len(chain)):
        try:
            full_tests = await harness.run_tests(full_wt)
            if not full_tests.passed:
                # Nothing can be measured against a head that is no longer correct.
                reason = "stored full stack no longer passes correctness tests"
                return AblationReport(run_id=run.id, full_median=None, metric=(run.baseline_benchmark.metric if run.baseline_benchmark else ""),
                                      error=reason,
                                      rows=[AblationRow(experiment_id=e.id, idea=e.hypothesis.idea, status="error",
                                                        reason=reason) for e in chain])
            for skip in chain:
                wt = ws.create_worktree(run.base_commit, f"ablate_{skip.id}")
                try:
                    for e in chain:
                        if e.id != skip.id:
                            ws.apply_edits(wt, e.edits, cfg.editable, cfg.locked)
                    tests = await harness.run_tests(wt)
                    if not tests.passed:
                        rows.append(AblationRow(experiment_id=skip.id, idea=skip.hypothesis.idea, status="error",
                                                reason="state without this change fails tests (changes are interdependent)"))
                        continue
                    # The stored full-stack benchmark may be hours old. Measure it
                    # again immediately beside each omitted-change candidate.
                    full_now = await harness.run_benchmark(full_wt)
                    full_runs.append(full_now)
                    bench = await harness.run_benchmark(wt)
                    cmp = compare(bench, full_now, run.baseline_benchmark, cfg.benchmark, run.baseline_noise_cv)
                    rows.append(AblationRow(experiment_id=skip.id, idea=skip.hypothesis.idea, status="measured",
                                            median_without=bench.median, median_full=full_now.median,
                                            contribution=cmp.speedup_vs_parent, pulls_weight=cmp.significant,
                                            reason=("pulls its weight" if cmp.significant else "removing it costs nothing measurable")))
                except PatchError as e:
                    rows.append(AblationRow(experiment_id=skip.id, idea=skip.hypothesis.idea, status="could_not_apply",
                                            reason=f"later edits depend on this one: {e}"))
                except Exception as e:
                    rows.append(AblationRow(experiment_id=skip.id, idea=skip.hypothesis.idea, status="error", reason=str(e)))
                finally:
                    ws.remove_worktree(wt)
            if not full_runs:
                full_runs.append(await harness.run_benchmark(full_wt))
        finally:
            ws.remove_worktree(full_wt)
    return AblationReport(run_id=run.id, full_median=statistics.median(r.median for r in full_runs),
                          metric=full_runs[0].metric, rows=rows)


async def prune(cfg: HotpathConfig, store: Store, run: RunState, report: AblationReport) -> PruneResult:
    """Drop every change the ablation found removable, together, and keep the result only if it
    stays correct and the full stack is not measurably faster than it."""
    if report.full_median is None:
        return PruneResult(status="kept_full", reason=f"ablation did not measure the stack: {report.error}")
    unmeasured = [r.experiment_id for r in report.rows if r.status != "measured"]
    removable = [r.experiment_id for r in report.rows if r.status == "measured" and not r.pulls_weight]
    if not removable:
        why = "every measured change pulls its weight"
        if unmeasured:
            why += f"; {len(unmeasured)} could not be measured alone and are kept"
        return PruneResult(status="nothing_to_prune", reason=why)
    chain = accepted_chain(store, run)
    keep = [e for e in chain if e.id not in removable]
    result = PruneResult(status="kept_full", dropped=removable, kept=[e.id for e in keep], reason="")
    ws = Workspace(Path(cfg.target), Path(cfg.workdir))
    harness = Harness(cfg, ws, store)
    full_wt = ws.create_worktree(run.head_commit, f"prune_full_{run.id}")
    pruned_wt = ws.create_worktree(run.base_commit, f"prune_{run.id}")
    with obs.span("hotpath.prune", run.id, dropped=len(removable)):
        try:
            try:
                for e in keep:
                    ws.apply_edits(pruned_wt, e.edits, cfg.editable, cfg.locked)
            except PatchError as e:
                result.reason = f"the remaining changes do not apply without the dropped ones: {e}"
                return result
            tests = await harness.run_tests(pruned_wt)
            if not tests.passed:
                result.reason = "the pruned stack fails correctness tests; the dropped changes are needed together"
                return result
            full = await harness.run_benchmark(full_wt)
            pruned = await harness.run_benchmark(pruned_wt)
            result.full_median, result.pruned_median = full.median, pruned.median
            cmp = compare(pruned, full, run.baseline_benchmark, cfg.benchmark, run.baseline_noise_cv)
            if cmp.significant:
                result.reason = (f"the full stack is measurably faster than the pruned one ({cmp.reason}); "
                                 "the dropped changes matter jointly")
                return result
            result.status = "pruned"
            result.speedup_vs_baseline = speedup_of(run.baseline_benchmark, pruned)
            result.commit = (ws.commit(pruned_wt, f"pruned_{run.id}", f"hotpath: {run.id} pruned of {len(removable)} change(s)")
                             if keep else run.base_commit)
            result.reason = (f"correct, and the full stack is not measurably faster ({cmp.reason}); "
                             f"kept {len(keep)} of {len(chain)} changes")
            return result
        finally:
            ws.remove_worktree(full_wt)
            ws.remove_worktree(pruned_wt)


def render(report: AblationReport) -> str:
    if report.full_median is None:
        return f"Ablation for {report.run_id}: not measured ({report.error})"
    lines = [f"Ablation for {report.run_id}: full stack median {report.full_median:.5f} {report.metric}", ""]
    lines.append(f"{'contribution':>13}  {'full':>10}  {'without':>10}  idea")
    for r in report.rows:
        c = f"{r.contribution:.3f}x" if r.contribution else r.status
        w = f"{r.median_without:.5f}" if r.median_without else "-"
        f = f"{r.median_full:.5f}" if r.median_full else "-"
        lines.append(f"{c:>13}  {f:>10}  {w:>10}  {r.idea}  ({r.reason})")
    if report.prune:
        p = report.prune
        lines += ["", f"Prune: {p.status}. {p.reason}"]
        if p.status == "pruned":
            lines.append(f"  dropped {', '.join(p.dropped)}; pruned stack {p.speedup_vs_baseline:.3f}x vs baseline "
                         f"(commit {p.commit[:8]})")
    return "\n".join(lines)
