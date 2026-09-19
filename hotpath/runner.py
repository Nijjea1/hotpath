"""Bounded subprocess output with timeout and cancellation process-tree cleanup."""
from __future__ import annotations
import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from hotpath.schema import CmdResult
TAIL_LINES = 60
_WINDOWS = sys.platform == "win32"


def tail(text: str, lines: int = TAIL_LINES) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def _kill_tree(proc):
    try:
        if _WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


async def run_process(command: str | list[str], cwd: Path, timeout: float,
                      env: dict[str, str] | None = None, output_limit: int = 1048576) -> CmdResult:
    """Capture at most output_limit bytes total; excessive output fails the command."""
    start = time.perf_counter()
    kwargs = dict(cwd=str(cwd), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    if not _WINDOWS:
        kwargs["start_new_session"] = True
    proc = (await asyncio.create_subprocess_shell(command, **kwargs) if isinstance(command, str)
            else await asyncio.create_subprocess_exec(*command, **kwargs))
    remaining = output_limit
    exceeded = False
    async def read(stream):
        nonlocal remaining, exceeded
        result = bytearray()
        while data := await stream.read(16384):
            count = min(remaining, len(data))
            result.extend(data[:count]); remaining -= count
            if count < len(data):
                exceeded = True
                _kill_tree(proc)
                break
        return bytes(result)
    readers = [asyncio.create_task(read(proc.stdout)), asyncio.create_task(read(proc.stderr))]
    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), timeout)
    except asyncio.TimeoutError:
        timed_out = True
        _kill_tree(proc)
        await proc.wait()
    except asyncio.CancelledError:
        _kill_tree(proc)
        await proc.wait()
        raise
    finally:
        # Descendants must not hold pipes open after their parent exits.
        try:
            out, err = await asyncio.wait_for(asyncio.gather(*readers), 2)
        except (asyncio.TimeoutError, ValueError):
            _kill_tree(proc)
            for task in readers:
                task.cancel()
            out, err = b"", b""
    if exceeded:
        err += b"\noutput limit exceeded"
    return CmdResult(cmd=command if isinstance(command, str) else " ".join(command),
                     exit_code=-9 if exceeded else (proc.returncode if proc.returncode is not None else -9),
                     stdout=out.decode(errors="replace"), stderr=err.decode(errors="replace"),
                     duration_s=time.perf_counter()-start, timed_out=timed_out)


async def run_cmd(cmd: str, cwd: Path, timeout: float, env: dict[str, str] | None = None,
                  output_limit: int = 1048576) -> CmdResult:
    """Trusted-local mode only: explicitly inherits the developer environment."""
    full_env = {**os.environ, "PYTHONHASHSEED": "0", "PYTHONUNBUFFERED": "1", **(env or {})}
    return await run_process(cmd, cwd, timeout, full_env, output_limit)
