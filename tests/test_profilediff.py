"""The bottleneck diff's job is to not overclaim. These tests are mostly about what it refuses to say."""
import json

import pytest

from hotpath.profilediff import DEFAULT_EPSILON, diff_profiles, render
from hotpath.profiler import parse_profile_output, render_profile
from hotpath.schema import ContextConfig, Hotspot, ProfileSummary


_commits = iter(f"{i:040x}" for i in range(1, 10_000))


def summary(rows, *, tool="cProfile", commit=None, total=None, retained=None, n_total=None):
    """A ProfileSummary as `parse_profile_output` would have built it.

    Commits default to distinct values: two profiles at the same commit are deliberately refused as
    "nothing accepted yet", which would otherwise silently empty every diff under test.
    """
    commit = commit or next(_commits)
    hs = [Hotspot(function=f, file=fl, line=ln, self_time=s, total_time=t, pct=0.0, calls=1)
          for f, fl, ln, s, t in rows]
    hs.sort(key=lambda h: h.self_time, reverse=True)
    total = sum(h.self_time for h in hs) if total is None else total
    for h in hs:
        h.pct = 100.0 * h.self_time / (total or 1)
    kept = len(hs) if retained is None else retained
    n_all = len(hs) if n_total is None else n_total
    return ProfileSummary(
        tool=tool, total_time=total, hotspots=hs[:kept], commit=commit,
        n_functions_total=n_all, retained=len(hs[:kept]),
        completeness_known=True,
        residual_self_time=max(0.0, total - sum(h.self_time for h in hs[:kept])),
        cutoff_self_time=hs[:kept][-1].self_time if kept and kept < n_all else 0.0,
    )


# --------------------------------------------------------------------------- #
# Retention: storage depth must exceed prompt depth, without widening the prompt
# --------------------------------------------------------------------------- #

def _profile_stdout(n: int) -> str:
    rows = ",".join(
        f'{{"function":"f{i}","file":"m.py","line":{i},"self_time":{(n - i) / 100:.4f},'
        f'"total_time":{(n - i) / 50:.4f},"pct":1.0,"calls":1}}' for i in range(n))
    return 'noise\n{"hotpath_profile":1,"tool":"cProfile","completeness_known":true,"total_time":2.0,"hotspots":[' + rows + "]}\n"


def test_retention_is_deeper_than_the_prompt_and_records_its_cutoff():
    p = parse_profile_output(_profile_stdout(60), retain=40, commit="c" * 40)
    assert p.retained == 40 and p.n_functions_total == 60
    # The smallest retained row bounds everything dropped — this is what keeps absences honest.
    assert p.cutoff_self_time == pytest.approx(p.hotspots[-1].self_time)
    assert p.cutoff_self_time > 0
    assert p.residual_self_time >= 0

    # Widening storage must not widen what the planner is shown.
    assert render_profile(p, ContextConfig().max_hotspots).count("\n") == 12  # header + 12 rows
    assert len(render_profile(p, 12)) < len(render_profile(p))


def test_untruncated_profile_reports_no_cutoff_so_absence_is_real():
    p = parse_profile_output(_profile_stdout(5), retain=40)
    assert p.retained == p.n_functions_total == 5
    assert p.cutoff_self_time == 0.0


def test_upstream_truncation_is_preserved():
    import json
    raw = json.loads(_profile_stdout(5).splitlines()[1])
    raw["n_functions_total"] = 80
    after = parse_profile_output(json.dumps(raw), retain=40, commit="after")
    before = summary([("missing", "m.py", 1, 1.0, 1.0)], commit="before")
    row = next(r for r in diff_profiles(before, after).rows if r.function == "missing")
    assert after.n_functions_total == 80
    assert row.bound == after.hotspots[-1].self_time > 0


def test_unknown_producer_completeness_never_implies_zero():
    import json
    raw = json.loads(_profile_stdout(5).splitlines()[1])
    del raw["completeness_known"]
    after = parse_profile_output(json.dumps(raw), retain=40, commit="after")
    before = summary([("missing", "m.py", 1, 1.0, 1.0)], commit="before")
    row = next(r for r in diff_profiles(before, after).rows if r.function == "missing")
    assert row.bound is None
    assert "unknown" in row.bound_note


def test_real_producer_reports_total_before_truncation(capsys):
    import json
    from hotpath.profilelib import run
    run(lambda: sum(sorted([3, 1, 2])), top=1)
    raw = json.loads(capsys.readouterr().out)
    assert len(raw["hotspots"]) == 1
    assert raw["n_functions_total"] > 1 and raw["completeness_known"]
    parsed = parse_profile_output(json.dumps(raw), retain=40)
    assert parsed.cutoff_self_time > 0
    assert "aggregated caller edges" in parsed.flamegraph_unavailable_reason


def test_parser_preserves_an_observed_nested_event_tree():
    raw = {
        "hotpath_profile": 1, "tool": "torch.profiler (cpu-time fallback)",
        "total_time": 1.0, "n_functions_total": 1, "completeness_known": True,
        "hotspots": [{"function": "root", "file": "", "line": 0, "self_time": 0.2,
                      "total_time": 1.0, "pct": 20.0, "calls": 1}],
        "flamegraph_source": "torch.profiler CPU event tree",
        "flamegraph": [{"function": "root", "self_time": 0.2, "total_time": 1.0, "calls": 1,
                        "children": [{"function": "child", "self_time": 0.8, "total_time": 0.8,
                                      "calls": 1}]}],
    }
    parsed = parse_profile_output(json.dumps(raw), retain=40)
    assert parsed.flamegraph_source == "torch.profiler CPU event tree"
    assert parsed.flamegraph[0].function == "root"
    assert parsed.flamegraph[0].children[0].function == "child"


def test_torch_event_tree_uses_only_observed_parentage():
    from hotpath.profilelib import _torch_cpu_event_tree

    class Event:
        def __init__(self, key, self_us, total_us, parent=None):
            self.key, self.self_cpu_time_total, self.cpu_time_total = key, self_us, total_us
            self.cpu_parent, self.cpu_children = parent, []
            if parent:
                parent.cpu_children.append(self)

    root, child, sibling = Event("decode", 10, 100), None, None
    child = Event("attention", 70, 80, root)
    sibling = Event("sample", 30, 30)
    tree = _torch_cpu_event_tree([root, child, sibling], max_events=10)
    assert [frame["function"] for frame in tree] == ["decode", "sample"]
    assert tree[0]["children"] == [{"function": "attention", "file": "", "line": 0,
                                     "self_time": 0.00007, "total_time": 0.00008,
                                     "calls": 1, "children": []}]


# --------------------------------------------------------------------------- #
# Alignment
# --------------------------------------------------------------------------- #

def test_matches_across_a_line_number_shift():
    """A patch that inserts lines above a function must not orphan it from its own history."""
    before = summary([("work", "mod.py", 10, 1.0, 2.0)], commit="b" * 40)
    after = summary([("work", "mod.py", 27, 0.25, 0.5)], commit="h" * 40)
    d = diff_profiles(before, after)
    assert d.comparable and len(d.rows) == 1
    row = d.rows[0]
    assert row.classification == "shrank"
    assert row.delta_ratio == pytest.approx(0.25)
    assert row.before_self == 1.0 and row.after_self == 0.25


def test_pct_inversion_does_not_read_as_growth():
    """Total halves while one function's absolute cost holds: its share doubles, its cost did not."""
    before = summary([("steady", "mod.py", 1, 1.0, 1.0), ("gone", "mod.py", 20, 1.0, 1.0)])
    after = summary([("steady", "mod.py", 1, 1.0, 1.0)])
    d = diff_profiles(before, after)
    steady = next(r for r in d.rows if r.function == "steady")
    assert steady.before_pct == pytest.approx(50.0) and steady.after_pct == pytest.approx(100.0)
    assert steady.classification == "unchanged", "share doubled but absolute self time did not move"


def test_duplicate_names_in_one_file_are_summed_and_flagged():
    before = summary([("forward", "model.py", 10, 1.0, 1.0), ("forward", "model.py", 50, 2.0, 2.0)])
    after = summary([("forward", "model.py", 10, 1.5, 1.5)])
    d = diff_profiles(before, after)
    row = d.rows[0]
    assert row.ambiguous and row.before_self == pytest.approx(3.0)


def test_c_builtins_are_kept_but_marked_not_editable():
    before = summary([("<method 'count' of 'list' objects>", "", 0, 1.0, 1.0)])
    after = summary([("<method 'count' of 'list' objects>", "", 0, 0.1, 0.1)])
    d = diff_profiles(before, after)
    assert d.rows[0].editable is False and d.rows[0].classification == "shrank"


# --------------------------------------------------------------------------- #
# The core honesty guarantee: truncation is never reported as elimination
# --------------------------------------------------------------------------- #

def test_absent_from_truncated_head_is_bounded_not_eliminated():
    before = summary([("slow", "mod.py", 1, 5.0, 5.0)], commit="b" * 40)
    # Head kept 2 of 40 rows, so anything dropped cost at most the smallest retained row.
    after = summary([("other", "mod.py", 2, 1.0, 1.0), ("more", "mod.py", 3, 0.5, 0.5)],
                    commit="h" * 40, retained=2, n_total=40)
    d = diff_profiles(before, after)
    slow = next(r for r in d.rows if r.function == "slow")
    assert slow.classification == "below_cutoff_after"
    assert slow.delta_ratio is None and slow.delta_self is None, "no point estimate may be invented"
    assert slow.bound == pytest.approx(0.5)
    assert "at most" in slow.bound_note


def test_absent_from_untruncated_head_is_a_real_elimination():
    before = summary([("slow", "mod.py", 1, 5.0, 5.0), ("keep", "mod.py", 2, 1.0, 1.0)], commit="b" * 40)
    after = summary([("keep", "mod.py", 2, 1.0, 1.0)], commit="h" * 40)
    slow = next(r for r in diff_profiles(before, after).rows if r.function == "slow")
    assert slow.classification == "below_cutoff_after" and slow.bound == 0.0
    assert "not truncated" in slow.bound_note


def test_legacy_profile_without_provenance_cannot_bound_absences():
    """Runs stored before retention tracking report retained == 0; they get no bound at all."""
    before = ProfileSummary(tool="cProfile", commit="b" * 40, total_time=5.0,
                            hotspots=[Hotspot(function="slow", file="mod.py", line=1, self_time=5.0,
                                              total_time=5.0, pct=100.0, calls=1)])
    after = ProfileSummary(tool="cProfile", commit="h" * 40, total_time=1.0,
                           hotspots=[Hotspot(function="other", file="mod.py", line=2, self_time=1.0,
                                             total_time=1.0, pct=100.0, calls=1)])
    d = diff_profiles(before, after)
    assert d.cutoff_known is False
    slow = next(r for r in d.rows if r.function == "slow")
    assert slow.bound is None and "cannot be bounded" in slow.bound_note


def test_new_function_is_bounded_against_the_baseline_profile():
    before = summary([("a", "m.py", 1, 1.0, 1.0), ("b", "m.py", 2, 0.4, 0.4)], retained=2, n_total=30)
    after = summary([("a", "m.py", 1, 1.0, 1.0), ("fresh", "m.py", 9, 0.2, 0.2)])
    fresh = next(r for r in diff_profiles(before, after).rows if r.function == "fresh")
    assert fresh.classification == "new" and fresh.before_self is None
    assert fresh.bound == pytest.approx(0.4) and "baseline" in fresh.bound_note


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("before,after,fragment", [
    (None, None, "no profile recorded"),
    (summary([("a", "m.py", 1, 1.0, 1.0)]), None, "no profile recorded"),
    (ProfileSummary(note="profile command failed: boom"), summary([("a", "m.py", 1, 1.0, 1.0)]), "baseline profile unavailable"),
    (summary([("a", "m.py", 1, 1.0, 1.0)]), ProfileSummary(note="no profile_cmd configured"), "head profile unavailable"),
])
def test_missing_or_failed_profiles_are_refused(before, after, fragment):
    d = diff_profiles(before, after)
    assert not d.comparable and fragment in d.incomparable_reason and d.rows == []


def test_tool_mismatch_is_refused():
    """The CUPTI fallback flip means one side is device time and the other CPU time."""
    before = summary([("aten::cat", "", 0, 1.0, 1.0)], tool="torch.profiler", commit="b" * 40)
    after = summary([("aten::cat", "", 0, 0.2, 0.2)], tool="torch.profiler (cpu-time fallback)", commit="h" * 40)
    d = diff_profiles(before, after)
    assert not d.comparable and "different tools" in d.incomparable_reason
    assert "not comparable" in d.incomparable_reason


def test_identical_commit_means_nothing_accepted_yet():
    p = summary([("a", "m.py", 1, 1.0, 1.0)], commit="s" * 40)
    d = diff_profiles(p, p)
    assert not d.comparable and "nothing has been accepted" in d.incomparable_reason


def test_render_states_the_refusal_instead_of_a_table():
    assert "no bottleneck diff" in render(diff_profiles(None, None))


# --------------------------------------------------------------------------- #
# Totals, ordering, coherence
# --------------------------------------------------------------------------- #

def test_rows_plus_residual_reconcile_with_the_total():
    before = summary([("a", "m.py", 1, 1.0, 1.0), ("b", "m.py", 2, 0.5, 0.5)],
                     total=2.0, retained=2, n_total=9)
    after = summary([("a", "m.py", 1, 0.4, 0.4)], total=0.6, retained=1, n_total=9)
    d = diff_profiles(before, after)
    kept_before = sum(r.before_self for r in d.rows if r.before_self is not None)
    assert kept_before + d.before_residual == pytest.approx(d.before_total)
    kept_after = sum(r.after_self for r in d.rows if r.after_self is not None)
    assert kept_after + d.after_residual == pytest.approx(d.after_total)


def test_rows_are_ordered_by_what_hurt_most_at_baseline():
    before = summary([("small", "m.py", 1, 0.1, 0.1), ("big", "m.py", 2, 9.0, 9.0)])
    after = summary([("small", "m.py", 1, 0.1, 0.1), ("big", "m.py", 2, 1.0, 1.0)])
    assert [r.function for r in diff_profiles(before, after).rows] == ["big", "small"]


def test_coherence_warning_when_profile_and_benchmark_disagree():
    before = summary([("a", "m.py", 1, 10.0, 10.0)], total=10.0, commit="b" * 40)
    after = summary([("a", "m.py", 1, 1.0, 1.0)], total=1.0, commit="h" * 40)
    loud = diff_profiles(before, after, benchmark_speedup=1.05)
    assert loud.total_ratio == pytest.approx(10.0)
    assert "authoritative" in loud.coherence_warning
    assert "WARNING" in render(loud)
    quiet = diff_profiles(before, after, benchmark_speedup=9.0)
    assert quiet.coherence_warning == ""
    assert diff_profiles(before, after).coherence_warning == "", "no benchmark, no claim"


def test_epsilon_is_echoed_because_it_is_a_display_threshold_not_significance():
    before = summary([("a", "m.py", 1, 1.0, 1.0)])
    after = summary([("a", "m.py", 1, 1.02, 1.02)])
    d = diff_profiles(before, after)
    assert d.unchanged_epsilon == DEFAULT_EPSILON and d.rows[0].classification == "unchanged"
    strict = diff_profiles(before, after, epsilon=0.001)
    assert strict.rows[0].classification == "grew" and strict.unchanged_epsilon == 0.001


def test_zero_baseline_cost_does_not_divide_by_zero():
    before = summary([("a", "m.py", 1, 0.0, 0.0), ("b", "m.py", 2, 1.0, 1.0)], total=1.0)
    after = summary([("a", "m.py", 1, 0.5, 0.5), ("b", "m.py", 2, 1.0, 1.0)], total=1.5)
    row = next(r for r in diff_profiles(before, after).rows if r.function == "a")
    assert row.classification == "grew" and row.delta_ratio is None


def test_render_never_words_a_bound_as_a_measurement():
    # Row names deliberately avoid the words the labels use, so a substring cannot fake a pass.
    before = summary([("vanished", "m.py", 1, 5.0, 5.0), ("keep", "m.py", 2, 1.0, 1.0)])
    after = summary([("keep", "m.py", 2, 0.5, 0.5), ("appeared", "m.py", 3, 0.1, 0.1)])
    text = render(diff_profiles(before, after))
    assert "gone" in text, "an untruncated absence is a real elimination"
    assert "new" in text
    assert "below_cutoff_after" not in text, "raw enum names must not leak into the report"

    # Truncated: the same absence must become an explicit upper bound instead.
    trunc_after = summary([("keep", "m.py", 2, 0.5, 0.5), ("other", "m.py", 9, 0.25, 0.25)],
                          retained=2, n_total=40)
    bounded = render(diff_profiles(before, trunc_after))
    assert "<=0.2500s" in bounded and "gone" not in bounded
