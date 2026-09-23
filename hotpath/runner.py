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


def _windows_descendants(root_pid: int) -> list[int]:
    """Snapshot descendant PIDs before killing a Windows process tree.

    ``taskkill /T`` can miss a grandchild when a short-lived launcher exits or
    when a virtual-environment executable inserts another Python process. Keep
    the snapshot so those descendants can be terminated explicitly afterward.
    """
    if not _WINDOWS:
        return []
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if snapshot == wintypes.HANDLE(-1).value:
        return []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    parents: dict[int, list[int]] = {}
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents.setdefault(int(entry.th32ParentProcessID), []).append(int(entry.th32ProcessID))
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    descendants, pending = [], list(parents.get(root_pid, ()))
    while pending:
        pid = pending.pop()
        descendants.append(pid)
        pending.extend(parents.get(pid, ()))
    return descendants


def _windows_terminate(pid: int) -> bool:
    """Terminate one same-user Windows process without spawning another shell."""
    if not _WINDOWS:
        return False
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


def tail(text: str, lines: int = TAIL_LINES) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def _kill_tree(proc):
    try:
        if _WINDOWS:
            descendants = _windows_descendants(proc.pid)
            # Stop the root first so it cannot spawn more work, then terminate
            # the captured tree from the leaves upward. Native handles avoid a
            # taskkill subprocess that can itself hang in constrained runners.
            root_stopped = _windows_terminate(proc.pid)
            for pid in reversed(descendants):
                _windows_terminate(pid)
            if not root_stopped and proc.returncode is None:
                try:
                    proc.kill()
                except (ProcessLookupError, OSError):
                    pass
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, OSError, FileNotFoundError):
        try:
            proc.kill()
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
            await asyncio.gather(*readers, return_exceptions=True)
            out, err = b"", b""
        # asyncio's Windows Proactor transport can otherwise survive until GC
        # and emit an unclosed-pipe warning even though the process was reaped.
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            transport.close()
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
