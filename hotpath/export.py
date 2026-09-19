"""Assemble a PR-ready bundle from an accepted run: the optimized tree, the diff, a benchmark
table, and (optionally) the ablation. Everything is read from the store the harness wrote — no
number is invented here."""
from __future__ import annotations

from pathlib import Path

from hotpath.ablation import PruneResult
from hotpath.profilediff import diff_profiles, render as render_bottlenecks
from hotpath.schema import Experiment, ExperimentStatus, HotpathConfig, RunState
from hotpath.store import Store
from hotpath.workspace import Workspace


def _md_cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ").strip()


def _benchmark_table(exps: list[Experiment], shipped: set[str]) -> str:
    head = ("| # | Iter | Hypothesis | Status | vs parent | vs baseline | Why |\n"
            "|---|------|------------|--------|-----------|-------------|-----|")
    rows = []
    for i, e in enumerate(exps, 1):
        c = e.comparison
        sp = f"{c.speedup_vs_parent:.3f}x" if c else "–"
        sb = f"{c.speedup_vs_baseline:.3f}x" if c else "–"
        # With beam search an accepted change can miss the final head; only the head's lineage ships.
        shipped_note = "shipped in this bundle" if e.id in shipped else "accepted, but not in the final head"
        why = e.reject_reason or (shipped_note if e.status == ExperimentStatus.accepted else "")
        idea = _md_cell(e.hypothesis.idea) + (f" _(retry of {e.retry_of})_" if e.retry_of else "")
        rows.append(f"| {i} | {e.iteration} | {idea} | {e.status.value} | {sp} | {sb} | {_md_cell(why)[:80]} |")
    return head + "\n" + "\n".join(rows)


def _accepted_chain(run: RunState, exps: list[Experiment]) -> list[Experiment]:
    by_id = {e.id: e for e in exps}
    chain, cur = [], run.head_experiment_id
    while cur and cur in by_id:
        chain.append(by_id[cur])
        cur = by_id[cur].parent_id
    return list(reversed(chain))


def build_bundle(cfg: HotpathConfig, store: Store, run: RunState, dest: Path, ws: Workspace,
                 ablation_md: str | None = None, pruned: PruneResult | None = None) -> Path:
    """Write REPORT.md, changes.patch, and optimized_src/ into `dest`. Returns `dest`.

    When `pruned` is a verified prune, the bundle ships the pruned stack instead of the run's head."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    exps = store.list_experiments(run.id)
    use_pruned = pruned is not None and pruned.status == "pruned"
    ship_commit = pruned.commit if use_pruned else run.head_commit

    ws.checkout_into(ship_commit, dest / "optimized_src")
    (dest / "changes.patch").write_text(ws.diff_commits(run.base_commit, ship_commit), encoding="utf-8")

    b, h = run.baseline_benchmark, run.head_benchmark
    metric = b.metric if b else "seconds"
    chain = _accepted_chain(run, exps)
    shipped = {e.id for e in chain} - set(pruned.dropped if use_pruned else [])
    accepted = sum(1 for e in exps if e.comparison is not None and e.comparison.significant)
    lines = [f"# Hotpath optimization report — {cfg.name}", ""]
    speedup = pruned.speedup_vs_baseline if use_pruned else run.best_speedup
    lines.append(f"**{speedup:.3f}x** vs baseline ({metric}); {len(shipped)} change(s) shipped. "
                 f"{accepted} of {len(exps)} candidates were accepted (correct and faster than their parent).")
    lines.append("")
    if use_pruned:
        lines += [f"This bundle ships the **pruned** stack (commit `{pruned.commit[:8]}`): ablation found "
                  f"{len(pruned.dropped)} change(s) that did not pull their weight, the stack without them "
                  "passed the locked tests again, and the full stack was not measurably faster "
                  f"(full {pruned.full_median:.5f} vs pruned {pruned.pruned_median:.5f} {metric}, measured side by side).",
                  ""]
    if b and h:
        shipped_median = f"`{pruned.pruned_median:.5f} {metric}` (pruned stack)" if use_pruned else f"`{h.median:.5f} {metric}`"
        lines += [f"- Baseline median: `{b.median:.5f} {metric}` ({b.n} samples)",
                  f"- Optimized median: {shipped_median}",
                  f"- Noise floor (run-to-run CV): {run.baseline_noise_cv * 100:.2f}%",
                  f"- Base commit `{run.base_commit[:8]}` → shipped `{ship_commit[:8]}`"
                  + (f" (the run's head was `{run.head_commit[:8]}`)" if use_pruned else ""), ""]
    if chain:
        lines += ["## Accepted chain (in order)", ""]
        for e in chain:
            sp = f"{e.comparison.speedup_vs_parent:.3f}x" if e.comparison else "–"
            files = ", ".join(f"`{f}`" for f in e.hypothesis.files)
            dropped = " — **dropped by pruning**" if e.id not in shipped else ""
            lines.append(f"1. **{_md_cell(e.hypothesis.idea)}** — {sp} vs its parent ({files}){dropped}")
        lines.append("")
    diff = diff_profiles(run.baseline_profile, run.head_profile, run.best_speedup)
    if diff.comparable:
        lines += ["## Where the time went", "", "```", render_bottlenecks(diff).rstrip(), "```", ""]
        if use_pruned:
            lines += ["The profiles above are of the run's full head, not the pruned stack; the pruned stack "
                      "was benchmarked but not re-profiled.", ""]
        if diff.coherence_warning:
            lines += [f"> **Note:** {diff.coherence_warning}", ""]
        lines += [f"Profile times come from `{diff.before_tool}` and show *where* time goes. A `<=` "
                  "entry is an upper bound, not a measurement: the function fell out of the retained "
                  "rows, so its cost is only known to be at most that value. Changes under "
                  f"{diff.unchanged_epsilon * 100:.0f}% are within the display threshold — a profile is a "
                  "single observation with no noise floor, so the benchmark table above is what "
                  "decided each verdict.", ""]
    lines += ["## All candidates", "", _benchmark_table(exps, shipped), ""]
    if ablation_md:
        lines += ["## Ablation (leave-one-out re-measurement)", "", "```", ablation_md.rstrip(), "```", ""]
    lines += ["## Files in this bundle", "",
              "- `optimized_src/` — the full source tree with every shipped change",
              "- `changes.patch` — unified diff from the baseline to the shipped stack", ""]
    (dest / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    return dest
