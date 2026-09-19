"""Record processing pipeline. Correct, but written the slow way."""


def dedupe_preserve_order(items):
    """Return unique items in first-seen order."""
    out = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def word_frequencies(words):
    """Map each word to how often it appears, sorted by word."""
    freq = {}
    for w in set(words):
        freq[w] = words.count(w)
    return dict(sorted(freq.items()))


def top_k(scores, k):
    """The k largest scores, descending."""
    result = []
    remaining = list(scores)
    for _ in range(min(k, len(remaining))):
        best = max(remaining)
        result.append(best)
        remaining.remove(best)
    return result


def pipeline(records, k=10):
    ids = dedupe_preserve_order([r["id"] for r in records])
    freq = word_frequencies([r["tag"] for r in records])
    top = top_k([r["score"] for r in records], k)
    return {"ids": ids, "freq": freq, "top": top}
