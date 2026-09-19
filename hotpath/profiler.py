"""Turn a profile command's output into a compact, planner-friendly summary.

Two consumers with different needs meet here. The planner wants a dozen rows it can read inside a
prompt budget; the before/after bottleneck diff wants a deep list, because a hotspot missing from a
profile must be distinguishable from one measured at zero. `parse_profile_output` therefore retains
more than it shows, and records what it dropped so the omission can be bounded rather than guessed.
"""
from __future__ import annotations

import json

from hotpath.schema import Hotspot, ProfileFrame, ProfileSummary


class ProfileParseError(Exception):
    pass


def parse_profile_output(stdout: str, retain: int, commit: str = "") -> ProfileSummary:
    """Parse the last JSON line carrying 'hotspots', keeping the `retain` costliest rows.

    `retain` governs storage depth, not prompt depth — see `render_profile` for the planner's view.
    """
    obj = None
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                cand = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(cand, dict) and "hotspots" in cand:
                obj = cand
                break
    if obj is None:
        raise ProfileParseError("profile command did not print a JSON line with 'hotspots'")
    hs = [Hotspot(**h) for h in obj["hotspots"]]
    hs.sort(key=lambda h: h.self_time, reverse=True)
    kept = hs[:retain]
    total = float(obj.get("total_time", 0.0))
    kept_self = sum(h.self_time for h in kept)
    known = obj.get("completeness_known") is True
    n_total = int(obj.get("n_functions_total", len(hs)))
    if n_total < len(hs):
        raise ProfileParseError("profile function count is smaller than emitted rows")
    return ProfileSummary(
        tool=str(obj.get("tool", "unknown")), total_time=total, hotspots=kept, commit=commit,
        n_functions_total=n_total, retained=len(kept), completeness_known=known,
        # Clamp: the profiler's own total can lag the summed rows by float noise, and a negative
        # residual would render as a nonsensical "everything else" row.
        residual_self_time=max(0.0, total - kept_self),
        # The smallest retained self time is a hard upper bound on anything that did not make the
        # cut, since rows are sorted by self time. An untruncated profile omits nothing, so the
        # bound is zero and the diff may speak of true eliminations.
        cutoff_self_time=kept[-1].self_time if kept and (len(kept) < n_total or not known) else 0.0,
        flamegraph=[ProfileFrame(**frame) for frame in obj.get("flamegraph", [])],
        flamegraph_source=str(obj.get("flamegraph_source", "")),
        flamegraph_unavailable_reason=str(obj.get("flamegraph_unavailable_reason", "")),
    )


def render_profile(p: ProfileSummary, limit: int | None = None) -> str:
    """Compact table for the model: a dozen rows instead of a raw trace.

    `limit` caps the rows shown so widening storage retention never widens the prompt.
    """
    rows_in = p.hotspots if limit is None else p.hotspots[:limit]
    if not rows_in:
        return "(no profile available)"
    rows = [f"{'pct':>5} {'self_s':>8} {'total_s':>8} {'calls':>7}  location"]
    for h in rows_in:
        loc = f"{h.file}:{h.line} {h.function}" if h.file else h.function
        rows.append(f"{h.pct:5.1f} {h.self_time:8.4f} {h.total_time:8.4f} {h.calls:7d}  {loc}")
    return "\n".join(rows)
