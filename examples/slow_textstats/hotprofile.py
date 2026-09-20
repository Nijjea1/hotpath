"""Profile of the benchmark workload, so Hotpath's planner sees where the time goes. Locked."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import TEXT  # noqa: E402  (the same corpus the benchmark times)
from hotpath.profilelib import run  # noqa: E402
from textstats import top_words  # noqa: E402

run(lambda: top_words(TEXT, k=25), top=20, repeat=2)
