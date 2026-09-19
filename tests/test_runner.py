"""Process timeout tests, including Windows descendant cleanup."""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hotpath.runner import run_process


def _windows_pid_exists(pid: int) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    return str(pid) in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process-tree semantics")
def test_timeout_kills_runaway_child_process(tmp_path: Path):
    child_pid = tmp_path / "child.pid"
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess, sys, time\n"
        f"pid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']).pid\n"
        f"open({str(child_pid)!r}, 'w').write(str(pid))\n"
        "time.sleep(120)\n"
    )

    async def run():
        return await run_process([sys.executable, str(parent)], tmp_path, timeout=0.5)

    result = asyncio.run(run())
    assert result.timed_out
    assert child_pid.exists(), "parent did not launch its child"
    pid = int(child_pid.read_text())

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _windows_pid_exists(pid):
        time.sleep(0.1)
    assert not _windows_pid_exists(pid), f"runaway child process {pid} survived timeout cleanup"
