"""Independent, obviously-correct reference used by the locked tests."""


def ref_dedupe(items):
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x); out.append(x)
    return out


def ref_freq(words):
    d = {}
    for w in words:
        d[w] = d.get(w, 0) + 1
    return dict(sorted(d.items()))


def ref_top_k(scores, k):
    return sorted(scores, reverse=True)[:k]
