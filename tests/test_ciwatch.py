"""Reading a pull request's CI, and deciding whose fault a red check is.

The rule that matters: Hotpath only ever tries to fix a check that *passes on the base commit and
fails on the head*. A repository whose CI is already red is reported, never touched, and never
claimed to have been fixed.
"""
import pytest

from hotpath.ciwatch import Check, Gh, Verdict, classify, inspect


def check(name, conclusion="success", status="completed", url=""):
    return Check(name=name, conclusion=conclusion, status=status, url=url)


def test_only_checks_this_branch_broke_are_ours():
    head = [check("test", "failure"), check("lint", "failure"), check("docs")]
    base = [check("test"), check("lint", "failure"), check("docs")]
    v = classify(head, base)
    assert [c.name for c in v.introduced] == ["test"]        # green on base, red on head
    assert [c.name for c in v.pre_existing] == ["lint"]      # red on base too: not ours
    assert [c.name for c in v.passed] == ["docs"]
    assert not v.green


def test_a_check_with_no_base_result_is_unknown_not_ours():
    """A check that only runs on pull requests has nothing to compare against. Reporting it as
    'introduced' would send a worker chasing a failure the branch may not have caused."""
    v = classify([check("pr-only", "failure")], [check("test")])
    assert [c.name for c in v.unknown] == ["pr-only"]
    assert not v.introduced and not v.green


def test_cancelled_and_action_required_are_never_fixed():
    for conclusion in ("cancelled", "action_required", "stale"):
        v = classify([check("job", conclusion)], [check("job")])
        assert not v.introduced, f"{conclusion} must not be treated as ours to fix"
        assert [c.name for c in v.unknown] == ["job"]


@pytest.mark.parametrize("conclusion", ["success", "neutral", "skipped"])
def test_neutral_and_skipped_are_not_failures(conclusion):
    v = classify([check("job", conclusion)], [check("job")])
    assert v.green and [c.name for c in v.passed] == ["job"]


def test_unsettled_checks_are_not_counted_as_passed():
    v = classify([check("job", "", "in_progress")], [])
    assert not v.passed and not v.introduced and v.green  # nothing failed; nothing concluded either


def test_missing_base_results_entirely_means_unknown_not_blame():
    v = classify([check("test", "failure")], None)
    assert [c.name for c in v.unknown] == ["test"] and not v.introduced


def test_summary_names_each_bucket():
    v = classify([check("a", "failure"), check("b", "failure"), check("c")],
                 [check("a"), check("b", "failure"), check("c")])
    text = v.summary()
    assert "1 passed" in text and "1 introduced by this PR" in text
    assert "1 already failing on the base" in text


def test_inspect_says_so_when_gh_is_missing(monkeypatch):
    gh = Gh.__new__(Gh)
    gh.binary = None
    v = inspect(gh, "head", "base")
    assert not v.green and "gh" in v.unavailable


def test_inspect_reports_a_repository_without_ci(monkeypatch):
    gh = Gh.__new__(Gh)
    gh.binary = "gh"
    monkeypatch.setattr(Gh, "checks_for_ref", lambda self, ref: [])
    v = inspect(gh, "head", "base")
    assert "no CI checks are configured" in v.unavailable
    assert not v.green, "no CI is not the same as passing CI"


def test_a_timed_out_wait_is_not_green():
    v = Verdict(passed=[check("a")], timed_out=True)
    assert not v.green and "still running" in v.summary()


def test_log_excerpt_keeps_the_error_and_drops_the_noise(monkeypatch, tmp_path):
    """`gh` prefixes every line with job, step and timestamp; a model needs the exception, not that."""
    raw = "\n".join([
        "build\tstep\t2026-09-22T19:24:07.4798070Z Collecting package metadata",
        "build\tstep\t2026-09-22T19:24:07.4799142Z ERROR collecting hotpath_bench.py",
        "build\tstep\t2026-09-22T19:24:07.4805321Z E   ModuleNotFoundError: No module named 'hotpath'",
        "build\tstep\t2026-09-22T19:24:07.4810836Z Downloading something irrelevant",
    ])

    class Res:
        stdout = raw
    monkeypatch.setattr("hotpath.ciwatch.subprocess.run", lambda *a, **k: Res())
    gh = Gh.__new__(Gh)
    gh.binary, gh.timeout = "gh", 5
    gh.repo = type("R", (), {"slug": lambda self: "o/r"})()
    excerpt = gh.failing_log(check("build", "failure", url="https://x/job/123"))
    assert "ModuleNotFoundError" in excerpt and "ERROR collecting" in excerpt
    assert "2026-09-22T" not in excerpt, "timestamps should be stripped"
    assert "Downloading something irrelevant" not in excerpt
