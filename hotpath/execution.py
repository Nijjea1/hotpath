"""Fail-closed execution backend. Docker access belongs to the trusted orchestrator only."""
from __future__ import annotations
import asyncio
import json
import os
import shutil
import stat
import tempfile
import uuid
from pathlib import Path
from hotpath.runner import run_cmd, run_process
from hotpath.workspace import _git, safe_target_path


class IsolationError(RuntimeError):
    pass


def stage_source(source: Path, dest: Path) -> None:
    """Copy only tracked regular files; never expose git metadata or host links."""
    # Use the same hardened Git invocation as worktree operations: target-local
    # fsmonitor/filter settings must never execute on the orchestrator host.
    tracked = _git(["ls-files", "-z"], source, strip=False)
    total = 0
    for rel in tracked.split("\0"):
        if not rel:
            continue
        parts = Path(rel).parts
        if any(p.lower() in {".git", ".hotpath", ".ssh", ".aws", ".azure", ".venv", "node_modules"}
               or p.lower().startswith(".env") or p.lower().endswith((".pem", ".key", ".p12", ".pfx")) for p in parts):
            continue
        src = safe_target_path(source, rel)
        original = source / rel
        if original.is_symlink() or any(p.is_symlink() for p in original.parents if p != source.parent):
            raise IsolationError(f"symlinks are not permitted in isolated source: {rel}")
        info = src.stat()
        if not stat.S_ISREG(info.st_mode):
            raise IsolationError(f"non-regular source file: {rel}")
        if info.st_nlink > 1:
            raise IsolationError(f"hardlinked source file is not permitted: {rel}")
        total += info.st_size
        if total > 512 * 1024 * 1024:
            raise IsolationError("source staging exceeds 512 MiB; bake large dependencies into the runner image")
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        target.chmod(0o755 if info.st_mode & stat.S_IXUSR else 0o644)


def docker_create_args(cfg, image_id: str, stage: Path, name: str, command: str) -> list[str]:
    if "," in str(stage):
        raise IsolationError("staging path contains unsupported mount delimiter")
    args = ["docker", "create", "--name", name, "--pull=never", "--network=none", "--read-only",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--user=65534:65534", "--init",
            "--no-healthcheck", "--log-driver=none", "--cpus", str(cfg.cpus),
            "--memory", f"{cfg.memory_mb}m", "--memory-swap", f"{cfg.memory_mb}m",
            "--pids-limit", str(cfg.pids_limit), "--ulimit", "nofile=1024:1024", "--ulimit", "core=0",
            "--shm-size", "16m", "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={cfg.tmpfs_mb}m,mode=1777",
            "--mount", f"type=bind,source={stage},target=/workspace,readonly",
            "--workdir=/workspace", "--env=HOME=/tmp", "--env=PYTHONHASHSEED=0",
            "--env=PYTHONUNBUFFERED=1", "--env=PYTHONDONTWRITEBYTECODE=1", "--entrypoint=/bin/sh"]
    if cfg.gpu:
        args += ["--gpus", cfg.gpu]
    if cfg.runtime:
        args += ["--runtime", cfg.runtime]
    return args + [image_id, "-c", command]


async def execution_metadata(cfg) -> dict:
    if cfg.backend == "local":
        return {"backend": "local", "isolated": False}
    try:
        result = await run_process(["docker", "image", "inspect", cfg.image], Path.cwd(), 20)
    except OSError as exc:
        raise IsolationError("Docker unavailable; refusing to execute target locally") from exc
    if result.exit_code:
        raise IsolationError("runner image unavailable; build the trusted image before running: " + result.stderr)
    record = json.loads(result.stdout)[0]
    if (record.get("Config") or {}).get("Volumes"):
        raise IsolationError("runner image declares writable VOLUMEs; use a reviewed image without VOLUME directives")
    return {"backend": "docker", "isolated": True, "image": cfg.image, "image_id": record["Id"],
            "repo_digests": record.get("RepoDigests", []), "runtime": cfg.runtime or "daemon-default",
            "gpu": cfg.gpu, "cpus": cfg.cpus, "memory_mb": cfg.memory_mb,
            "pids_limit": cfg.pids_limit, "network": "none"}


async def run_target(cmd: str, cwd: Path, timeout: float, execution):
    if execution.backend == "local":
        return await run_cmd(cmd, cwd, timeout, output_limit=execution.output_limit_bytes)
    metadata = await execution_metadata(execution)
    name = "hotpath-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="hotpath-sandbox-") as temp:
        stage = Path(temp)
        stage_source(cwd, stage)
        # TemporaryDirectory is 0700; the non-root container user must traverse the bind source.
        stage.chmod(0o755)
        async def remove():
            res = await run_process(["docker", "rm", "-f", name], Path.cwd(), 20)
            if res.exit_code and "No such container" not in res.stderr:
                raise IsolationError("failed to remove execution container: " + res.stderr)
        try:
            created = await run_process(docker_create_args(execution, metadata["image_id"], stage, name, cmd), Path.cwd(), 30)
            if created.exit_code:
                raise IsolationError("isolated container creation failed: " + created.stderr)
            result = await run_process(["docker", "start", "--attach", name], Path.cwd(), timeout,
                                       output_limit=execution.output_limit_bytes)
            result.cmd = cmd
            return result
        finally:
            cleanup = asyncio.create_task(remove())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise


execution_environment = execution_metadata
