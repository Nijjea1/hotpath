"""Time any command and print Hotpath benchmark JSON.

    python -m hotpath.benchwrap --trials 7 --warmup 1 -- npm run bench --silent

For repositories whose benchmark is a command rather than a Python function (Node, Rust, Go, or a plain
script), and as the fallback benchmark when none can be generated: the test suite's own runtime. A trial
that exits non-zero fails the benchmark, because a timing of a broken run is not a measurement.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

from hotpath.benchlib import emit


def time_command(cmd: list[str], *, trials: int, warmup: int, timeout: float) -> list[float]:
    shell = os.name == "nt"  # npm/cargo shims on Windows are .cmd files that need the shell
    target = subprocess.list2cmdline(cmd) if shell else cmd
    samples: list[float] = []
    for i in range(warmup + trials):
        t0 = time.perf_counter()
        res = subprocess.run(target, shell=shell, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             timeout=timeout)
        dt = time.perf_counter() - t0
        if res.returncode != 0:
            err = res.stderr.decode("utf-8", "replace")[-2000:]
            raise SystemExit(f"benchmark command exited {res.returncode} on trial {i + 1}:\n{err}")
        if i >= warmup:
            samples.append(dt)
    return samples


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m hotpath.benchwrap")
    p.add_argument("--trials", type=int, default=7)
    p.add_argument("--warmup", type=int, default=1)
    p.add_argument("--timeout", type=float, default=600.0, help="seconds per trial")
    p.add_argument("cmd", nargs=argparse.REMAINDER, help="-- command to time")
    args = p.parse_args(argv)
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    if not cmd:
        p.error("give the command to time after --")
    samples = time_command(cmd, trials=args.trials, warmup=args.warmup, timeout=args.timeout)
    emit(samples, metric="seconds", higher_is_better=False, source="command", command=" ".join(cmd))
    return 0


if __name__ == "__main__":
    sys.exit(main())
