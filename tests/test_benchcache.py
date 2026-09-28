"""Remembering which benchmark a repository was measured against.

The bug this exists for: three `hotpath go` runs against the same commit of the same repository
reported 1.47x, nothing, and 1.45x, because each asked a model for a fresh workload and got a
different one. The numbers were never comparable and nothing said so.

Two properties matter here, and they pull in opposite directions:

- reuse, so two runs measure the same thing
- **never** reuse something that no longer validates, because the bar a workload has to clear is the
  whole point and caching must not lower it
"""
import json

import pytest

from hotpath.benchcache import CACHE_FORMAT, BenchmarkCache, slug
from hotpath.benchgen import BenchChoice, bench_files, workload_digest

WORKLOAD = "def workload():\n    return sum(i * i for i in range(1000))\n"


@pytest.fixture
def cache(tmp_path):
    return BenchmarkCache(tmp_path / "cache")


def test_a_miss_is_none_not_an_error(cache):
    assert cache.load("never/seen") is None


def test_a_saved_workload_comes_back_intact(cache):
    cache.save("o/r", WORKLOAD, "sums squares", "abc123", base_commit="deadbeef",
               median_s=0.25, noise=0.04, details=["noise 4.0%"])
    got = cache.load("o/r")
    assert got.code == WORKLOAD and got.description == "sums squares"
    assert got.digest == "abc123" and got.base_commit == "deadbeef"
    assert got.median_s == 0.25 and got.noise == 0.04


def test_the_summary_says_what_it_measured(cache):
    entry = cache.save("o/r", WORKLOAD, "d", "abc123", median_s=0.25, noise=0.04)
    text = entry.summary()
    assert "abc123" in text and "250.0 ms" in text and "4.0%" in text


def test_a_format_bump_invalidates_the_entry(cache):
    """A cached workload only means something together with the bench and profile templates that wrap
    and time it. If those change, the remembered numbers describe a different measurement."""
    cache.save("o/r", WORKLOAD, "d", "abc123")
    path = cache.path_for("o/r")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["format"] = CACHE_FORMAT + 1
    path.write_text(json.dumps(data), encoding="utf-8")
    assert cache.load("o/r") is None


def test_corrupt_or_truncated_json_is_a_miss(cache):
    cache.save("o/r", WORKLOAD, "d", "abc123")
    cache.path_for("o/r").write_text("{not json", encoding="utf-8")
    assert cache.load("o/r") is None


def test_an_entry_without_a_workload_is_a_miss(cache):
    cache.root.mkdir(parents=True, exist_ok=True)
    cache.path_for("o/r").write_text(json.dumps({"format": CACHE_FORMAT, "code": "x = 1"}), encoding="utf-8")
    assert cache.load("o/r") is None, "whatever that is, it has no workload() to call"


def test_an_unwritable_cache_is_not_an_error(tmp_path):
    """Losing the cache costs a regeneration. It must never cost the run."""
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    entry = BenchmarkCache(blocker / "sub").save("o/r", WORKLOAD, "d", "abc123")
    assert entry.digest == "abc123"


def test_forget_removes_the_entry(cache):
    cache.save("o/r", WORKLOAD, "d", "abc123")
    assert cache.forget("o/r") and cache.load("o/r") is None
    assert cache.forget("o/r") is False


@pytest.mark.parametrize("target, expected", [
    ("https://github.com/Nijjea1/inflect.git", "github.com-Nijjea1-inflect"),
    ("git@github.com:o/r.git", "github.com-o-r"),
    ("Nijjea1/inflect", "Nijjea1-inflect"),
])
def test_a_url_and_its_shorthand_map_to_the_same_slug(target, expected):
    assert slug(target) == expected


def test_slugs_stay_filesystem_safe():
    assert "/" not in slug("a/b/c") and "\\" not in slug(r"C:\x\y")
    assert slug("") == "target"
    assert len(slug("x" * 500)) <= 120


# --------------------------------------------------------------------------- #
# The digest, which is what makes two runs comparable or visibly not.

def test_the_digest_ignores_trailing_whitespace_but_not_content():
    assert workload_digest("def workload():\n    pass\n") == workload_digest("def workload():  \n    pass")
    assert workload_digest("def workload():\n    pass\n") != workload_digest("def workload():\n    return 1\n")


def test_two_different_workloads_get_different_ids():
    a = BenchChoice("generated", "python hotpath_bench.py", None, bench_files(WORKLOAD, 3))
    b = BenchChoice("generated", "python hotpath_bench.py", None,
                    bench_files("def workload():\n    return 0\n", 3))
    assert a.digest != b.digest, "the whole point is being able to tell these apart"


def test_the_same_workload_gets_the_same_id_every_time():
    ids = {BenchChoice("generated", "c", None, bench_files(WORKLOAD, 3)).digest for _ in range(3)}
    assert len(ids) == 1


def test_a_reused_choice_says_it_was_reused():
    fresh = BenchChoice("generated", "c", None, bench_files(WORKLOAD, 3))
    reused = BenchChoice("generated", "c", None, bench_files(WORKLOAD, 3), reused=True)
    assert not fresh.reused and reused.reused
    assert fresh.digest == reused.digest, "same workload, same id, regardless of where it came from"
