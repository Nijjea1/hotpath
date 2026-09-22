"""Text statistics. Correct, but written the slow way."""


def tokenize(text):
    """Lowercase words with punctuation stripped, in order."""
    words = []
    for raw in text.split():
        word = ""
        for ch in raw.lower():
            if ch.isalnum():
                word += ch
        if word:
            words.append(word)
    return words


def unique_words(words):
    """Distinct words in first-seen order."""
    seen = []
    for word in words:
        if word not in seen:
            seen.append(word)
    return seen


def word_freq(words):
    """How often each distinct word occurs, keyed in first-seen order."""
    freq = {}
    for word in unique_words(words):
        freq[word] = words.count(word)
    return freq


def top_words(text, k=10):
    """The k most frequent words; ties are broken alphabetically."""
    freq = word_freq(tokenize(text))
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:k]
