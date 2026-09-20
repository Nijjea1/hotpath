"""`hotpath go`: the guided flow end to end (offline: the mock provider replays patches; everything
else, from cloning to the pushed PR branch, is real)."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hotpath.go import Go, GoOptions, parse_target, GoStop
from hotpath.pr import SETUP_TRAILER

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "examples" / "slow_textstats"
PATCHES = ROOT / "examples" / "slow_textstats_mock_patches"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false", *args],
                          cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def make_remote(tmp_path: Path, files_from: Path = FIXTURE, edit=None) -> Path:
    src = tmp_path / "src"
    shutil.copytree(files_from, src, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".hotpath"))
    if edit:
        edit(src)
    git(src, "init", "-q", "-b", "main")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "initial")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(src, "remote", "add", "origin", str(remote))
    git(src, "push", "-q", "origin", "main")
    return remote


def opts(tmp_path: Path, target: str, **kw) -> GoOptions:
    base = dict(target=target, provider="mock", mock_patches=str(PATCHES), sandbox="local", open_browser=False,
                workspaces=str(tmp_path / "ws"), iterations=2, candidates=3, test_runs=2)
    base.update(kw)
    return GoOptions(**base)


def run_go(o: GoOptions, ask=None) -> tuple[int, str]:
    lines: list[str] = []
    code = Go(o, out=lines.append, ask=ask).run()
    return code, "\n".join(lines)


def test_parse_target():
    assert parse_target("octo/repo") == ("github", "https://github.com/octo/repo.git", "octo/repo")
    assert parse_target("https://github.com/octo/repo.git")[2] == "octo/repo"
    assert parse_target("git@github.com:octo/repo.git")[2] == "octo/repo"
    assert parse_target("file:///tmp/x.git")[0] == "git"
    with pytest.raises(GoStop):
        parse_target("not a target")


def test_go_end_to_end_pushes_a_verified_branch_with_the_setup_commit_first(tmp_path):
    remote = make_remote(tmp_path)
    code, out = run_go(opts(tmp_path, remote.as_uri(), yes=True))
    assert code == 0, out
    for stage in ("[1/8] Setup", "[4/8] Baseline", "[5/8] Benchmark", "[7/8] Optimize", "[8/8] Publish"):
        assert stage in out
    assert "not flaky" in out and "pushed to origin" in out
    assert "existing benchmark" in out and "python bench.py" in out
    assert re.search(r"\d+ candidates . [1-9]\d* accepted", out), out

    branches = git(remote, "branch", "--list", "hotpath/*")
    assert branches, out
    branch = branches.split()[-1]
    subjects = git(remote, "log", "--format=%s", f"main..{branch}").splitlines()
    assert subjects[-1].startswith("hotpath: set up")               # oldest first commit = setup
    assert all(s.startswith("perf:") for s in subjects[:-1]) and len(subjects) >= 2
    setup = git(remote, "rev-list", "--reverse", f"main..{branch}").splitlines()[0]
    assert SETUP_TRAILER in git(remote, "log", "-1", "--format=%B", setup).splitlines()
    setup_files = git(remote, "diff-tree", "--no-commit-id", "--name-only", "-r", setup).splitlines()
    assert ".hotpath.yaml" in setup_files and ".github/workflows/hotpath-verify.yml" in setup_files
    # Everything after the setup commit touches editable code only.
    changed = git(remote, "diff", "--name-only", setup, branch).splitlines()
    assert changed == ["textstats/core.py"]
    # main itself was never touched.
    assert git(remote, "rev-list", "--count", "main") == "1"


def test_go_never_pushes_without_confirmation(tmp_path):
    remote = make_remote(tmp_path)
    asked = []

    def ask(question):
        asked.append(question)
        return "n" if "Push branch" in question else "y"
    code, out = run_go(opts(tmp_path, remote.as_uri(), yes=False), ask=ask)
    assert code == 0, out
    assert any("benchmark" in q for q in asked)            # the benchmark is always shown for approval
    # Whether a win is found depends on the machine's noise; what must always hold is that a push only
    # ever happens after an explicit yes, so nothing reached the remote here.
    assert git(remote, "branch", "--list", "hotpath/*") == ""
    if "[8/8]" in out:
        assert any("Push branch" in q for q in asked) and "built locally" in out


def test_go_without_a_terminal_or_yes_asks_nothing_and_changes_nothing(tmp_path):
    remote = make_remote(tmp_path)
    code, out = run_go(opts(tmp_path, remote.as_uri(), yes=False), ask=None)
    assert code == 1 and "benchmark not approved" in out
    assert git(remote, "branch", "--list", "hotpath/*") == ""


def test_go_falls_back_to_timing_the_test_suite(tmp_path):
    def drop_benchmark(src: Path):
        (src / "bench.py").unlink()
        (src / "hotprofile.py").unlink()
    remote = make_remote(tmp_path, edit=drop_benchmark)
    # --provider mock cannot generate a benchmark, so the test suite's own runtime is the fallback.
    code, out = run_go(opts(tmp_path, remote.as_uri(), yes=True, iterations=1))
    assert code == 0, out
    assert "no benchmark found" in out and "test suite's runtime" in out and "benchwrap" in out
    assert "coarse" in out


def test_go_stops_on_failing_baseline_tests(tmp_path):
    def break_it(src: Path):
        p = src / "textstats" / "core.py"
        p.write_text(p.read_text().replace("return seen", "return seen[::-1]"))
    remote = make_remote(tmp_path, edit=break_it)
    code, out = run_go(opts(tmp_path, remote.as_uri(), yes=True))
    assert code == 1 and "tests fail on the untouched code" in out
    assert "[5/8]" not in out


def test_go_detects_flaky_tests(tmp_path):
    counter = tmp_path / "count.txt"

    def flaky(src: Path):
        (src / "tests" / "test_flaky.py").write_text(
            "import os\nfrom pathlib import Path\n\n"
            "def test_sometimes():\n"
            f"    p = Path({str(counter)!r})\n"
            "    n = int(p.read_text()) if p.exists() else 0\n"
            "    p.write_text(str(n + 1))\n"
            "    assert n % 2 == 0\n")
    remote = make_remote(tmp_path, edit=flaky)
    code, out = run_go(opts(tmp_path, remote.as_uri(), yes=True))
    assert code == 1 and "flaky" in out


def test_go_refuses_local_execution_without_consent(tmp_path):
    remote = make_remote(tmp_path)
    o = opts(tmp_path, remote.as_uri(), sandbox="auto")
    from hotpath import sandbox
    orig = sandbox.docker_available
    sandbox.docker_available = lambda timeout=15: False
    try:
        code, out = run_go(o, ask=None)
    finally:
        sandbox.docker_available = orig
    assert code == 1 and "no consent to run locally" in out


def test_go_on_a_local_checkout_restores_the_users_branch(tmp_path):
    remote = make_remote(tmp_path)
    src = tmp_path / "src"
    code, out = run_go(opts(tmp_path, str(src), yes=True, no_pr=True))
    assert code == 0, out
    assert git(src, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(src, "status", "--porcelain") == ""
    assert git(src, "branch", "--list", "hotpath/*")          # built locally, not pushed
    assert git(remote, "branch", "--list", "hotpath/*") == ""


def test_go_refuses_a_dirty_local_checkout(tmp_path):
    make_remote(tmp_path)
    src = tmp_path / "src"
    (src / "scratch.txt").write_text("wip")
    code, out = run_go(opts(tmp_path, str(src), yes=True))
    assert code == 1 and "uncommitted or untracked" in out
