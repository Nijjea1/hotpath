"""Align two profiles into a before/after bottleneck diff.

This is a *presentation* layer over measurements the harness already took: it reads two
`ProfileSummary` records and never runs, times, or decides anything. It lives in `hotpath/` rather
than `server/` so `hotpath export` can reuse the same table in `REPORT.md`.

Three traps make a naive profile diff lie, and the whole module is shaped around avoiding them.

1. **Truncation is not elimination.** Profiles are stored top-N. A function that fell from rank 3 to
   rank 50 is simply absent from the later profile, and subtracting an absent row from a present one
   yields a confident "-100%, eliminated". Every claim about an absent row here is therefore a
   *bound* derived from `ProfileSummary.cutoff_self_time`, never a point value.
2. **`pct` is per-profile.** It is normalised to each profile's own total, so when total time halves,
   a function whose absolute cost never moved sees its `pct` double. Absolute self seconds are the
   only comparable quantity; `pct` is carried for display and never drives a classification.
3. **Different tools measure different things.** `profilelib.torch_run` labels itself
   `torch.profiler` or `torch.profiler (cpu-time fallback)` depending on whether CUPTI initialised
   at that moment. Across such a flip the two profiles are incommensurable, and this module refuses
   to diff them rather than producing a plausible-looking table.

The `unchanged` band is a *display* threshold, not a significance test: a profile is a single
observation, so this module has no noise floor to test against and does not pretend otherwise. That
distinction is why `unchanged_epsilon` is echoed back in the response.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from hotpath.schema import ProfileSummary

#: Relative change below which a row is called `unchanged`. A display convention, not statistics.
DEFAULT_EPSILON = 0.05

#: How far the profile's own total-time ratio may diverge from the benchmark's measured speedup
#: before the two are reported as telling different stories. Generous on purpose: profilers carry
#: real overhead, and this is a hint to the reader, not a verdict.
COHERENCE_TOLERANCE = 2.0

#: "new" is the absent-from-baseline case; there is deliberately no `below_cutoff_before` twin,
#: because a row's bound on the baseline side is carried in `bound`/`bound_note` instead.
Classification = Literal["shrank", "grew", "unchanged", "new", "below_cutoff_after"]


class BottleneckDelta(BaseModel):
    """One function, before and after. `None` on a side means it was absent from that profile."""
    key: str = Field(description="'file::function'; file is empty for C builtins and torch ops")
    function: str
    file: str
    line: int = Field(0, description="Line in the *before* profile when known; later lines shift and are not matched on")
    before_self: Optional[float] = None
    after_self: Optional[float] = None
    before_total: Optional[float] = None
    after_total: Optional[float] = None
    before_pct: Optional[float] = None
    after_pct: Optional[float] = None
    delta_self: Optional[float] = Field(None, description="after - before; None when either side is only bounded")
    delta_ratio: Optional[float] = Field(None, description="after / before; None when either side is only bounded")
    classification: Classification
    bound: Optional[float] = Field(None, description="Upper bound on the absent side's self time")
    bound_note: str = ""
    ambiguous: bool = Field(False, description="Several rows shared this key within one profile and were summed")
    editable: bool = Field(True, description="False for C builtins and library ops: visible, but not the agent's to change")


class ProfileDiff(BaseModel):
    comparable: bool
    incomparable_reason: str = ""
    before_tool: str = ""
    after_tool: str = ""
    before_commit: str = ""
    after_commit: str = ""
    before_total: float = 0.0
    after_total: float = 0.0
    total_ratio: Optional[float] = Field(None, description="before_total / after_total: >1 means total time shrank")
    before_residual: float = Field(0.0, description="Self time outside the retained rows, so the column reconciles")
    after_residual: float = 0.0
    before_truncated: bool = False
    after_truncated: bool = False
    cutoff_known: bool = Field(True, description="False for profiles stored before truncation was recorded; absences cannot be bounded")
    unchanged_epsilon: float = DEFAULT_EPSILON
    rows: list[BottleneckDelta] = Field(default_factory=list)
    benchmark_speedup: Optional[float] = None
    coherence_warning: str = ""


def _index(p: ProfileSummary) -> tuple[dict[str, dict], set[str]]:
    """Index a profile by `(file, function)`, summing duplicates.

    Line numbers shift whenever a patch adds or removes lines above a function, so they cannot be
    part of the key. That leaves collisions possible — cProfile reports only a bare function name,
    so `Block.forward` and `Model.forward` in one file key identically. Summing them and flagging
    the row is honest; silently keeping whichever sorted first is not.
    """
    rows: dict[str, dict] = {}
    ambiguous: set[str] = set()
    for h in p.hotspots:
        key = f"{h.file}::{h.function}"
        if key in rows:
            ambiguous.add(key)
            r = rows[key]
            r["self"] += h.self_time
            r["total"] += h.total_time
            r["pct"] += h.pct
            r["calls"] += h.calls
        else:
            rows[key] = {"self": h.self_time, "total": h.total_time, "pct": h.pct,
                         "calls": h.calls, "file": h.file, "function": h.function, "line": h.line}
    return rows, ambiguous


def _classify(before: Optional[float], after: Optional[float], epsilon: float) -> tuple[Classification, Optional[float]]:
    """Classify a row from absolute self times. Either side may be None (absent from that profile)."""
    if before is None:
        return "new", None
    if after is None:
        return "below_cutoff_after", None
    if before <= 0:
        # No meaningful ratio to take. Any positive `after` is growth in absolute terms.
        return ("grew" if after > 0 else "unchanged"), None
    ratio = after / before
    if abs(ratio - 1.0) <= epsilon:
        return "unchanged", ratio
    return ("shrank" if ratio < 1.0 else "grew"), ratio


def diff_profiles(before: Optional[ProfileSummary], after: Optional[ProfileSummary],
                  benchmark_speedup: Optional[float] = None,
                  epsilon: float = DEFAULT_EPSILON) -> ProfileDiff:
    """Align `before` and `after` into per-function deltas, or explain why they cannot be compared."""
    reason = _incomparable_reason(before, after)
    if reason:
        return ProfileDiff(
            comparable=False, incomparable_reason=reason, unchanged_epsilon=epsilon,
            before_tool=before.tool if before else "", after_tool=after.tool if after else "",
            before_commit=before.commit if before else "", after_commit=after.commit if after else "",
            before_total=before.total_time if before else 0.0,
            after_total=after.total_time if after else 0.0,
            benchmark_speedup=benchmark_speedup,
        )
    assert before is not None and after is not None  # guaranteed by _incomparable_reason

    b_rows, b_ambig = _index(before)
    a_rows, a_ambig = _index(after)
    b_truncated = before.retained < before.n_functions_total
    a_truncated = after.retained < after.n_functions_total
    # A profile written before truncation provenance existed reports retained == 0. We cannot bound
    # its omissions, so every absence stays explicitly unquantified instead of implying elimination.
    cutoff_known = before.completeness_known and after.completeness_known

    rows: list[BottleneckDelta] = []
    for key in b_rows.keys() | a_rows.keys():
        b, a = b_rows.get(key), a_rows.get(key)
        b_self = b["self"] if b else None
        a_self = a["self"] if a else None
        classification, ratio = _classify(b_self, a_self, epsilon)
        src = b or a
        assert src is not None  # a key exists in at least one side by construction

        bound: Optional[float] = None
        note = ""
        if classification == "below_cutoff_after":
            bound, note = _absence_note(after, "head", a_truncated, cutoff_known)
        elif classification == "new":
            bound, note = _absence_note(before, "baseline", b_truncated, cutoff_known)

        rows.append(BottleneckDelta(
            key=key, function=src["function"], file=src["file"], line=(b or {}).get("line", 0),
            before_self=b_self, after_self=a_self,
            before_total=b["total"] if b else None, after_total=a["total"] if a else None,
            before_pct=b["pct"] if b else None, after_pct=a["pct"] if a else None,
            delta_self=(a_self - b_self) if (b_self is not None and a_self is not None) else None,
            delta_ratio=ratio, classification=classification, bound=bound, bound_note=note,
            ambiguous=key in b_ambig or key in a_ambig,
            editable=bool(src["file"]),
        ))
    # Order by what used to hurt most, so the reader's eye starts at the original bottleneck; rows
    # that only exist after the change fall back to their own cost.
    rows.sort(key=lambda r: (r.before_self if r.before_self is not None else (r.after_self or 0.0), r.key),
              reverse=True)

    total_ratio = (before.total_time / after.total_time) if after.total_time > 0 else None
    return ProfileDiff(
        comparable=True, before_tool=before.tool, after_tool=after.tool,
        before_commit=before.commit, after_commit=after.commit,
        before_total=before.total_time, after_total=after.total_time, total_ratio=total_ratio,
        before_residual=before.residual_self_time, after_residual=after.residual_self_time,
        before_truncated=b_truncated, after_truncated=a_truncated, cutoff_known=cutoff_known,
        unchanged_epsilon=epsilon, rows=rows, benchmark_speedup=benchmark_speedup,
        coherence_warning=_coherence_warning(total_ratio, benchmark_speedup),
    )


def _incomparable_reason(before: Optional[ProfileSummary], after: Optional[ProfileSummary]) -> str:
    """Why these two profiles must not be diffed, or '' when they may be."""
    if before is None or after is None:
        return "no profile recorded for this run (set profile_cmd in the config to enable one)"
    if before.note:
        return f"baseline profile unavailable: {before.note}"
    if after.note:
        return f"head profile unavailable: {after.note}"
    if not before.hotspots or not after.hotspots:
        return "one of the profiles has no hotspots"
    if before.tool != after.tool:
        # Chiefly the CUPTI flip: device time on one side, CPU time on the other.
        return (f"profiles were taken with different tools ({before.tool} vs {after.tool}), "
                "so their times measure different things and are not comparable")
    if before.commit and before.commit == after.commit:
        return "head is still the baseline — nothing has been accepted yet"
    return ""


def _absence_note(missing_from: ProfileSummary, side: str, truncated: bool,
                  cutoff_known: bool) -> tuple[Optional[float], str]:
    """Bound a row's cost on the side it is absent from, and say so in words.

    Rows are stored sorted by self time, so the smallest retained row bounds everything below it.
    That bound is the difference between "we know this shrank to at most X" and the unfounded claim
    that it disappeared.
    """
    if not cutoff_known:
        return None, f"absent from the {side} profile; producer completeness is unknown, so the cost cannot be bounded"
    if truncated:
        bound = missing_from.cutoff_self_time
        return bound, f"absent from the {side} profile's top {missing_from.retained}: cost is at most {bound:.4f}s"
    # Nothing was dropped, so absence really is absence.
    return 0.0, f"not present in the {side} profile, which was not truncated"


def _coherence_warning(total_ratio: Optional[float], benchmark_speedup: Optional[float]) -> str:
    """Flag a profile whose story contradicts the benchmark's.

    The benchmark is the authority — it is repeated, noise-aware and bootstrap-tested, while a
    profile is one instrumented observation. When they disagree it is the profile that should be
    distrusted, and saying which way round that goes is more useful than hiding the discrepancy.
    """
    if not total_ratio or not benchmark_speedup or total_ratio <= 0 or benchmark_speedup <= 0:
        return ""
    if max(total_ratio / benchmark_speedup, benchmark_speedup / total_ratio) <= COHERENCE_TOLERANCE:
        return ""
    return (f"The profile's total time changed {total_ratio:.2f}x while the benchmark measured "
            f"{benchmark_speedup:.2f}x. The benchmark is the authoritative number; a profile can "
            "diverge because of profiler overhead, because device timing fell back to CPU time, or "
            "because profile_cmd and bench_cmd do not exercise the same work. Read the rows below "
            "as showing where time goes, not how much was saved.")


def _change_label(r: BottleneckDelta) -> str:
    """The change column, worded so a bound never reads as a measurement."""
    if r.delta_ratio is not None:
        return f"{(r.delta_ratio - 1) * 100:+8.1f}%"
    if r.classification == "new":
        return f"{'new':>9}"
    if r.classification == "below_cutoff_after":
        if r.bound == 0.0:
            return f"{'gone':>9}"                    # untruncated profile: absence really is absence
        if r.bound is not None:
            return f"{'<=' + format(r.bound, '.4f') + 's':>9}"
        return f"{'absent':>9}"
    return f"{r.classification:>9}"


def render(diff: ProfileDiff, limit: int = 15) -> str:
    """Plain-text table for `REPORT.md` and the CLI."""
    if not diff.comparable:
        return f"(no bottleneck diff: {diff.incomparable_reason})"
    out = [f"Total self time {diff.before_total:.4f}s -> {diff.after_total:.4f}s"
           + (f" ({diff.total_ratio:.2f}x)" if diff.total_ratio else ""),
           f"{'before_s':>9} {'after_s':>9} {'change':>9}  location"]
    for r in diff.rows[:limit]:
        before = f"{r.before_self:9.4f}" if r.before_self is not None else f"{'—':>9}"
        after = f"{r.after_self:9.4f}" if r.after_self is not None else f"{'—':>9}"
        change = _change_label(r)
        loc = f"{r.file}:{r.line} {r.function}" if r.file else r.function
        out.append(f"{before} {after} {change}  {loc}")
    if diff.rows[limit:]:
        out.append(f"... {len(diff.rows) - limit} more rows")
    if diff.coherence_warning:
        out.append("")
        out.append("WARNING: " + diff.coherence_warning)
    return "\n".join(out)
