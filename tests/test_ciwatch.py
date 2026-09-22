"""Reading a pull request's CI, and deciding whose fault a red check is.

The rule that matters: Hotpath only ever tries to fix a check that *passes on the base commit and
fails on the head*. A repository whose CI is already red is reported, never touched, and never
claimed to have been fixed.
"""
import pytest

from hotpath.ciwatch import Check, Gh, Verdict, classify, inspect
from hotpath.github import RepoRef


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
    monkeypatch.setattr("hotpath.ciwatch.gh_binary", lambda: None)   # machine without the CLI
    gh = Gh(RepoRef("github.com", "o", "r"))
    assert not gh.available
    v = inspect(gh, "head", "base")
    assert not v.green and "gh" in v.unavailable


def test_inspect_reports_a_repository_without_ci(monkeypatch):
    gh = Gh(RepoRef("github.com", "o", "r"), binary="gh")
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
    # The real RepoRef, never a stand-in: a fake whose `slug` was a method instead of a property
    # let `self.repo.slug()` ship, and it crashed on the first live pull request.
    gh = Gh(RepoRef("github.com", "o", "r"), binary="gh")
    excerpt = gh.failing_log(check("build", "failure", url="https://x/job/123"))
    assert "ModuleNotFoundError" in excerpt and "ERROR collecting" in excerpt
    assert "2026-09-22T" not in excerpt, "timestamps should be stripped"
    assert "Downloading something irrelevant" not in excerpt


def test_queries_are_built_against_the_real_RepoRef(monkeypatch):
    """`slug` is a property on RepoRef. Calling it broke the first live run, and the unit tests
    missed it because they used a hand-made double whose `slug` was a method."""
    seen = []

    class Res:
        returncode, stdout = 0, '[]'
    monkeypatch.setattr("hotpath.ciwatch.subprocess.run",
                        lambda args, **k: (seen.append(args), Res())[1])
    gh = Gh(RepoRef("github.com", "octo", "repo"), binary="gh")
    gh.checks_for_ref("deadbeef")
    flat = " ".join(seen[0])
    assert "octo/repo" in flat and "slug" not in flat
    assert "repos/octo/repo/commits/deadbeef/check-runs" in flat


def test_an_unreadable_answer_is_never_reported_as_no_ci(monkeypatch):
    """`gh api --paginate --jq` emits one JSON value per page, so a single json.loads fails. That
    parse error was being reported as "no CI checks are configured" — a broken query presented as
    a clean bill of health, which is the one thing this project must not do."""
    class Res:
        returncode, stdout = 0, '{"name": "a"}\nnot json at all\n'
    monkeypatch.setattr("hotpath.ciwatch.subprocess.run", lambda *a, **k: Res())
    gh = Gh(RepoRef("github.com", "o", "r"), binary="gh")
    assert gh.checks_for_ref("sha") is None, "a garbled answer must not look like an empty one"

    monkeypatch.setattr(Gh, "checks_for_ref", lambda self, ref: None)
    v = inspect(gh, "head", "base")
    assert "could not be read" in v.unavailable and not v.green
    assert "no CI checks are configured" not in v.unavailable


def test_checks_are_parsed_from_one_object_per_line(monkeypatch):
    class Res:
        returncode = 0
        stdout = ('{"name": "test", "conclusion": "failure", "status": "completed"}\n'
                  '{"name": "docs", "conclusion": "success", "status": "completed"}\n')
    monkeypatch.setattr("hotpath.ciwatch.subprocess.run", lambda *a, **k: Res())
    gh = Gh(RepoRef("github.com", "o", "r"), binary="gh")
    checks = gh.checks_for_ref("sha")
    assert [c.name for c in checks] == ["test", "docs"]
    assert checks[0].failed and not checks[1].failed


def test_a_forks_empty_base_falls_back_to_the_upstream_repository(monkeypatch):
    """`go` tells you to fork when you lack push access, and a fork's default branch has no CI
    history — so without this every failure lands in 'unknown' and nothing is ever attributable."""
    fork = Gh(RepoRef("github.com", "me", "proj"), binary="gh")
    upstream = Gh(RepoRef("github.com", "them", "proj"), binary="gh")

    head = [check("test", "failure"), check("docs")]
    monkeypatch.setattr(Gh, "parent", lambda self: upstream if self is fork else None)
    monkeypatch.setattr("hotpath.ciwatch.wait_for_checks", lambda *a, **k: (head, False))
    monkeypatch.setattr(Gh, "checks_for_ref",
                        lambda self, ref: [] if self is fork else [check("test", "failure"), check("docs")])

    said = []
    v = inspect(fork, "head", "base", say=said.append)
    assert [c.name for c in v.pre_existing] == ["test"], "upstream also fails it: not ours"
    assert not v.introduced
    assert any("them/proj" in line for line in said), "the different source must be stated"


# --------------------------------------------------------------------------- #
# The repair loop in `hotpath go`'s ninth stage.
#
# These drive the loop's control flow with the model call, the harness verdict and the push
# stubbed out. What they pin is the part that must never go wrong: a fix is only kept when the
# harness accepts it, the loop stops rather than pushing something unverified, and CI is never
# declared green on anything but a fresh reading.

class _Exp:
    def __init__(self, status, speedup=None, reason=""):
        self.status = type("S", (), {"value": status})()
        self.reject_reason = reason
        self.comparison = type("C", (), {"speedup_vs_parent": speedup})() if speedup else None


def _go(monkeypatch, tmp_path, **attrs):
    from hotpath.go import Go, GoOptions
    said = []
    go = Go(GoOptions(target=str(tmp_path), ci_attempts=2), out=said.append, ask=None)
    go.state.pr_head_sha, go.state.base_commit, go.state.github = "head", "base", "o/r"
    go.said = said
    for k, v in attrs.items():
        setattr(go, k, v)
    return go


def test_a_fix_the_harness_rejects_is_never_pushed(monkeypatch, tmp_path):
    pushed = []
    go = _go(monkeypatch, tmp_path)
    monkeypatch.setattr(type(go), "_ci_experiment",
                        lambda self, failure: _Exp("rejected_correctness", reason="tests failed"))
    monkeypatch.setattr(type(go), "_republish", lambda self: pushed.append(1) or True)
    gh = Gh(RepoRef("github.com", "o", "r"), binary="gh")
    monkeypatch.setattr(Gh, "failing_log", lambda self, c, max_chars=4000: "E AssertionError: boom")

    assert go._fix_ci(gh, classify([check("test", "failure")], [check("test")]), 9) is False
    assert pushed == [], "a fix the harness rejected must never reach the remote"
    assert any("rejected by the harness" in line for line in go.said)


def test_a_verified_fix_is_pushed_and_ci_is_read_again(monkeypatch, tmp_path):
    go = _go(monkeypatch, tmp_path)
    monkeypatch.setattr(type(go), "_ci_experiment", lambda self, failure: _Exp("accepted", speedup=1.4))
    monkeypatch.setattr(type(go), "_republish", lambda self: True)
    monkeypatch.setattr("hotpath.ciwatch.inspect",
                        lambda *a, **k: classify([check("test")], [check("test")]))
    gh = Gh(RepoRef("github.com", "o", "r"), binary="gh")
    monkeypatch.setattr(Gh, "failing_log", lambda self, c, max_chars=4000: "E AssertionError: boom")

    assert go._fix_ci(gh, classify([check("test", "failure")], [check("test")]), 9) is True
    assert any("CI green after 1 fix attempt" in line for line in go.said)
    assert go.state.ci_state == "green"


def test_the_loop_gives_up_after_the_attempt_budget(monkeypatch, tmp_path):
    tries = []
    go = _go(monkeypatch, tmp_path)
    monkeypatch.setattr(type(go), "_ci_experiment",
                        lambda self, failure: tries.append(failure) or _Exp("accepted", speedup=1.4))
    monkeypatch.setattr(type(go), "_republish", lambda self: True)
    still_red = classify([check("test", "failure")], [check("test")])
    monkeypatch.setattr("hotpath.ciwatch.inspect", lambda *a, **k: still_red)
    gh = Gh(RepoRef("github.com", "o", "r"), binary="gh")
    monkeypatch.setattr(Gh, "failing_log", lambda self, c, max_chars=4000: "E AssertionError: boom")

    assert go._fix_ci(gh, still_red, 9) is False
    assert len(tries) == 2, "ci_attempts bounds the loop"
    assert "AssertionError" in tries[0], "the failing log is what gets fed back"


def test_unreadable_logs_stop_the_loop_instead_of_guessing(monkeypatch, tmp_path):
    called = []
    go = _go(monkeypatch, tmp_path)
    monkeypatch.setattr(type(go), "_ci_experiment", lambda self, failure: called.append(1))
    gh = Gh(RepoRef("github.com", "o", "r"), binary="gh")
    monkeypatch.setattr(Gh, "failing_log", lambda self, c, max_chars=4000: "")

    assert go._fix_ci(gh, classify([check("test", "failure")], [check("test")]), 9) is False
    assert called == [], "with no log to feed back, no model call should be made"
