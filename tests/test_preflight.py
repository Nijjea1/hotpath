"""`hotpath check`: the cheap gates, against the shapes that actually wasted live runs.

Each test here is a real repository shape that cost ten minutes and, in two cases, real API spend
before `go` gave up. The point of the command is that none of them should cost more than a second.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from hotpath.preflight import (Finding, declared_markers, estimate_cost, preflight,
                               property_tests_without_deadline)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=repo, capture_output=True, text=True, check=True)


def make(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    git(repo.parent, "init", "-q", str(repo))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "initial")
    return repo


BASE = {"pkg/__init__.py": "", "pkg/core.py": "def work(n):\n    return sum(range(n))\n",
        "tests/test_core.py": "from pkg.core import work\n\n\ndef test_work():\n    assert work(3) == 3\n"}


def codes(result) -> set[str]:
    return {f.code for f in result.findings}


# --------------------------------------------------------------------------- #
# Hypothesis deadlines: what made textdistance unusable, three runs in a row.

def test_property_tests_without_a_deadline_are_counted_per_test():
    """A file-level check read textdistance as safe because 2 of its 7 files set deadline=None.
    The other 5 were still exposed, and those are the ones that failed."""
    src = ("import hypothesis\nfrom hypothesis import given, strategies as st\n\n"
           "@given(st.text())\ndef test_exposed(s):\n    pass\n\n"
           "@hypothesis.settings(deadline=None)\n@given(st.text())\ndef test_guarded(s):\n    pass\n\n"
           "@given(st.text())\ndef test_also_exposed(s):\n    pass\n")
    assert property_tests_without_deadline(src) == 2


def test_a_plain_test_is_not_a_property_test():
    assert property_tests_without_deadline("def test_plain():\n    assert True\n") == 0


def test_unparseable_source_is_not_reported_as_safe():
    assert property_tests_without_deadline("this is not python(") == 1


def test_hypothesis_without_deadlines_is_a_caution(tmp_path):
    repo = make(tmp_path, {**BASE, "tests/test_props.py":
                "from hypothesis import given, strategies as st\n\n"
                "@given(st.text())\ndef test_p(s):\n    pass\n"})
    result = preflight(repo)
    assert "timing-sensitive-tests" in codes(result)
    assert result.verdict == "caution"
    finding = next(f for f in result.findings if f.code == "timing-sensitive-tests")
    assert "deadline" in finding.fix


def test_a_conftest_profile_covers_the_whole_suite(tmp_path):
    repo = make(tmp_path, {**BASE,
                           "conftest.py": "from hypothesis import settings\n"
                                          "settings.register_profile('ci', deadline=None)\n"
                                          "settings.load_profile('ci')\n",
                           "tests/test_props.py": "from hypothesis import given, strategies as st\n\n"
                                                  "@given(st.text())\ndef test_p(s):\n    pass\n"})
    result = preflight(repo)
    assert "hypothesis-deadlines-disabled" in codes(result)
    assert result.verdict == "go"


# --------------------------------------------------------------------------- #
# Optional-dependency tests: the second textdistance failure, and the flag that fixes it.

def test_an_optional_marker_is_flagged_with_the_command_that_avoids_it(tmp_path):
    repo = make(tmp_path, {**BASE,
                           "setup.cfg": "[tool:pytest]\nmarkers =\n"
                                        "    external: tests that require external libs to run\n",
                           "tests/test_ext.py": "import pytest\n\n"
                                                "@pytest.mark.external\ndef test_needs_lib():\n    pass\n"})
    result = preflight(repo)
    assert "optional-dependency-tests" in codes(result)
    assert result.suggested_test_cmd == 'python -m pytest -q -m "not external"'
    finding = next(f for f in result.findings if f.code == "optional-dependency-tests")
    assert "external libs" in finding.message, "the repository's own description should be quoted"


def test_ordinary_markers_are_not_mistaken_for_optional_ones(tmp_path):
    repo = make(tmp_path, {**BASE, "tests/test_p.py":
                "import pytest\n\n@pytest.mark.parametrize('n', [1, 2])\ndef test_n(n):\n    pass\n"})
    result = preflight(repo)
    assert "optional-dependency-tests" not in codes(result)
    assert result.suggested_test_cmd is None


def test_markers_are_read_from_pyproject_too(tmp_path):
    repo = make(tmp_path, {**BASE,
                           "pyproject.toml": '[tool.pytest.ini_options]\nmarkers = ["network: hits the internet"]\n'})
    assert declared_markers(repo).get("network") == "hits the internet"


# --------------------------------------------------------------------------- #
# Module size: why inflect produced twelve patch_failed before a model was ever called.

def test_a_module_past_the_ceiling_is_a_blocker(tmp_path):
    repo = make(tmp_path, {**BASE, "pkg/huge.py": "# " + "x" * 300_000 + "\n"})
    result = preflight(repo, source_ceiling=240_000)
    assert "module-too-large" in codes(result)
    assert result.verdict in ("caution", "stop")
    finding = next(f for f in result.findings if f.code == "module-too-large")
    assert "editable" in finding.fix


def test_every_editable_file_too_large_stops_the_run(tmp_path):
    repo = make(tmp_path, {"pkg/__init__.py": "# " + "x" * 300_000 + "\n",
                           "tests/test_a.py": "def test_a():\n    pass\n"})
    result = preflight(repo, source_ceiling=240_000)
    assert result.verdict == "stop", "nothing editable can be shown to a worker"


def test_a_merely_large_module_is_only_a_note(tmp_path):
    repo = make(tmp_path, {**BASE, "pkg/big.py": "# " + "x" * 50_000 + "\n"})
    result = preflight(repo)
    assert "large-module" in codes(result) and result.verdict == "go"


# --------------------------------------------------------------------------- #
# Everything else

def test_no_test_suite_stops_before_anything_else_is_reported(tmp_path):
    repo = make(tmp_path, {"pkg/__init__.py": "", "pkg/core.py": "x = 1\n"})
    result = preflight(repo)
    assert result.verdict == "stop"
    assert result.estimate.tokens == 0, "a run that cannot happen should not be priced"


def test_randomised_test_order_is_flagged(tmp_path):
    repo = make(tmp_path, {**BASE, "pyproject.toml": '[project]\nname="p"\ndependencies=["pytest-randomly"]\n'})
    result = preflight(repo)
    assert "randomised-test-order" in codes(result)
    assert "no:randomly" in next(f for f in result.findings if f.code == "randomised-test-order").fix


def test_compiled_sources_warn_that_the_hot_path_may_not_be_python(tmp_path):
    repo = make(tmp_path, {**BASE, "pkg/_speedup.c": "int main(void) { return 0; }\n"})
    result = preflight(repo)
    assert "hot-code-may-be-compiled" in codes(result)


def test_an_existing_benchmark_is_preferred_and_said_so(tmp_path):
    # `assess` only treats a bench script as Hotpath's own when it emits Hotpath JSON.
    bench = 'import json\nprint(json.dumps({"hotpath_benchmark": 1, "samples": [0.1]}))\n'
    repo = make(tmp_path, {**BASE, "hotpath_bench.py": bench})
    result = preflight(repo)
    assert "benchmark-exists" in codes(result)
    assert result.estimate.benchgen_calls == 0, "nothing needs generating"


def test_a_plain_benchmark_script_is_reported_as_coarse(tmp_path):
    """A bench.py that does not print Hotpath JSON has its whole runtime timed, which dilutes any
    speedup with import and setup time. Worth knowing before the threshold surprises you."""
    repo = make(tmp_path, {**BASE, "bench.py": "print('timing something')\n"})
    result = preflight(repo)
    assert "benchmark-wrapped" in codes(result)


def test_the_estimate_scales_with_the_biggest_editable_file(tmp_path):
    small = make(tmp_path / "a", BASE)
    big = make(tmp_path / "b", {**BASE, "pkg/big.py": "# " + "x" * 200_000 + "\n"})
    from hotpath.assess import assess
    cheap = estimate_cost(small, assess(small))
    dear = estimate_cost(big, assess(big))
    assert dear.tokens > cheap.tokens * 5, "a worker is shown a whole file, so size dominates the bill"
    assert dear.usd > 0


def test_verdict_and_exit_code_agree(tmp_path, capsys):
    from hotpath.cli import main
    repo = make(tmp_path, BASE)
    assert main(["check", str(repo)]) == 0
    assert "GO" in capsys.readouterr().out

    blocked = make(tmp_path / "x", {"pkg/__init__.py": "", "pkg/a.py": "x = 1\n"})
    assert main(["check", str(blocked)]) == 2
    assert "STOP" in capsys.readouterr().out


def test_json_output_is_machine_readable(tmp_path, capsys):
    import json
    from hotpath.cli import main
    repo = make(tmp_path, BASE)
    main(["check", str(repo), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "go"
    assert "estimate" in data and "assessment" in data
    assert all({"level", "code", "message"} <= set(f) for f in data["findings"])
