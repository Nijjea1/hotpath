import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from hotpath.execution import IsolationError, docker_create_args, execution_metadata, run_target, stage_source
from hotpath.runner import run_process
from hotpath.schema import CmdResult, ExecutionConfig


def test_docker_security_arguments(tmp_path):
    args = docker_create_args(ExecutionConfig(gpu="device=0"), "sha256:fixed", tmp_path, "hotpath-test", "python test.py")
    for flag in ["--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--user=65534:65534", "--log-driver=none", "--pull=never"]:
        assert flag in args
    assert args[-3:] == ["sha256:fixed", "-c", "python test.py"]
    assert "readonly" in args[args.index("--mount") + 1]
    assert not any("docker.sock" in arg for arg in args)
    assert "--memory-swap" in args and "--pids-limit" in args and "--cpus" in args
    assert not any("API_KEY" in arg for arg in args)


def test_stage_only_tracked_and_no_credentials(ws, tmp_path):
    from hotpath.workspace import _git
    wt = ws.create_worktree(ws.head(), "stage")
    (wt / ".env.secret").write_text("TOKEN=secret")
    (wt / "private.pem").write_text("secret")
    _git(["add", "-f", ".env.secret", "private.pem"], wt)
    (wt / "untracked.txt").write_text("secret")
    dest = tmp_path / "stage"; dest.mkdir()
    stage_source(wt, dest)
    assert (dest / "mod.py").exists()
    assert not (dest / ".git").exists()
    assert not (dest / ".env.secret").exists()
    assert not (dest / "private.pem").exists()
    assert not (dest / "untracked.txt").exists()


def test_staging_uses_hardened_git_not_target_hooks(ws, tmp_path, monkeypatch):
    import hotpath.workspace as workspace
    real_run = subprocess.run
    observed = []
    def spy(args, *a, **kw):
        if isinstance(args, list) and "ls-files" in args:
            observed.append((args, kw.get("env", {})))
        return real_run(args, *a, **kw)
    monkeypatch.setattr(workspace.subprocess, "run", spy)
    dest = tmp_path / "stage-safe-git"; dest.mkdir()
    stage_source(ws.target, dest)
    assert observed and (dest / "mod.py").exists()
    args, env = observed[0]
    assert "core.fsmonitor=false" in args
    assert "GIT_CONFIG_NOSYSTEM" in env and env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert "GIT_CONFIG_GLOBAL" in env


def test_stage_rejects_tracked_hardlink_to_host_file(ws, tmp_path):
    from hotpath.workspace import _git
    outside = tmp_path / "host-secret.txt"
    outside.write_text("host credential")
    wt = ws.create_worktree(ws.head(), "hardlink")
    linked = wt / "apparently-safe.txt"
    try:
        os.link(outside, linked)
    except OSError:
        pytest.skip("hardlinks unavailable across this filesystem")
    _git(["add", "apparently-safe.txt"], wt)
    dest = tmp_path / "stage-hardlink"; dest.mkdir()
    with pytest.raises(IsolationError, match="hardlinked"):
        stage_source(wt, dest)
    assert not (dest / "apparently-safe.txt").exists()


async def test_output_is_bounded_and_fails(tmp_path):
    result = await run_process([sys.executable, "-c", "import sys; sys.stdout.write('x'*1000000)"], tmp_path, 10, output_limit=1024)
    assert result.exit_code != 0
    assert len(result.stdout) <= 1024
    assert "output limit exceeded" in result.stderr


@pytest.mark.parametrize("failure", ["timeout", "cancel", "create"])
async def test_container_cleanup_always_runs(tmp_path, monkeypatch, failure):
    import hotpath.execution as execution
    calls = []
    async def metadata(cfg): return {"image_id": "sha256:fixed"}
    async def command(args, *a, **kw):
        calls.append(args)
        if args[1] == "create" and failure == "create":
            return CmdResult(cmd="create", exit_code=1, stdout="", stderr="failure", duration_s=0)
        if args[1] == "start" and failure == "cancel":
            raise asyncio.CancelledError()
        return CmdResult(cmd="cmd", exit_code=0, stdout="", stderr="", duration_s=0, timed_out=failure == "timeout")
    monkeypatch.setattr(execution, "execution_metadata", metadata)
    monkeypatch.setattr(execution, "stage_source", lambda *a: None)
    monkeypatch.setattr(execution, "run_process", command)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError): await run_target("test", tmp_path, 1, ExecutionConfig())
    elif failure == "create":
        with pytest.raises(IsolationError): await run_target("test", tmp_path, 1, ExecutionConfig())
    else:
        assert (await run_target("test", tmp_path, 1, ExecutionConfig())).timed_out
    assert calls[-1][1:3] == ["rm", "-f"]
    assert calls[-1][-1] == calls[0][calls[0].index("--name") + 1]


async def test_missing_docker_fails_closed(monkeypatch):
    import hotpath.execution as execution
    async def missing(*a, **kw): raise FileNotFoundError("docker")
    monkeypatch.setattr(execution, "run_process", missing)
    with pytest.raises(IsolationError, match="refusing"):
        await execution_metadata(ExecutionConfig())


async def test_runner_image_with_anonymous_volumes_is_rejected(monkeypatch):
    import hotpath.execution as execution
    async def inspect(*args, **kwargs):
        return CmdResult(cmd="inspect", exit_code=0, duration_s=0, stderr="",
                         stdout='[{"Id":"sha256:fixed","Config":{"Volumes":{"/cache":{}}}}]')
    monkeypatch.setattr(execution, "run_process", inspect)
    with pytest.raises(IsolationError, match="VOLUMEs"):
        await execution_metadata(ExecutionConfig())


@pytest.mark.skipif(os.environ.get("HOTPATH_TEST_DOCKER") != "1", reason="real Docker probe requires HOTPATH_TEST_DOCKER=1 and reviewed runner image")
async def test_real_docker_boundary(ws, monkeypatch):
    from hotpath.workspace import _git
    wt = ws.create_worktree(ws.head(), "probe")
    (wt / "probe.py").write_text('''import os, socket, pathlib
assert os.getuid() == 65534
assert 'HOTPATH_HOST_SECRET' not in os.environ
assert not pathlib.Path('/var/run/docker.sock').exists()
try:
    pathlib.Path('/workspace/mod.py').write_text('tampered')
except OSError: pass
else: raise AssertionError('writable source')
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
except OSError: pass
else: raise AssertionError('network permitted')
pids = pathlib.Path('/sys/fs/cgroup/pids.max')
if pids.exists(): assert pids.read_text().strip() == '128'
print('isolated')
''')
    _git(["add", "probe.py"], wt)
    monkeypatch.setenv("HOTPATH_HOST_SECRET", "must-not-leak")
    result = await run_target("python probe.py", wt, 30, ExecutionConfig())
    assert result.exit_code == 0, result.stderr
    assert "isolated" in result.stdout
