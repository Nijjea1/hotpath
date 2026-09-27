"""Remember the benchmark a repository was measured against, so two runs can be compared.

Three `hotpath go` runs against the same commit of the same repository produced **1.47x, nothing,
and 1.45x**. Nothing was broken: each run asked a model for a fresh `workload()`, and got a different
one — over different functions, with different noise (13% one time, clean the next). Those three runs
never measured the same thing, so their numbers were never comparable, and a demo could produce any
of the three.

A generated benchmark is committed to the setup branch, but a later run clones the *base* commit,
which has no Hotpath files in it, so the repository itself cannot carry the answer forward. This
keeps it outside the repository instead, keyed by target and base commit.

Reuse is never blind. A cached workload is written into the worktree and **validated by running it**,
exactly like a fresh one: if the project moved underneath it and it no longer runs, or no longer
measures what it used to, it is discarded and a new one is generated. Caching changes which workload
is tried first; it never lowers the bar a workload has to clear.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

#: Bumped when the fixed bench/profile templates change, because a cached workload is only meaningful
#: together with the code that wraps and times it.
CACHE_FORMAT = 2


@dataclass
class CachedWorkload:
    """A workload that validated once, and what it measured when it did."""
    code: str
    description: str
    digest: str
    median_s: float = 0.0
    noise: float = 0.0
    target: str = ""
    base_commit: str = ""
    created_at: str = ""
    format: int = CACHE_FORMAT
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Optional["CachedWorkload"]:
        if not isinstance(data, dict) or data.get("format") != CACHE_FORMAT:
            return None                      # a different wrapper: the numbers would not mean the same
        code = data.get("code")
        if not isinstance(code, str) or "def workload" not in code:
            return None
        known = {f: data.get(f) for f in cls.__dataclass_fields__ if f in data}
        try:
            return cls(**{**known, "code": code})
        except TypeError:
            return None

    def summary(self) -> str:
        when = (self.created_at or "")[:10]
        measured = f"{self.median_s * 1000:.1f} ms median, noise {self.noise:.1%}" if self.median_s else "unmeasured"
        return f"{self.digest} ({measured}{', ' + when if when else ''})"


def slug(target: str) -> str:
    """A filesystem-safe name for a target path or URL."""
    text = re.sub(r"^https?://|^git@|\.git$", "", str(target).replace("\\", "/")).strip("/")
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-.")[-120:] or "target"


class BenchmarkCache:
    """One JSON file per target under `<workspaces>/.cache/benchmarks/`."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def path_for(self, target: str) -> Path:
        return self.root / f"{slug(target)}.json"

    def load(self, target: str, base_commit: str = "") -> Optional[CachedWorkload]:
        """The remembered workload for this target, or None.

        A different base commit does not disqualify it — the point is to keep measuring the same thing
        as the code changes — but it is recorded so the caller can say where the workload came from.
        """
        path = self.path_for(target)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return CachedWorkload.from_dict(data)

    def save(self, target: str, code: str, description: str, digest: str, *, base_commit: str = "",
             median_s: float = 0.0, noise: float = 0.0, details: Optional[list[str]] = None) -> CachedWorkload:
        entry = CachedWorkload(code=code, description=description, digest=digest, median_s=median_s,
                              noise=noise, target=str(target), base_commit=base_commit,
                              created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              details=list(details or []))
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.path_for(target).write_text(json.dumps(entry.to_dict(), indent=2), encoding="utf-8")
        except OSError:
            pass          # a cache that cannot be written is a missed optimisation, never an error
        return entry

    def forget(self, target: str) -> bool:
        try:
            self.path_for(target).unlink()
            return True
        except OSError:
            return False
