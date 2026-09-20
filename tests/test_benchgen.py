"""Generated benchmarks must earn trust by running, not by a model's say-so."""
import os
import sys
from pathlib import Path

import pytest

from hotpath import benchgen
from hotpath.benchmark import parse_benchmark_output
from hotpath.benchwrap import main as benchwrap_main
from hotpath.sandbox import TargetEnv, make_runner

SLOW_LIB = (
    "def dedupe(items):\n"
    "    out = []\n"
    "    for x in items:\n"
    "        if x not in out:\n"
    "            out.append(x)\n"
    "    return out\n")
TESTS = "from lib import dedupe\n\ndef test_dedupe():\n    assert dedupe([3, 1, 3, 2, 1]) == [3, 1, 2]\n"
GOOD = ("import random\nfrom lib import dedupe\n\n_rng = random.Random(0)\nDATA = [_rng.randrange(2500) for _ in range(9000)]\n\n\n"
        "def workload():\n    return dedupe(DATA)\n")
TOO_FAST = "from lib import dedupe\n\n\ndef workload():\n    return dedupe([1, 2, 1])\n"
USES_TESTS = "from tests.test_lib import *\n\n\ndef workload():\n    return 1\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "lib.py").write_text(SLOW_LIB)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_lib.py").write_text(TESTS)
    return tmp_path


@pytest.fixture
def runner():
    env = {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""), "PYTHONHASHSEED": "0"}
    return make_runner(TargetEnv("venv", sys.executable, env))


def _write(repo: Path, code: str) -> None:
    for rel, text in {**benchgen.bench_files(code, trials=7), benchgen.DIGEST_FILE: benchgen._DIGEST_SCRIPT}.items():
        (repo / rel).write_text(text)


def test_forbidden_imports_and_missing_workload():
    assert benchgen.forbidden_imports(USES_TESTS) == ["tests.test_lib"]
    assert benchgen.forbidden_imports("import unittest.mock\ndef workload(): pass\n") == ["unittest.mock"]
    assert "no top-level `def workload()`" in benchgen.forbidden_imports("x = 1\n")
    assert benchgen.forbidden_imports(GOOD) == []


def test_robust_noise_ignores_one_outlier():
    assert benchgen.robust_noise([1.0, 1.01, 0.99, 1.0, 5.0]) < 0.02


def test_a_good_workload_validates(repo, runner):
    _write(repo, GOOD)
    v = benchgen.validate(repo, runner)
    assert v.ok, v.reason
    assert v.median_s >= benchgen.MIN_MEDIAN_S and v.own_share >= benchgen.MIN_OWN_SHARE
    assert any("same result" in d for d in v.details)


def test_a_too_fast_workload_is_rejected_with_a_scaling_hint(repo, runner):
    _write(repo, TOO_FAST)
    v = benchgen.validate(repo, runner, check_share=False, check_determinism=False)
    assert not v.ok and "larger" in v.reason


def test_a_nondeterministic_workload_is_rejected(repo, runner):
    code = GOOD.replace("random.Random(0)", "random.Random()")
    _write(repo, code)
    v = benchgen.validate(repo, runner, check_share=False)
    assert not v.ok and "different results" in v.reason


def test_generation_feeds_failures_back_until_one_validates(repo, runner):
    replies = iter([USES_TESTS, TOO_FAST, GOOD])
    seen_prompts = []

    def generator(messages):
        seen_prompts.append(messages[-1]["content"])
        return benchgen.WorkloadResponse(code=next(replies), description="dedupe 6000 ints", functions=["dedupe"])

    choice = benchgen.generate_benchmark(repo, runner, generator, attempts=3)
    assert choice is not None and choice.kind == "generated"
    assert choice.bench_cmd == "python hotpath_bench.py" and choice.profile_cmd == "python hotpath_profile.py"
    assert set(choice.files) == {"hotpath_workload.py", "hotpath_bench.py", "hotpath_profile.py"}
    assert len(choice.attempts) == 2
    # The profile of the test suite reached the model, and so did each rejection reason.
    assert "dedupe" in seen_prompts[0]
    assert "not allowed" in seen_prompts[1] and "larger" in seen_prompts[2]


def test_generation_gives_up_after_the_attempt_budget(repo, runner):
    def generator(messages):
        return benchgen.WorkloadResponse(code=USES_TESTS, description="x", functions=[])
    assert benchgen.generate_benchmark(repo, runner, generator, attempts=2) is None


def test_tests_fallback_times_the_suite():
    c = benchgen.tests_fallback("python -m pytest -q", "none found")
    assert c.kind == "tests" and "benchwrap" in c.bench_cmd and c.bench_cmd.endswith("-p no:cacheprovider")
    assert c.profile_cmd == "python -m hotpath.testprofile -q"


def test_benchwrap_times_a_command(capsys):
    assert benchwrap_main(["--trials", "3", "--warmup", "0", "--", sys.executable, "-c", "pass"]) == 0
    out = parse_benchmark_output(capsys.readouterr().out)
    assert len(out["samples"]) == 3 and out["metric"] == "seconds"


def test_benchwrap_refuses_to_time_a_failing_command():
    with pytest.raises(SystemExit, match="exited 3"):
        benchwrap_main(["--trials", "2", "--", sys.executable, "-c", "raise SystemExit(3)"])
