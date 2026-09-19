"""Deterministic synthetic request events used by the locked tests and benchmark."""
from __future__ import annotations

import random


def make_events(n: int = 8_000, seed: int = 732) -> list[dict[str, object]]:
    rng = random.Random(seed)
    paths = [f"/product/{i:04d}" for i in range(300)]
    return [
        {"path": rng.choice(paths), "status": 200 if rng.random() < 0.94 else 500,
         "bytes": rng.randrange(200, 12_000)}
        for _ in range(n)
    ]
