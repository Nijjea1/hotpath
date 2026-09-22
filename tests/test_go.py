"""`hotpath go`: the guided flow end to end (offline: the mock provider replays patches; everything
else, from cloning to the pushed PR branch, is real)."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hotpath.go import STAGES, Go, GoOptions, parse_target, GoStop
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
                dashboard=False, workspaces=str(tmp_path / "ws"), iterations=2, candidates=3, test_runs=2)
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
    for stage in ("[1/9] Setup", "[4/9] Baseline", "[5/9] Benchmark", "[7/9] Optimize", "[8/9] Publish"):
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
    if "[8/9]" in out:
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
    assert "[5/9]" not in out


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


class FakeServer:
    """Stands in for the `hotpath serve` subprocess so the tests never bind a port."""

    def __init__(self, *args, **kwargs):
        self.argv = list(args[0]) if args else []
        self.terminated = False
        self.waited = False
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated, self._alive = True, False

    def wait(self, timeout=None):
        self.waited, self._alive = True, False
        return 0


@pytest.fixture
def fake_dashboard(monkeypatch):
    """Capture the dashboard subprocess and every browser tab the run opens.

    Only the `hotpath serve` spawn is faked: `subprocess.run` builds on Popen too, so replacing it
    wholesale would break every git call the run makes."""
    servers: list[FakeServer] = []
    opened: list[str] = []
    real_popen = subprocess.Popen

    def popen(args, *rest, **kwargs):
        argv = list(args) if isinstance(args, (list, tuple)) else [args]
        if "hotpath.cli" in argv and "serve" in argv:
            servers.append(FakeServer(argv))
            return servers[-1]
        return real_popen(args, *rest, **kwargs)

    monkeypatch.setattr("hotpath.go.subprocess.Popen", popen)
    monkeypatch.setattr("hotpath.go.webbrowser.open", lambda url, *a, **kw: opened.append(url))
    monkeypatch.setattr("hotpath.go.time.sleep", lambda _s: None)
    return servers, opened


def test_dashboard_is_served_and_opened_without_being_asked(tmp_path, fake_dashboard):
    servers, opened = fake_dashboard
    remote = make_remote(tmp_path)
    o = opts(tmp_path, remote.as_uri(), yes=True, no_pr=True, dashboard=True, open_browser=True,
             iterations=1, candidates=1)
    code, out = run_go(o, ask=lambda _q: "y")
    assert code == 0, out
    assert len(servers) == 1, "the search should serve the dashboard on its own"
    assert servers[0].argv[1:4] == ["-m", "hotpath.cli", "serve"]
    assert opened == [f"http://127.0.0.1:{o.port}"]
    assert f"dashboard: http://127.0.0.1:{o.port}  (still running)" in out


def test_dashboard_survives_the_run_when_a_terminal_can_stop_it(tmp_path, fake_dashboard):
    servers, _ = fake_dashboard
    remote = make_remote(tmp_path)
    code, out = run_go(opts(tmp_path, remote.as_uri(), yes=True, no_pr=True, dashboard=True,
                            iterations=1, candidates=1), ask=lambda _q: "y")
    assert code == 0, out
    assert servers[0].waited, "the run should hold the dashboard open, not kill it at exit"
    assert not servers[0].terminated
    assert "Ctrl-C to stop the dashboard." in out


def test_dashboard_does_not_block_without_a_terminal(tmp_path, fake_dashboard):
    servers, _ = fake_dashboard
    remote = make_remote(tmp_path)
    # ask=None and no tty: nobody could press Ctrl-C, so the run must exit and say how to reopen it.
    code, out = run_go(opts(tmp_path, remote.as_uri(), yes=True, no_pr=True, dashboard=True,
                            iterations=1, candidates=1), ask=None)
    assert code == 0, out
    assert not servers[0].waited and servers[0].terminated
    assert "reopen it later with: hotpath serve" in out


def test_no_dashboard_and_no_open_are_honoured(tmp_path, fake_dashboard):
    servers, opened = fake_dashboard
    remote = make_remote(tmp_path)
    code, out = run_go(opts(tmp_path, remote.as_uri(), yes=True, no_pr=True, dashboard=False,
                            iterations=1, candidates=1), ask=lambda _q: "y")
    assert code == 0, out
    assert servers == [] and opened == []

    remote2 = make_remote(tmp_path / "second")
    code, out = run_go(opts(tmp_path / "second", remote2.as_uri(), yes=True, no_pr=True, dashboard=True,
                            open_browser=False, iterations=1, candidates=1), ask=lambda _q: "y")
    assert code == 0, out
    assert len(servers) == 1 and opened == [], "--no-open serves the dashboard but opens no tab"


def test_dashboard_is_killed_when_a_stage_fails(tmp_path):
    g = Go(opts(tmp_path, str(tmp_path), dashboard=True), out=lambda _s: None, ask=lambda _q: "y")
    g.dashboard_proc = FakeServer()
    g._cleanup()
    assert g.dashboard_proc.terminated and not g.dashboard_proc.waited


def test_source_budget_fits_the_repository_largest_editable_file(tmp_path, monkeypatch):
    """The 14,000-character default is smaller than one ordinary module, and `read_target_file`
    refuses an oversize file before calling a model — so every candidate failed on a repository
    like `inflect`, whose package is a single 280,000-character file."""
    from hotpath.assess import assess

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("# " + "x" * 60_000 + "\n")
    (repo / "pkg" / "small.py").write_text("y = 1\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text("def test_a(): pass\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "initial")

    go = Go(opts(tmp_path, str(repo)), out=lambda _s: None, ask=None)
    go.repo, go.a = repo, assess(repo)
    budget = go._source_budget(14_000)
    assert budget > 60_000, "the budget should fit the largest editable file"
    assert budget <= Go.MAX_SOURCE_CHARS


def test_source_budget_reports_a_file_no_worker_can_be_shown(tmp_path):
    from hotpath.assess import assess

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("# " + "x" * (Go.MAX_SOURCE_CHARS + 5_000) + "\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text("def test_a(): pass\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "initial")

    said: list[str] = []
    go = Go(opts(tmp_path, str(repo)), out=said.append, ask=None)
    go.repo, go.a = repo, assess(repo)
    assert go._source_budget(14_000) == 14_000          # nothing editable fits, so leave it alone
    assert any("will not be offered to a worker" in line for line in said)


def test_stage_labels_match_the_stage_list():
    """The banner and the tests both hard-code stage numbers; adding a stage must update both."""
    from hotpath import go as go_module

    assert len(STAGES) == 9 and STAGES[-1] == "Verify"
    banner = go_module.__doc__ or ""
    for n, name in enumerate(STAGES, start=1):
        assert f"[{n}/{len(STAGES)}] {name}" in banner, f"stage {n} missing from the module banner"
