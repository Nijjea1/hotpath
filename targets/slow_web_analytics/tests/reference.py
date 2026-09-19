"""Independent reference implementation; this file is locked during optimization."""
from __future__ import annotations


def summarize_by_path(events: list[dict[str, object]]) -> list[dict[str, object]]:
    totals: dict[str, list[int]] = {}
    for event in events:
        path = str(event["path"])
        row = totals.setdefault(path, [0, 0])
        row[0] += 1
        row[1] += int(event["bytes"])
    return [
        {"path": path, "requests": totals[path][0], "bytes": totals[path][1]}
        for path in sorted(totals)
    ]


def error_counts(events: list[dict[str, object]]) -> dict[str, int]:
    ok = sum(1 for event in events if event["status"] == 200)
    return {"ok": ok, "error": len(events) - ok}
