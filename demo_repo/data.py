"""Deterministic synthetic dataset. Same seed -> same records on every run."""
import random

WORDS = [f"tag{i:04d}" for i in range(1500)]


def make_records(n: int = 4000, seed: int = 1234) -> list[dict]:
    rng = random.Random(seed)
    ids = [f"id-{rng.randrange(n // 3)}" for _ in range(n)]
    return [{"id": ids[i], "tag": rng.choice(WORDS), "score": rng.random() * 100} for i in range(n)]
