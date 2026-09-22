"""`hotpath init`: set a repository up for Hotpath in one step.

Writes three things at the repository root, and never overwrites without --force:

- `.hotpath.yaml`: how to check correctness, how to time it, and what the agent may edit.
- `.github/workflows/hotpath-verify.yml`: on every PR, re-runs the locked correctness check on
  GitHub's machines, and on `hotpath/*` branches fails if any locked or non-editable path changed.
- a `.gitignore` entry for `.hotpath/` (worktrees and the run database).

If the repository has no benchmark yet, a `hotpath_bench.py` scaffold is written for the user to fill.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from hotpath.config import CONFIG_NAMES, load_config

WORKFLOW_PATH = Path(".github") / "workflows" / "hotpath-verify.yml"
BENCH_SCAFFOLD = "hotpath_bench.py"
BASETEN_URL = "https://inference.baseten.co/v1"
BASETEN_WORKER_MODEL = "moonshotai/Kimi-K2.7-Code"
PLANNER_MODEL = "gpt-4.1"
OPENAI_WORKER_MODEL = "gpt-4.1-mini"
#: Always locked, whatever the user answers: the files that define "correct" and "faster", the CI
#: that re-checks them, and Hotpath's own settings.
ALWAYS_LOCKED = ["tests/*", "test/*", "test_*.py", "*_test.py", "conftest.py", "setup.py", "noxfile.py",
                 ".github/*", ".hotpath.yaml"]
#: What a `hotpath go` setup commit may add or change: the config, this CI check, the benchmark files,
#: and the .gitignore entry for `.hotpath/`.
SETUP_FILES = [".hotpath.yaml", ".github/workflows/hotpath-verify.yml", ".gitignore", "hotpath_workload.py",
               "hotpath_bench.py", "hotpath_profile.py"]


class InitError(RuntimeError):
    pass


@dataclass
class InitAnswers:
    name: str
    test_cmd: str
    bench_cmd: str
    profile_cmd: Optional[str]
    editable: list[str]
    locked: list[str]
    execution: str                      # "local" | "docker"
    planner: str                        # "openai" | "mock"
    worker: str                         # "openai" | "baseten" | "mock"
    mock_patches_dir: Optional[str] = None
    iterations: int = 3
    candidates: int = 3


@dataclass
class InitResult:
    config_path: Path
    written: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #

def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def detect_test_cmd(repo: Path) -> Optional[str]:
    pyproject = _read(repo / "pyproject.toml")
    has_pytest_tests = any(repo.glob("tests/test_*.py")) or any(repo.glob("test_*.py")) or any(repo.glob("tests/*_test.py"))
    if (repo / "pytest.ini").exists() or "[tool.pytest" in pyproject or (repo / "conftest.py").exists() or has_pytest_tests:
        return "python -m pytest -q"
    for candidate in ("tests/check.py", "tests/run_tests.py", "run_tests.py"):
        if (repo / candidate).is_file():
            return f"python {candidate}"
    return None


def detect_bench_cmd(repo: Path) -> Optional[str]:
    for candidate in ("bench.py", "benchmark.py", BENCH_SCAFFOLD, "benchmarks/bench.py", "benchmarks/benchmark.py"):
        if (repo / candidate).is_file():
            return f"python {candidate}"
    return None


def detect_profile_cmd(repo: Path) -> Optional[str]:
    for candidate in ("hotprofile.py", "profile_target.py"):
        if (repo / candidate).is_file():
            return f"python {candidate}"
    return None


def detect_editable(repo: Path) -> list[str]:
    return ["src/*.py"] if (repo / "src").is_dir() else ["*.py"]


def _script_of(cmd: Optional[str]) -> Optional[str]:
    """`python bench.py --x` -> `bench.py`: the file a command runs, so it can be locked."""
    if not cmd:
        return None
    parts = cmd.split()
    if len(parts) >= 2 and parts[0] in ("python", "python3", "py") and parts[1].endswith(".py"):
        return parts[1].replace("\\", "/")
    return None


def default_locked(test_cmd: str, bench_cmd: str, profile_cmd: Optional[str]) -> list[str]:
    locked = list(ALWAYS_LOCKED)
    for cmd in (test_cmd, bench_cmd, profile_cmd):
        script = _script_of(cmd)
        if script and script not in locked:
            locked.append(script)
    return locked


def docker_image_ready(image: str = "hotpath-runner:local") -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def default_models() -> tuple[str, str]:
    """(planner, worker): OpenAI plans; Baseten explores when its key is present."""
    return "openai", ("baseten" if os.environ.get("BASETEN_API_KEY") else "openai")


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _q(value) -> str:
    """A YAML scalar or flow list. JSON is valid YAML and quotes anything a shell command may contain."""
    return json.dumps(value)


def render_config(a: InitAnswers) -> str:
    lines = [
        "# Hotpath settings for this repository (written by `hotpath init`).",
        "# Hotpath proposes optimizations with AI, then keeps a change only if `test_cmd` still passes",
        "# and `bench_cmd` shows it is faster than the measured noise. `locked` files can never be edited.",
        f"name: {_q(a.name)}",
        "target: .",
        f"test_cmd: {_q(a.test_cmd)}",
        f"bench_cmd: {_q(a.bench_cmd)}",
    ]
    if a.profile_cmd:
        lines.append(f"profile_cmd: {_q(a.profile_cmd)}")
    lines += [
        f"editable: {_q(a.editable)}",
        f"locked: {_q(a.locked)}",
        f"search: {{iterations: {a.iterations}, candidates_per_iteration: {a.candidates}}}",
        "provider:",
        f"  planner: {'mock' if a.planner == 'mock' else 'openai'}",
        f"  planner_model: {PLANNER_MODEL}",
        "  planner_api_key_env: OPENAI_API_KEY",
    ]
    if a.worker == "baseten":
        lines += ["  worker: openai                  # OpenAI-compatible client pointed at Baseten",
                  f"  worker_model: {BASETEN_WORKER_MODEL}",
                  f"  worker_base_url: {BASETEN_URL}",
                  "  worker_api_key_env: BASETEN_API_KEY"]
    else:
        lines += [f"  worker: {'mock' if a.worker == 'mock' else 'openai'}",
                  f"  worker_model: {OPENAI_WORKER_MODEL}",
                  "  worker_api_key_env: OPENAI_API_KEY"]
    if a.mock_patches_dir:
        lines.append(f"  mock_patches_dir: {_q(a.mock_patches_dir)}   # used by `--provider mock` (offline replay)")
    if a.execution == "docker":
        lines += ["# Model-written code runs in the isolated runner image (see docs/ISOLATION.md).",
                  "execution: {backend: docker}"]
    else:
        lines += ["# Model-written code runs directly on this machine, like your own test runs do.",
                  "# Switch to {backend: docker} once the hotpath-runner image is built (docs/ISOLATION.md).",
                  "execution: {backend: local}"]
    return "\n".join(lines) + "\n"


_SCOPE_SCRIPT = '''\
import fnmatch, os, subprocess, sys
from pathlib import PurePosixPath
EDITABLE = {editable}
LOCKED = {locked}
SETUP_FILES = {setup_files}
def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout
base = "origin/" + os.environ["BASE_REF"]
# Each commit is checked on its own. The one exception is a first commit marked as Hotpath's setup
# (it adds this check, the config, and the benchmark); it may touch those files and nothing else.
commits = git("rev-list", "--reverse", "--no-merges", base + "..HEAD").split()
bad = []
for i, sha in enumerate(commits):
    files = [p for p in git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines() if p]
    setup = i == 0 and "Hotpath-Setup: 1" in git("log", "-1", "--format=%B", sha).splitlines()
    for path in files:
        name = PurePosixPath(path).name
        if setup and path in SETUP_FILES:
            continue
        if any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p) for p in LOCKED):
            bad.append(path + " is locked (tests, benchmark, CI, or Hotpath settings)")
        elif not any(fnmatch.fnmatch(path, p) for p in EDITABLE):
            bad.append(path + " is outside the editable patterns")
if bad:
    print("Hotpath may only change editable, unlocked files:")
    print("\\n".join("  " + b for b in bad))
    sys.exit(1)
print("ok: every changed path is editable and unlocked")
'''


def render_workflow(a: InitAnswers) -> str:
    script = _SCOPE_SCRIPT.format(editable=json.dumps(a.editable), locked=json.dumps(a.locked),
                                  setup_files=json.dumps(SETUP_FILES))
    script = "\n".join(("          " + line) if line else "" for line in script.splitlines())
    pytest_install = "\n          pip install pytest" if "pytest" in a.test_cmd else ""
    return f"""# Written by `hotpath init`. Re-verifies Hotpath pull requests on GitHub's machines,
# so a reviewer does not have to take Hotpath's report on trust.
name: hotpath-verify
on:
  pull_request:
permissions:
  contents: read
jobs:
  correctness:
    name: Locked correctness check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
          if [ -f pyproject.toml ] || [ -f setup.py ]; then pip install -e . || echo "::warning::could not install this project as a package"; fi{pytest_install}
      - name: {_q("Run " + a.test_cmd)}
        run: {_q(a.test_cmd)}
  scope:
    name: Only editable files changed
    if: startsWith(github.head_ref, 'hotpath/')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Compare changed paths with the rules in .hotpath.yaml
        env:
          BASE_REF: ${{{{ github.base_ref }}}}
        run: |
          python - <<'EOF'
{script}
          EOF
"""


BENCH_TEMPLATE = '''\
"""Benchmark for Hotpath. Locked: Hotpath runs this file but can never edit it.

Replace `workload` with the slow thing you want faster. `run` warms up, times many trials,
and prints the JSON Hotpath reads.
"""
from hotpath.benchlib import run


def workload():
    raise NotImplementedError("edit hotpath_bench.py: call the code you want Hotpath to speed up")


run(workload, warmup=2, trials=15)
'''


# --------------------------------------------------------------------------- #
# The command
# --------------------------------------------------------------------------- #

def gather_answers(repo: Path, *, ask: Optional[Callable[[str, str], str]] = None, **given) -> tuple[InitAnswers, list[str]]:
    """Fill every answer from `given`, then detection, then (if `ask` is set) the user. Returns the
    answers and notes about anything the user still has to do."""
    notes: list[str] = []

    def pick(key: str, prompt: str, default: Optional[str]) -> Optional[str]:
        if given.get(key) is not None:
            return given[key]
        if ask is None:
            return default
        answer = ask(prompt, default or "").strip()
        return answer or default

    test_cmd = pick("test_cmd", "Command that checks correctness (exit 0 = correct)", detect_test_cmd(repo))
    if not test_cmd:
        raise InitError("no correctness check found; pass --test-cmd (for example \"python -m pytest -q\")")
    bench_default = detect_bench_cmd(repo)
    bench_cmd = pick("bench_cmd", "Command that times the code (prints Hotpath benchmark JSON)",
                     bench_default or f"python {BENCH_SCAFFOLD}")
    profile_cmd = pick("profile_cmd", "Command that profiles it (optional, blank to skip)", detect_profile_cmd(repo))
    editable = given.get("editable") or _split(pick("editable_text", "Files Hotpath may edit (comma-separated globs)",
                                                    ", ".join(detect_editable(repo))))
    locked = default_locked(test_cmd, bench_cmd, profile_cmd or None)
    for extra in given.get("locked") or []:
        if extra not in locked:
            locked.append(extra)
    execution = given.get("execution") or ("docker" if docker_image_ready() else "local")
    if execution == "local":
        notes.append("model-written code will run directly on this machine (execution: local)")
    planner, worker = default_models()
    planner = given.get("planner") or planner
    worker = given.get("worker") or worker
    if planner != "mock" and not os.environ.get("OPENAI_API_KEY"):
        notes.append("set OPENAI_API_KEY (in your environment or ~/.hotpath/.env) before `hotpath run`")
    if worker == "baseten" and not os.environ.get("BASETEN_API_KEY"):
        notes.append("set BASETEN_API_KEY (in your environment or ~/.hotpath/.env) for the Baseten worker")
    return InitAnswers(name=given.get("name") or repo.resolve().name, test_cmd=test_cmd, bench_cmd=bench_cmd,
                       profile_cmd=profile_cmd or None, editable=editable, locked=locked, execution=execution,
                       planner=planner, worker=worker, mock_patches_dir=given.get("mock_patches_dir")), notes


def _split(text: Optional[str]) -> list[str]:
    return [x.strip() for x in (text or "").split(",") if x.strip()]


def init_repo(repo: Path, answers: InitAnswers, *, force: bool = False, workflow: bool = True) -> InitResult:
    repo = repo.resolve()
    if not (repo / ".git").exists():
        raise InitError(f"{repo} is not a git repository (run `git init` first)")
    existing = next((repo / n for n in CONFIG_NAMES if (repo / n).exists()), None)
    result = InitResult(config_path=repo / CONFIG_NAMES[0])
    if existing and not force:
        raise InitError(f"{existing.name} already exists; pass --force to overwrite it")

    def write(rel: Path, text: str, overwrite: bool) -> None:
        p = repo / rel
        if p.exists() and not overwrite:
            result.skipped.append(p)
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        result.written.append(p)

    write(Path(CONFIG_NAMES[0]), render_config(answers), True)
    if workflow:
        write(WORKFLOW_PATH, render_workflow(answers), force)
    if _script_of(answers.bench_cmd) == BENCH_SCAFFOLD and not (repo / BENCH_SCAFFOLD).exists():
        write(Path(BENCH_SCAFFOLD), BENCH_TEMPLATE, False)
        result.notes.append(f"fill in {BENCH_SCAFFOLD}: it is the benchmark Hotpath will optimize against")
    gitignore = repo / ".gitignore"
    existing_ignore = _read(gitignore)
    if ".hotpath/" not in existing_ignore.splitlines():
        with gitignore.open("a", encoding="utf-8", newline="\n") as f:
            f.write(("" if existing_ignore.endswith("\n") or not existing_ignore else "\n") + ".hotpath/\n")
        result.written.append(gitignore)
    load_config(result.config_path)  # what we wrote must be a valid config
    return result
