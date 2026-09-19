"""Web-request rollups. Correct, but the summary implementation is intentionally slow."""
from __future__ import annotations


def summarize_by_path(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return paths sorted lexically with request count and total response bytes."""
    paths = sorted({str(event["path"]) for event in events})
    rows = []
    for path in paths:
        requests = sum(1 for event in events if event["path"] == path)
        total_bytes = sum(int(event["bytes"]) for event in events if event["path"] == path)
        rows.append({"path": path, "requests": requests, "bytes": total_bytes})
    return rows


def error_counts(events: list[dict[str, object]]) -> dict[str, int]:
    """Small side metric included to ensure the patch preserves unrelated behavior."""
    return {
        "ok": sum(1 for event in events if event["status"] == 200),
        "error": sum(1 for event in events if event["status"] != 200),
    }


def dashboard_payload(events: list[dict[str, object]]) -> dict[str, object]:
    return {"paths": summarize_by_path(events), "status": error_counts(events)}
