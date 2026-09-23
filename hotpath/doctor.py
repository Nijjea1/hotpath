"""Environment diagnostics for a usable Hotpath installation."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version


def _command_version(command: list[str]) -> tuple[bool, str]:
    if not shutil.which(command[0]):
        return False, "not installed"
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    text = (result.stdout or result.stderr).strip().splitlines()
    return result.returncode == 0, (text[0] if text else f"exit {result.returncode}")


def accelerator_info() -> dict:
    """Report PyTorch acceleration without making torch a core dependency."""
    try:
        import torch
    except (ImportError, OSError) as exc:
        return {"installed": False, "device": "cpu", "backend": "cpu",
                "detail": f"PyTorch unavailable: {exc}"}
    from hotpath.benchlib import torch_backend, torch_device

    try:
        device = torch_device(torch_module=torch)
        backend = torch_backend(device, torch_module=torch)
        if backend in {"cuda", "rocm"}:
            name = torch.cuda.get_device_name(device)
        elif backend == "xpu":
            name = torch.xpu.get_device_name(device)
        elif backend == "mps":
            name = "Apple Metal Performance Shaders"
        else:
            name = platform.processor() or platform.machine() or "CPU"
        return {"installed": True, "torch": torch.__version__, "device": device,
                "backend": backend, "detail": name}
    except (RuntimeError, ValueError, OSError) as exc:
        return {"installed": True, "torch": torch.__version__, "device": "unavailable",
                "backend": "unavailable", "detail": str(exc)}


def collect() -> dict:
    git_ok, git_detail = _command_version(["git", "--version"])
    docker_cli = bool(shutil.which("docker"))
    docker_ok, docker_detail = _command_version(["docker", "info", "--format", "{{.ServerVersion}}"])
    try:
        package_version = version("hotpath")
    except PackageNotFoundError:
        package_version = "source checkout"
    return {
        "hotpath": package_version,
        "platform": {"system": platform.system(), "release": platform.release(),
                     "machine": platform.machine()},
        "python": {"version": platform.python_version(), "supported": sys.version_info >= (3, 11),
                   "executable": sys.executable},
        "git": {"ok": git_ok, "detail": git_detail},
        "docker": {"installed": docker_cli, "running": docker_ok if docker_cli else False,
                   "detail": docker_detail},
        "accelerator": accelerator_info(),
        "credentials": {name: bool(os.environ.get(name)) for name in
                        ("OPENAI_API_KEY", "BASETEN_API_KEY", "GITHUB_TOKEN", "GH_TOKEN", "SENTRY_DSN")},
    }


def render(report: dict) -> str:
    accelerator = report["accelerator"]
    docker = report["docker"]
    credentials = ", ".join(name for name, present in report["credentials"].items() if present) or "none"
    return "\n".join([
        f"Hotpath {report['hotpath']}",
        f"Platform  {report['platform']['system']} {report['platform']['release']} ({report['platform']['machine']})",
        f"Python    {report['python']['version']}  {'OK' if report['python']['supported'] else 'NEEDS 3.11+'}",
        f"Git       {'OK' if report['git']['ok'] else 'MISSING'}  {report['git']['detail']}",
        f"Docker    {'running' if docker['running'] else 'installed, not running' if docker['installed'] else 'not installed'}",
        f"Compute   {accelerator['backend']} / {accelerator['device']}  {accelerator['detail']}",
        f"Keys      {credentials} (presence only; values are never printed)",
    ])


def run(*, as_json: bool = False, require_gpu: bool = False) -> int:
    report = collect()
    print(json.dumps(report, indent=2) if as_json else render(report))
    required_ok = report["python"]["supported"] and report["git"]["ok"]
    gpu_ok = report["accelerator"]["backend"] not in {"cpu", "unavailable"}
    if require_gpu and not gpu_ok:
        print("GPU required but no supported PyTorch accelerator is available.", file=sys.stderr)
    return 0 if required_ok and (gpu_ok or not require_gpu) else 1
