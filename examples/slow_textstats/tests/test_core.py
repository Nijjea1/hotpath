import random
from collections import Counter

from textstats import tokenize, top_words, unique_words, word_freq


def corpus(n_words=12000, vocab=1200, seed=7):
    rng = random.Random(seed)
    words = [f"Word{rng.randrange(vocab)}{rng.choice(['', ',', '.', '!'])}" for _ in range(n_words)]
    return " ".join(words)


def test_tokenize_strips_punctuation_and_case():
    assert tokenize("Hello, WORLD! it's  fine.") == ["hello", "world", "its", "fine"]


def test_unique_words_keeps_first_seen_order():
    assert unique_words(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


def test_word_freq_counts_every_word():
    assert word_freq(["x", "y", "x"]) == {"x": 2, "y": 1}
    assert list(word_freq(["y", "x", "y"])) == ["y", "x"]


def test_top_words_breaks_ties_alphabetically():
    assert top_words("b a c a b", k=2) == [("a", 2), ("b", 2)]


def test_top_words_on_a_large_corpus_matches_a_reference():
    text = corpus()
    words = tokenize(text)
    expected = sorted(Counter(words).items(), key=lambda kv: (-kv[1], kv[0]))[:25]
    assert top_words(text, k=25) == expected
    assert word_freq(words) == dict(Counter(words))
