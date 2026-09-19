"""Correctness gate for demo_repo. Exit 0 only if every function matches the reference exactly."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data import make_records  # noqa: E402
from reference import ref_dedupe, ref_freq, ref_top_k  # noqa: E402
import slowlib  # noqa: E402

failures = 0


def check(name, got, want):
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {str(got)[:120]} want {str(want)[:120]}")
    else:
        print(f"ok   {name}")


for seed in (1, 2, 3):
    recs = make_records(600, seed=seed)
    ids = [r["id"] for r in recs]
    tags = [r["tag"] for r in recs]
    scores = [r["score"] for r in recs]
    check(f"dedupe seed{seed}", slowlib.dedupe_preserve_order(ids), ref_dedupe(ids))
    check(f"freq seed{seed}", slowlib.word_frequencies(tags), ref_freq(tags))
    check(f"top_k seed{seed}", slowlib.top_k(scores, 7), ref_top_k(scores, 7))
    out = slowlib.pipeline(recs, k=5)
    check(f"pipeline seed{seed}", out, {"ids": ref_dedupe(ids), "freq": ref_freq(tags), "top": ref_top_k(scores, 5)})

# Edge cases
check("dedupe empty", slowlib.dedupe_preserve_order([]), [])
check("dedupe mixed", slowlib.dedupe_preserve_order([3, 1, 3, 2, 1]), [3, 1, 2])
check("freq empty", slowlib.word_frequencies([]), {})
check("freq case sensitive", slowlib.word_frequencies(["A", "a", "A"]), {"A": 2, "a": 1})
check("top_k k>n", slowlib.top_k([1.0, 2.0], 5), [2.0, 1.0])
check("top_k ties", slowlib.top_k([2.0, 2.0, 1.0], 2), [2.0, 2.0])

sys.exit(1 if failures else 0)
