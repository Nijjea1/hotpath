"""Profile a pytest run and print Hotpath profile JSON.

    python -m hotpath.testprofile [pytest args...]

The test suite is the one workload every repository already has, so its profile shows where the
project's own code spends time. Only frames inside the current directory are kept (profilelib's rule),
and test files are dropped: the agent can never edit them, so they are not actionable hotspots.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys


def main(argv: list[str] | None = None) -> int:
    import pytest

    from hotpath import profilelib
    from hotpath.pathrules import is_test_path

    args = list(sys.argv[1:] if argv is None else argv) or ["-q"]
    args = [*args, "-p", "no:cacheprovider"]
    exit_code: list[int] = []
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        profilelib.run(lambda: exit_code.append(int(pytest.main(args))), top=400)
    lines = captured.getvalue().splitlines()
    doc = next((json.loads(line) for line in reversed(lines) if line.startswith('{"hotpath_profile"')), None)
    if doc is None or (exit_code and exit_code[0] != 0):
        sys.stderr.write("\n".join(lines[-40:]) + "\n")
        return exit_code[0] if exit_code and exit_code[0] else 1
    doc["hotspots"] = [h for h in doc["hotspots"] if h["file"] and not is_test_path(h["file"])][:40]
    doc["note"] = "profile of the test suite; test files omitted"
    print(json.dumps(doc), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
