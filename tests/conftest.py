import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hotpath.schema import HotpathConfig, ProviderConfig
from hotpath.store import Store
from hotpath.workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_external_telemetry(monkeypatch):
    # Tests never load a developer's real DSN from .env or send telemetry.
    monkeypatch.setenv("SENTRY_DSN", "")
    # Nor a developer's user-level API keys (~/.hotpath/.env), which the CLI loads.
    monkeypatch.setenv("HOTPATH_HOME", str(ROOT / ".tmp" / "no-hotpath-home"))
    # Temporary target worktrees call `python` and import the installed Hotpath
    # helpers. Keep those subprocesses on the interpreter running pytest.
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))


@pytest.fixture
def tiny_repo(tmp_path: Path) -> Path:
    """A minimal target: one editable module, a locked test, a locked benchmark."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "def work(n):\n    out = []\n    for i in range(n):\n        if i not in out:\n            out.append(i)\n    return len(out)\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "check.py").write_text(
        "import sys; sys.path.insert(0, '.')\nfrom mod import work\nassert work(50) == 50\nassert work(0) == 0\nprint('ok')\n")
    (repo / "bench.py").write_text(
        "import sys; sys.path.insert(0, '.')\nfrom hotpath.benchlib import run\nfrom mod import work\nrun(lambda: work(1500), warmup=1, trials=8)\n")
    (repo / "prof.py").write_text(
        "import sys; sys.path.insert(0, '.')\nfrom hotpath.profilelib import run\nfrom mod import work\nrun(lambda: work(800), top=10)\n")
    return repo


@pytest.fixture
def cfg(tiny_repo: Path) -> HotpathConfig:
    return HotpathConfig(
        name="tiny", target=str(tiny_repo), test_cmd="python tests/check.py", bench_cmd="python bench.py",
        execution={"backend": "local"},
        profile_cmd="python prof.py", editable=["*.py"], locked=["tests/*", "bench.py", "prof.py"],
        timeouts={"test": 30, "bench": 60, "profile": 30, "model": 10},
        benchmark={"min_speedup": 1.03, "noise_multiplier": 2.0, "baseline_repeats": 2, "bootstrap_samples": 300},
        search={"iterations": 2, "candidates_per_iteration": 3},
        provider=ProviderConfig(planner="mock", worker="mock"),
    )


@pytest.fixture
def ws(cfg: HotpathConfig) -> Workspace:
    w = Workspace(Path(cfg.target), Path(cfg.workdir))
    w.ensure_repo()
    yield w
    w.cleanup()


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "db.sqlite")
