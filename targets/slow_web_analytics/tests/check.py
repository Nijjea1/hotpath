"""Locked correctness gate for slow_web_analytics."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from analytics import dashboard_payload, error_counts, summarize_by_path  # noqa: E402
from data import make_events  # noqa: E402
from reference import error_counts as ref_errors, summarize_by_path as ref_summary  # noqa: E402

failures = 0


def check(name: str, got: object, want: object) -> None:
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {str(got)[:180]} want {str(want)[:180]}")
    else:
        print(f"ok   {name}")


for seed, n in ((4, 1), (5, 57), (6, 913)):
    events = make_events(n=n, seed=seed)
    expected_paths = ref_summary(events)
    expected_errors = ref_errors(events)
    check(f"summary seed={seed}", summarize_by_path(events), expected_paths)
    check(f"errors seed={seed}", error_counts(events), expected_errors)
    check(f"payload seed={seed}", dashboard_payload(events), {"paths": expected_paths, "status": expected_errors})

check("empty summary", summarize_by_path([]), [])
check("empty errors", error_counts([]), {"ok": 0, "error": 0})
sys.exit(1 if failures else 0)
