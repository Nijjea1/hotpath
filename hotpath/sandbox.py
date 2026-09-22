"""Where a target's code runs during `hotpath go`: its own virtualenv, or a Docker runner image.

Both install the target's *dependencies* and never the target itself. Hotpath measures each candidate
from its own git worktree, and an installed copy of the project would silently shadow that code.
Both also carry Hotpath's small measurement helpers (benchlib, profilelib, benchwrap, testprofile),
copied as a stdlib-only `hotpath` package rather than installing Hotpath, so nothing of Hotpath's
own (its tests, its server) can collide with the target's imports.

The Docker image is built from Hotpath's template, never from the target's Dockerfile. Network access
exists only while the image is built; candidate code later runs with no network (see ISOLATION.md).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

HELPER_MODULES = ("benchlib.py", "profilelib.py", "benchwrap.py", "testprofile.py", "pathrules.py")
_HERE = Path(__file__).resolve().parent


class SandboxError(RuntimeError):
    pass


@dataclass
class TargetEnv:
    kind: str                                   # "venv" | "docker"
    python: str                                 # interpreter used on the host for validation runs
    env: dict[str, str] = field(default_factory=dict)   # environment for host-side commands
    image: Optional[str] = None                 # runner image tag for execution.backend docker
    notes: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    duration_s: float = 0.0
    timed_out: bool = False


#: Runs one shell command in a directory with a timeout: how `hotpath go` executes the target's code
#: before the search starts (baseline tests, benchmark validation). Same backend as the search itself.
Runner = Callable[[str, Path, float], RunResult]


def make_runner(tenv: TargetEnv) -> Runner:
    if tenv.kind == "docker":
        import asyncio

        from hotpath.execution import run_target
        from hotpath.schema import ExecutionConfig
        execution = ExecutionConfig(backend="docker", image=tenv.image or "")

        def run_docker(cmd: str, cwd: Path, timeout: float) -> RunResult:
            r = asyncio.run(run_target(cmd, cwd, timeout, execution))
            return RunResult(r.exit_code, r.stdout, r.stderr, r.duration_s, r.timed_out)
        return run_docker

    def run_local(cmd: str, cwd: Path, timeout: float) -> RunResult:
        import time
        t0 = time.perf_counter()
        try:
            r = subprocess.run(cmd, cwd=str(cwd), shell=True, env={**os.environ, **tenv.env}, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            return RunResult(124, out, f"timed out after {timeout:.0f}s", time.perf_counter() - t0, True)
        return RunResult(r.returncode, r.stdout, r.stderr, time.perf_counter() - t0)
    return run_local


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_bin(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def install_helpers(site_packages: Path) -> None:
    """Copy Hotpath's measurement helpers into `site_packages/hotpath`."""
    pkg = site_packages / "hotpath"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text('"""Hotpath measurement helpers (copied by `hotpath go`)."""\n', encoding="utf-8")
    for name in HELPER_MODULES:
        shutil.copyfile(_HERE / name, pkg / name)


def _pip(python: Path, args: list[str], say: Callable[[str], None], timeout: float = 1800) -> None:
    cmd = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", *args]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if res.returncode != 0:
        raise SandboxError("installing the target's dependencies failed:\n" + (res.stderr or res.stdout)[-3000:])


def _write_requirements(dest: Path, specs: list[str]) -> Path:
    dest.write_text("\n".join(specs) + "\n", encoding="utf-8")
    return dest


def base_env(src_layout: bool) -> dict[str, str]:
    env = {"PYTHONHASHSEED": "0", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    if src_layout:
        # Relative on purpose: every command runs with the candidate's worktree as its working directory.
        env["PYTHONPATH"] = "src"
    return env


def make_venv(venv: Path, deps: list[str], *, src_layout: bool, python: str = sys.executable,
              say: Callable[[str], None] = lambda _m: None) -> TargetEnv:
    """Create (or reuse) a virtualenv holding the target's dependencies and Hotpath's helpers."""
    marker = venv / ".hotpath-deps"
    wanted = hashlib.sha256(json.dumps(sorted(deps)).encode()).hexdigest()
    vpy = _venv_python(venv)
    if not vpy.exists():
        say(f"creating a virtualenv at {venv}")
        res = subprocess.run([python, "-m", "venv", str(venv)], capture_output=True, text=True)
        if res.returncode != 0:
            raise SandboxError(f"could not create a virtualenv: {res.stderr.strip()}")
    if deps and (not marker.exists() or marker.read_text() != wanted):
        say(f"installing {len(deps)} dependenc{'y' if len(deps) == 1 else 'ies'} (not the project itself)")
        with tempfile.TemporaryDirectory() as tmp:
            _pip(vpy, ["-r", str(_write_requirements(Path(tmp) / "requirements.txt", deps))], say)
        marker.write_text(wanted)
    site = subprocess.run([str(vpy), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                          capture_output=True, text=True, check=True).stdout.strip()
    install_helpers(Path(site))
    env = {**base_env(src_layout), "VIRTUAL_ENV": str(venv),
           "PATH": str(_venv_bin(venv)) + os.pathsep + os.environ.get("PATH", "")}
    return TargetEnv("venv", str(vpy), env)


def run_install_cmds(repo: Path, cmds: list[str], say: Callable[[str], None]) -> None:
    """Tier 2 installs (npm ci, cargo fetch, go mod download) in the target checkout itself: worktrees
    live inside it, so Node's parent-directory lookup finds the same node_modules."""
    for cmd in cmds:
        say(f"running `{cmd}`")
        res = subprocess.run(cmd, cwd=str(repo), shell=True, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=1800)
        if res.returncode != 0:
            raise SandboxError(f"`{cmd}` failed:\n{(res.stderr or res.stdout)[-3000:]}")


# --------------------------------------------------------------------------- #
# Docker
# --------------------------------------------------------------------------- #

def docker_available(timeout: float = 15) -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True,
                              timeout=timeout).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def image_tag(name: str, deps: list[str]) -> str:
    slug = re.sub(r"[^a-z0-9_.-]+", "-", name.lower()).strip("-.") or "target"
    digest = hashlib.sha256(json.dumps(sorted(deps)).encode()).hexdigest()[:10]
    return f"hotpath-runner:{slug[:40]}-{digest}"


def render_dockerfile(python_version: str = "3.12", src_layout: bool = False) -> str:
    pythonpath = "/opt/hotpath" + (":/workspace/src" if src_layout else "")
    return f"""# Written by `hotpath go` from Hotpath's template (never the target's Dockerfile).
FROM python:{python_version}-slim
WORKDIR /opt/hotpath
COPY requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /opt/requirements.txt
COPY hotpath/ ./hotpath/
ENV PYTHONPATH={pythonpath}
USER 65534:65534
WORKDIR /workspace
"""


def build_image(name: str, deps: list[str], *, src_layout: bool, say: Callable[[str], None]) -> TargetEnv:
    tag = image_tag(name, deps)
    exists = subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0
    if not exists:
        say(f"building runner image {tag} (dependencies only; network is used only for this build)")
        with tempfile.TemporaryDirectory(prefix="hotpath-image-") as tmp:
            ctx = Path(tmp)
            (ctx / "Dockerfile").write_text(render_dockerfile(src_layout=src_layout), encoding="utf-8")
            _write_requirements(ctx / "requirements.txt", deps or ["pip"])
            install_helpers(ctx)
            res = subprocess.run(["docker", "build", "-t", tag, str(ctx)], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=3600)
            if res.returncode != 0:
                raise SandboxError("building the runner image failed:\n" + (res.stderr or res.stdout)[-3000:])
    return TargetEnv("docker", sys.executable, base_env(src_layout), image=tag)
