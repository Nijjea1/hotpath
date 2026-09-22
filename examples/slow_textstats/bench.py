"""Benchmark for Hotpath: how long the ranking pipeline takes on a fixed corpus.

Locked. Hotpath runs this file but can never edit it.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hotpath.benchlib import run  # noqa: E402
from textstats import top_words  # noqa: E402

_rng = random.Random(11)
TEXT = " ".join(f"Word{_rng.randrange(1200)}{_rng.choice(['', ',', '.', '!'])}" for _ in range(14000))

run(lambda: top_words(TEXT, k=25), warmup=2, trials=15)
